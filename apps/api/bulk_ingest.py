"""
bulk_ingest.py — LexAI Legal Act Bulk Ingestion Pipeline
==========================================================
Reads all PDFs from `downloaded_acts/`, uploads each to MinIO,
extracts text (with Gemini OCR fallback for scanned PDFs),
and saves the full act record to PostgreSQL.

This script is IDEMPOTENT — it tracks ingestion status per act
in the `legal_acts` table and skips any act already marked 'completed'.
It is SAFE to interrupt and resume at any time.

Pre-requisites:
  1. Run:  docker compose exec api python scripts/seed.py
  2. Run:  docker compose exec api python batch_download.py
  3. Then: docker compose exec api python bulk_ingest.py

Usage:
    docker compose exec api python bulk_ingest.py
    docker compose exec api python bulk_ingest.py --retry-failed
    docker compose exec api python bulk_ingest.py --dry-run
    docker compose exec api python bulk_ingest.py --batch-size 5
"""
import argparse
import asyncio
import io
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# ── Make 'app' package importable ─────────────────────────────────────────────
API_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, API_DIR)

from app.core.config import settings
from app.core.title_matching import normalize_legal_act_title
from app.models.legal_act import LegalAct, LegalActSeedLog
from app.services.indiacode_scraper import search_act, download_act_pdf

import pdfplumber
from minio import Minio
from minio.error import S3Error
from sqlalchemy import create_engine, or_
from sqlmodel import Session as SyncSession, select, SQLModel

# ── Config ─────────────────────────────────────────────────────────────────────
ACTS_FILE = os.path.normpath(os.path.join(API_DIR, "..", "..", "acts.txt"))
DOWNLOAD_DIR = os.path.join(API_DIR, "downloaded_acts")
LEGAL_ACTS_BUCKET = "legal-acts"
TEXT_THRESHOLD = 100   # avg chars/page to consider a text-layer (not scanned) PDF
MIN_PDF_SIZE = 1_000   # bytes

# Statuses that should be retried when --retry-failed is set
_RETRY_STATUSES = {"failed", "not_found", "needs_review"}
# Statuses that were interrupted mid-run — always retry
_INFLIGHT_STATUSES = {"pending", "searching", "downloading", "ocr_processing"}
# All non-completed statuses (used for dedup cleanup)
_REMOVABLE_STATUSES = _RETRY_STATUSES | _INFLIGHT_STATUSES | {"pdf_unavailable"}

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_FILE = os.path.join(API_DIR, f"ingestion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def _setup_logging() -> logging.Logger:
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    try:
        ch.stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    root.addHandler(ch)

    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger("bulk_ingest")


LOG: logging.Logger = None  # initialized in main()


# ── Database (sync) ───────────────────────────────────────────────────────────
_sync_engine = None


def _get_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            settings.POSTGRES_SYNC_URL, echo=False, pool_pre_ping=True
        )
    return _sync_engine


def _ensure_tables():
    """Create legal_acts and legal_acts_seed_log tables if they don't exist."""
    try:
        SQLModel.metadata.create_all(
            _get_engine(),
            tables=[LegalAct.__table__, LegalActSeedLog.__table__],
            checkfirst=True,
        )
    except Exception as e:
        LOG.warning(f"[DB] Table creation warning (may already exist): {e}")


def _normalize(title: str) -> str:
    return normalize_legal_act_title(title)


def _get_or_create_act(db: SyncSession, title: str) -> LegalAct:
    """Return the existing LegalAct row or insert a new pending one."""
    normalized = _normalize(title)
    act = db.exec(
        select(LegalAct).where(LegalAct.normalized_title == normalized)
    ).first()
    if act:
        return act
    act = LegalAct(
        title=title,
        normalized_title=normalized,
        ingestion_status="pending",
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    return act


def _update_act(db: SyncSession, act: LegalAct, **fields):
    """Patch act fields and commit."""
    for k, v in fields.items():
        setattr(act, k, v)
    act.updated_at = datetime.utcnow()
    db.add(act)
    db.commit()


def _log_seed(
    db: SyncSession,
    raw_name: str,
    matched_title: Optional[str],
    handle_id: Optional[str],
    confidence: float,
    status: str,
    error: Optional[str],
):
    entry = LegalActSeedLog(
        raw_name=raw_name,
        matched_title=matched_title,
        handle_id=handle_id,
        confidence_score=confidence,
        status=status,
        error=error,
    )
    db.add(entry)
    db.commit()


def _cleanup_duplicate_failures(db: SyncSession) -> int:
    """
    Delete non-completed rows that are duplicate entries of an already-completed act.
    Matches by handle_id, normalized_title, or raw title.
    Safe to run multiple times (idempotent).
    Returns the number of rows deleted.
    """
    completed_acts = db.exec(
        select(LegalAct).where(LegalAct.ingestion_status == "completed")
    ).all()

    deleted_ids: set = set()
    deleted = 0

    for comp in completed_acts:
        conditions = []
        if comp.handle_id:
            conditions.append(LegalAct.handle_id == comp.handle_id)
        if comp.normalized_title:
            conditions.append(LegalAct.normalized_title == comp.normalized_title)
        if comp.title:
            conditions.append(LegalAct.title == comp.title)

        if not conditions:
            continue

        match_clause = or_(*conditions)
        dupes = db.exec(
            select(LegalAct).where(
                LegalAct.id != comp.id,
                LegalAct.id.not_in(list(deleted_ids)) if deleted_ids else True,
                match_clause,
                LegalAct.ingestion_status.in_(list(_REMOVABLE_STATUSES)),
            )
        ).all()

        for dupe in dupes:
            if dupe.id in deleted_ids:
                continue
            LOG.info(
                f"[Dedup] Removing '{dupe.title}' ({dupe.ingestion_status}) "
                f"— already completed as '{comp.title}'"
            )
            db.delete(dupe)
            deleted_ids.add(dupe.id)
            deleted += 1

    if deleted:
        db.commit()
    return deleted


# ── MinIO ─────────────────────────────────────────────────────────────────────
_minio: Optional[Minio] = None


def _get_minio() -> Minio:
    global _minio
    if _minio is None:
        endpoint = (
            settings.MINIO_ENDPOINT
            .replace("http://", "")
            .replace("https://", "")
        )
        secure = settings.MINIO_ENDPOINT.startswith("https://")
        _minio = Minio(
            endpoint,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=secure,
        )
    return _minio


def _ensure_bucket():
    client = _get_minio()
    if not client.bucket_exists(LEGAL_ACTS_BUCKET):
        client.make_bucket(LEGAL_ACTS_BUCKET)
        LOG.info(f"[MinIO] Created bucket '{LEGAL_ACTS_BUCKET}'")
    else:
        LOG.info(f"[MinIO] Bucket '{LEGAL_ACTS_BUCKET}' already exists")


def _minio_object_exists(object_path: str) -> bool:
    """Return True if the object already exists in MinIO (idempotency check)."""
    try:
        _get_minio().stat_object(LEGAL_ACTS_BUCKET, object_path)
        return True
    except S3Error:
        return False


def _upload_pdf(object_path: str, data: bytes) -> str:
    """Upload PDF bytes to MinIO and return the internal URL."""
    _get_minio().put_object(
        LEGAL_ACTS_BUCKET,
        object_path,
        io.BytesIO(data),
        length=len(data),
        content_type="application/pdf",
    )
    return f"{settings.MINIO_ENDPOINT}/{LEGAL_ACTS_BUCKET}/{object_path}"


# ── Text Extraction (pdfplumber + Gemini OCR fallback) ───────────────────────
def _extract_text(pdf_bytes: bytes) -> dict:
    """
    Extract text from PDF bytes.
    Strategy:
      1. Try pdfplumber (fast, free, works for text-layer PDFs).
      2. If avg chars/page < TEXT_THRESHOLD, fall back to Gemini Vision OCR
         (handles scanned / image-only PDFs).
    Returns: {text: str, total_pages: int, has_text_layer: bool}
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
            texts = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        LOG.warning(f"         [OCR] pdfplumber failed: {e}")
        return {"text": "", "total_pages": 0, "has_text_layer": False}

    if total == 0:
        return {"text": "", "total_pages": 0, "has_text_layer": False}

    avg_chars = sum(len(t) for t in texts) / total

    if avg_chars >= TEXT_THRESHOLD:
        return {
            "text": "\n\n".join(texts),
            "total_pages": total,
            "has_text_layer": True,
        }

    # Scanned PDF — use Gemini Direct PDF parse (V3 approach)
    LOG.info(f"         [OCR] Sparse text ({avg_chars:.0f} avg chars/page) → Gemini OCR fallback…")
    try:
        gemini_text = asyncio.run(_gemini_pdf_ocr(pdf_bytes))
        if gemini_text.strip():
            return {"text": gemini_text, "total_pages": total, "has_text_layer": False}
    except Exception as e:
        LOG.warning(f"         [OCR] Gemini OCR fallback failed: {e} — using sparse pdfplumber text")

    # Last resort: use the sparse pdfplumber text anyway
    return {
        "text": "\n\n".join(texts),
        "total_pages": total,
        "has_text_layer": False,
    }


async def _gemini_pdf_ocr(pdf_bytes: bytes) -> str:
    """
    Send the whole PDF directly to Gemini for text extraction.
    This is the V3 approach — one API call per document, no per-page rendering needed.
    Requires GOOGLE_API_KEY to be set in .env.
    """
    import google.generativeai as genai

    genai.configure(api_key=settings.GOOGLE_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    prompt = (
        "You are a legal document OCR and text-extraction system.\n"
        "Extract ALL text from this document exactly as it appears.\n"
        "Preserve the original structure: headings, numbering, sub-clauses, "
        "paragraphs, tables, and lists.\n"
        "For each page, prefix the content with a line: --- Page N ---\n"
        "Return ONLY the extracted text — no commentary, no markdown fences, no labels."
    )

    response = await model.generate_content_async(
        [{"mime_type": "application/pdf", "data": pdf_bytes}, prompt],
        request_options={"timeout": 300},
    )
    return (response.text or "").strip()


# ── Acts file parser ───────────────────────────────────────────────────────────
def _parse_acts_file() -> List[str]:
    if not os.path.exists(ACTS_FILE):
        LOG.error(f"acts.txt not found at: {ACTS_FILE}")
        sys.exit(1)

    acts: List[str] = []
    with open(ACTS_FILE, encoding="utf-8") as fh:
        for line in fh:
            name = line.strip()
            if not name:
                continue
            if name.lower().startswith("here is") or name.startswith("---"):
                continue
            name = re.sub(r"^\d+[.)]\s*", "", name).strip()
            if len(name) > 5:
                acts.append(name)
    return acts


# ── Per-act ingestion pipeline ────────────────────────────────────────────────
def _ingest_one(
    act_title: str,
    global_num: int,
    total: int,
    pdf_dir: str,
) -> Tuple[str, Optional[str]]:
    """
    Full ingestion pipeline for a single act.
    Tries to use a pre-downloaded PDF from `pdf_dir` first.
    If not found, falls back to downloading live from IndiaCode.
    Opens its own DB session — never stale state.
    Returns: (status, error_message)
    """
    t0 = time.time()
    label = f"[{global_num:>3}/{total}]"

    with SyncSession(_get_engine()) as db:
        act = _get_or_create_act(db, act_title)
        LOG.info(f"  {label} {act_title}")

        try:
            # ── Step 1: Resolve PDF bytes (local file first, else download) ──
            pdf_bytes: Optional[bytes] = None
            handle_id: Optional[str] = None
            matched_title: Optional[str] = None

            # Check if already in MinIO (skip entire act — truly completed)
            # Look for a pre-downloaded file keyed by handle_id from DB
            if act.handle_id and act.ingestion_status == "completed":
                LOG.info(f"         ↩ Already completed — skipping")
                return "completed", None

            # ── Sub-step 1a: Search IndiaCode for metadata ────────────────────
            LOG.info(f"         Searching IndiaCode…")
            _update_act(db, act, ingestion_status="searching")
            sr = search_act(act_title)

            if sr.status == "not_found":
                msg = sr.error_message or "No match found"
                LOG.warning(f"         ✗ NOT FOUND: {msg}")
                _update_act(db, act, ingestion_status="not_found",
                            error_message=msg, indiacode_url=sr.indiacode_url)
                _log_seed(db, act_title, None, None, 0, "not_found", msg)
                return "not_found", msg

            if sr.status == "failed":
                msg = sr.error_message or "Search request failed"
                LOG.error(f"         ✗ SEARCH FAILED: {msg[:120]}")
                _update_act(db, act, ingestion_status="failed", error_message=msg)
                _log_seed(db, act_title, None, None, 0, "failed", msg)
                return "failed", msg

            best = sr.best_match
            handle_id = best.handle_id
            matched_title = best.title
            LOG.info(f"         ✓ Matched: '{best.title}' (score={best.confidence_score})")

            if sr.status == "needs_review":
                LOG.warning(f"         ⚠ Low confidence ({best.confidence_score}) — proceeding with review flag")

            # Check if another act already owns this handle_id (conflict)
            existing_owner = db.exec(
                select(LegalAct).where(
                    LegalAct.handle_id == handle_id,
                    LegalAct.id != act.id,
                )
            ).first()
            if existing_owner:
                msg = (
                    f"handle_id {handle_id} already linked to '{existing_owner.title}'; "
                    f"manual review required"
                )
                LOG.warning(f"         ~ CONFLICT: {msg}")
                _update_act(db, act, ingestion_status="needs_review",
                            error_message=msg, indiacode_url=sr.indiacode_url,
                            confidence_score=best.confidence_score,
                            act_number=best.act_number,
                            enactment_date=best.enactment_date)
                _log_seed(db, act_title, best.title, None, best.confidence_score, "needs_review", msg)
                return "needs_review", msg

            _update_act(db, act,
                        ingestion_status="downloading",
                        handle_id=handle_id,
                        act_number=best.act_number,
                        enactment_date=best.enactment_date,
                        confidence_score=best.confidence_score,
                        indiacode_url=sr.indiacode_url)

            # ── Sub-step 1b: Get PDF bytes ────────────────────────────────────
            minio_path = f"acts/{handle_id}/original.pdf"

            # Check if already in MinIO (idempotent — skip re-upload)
            if _minio_object_exists(minio_path):
                LOG.info(f"         ↩ Already in MinIO — skipping download & upload")
                pdf_bytes = None  # We won't re-extract text if already completed
                # If DB says not completed, we need to get bytes for OCR
                if act.pdf_text:
                    LOG.info(f"         ↩ Text already extracted — marking completed")
                    _update_act(db, act, ingestion_status="completed",
                                minio_path=minio_path, minio_bucket=LEGAL_ACTS_BUCKET)
                    _log_seed(db, act_title, best.title, handle_id,
                              best.confidence_score, "completed", None)
                    return "completed", None

            # Try local pre-downloaded file first
            if pdf_bytes is None:
                local_pattern = os.path.join(pdf_dir, f"{handle_id}_*.pdf")
                import glob
                local_matches = glob.glob(local_pattern)
                if local_matches:
                    local_path = local_matches[0]
                    with open(local_path, "rb") as f:
                        pdf_bytes = f.read()
                    LOG.info(f"         ✓ Using local file: {os.path.basename(local_path)}")

            # Fall back to live download from IndiaCode
            if pdf_bytes is None:
                LOG.info(f"         Downloading PDF from IndiaCode (handle_id={handle_id})…")
                dl = download_act_pdf(handle_id)

                if dl.status == "pdf_unavailable":
                    msg = dl.error_message or "PDF not available — Act under updation"
                    LOG.warning(f"         ~ PDF UNAVAILABLE: {msg}")
                    _update_act(db, act, ingestion_status="pdf_unavailable",
                                error_message=msg, bitstream_url=dl.bitstream_url,
                                indiacode_url=dl.indiacode_url)
                    _log_seed(db, act_title, best.title, handle_id,
                              best.confidence_score, "pdf_unavailable", msg)
                    return "pdf_unavailable", msg

                if dl.status != "completed" or not dl.pdf_bytes:
                    msg = dl.error_message or "Download returned no bytes"
                    LOG.error(f"         ✗ DOWNLOAD FAILED: {msg[:120]}")
                    _update_act(db, act, ingestion_status="failed",
                                error_message=msg, bitstream_url=dl.bitstream_url)
                    _log_seed(db, act_title, best.title, handle_id,
                              best.confidence_score, "failed", msg)
                    return "failed", msg

                pdf_bytes = dl.pdf_bytes
                LOG.info(f"         ✓ Downloaded: {len(pdf_bytes):,} bytes")

            if len(pdf_bytes) < MIN_PDF_SIZE:
                msg = f"PDF too small ({len(pdf_bytes)} bytes)"
                LOG.error(f"         ✗ {msg}")
                _update_act(db, act, ingestion_status="failed", error_message=msg)
                return "failed", msg

            # ── Step 2: Upload to MinIO ───────────────────────────────────────
            LOG.info(f"         Uploading to MinIO → {minio_path}…")
            try:
                if _minio_object_exists(minio_path):
                    LOG.info(f"         ↩ Already in MinIO — skipping upload")
                else:
                    _upload_pdf(minio_path, pdf_bytes)
                    LOG.info(f"         ✓ Uploaded to MinIO")
            except S3Error as e:
                msg = f"MinIO upload error: {e}"
                LOG.error(f"         ✗ {msg}")
                _update_act(db, act, ingestion_status="failed", error_message=msg)
                _log_seed(db, act_title, best.title, handle_id,
                          best.confidence_score, "failed", msg)
                return "failed", msg

            _update_act(db, act, minio_path=minio_path, minio_bucket=LEGAL_ACTS_BUCKET)

            # ── Step 3: Text Extraction ───────────────────────────────────────
            LOG.info(f"         Extracting text (pdfplumber + Gemini OCR fallback)…")
            _update_act(db, act, ingestion_status="ocr_processing")

            ocr = _extract_text(pdf_bytes)
            pages = ocr["total_pages"]
            has_text = ocr["has_text_layer"]
            text = ocr["text"]
            avg_chars = len(text) / pages if pages > 0 else 0
            LOG.info(
                f"         ✓ Text: {pages} pages, {avg_chars:.0f} avg chars/page, "
                f"text_layer={has_text}, total_chars={len(text):,}"
            )

            # ── Step 4: Mark completed ────────────────────────────────────────
            elapsed = time.time() - t0
            final_status = "completed" if sr.status != "needs_review" else "needs_review"
            _update_act(db, act,
                        ingestion_status=final_status,
                        pdf_text=text,
                        total_pages=pages,
                        has_text_layer=has_text)
            _log_seed(db, act_title, best.title, handle_id,
                      best.confidence_score, final_status, None)
            LOG.info(f"         ✓ {final_status.upper()} in {elapsed:.1f}s")
            return final_status, None

        except Exception as exc:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            LOG.error(f"         ✗ EXCEPTION after {elapsed:.1f}s: {exc}")
            LOG.debug(tb)
            try:
                _update_act(db, act, ingestion_status="failed",
                            error_message=f"{exc}\n{tb[:500]}")
                _log_seed(db, act_title, None, None, 0, "failed", str(exc))
            except Exception:
                pass
            return "failed", str(exc)


# ── Work-list builder ─────────────────────────────────────────────────────────
def _build_work_list(
    act_titles: List[str],
    retry_failed: bool,
    start_offset: int,
) -> Tuple[List[Tuple[int, str]], int]:
    """
    Upsert all acts into DB, then build list of acts that still need processing.
    Returns: (work_items, skipped_count)
    """
    work: List[Tuple[int, str]] = []
    skipped = 0

    with SyncSession(_get_engine()) as db:
        for i, title in enumerate(act_titles, start=1):
            act = _get_or_create_act(db, title)

            if i <= start_offset:
                skipped += 1
                continue

            status = act.ingestion_status
            if status == "completed":
                skipped += 1
                continue
            if status in _RETRY_STATUSES and not retry_failed:
                skipped += 1
                continue
            # pending, in-flight (interrupted), or retry-eligible
            work.append((i, title))

    return work, skipped


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    global LOG

    parser = argparse.ArgumentParser(
        description="Bulk ingest all legal acts: PDFs → MinIO → PostgreSQL"
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Also retry acts marked failed / not_found / needs_review"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the work plan only — no DB writes, no MinIO, no downloads"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10,
        help="Number of acts per batch (default: 10)"
    )
    parser.add_argument(
        "--start", type=int, default=0,
        help="Skip first N acts from acts.txt (useful for resuming)"
    )
    args = parser.parse_args()

    LOG = _setup_logging()

    LOG.info("=" * 72)
    LOG.info("  LexAI — Bulk Legal Act Ingestion")
    LOG.info(f"  Started    : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    LOG.info(f"  Log file   : {_LOG_FILE}")
    LOG.info(
        f"  Options    : retry_failed={args.retry_failed}  "
        f"dry_run={args.dry_run}  "
        f"batch_size={args.batch_size}  "
        f"start_offset={args.start}"
    )
    LOG.info("=" * 72)

    # ── 1. Parse acts.txt ──────────────────────────────────────────────────────
    all_acts = _parse_acts_file()
    LOG.info(f"[Acts] {len(all_acts)} acts loaded from {ACTS_FILE}")

    if args.dry_run:
        LOG.info("\n[DRY RUN] Full act list:")
        for i, name in enumerate(all_acts, 1):
            LOG.info(f"  {i:>3}. {name}")
        n_batches = (len(all_acts) + args.batch_size - 1) // args.batch_size
        LOG.info(f"\nTotal: {len(all_acts)} acts → {n_batches} batches of ≤{args.batch_size}")
        return

    # ── 2. Init DB tables & MinIO bucket ──────────────────────────────────────
    LOG.info("[DB] Ensuring legal_acts tables exist…")
    _ensure_tables()
    LOG.info("[DB] OK")

    LOG.info("[MinIO] Checking bucket…")
    try:
        _ensure_bucket()
    except Exception as e:
        LOG.error(f"[MinIO] Cannot connect: {e}")
        LOG.error("Make sure MinIO is running: docker compose up -d minio")
        sys.exit(1)

    # ── 3. Deduplication cleanup ───────────────────────────────────────────────
    LOG.info("[Dedup] Cleaning up duplicate failed/pending rows for completed acts…")
    with SyncSession(_get_engine()) as db:
        deleted = _cleanup_duplicate_failures(db)
    LOG.info(f"[Dedup] {deleted} duplicate row(s) removed" if deleted else "[Dedup] No duplicates found")

    # ── 4. Build work list ─────────────────────────────────────────────────────
    LOG.info("[Plan] Building work list…")
    work_items, skipped = _build_work_list(all_acts, args.retry_failed, args.start)
    total_work = len(work_items)
    LOG.info(f"[Plan] {total_work} acts to process  |  {skipped} skipped (completed/failed/offset)")

    if total_work == 0:
        LOG.info("[Done] Nothing to process. Use --retry-failed to re-run failed items.")
        return

    # ── 5. Process in batches ──────────────────────────────────────────────────
    batch_size = args.batch_size
    num_batches = (total_work + batch_size - 1) // batch_size

    stats = {
        "completed": 0, "needs_review": 0,
        "not_found": 0, "failed": 0, "pdf_unavailable": 0
    }
    all_failures: List[Tuple[int, str, str, str]] = []
    bulk_start = time.time()

    for b_idx in range(num_batches):
        batch = work_items[b_idx * batch_size: (b_idx + 1) * batch_size]

        LOG.info("")
        LOG.info("─" * 72)
        LOG.info(
            f"  BATCH {b_idx + 1}/{num_batches}  ─  "
            f"Acts {b_idx * batch_size + 1}–{b_idx * batch_size + len(batch)} of {total_work}  "
            f"(overall #{batch[0][0]}–#{batch[-1][0]})"
        )
        LOG.info("─" * 72)

        batch_stats = {k: 0 for k in stats}
        batch_failures = []

        for orig_idx, act_title in batch:
            status, errmsg = _ingest_one(act_title, orig_idx, len(all_acts), DOWNLOAD_DIR)

            key = status if status in stats else "failed"
            stats[key] += 1
            batch_stats[key] += 1

            if status in ("failed", "not_found"):
                short_err = (errmsg or "")[:120]
                batch_failures.append((orig_idx, act_title, short_err))
                all_failures.append((orig_idx, act_title, status, errmsg or ""))

        # Batch summary
        LOG.info("")
        LOG.info(
            f"  BATCH {b_idx + 1} RESULT: "
            f"{batch_stats['completed']} ok  "
            f"{batch_stats['needs_review']} ~review  "
            f"{batch_stats['not_found']} not_found  "
            f"{batch_stats['failed']} failed  "
            f"{batch_stats['pdf_unavailable']} pdf_unavailable"
        )
        if batch_failures:
            LOG.info(f"  Failures this batch:")
            for gnum, title, err in batch_failures:
                LOG.info(f"    [{gnum:>3}] {title}")
                LOG.info(f"          ↳ {err}")

        # Polite pause between batches
        if b_idx < num_batches - 1:
            LOG.info("  Pausing 3s between batches…")
            time.sleep(3)

    # ── 6. Final report ────────────────────────────────────────────────────────
    elapsed_total = time.time() - bulk_start
    LOG.info("")
    LOG.info("=" * 72)
    LOG.info("  FINAL INGESTION REPORT")
    LOG.info(f"  Finished   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    LOG.info(f"  Duration   : {elapsed_total / 60:.1f} min ({elapsed_total:.0f}s)")
    LOG.info("─" * 72)
    LOG.info(f"  Processed       : {total_work}")
    LOG.info(f"  ✓ Completed      : {stats['completed']}")
    LOG.info(f"  ~ Needs review   : {stats['needs_review']}  (score 60-84, viewable in admin UI)")
    LOG.info(f"  ✗ Not found      : {stats['not_found']}  (no match on IndiaCode)")
    LOG.info(f"  ✗ Failed         : {stats['failed']}  (download/upload/parsing error)")
    LOG.info(f"  ~ PDF unavailable: {stats['pdf_unavailable']}  (Act under updation on IndiaCode)")
    LOG.info(f"\n  Log file  : {_LOG_FILE}")
    LOG.info("─" * 72)

    if all_failures:
        LOG.info(f"  FAILED / NOT FOUND ({len(all_failures)} total):")
        for gnum, title, status, error in all_failures:
            LOG.info(f"    [{gnum:>3}] [{status.upper():<10}] {title}")
            LOG.info(f"          ↳ {(error or 'unknown')[:100]}")
    else:
        LOG.info("  All acts processed successfully!")

    LOG.info("=" * 72)
    LOG.info("  ✅ DONE!")
    LOG.info(f"  View acts at: http://[YOUR_IP]/dashboard/acts")
    LOG.info(f"  MinIO console: http://[YOUR_IP]:9001  (minioadmin / minioadmin)")
    LOG.info("=" * 72)


if __name__ == "__main__":
    main()
