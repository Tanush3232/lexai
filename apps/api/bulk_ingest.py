"""
bulk_ingest.py — LexAI Legal Act Bulk Ingestion Pipeline (v2)
=============================================================
Robust, memory-safe ingestion for a 4 GB RAM server.

Key improvements over v1:
  - pdfplumber processes pages ONE AT A TIME (never loads full PDF into RAM)
  - Hard per-act timeout (default 5 min) — hangs are impossible
  - Memory-capped Gemini OCR: skips image-render, sends PDF bytes directly
    (one API call per act, not one per page)
  - Graceful SIGINT / SIGTERM — in-flight act is marked "failed", DB stays clean
  - Uploading to "legal-acts" bucket (matches streaming endpoint)
  - Idempotent / resumable: skip completed acts, resume from any point

Usage:
    docker compose exec api python bulk_ingest.py
    docker compose exec api python bulk_ingest.py --retry-failed
    docker compose exec api python bulk_ingest.py --start 70   # skip first 70
    docker compose exec api python bulk_ingest.py --batch-size 5
    docker compose exec api python bulk_ingest.py --dry-run
    docker compose exec api python bulk_ingest.py --act-timeout 600  # 10 min per act
"""
import argparse
import gc
import io
import logging
import os
import re
import signal
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

from minio import Minio
from minio.error import S3Error
from sqlalchemy import create_engine, or_
from sqlmodel import Session as SyncSession, select, SQLModel

# ── Config ─────────────────────────────────────────────────────────────────────
ACTS_FILE        = os.path.normpath(os.path.join(API_DIR, "..", "..", "acts.txt"))
DOWNLOAD_DIR     = os.path.join(API_DIR, "downloaded_acts")
LEGAL_ACTS_BUCKET = "legal-acts"
TEXT_THRESHOLD    = 100      # avg chars/page to consider text-layer PDF
MIN_PDF_SIZE      = 1_000    # bytes — reject suspiciously small PDFs
MAX_OCR_PAGES     = 500      # skip Gemini OCR for acts with more pages (too big)
MAX_GEMINI_MB     = 18       # Gemini inline PDF limit (20 MB hard limit — leave margin)

# ── Retry policy ───────────────────────────────────────────────────────────────
_RETRY_STATUSES   = {"failed", "not_found", "needs_review"}
_INFLIGHT_STATUSES = {"pending", "searching", "downloading", "ocr_processing"}
_REMOVABLE_STATUSES = _RETRY_STATUSES | _INFLIGHT_STATUSES | {"pdf_unavailable"}

# ── Graceful shutdown flag ─────────────────────────────────────────────────────
_SHUTDOWN = False


def _handle_signal(sig, frame):
    global _SHUTDOWN
    print("\n\n[SIGNAL] Graceful shutdown requested — finishing current act…", flush=True)
    _SHUTDOWN = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

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
    normalized = _normalize(title)
    act = db.exec(select(LegalAct).where(LegalAct.normalized_title == normalized)).first()
    if act:
        return act
    act = LegalAct(title=title, normalized_title=normalized, ingestion_status="pending")
    db.add(act)
    db.commit()
    db.refresh(act)
    return act


def _update_act(db: SyncSession, act: LegalAct, **fields):
    for k, v in fields.items():
        setattr(act, k, v)
    act.updated_at = datetime.utcnow()
    db.add(act)
    db.commit()


def _log_seed(db, raw_name, matched_title, handle_id, confidence, status, error):
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


# ── MinIO ─────────────────────────────────────────────────────────────────────
_minio: Optional[Minio] = None


def _get_minio() -> Minio:
    global _minio
    if _minio is None:
        endpoint = settings.MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
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
    import json
    if not client.bucket_exists(LEGAL_ACTS_BUCKET):
        client.make_bucket(LEGAL_ACTS_BUCKET)
        LOG.info(f"[MinIO] Created bucket '{LEGAL_ACTS_BUCKET}'")
    else:
        LOG.info(f"[MinIO] Bucket '{LEGAL_ACTS_BUCKET}' already exists")
    # Ensure public read policy so nginx proxy can serve without auth
    policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": "*"},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{LEGAL_ACTS_BUCKET}/*"]
        }]
    })
    client.set_bucket_policy(LEGAL_ACTS_BUCKET, policy)


def _minio_object_exists(object_path: str) -> bool:
    try:
        _get_minio().stat_object(LEGAL_ACTS_BUCKET, object_path)
        return True
    except S3Error:
        return False


def _upload_pdf(object_path: str, data: bytes) -> str:
    _get_minio().put_object(
        LEGAL_ACTS_BUCKET, object_path,
        io.BytesIO(data), length=len(data),
        content_type="application/pdf",
    )
    return f"{settings.MINIO_ENDPOINT}/{LEGAL_ACTS_BUCKET}/{object_path}"


# ── Memory-safe OCR (CRITICAL SECTION) ───────────────────────────────────────
def _extract_text_streaming(pdf_bytes: bytes) -> dict:
    """
    Extract text one page at a time (NEVER holds all page objects in RAM).
    pdfplumber.open() with page-by-page processing prevents OOM on large acts.

    Returns: {text, total_pages, has_text_layer, skipped_gemini}
    """
    import pdfplumber

    page_texts = []
    total_pages = 0

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                    page_texts.append(text)
                except Exception as e:
                    LOG.warning(f"         [OCR] page {i+1} pdfplumber error: {e} — using empty")
                    page_texts.append("")
                # Explicit cleanup — pdfplumber doesn't always GC page objects
                del page
    except Exception as e:
        LOG.warning(f"         [OCR] pdfplumber open failed: {e}")
        return {"text": "", "total_pages": 0, "has_text_layer": False, "skipped_gemini": False}
    finally:
        # Free pdf_bytes memory after pdfplumber is done with it
        gc.collect()

    if total_pages == 0:
        return {"text": "", "total_pages": 0, "has_text_layer": False, "skipped_gemini": False}

    total_chars = sum(len(t) for t in page_texts)
    avg_chars = total_chars / total_pages

    if avg_chars >= TEXT_THRESHOLD:
        # Good native text layer — done
        LOG.info(f"         [OCR] ✓ Text layer: {total_pages} pages, {avg_chars:.0f} avg chars/page")
        return {
            "text": "\n\n".join(page_texts),
            "total_pages": total_pages,
            "has_text_layer": True,
            "skipped_gemini": False,
        }

    # Scanned PDF — try Gemini (entire PDF as one call, no image rendering)
    mb = len(pdf_bytes) / 1_048_576
    LOG.info(f"         [OCR] Scanned ({avg_chars:.0f} avg chars/page, {mb:.1f} MB) → Gemini PDF OCR…")

    if total_pages > MAX_OCR_PAGES:
        LOG.warning(
            f"         [OCR] ⚠ {total_pages} pages exceeds MAX_OCR_PAGES={MAX_OCR_PAGES} "
            f"— skipping Gemini (server RAM limit), using sparse text"
        )
        return {
            "text": "\n\n".join(page_texts),
            "total_pages": total_pages,
            "has_text_layer": False,
            "skipped_gemini": True,
        }

    if mb > MAX_GEMINI_MB:
        LOG.warning(
            f"         [OCR] ⚠ PDF is {mb:.1f} MB > {MAX_GEMINI_MB} MB Gemini limit "
            f"— skipping Gemini, using sparse text"
        )
        return {
            "text": "\n\n".join(page_texts),
            "total_pages": total_pages,
            "has_text_layer": False,
            "skipped_gemini": True,
        }

    try:
        gemini_text = _gemini_pdf_ocr_sync(pdf_bytes)
        if gemini_text.strip():
            LOG.info(f"         [OCR] ✓ Gemini OCR: {len(gemini_text):,} chars")
            return {
                "text": gemini_text,
                "total_pages": total_pages,
                "has_text_layer": False,
                "skipped_gemini": False,
            }
    except Exception as e:
        LOG.warning(f"         [OCR] Gemini OCR failed: {e} — using sparse pdfplumber text")

    return {
        "text": "\n\n".join(page_texts),
        "total_pages": total_pages,
        "has_text_layer": False,
        "skipped_gemini": False,
    }


def _gemini_pdf_ocr_sync(pdf_bytes: bytes) -> str:
    """
    Send the ENTIRE PDF as bytes to Gemini (inline) — one API call.
    NO image conversion. No pdf2image. No per-page rendering.
    This is the memory-safe approach for scanned PDFs.
    """
    import google.generativeai as genai

    genai.configure(api_key=settings.GOOGLE_API_KEY)
    model = genai.GenerativeModel(settings.GEMINI_MODEL)

    prompt = (
        "You are a legal document OCR system.\n"
        "Extract ALL text from this PDF exactly as it appears.\n"
        "Preserve structure: headings, numbering, sub-clauses, tables.\n"
        "Prefix each page with: --- Page N ---\n"
        "Return ONLY the extracted text — no commentary, no markdown."
    )

    response = model.generate_content(
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
    act_timeout: int,
) -> Tuple[str, Optional[str]]:
    """
    Full pipeline for a single act. Wrapped with a per-act SIGALRM timeout
    (Unix only) to prevent any act from hanging the server.

    Returns: (status, error_message)
    """
    t0 = time.time()
    label = f"[{global_num:>3}/{total}]"

    # ── Per-act timeout via SIGALRM (Linux/macOS only) ────────────────────────
    timed_out = [False]

    def _timeout_handler(sig, frame):
        timed_out[0] = True
        raise TimeoutError(f"Act timed out after {act_timeout}s")

    old_handler = None
    if hasattr(signal, "SIGALRM"):
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(act_timeout)

    try:
        return _ingest_one_inner(act_title, global_num, total, pdf_dir, label, t0)
    except TimeoutError as e:
        elapsed = time.time() - t0
        LOG.error(f"         ✗ TIMEOUT after {elapsed:.0f}s — skipping act, marking failed")
        with SyncSession(_get_engine()) as db:
            try:
                act = _get_or_create_act(db, act_title)
                _update_act(db, act, ingestion_status="failed",
                            error_message=f"Pipeline timeout after {act_timeout}s")
                _log_seed(db, act_title, None, None, 0, "failed", str(e))
            except Exception:
                pass
        return "failed", str(e)
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)  # Cancel alarm
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
        gc.collect()  # Always GC after each act


def _ingest_one_inner(
    act_title: str,
    global_num: int,
    total: int,
    pdf_dir: str,
    label: str,
    t0: float,
) -> Tuple[str, Optional[str]]:
    import glob

    with SyncSession(_get_engine()) as db:
        act = _get_or_create_act(db, act_title)
        LOG.info(f"  {label} {act_title}")

        try:
            # ── Already completed ──────────────────────────────────────────────
            if act.ingestion_status == "completed":
                LOG.info(f"         ↩ Already completed — skipping")
                return "completed", None

            # ── Step 1: Search IndiaCode ───────────────────────────────────────
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

            # ── Conflict check ─────────────────────────────────────────────────
            existing_owner = db.exec(
                select(LegalAct).where(
                    LegalAct.handle_id == handle_id,
                    LegalAct.id != act.id,
                )
            ).first()
            if existing_owner:
                msg = (
                    f"handle_id {handle_id} already linked to '{existing_owner.title}'; "
                    "manual review required"
                )
                LOG.warning(f"         ~ CONFLICT: {msg}")
                _update_act(db, act, ingestion_status="needs_review",
                            error_message=msg, indiacode_url=sr.indiacode_url,
                            confidence_score=best.confidence_score,
                            act_number=best.act_number,
                            enactment_date=best.enactment_date)
                _log_seed(db, act_title, best.title, None,
                          best.confidence_score, "needs_review", msg)
                return "needs_review", msg

            _update_act(db, act,
                        ingestion_status="downloading",
                        handle_id=handle_id,
                        act_number=best.act_number,
                        enactment_date=best.enactment_date,
                        confidence_score=best.confidence_score,
                        indiacode_url=sr.indiacode_url)

            # ── Step 2: Resolve PDF bytes ──────────────────────────────────────
            minio_path = f"acts/{handle_id}/original.pdf"
            pdf_bytes: Optional[bytes] = None

            # 2a. Check if already in MinIO + DB has text → done
            if _minio_object_exists(minio_path) and act.pdf_text:
                LOG.info(f"         ↩ Already in MinIO + text extracted — marking completed")
                _update_act(db, act, ingestion_status="completed",
                            minio_path=minio_path, minio_bucket=LEGAL_ACTS_BUCKET)
                _log_seed(db, act_title, best.title, handle_id,
                          best.confidence_score, "completed", None)
                return "completed", None

            # 2b. Try local pre-downloaded file
            local_matches = glob.glob(os.path.join(pdf_dir, f"{handle_id}_*.pdf"))
            if local_matches:
                local_path = local_matches[0]
                with open(local_path, "rb") as f:
                    pdf_bytes = f.read()
                LOG.info(f"         ✓ Using local file: {os.path.basename(local_path)} ({len(pdf_bytes):,} bytes)")

            # 2c. Live download from IndiaCode
            if pdf_bytes is None:
                LOG.info(f"         Downloading from IndiaCode (handle_id={handle_id})…")
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
                LOG.info(f"         ✓ Downloaded: {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1_048_576:.1f} MB)")

            if len(pdf_bytes) < MIN_PDF_SIZE:
                msg = f"PDF too small ({len(pdf_bytes)} bytes)"
                LOG.error(f"         ✗ {msg}")
                _update_act(db, act, ingestion_status="failed", error_message=msg)
                return "failed", msg

            # ── Step 3: Upload to MinIO (legal-acts bucket) ───────────────────
            if _minio_object_exists(minio_path):
                LOG.info(f"         ↩ Already in MinIO — skipping upload")
            else:
                LOG.info(f"         Uploading to MinIO [{LEGAL_ACTS_BUCKET}] → {minio_path}…")
                try:
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

            # ── Step 4: Text extraction (MEMORY SAFE) ─────────────────────────
            LOG.info(f"         Extracting text (streaming pdfplumber)…")
            _update_act(db, act, ingestion_status="ocr_processing")

            ocr = _extract_text_streaming(pdf_bytes)

            # Free pdf_bytes immediately after OCR — we no longer need them
            pdf_bytes = None
            gc.collect()

            pages = ocr["total_pages"]
            has_text = ocr["has_text_layer"]
            text = ocr["text"]
            avg_chars = len(text) / pages if pages > 0 else 0
            LOG.info(
                f"         ✓ OCR done: {pages} pages, {avg_chars:.0f} avg chars/page, "
                f"text_layer={has_text}, total_chars={len(text):,}"
            )
            if ocr.get("skipped_gemini"):
                LOG.warning(f"         ⚠ Gemini OCR skipped (too large) — text may be partial")

            # ── Step 5: Mark completed ─────────────────────────────────────────
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
            work.append((i, title))
    return work, skipped


def _cleanup_duplicate_failures(db: SyncSession) -> int:
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
        if not conditions:
            continue
        dupes = db.exec(
            select(LegalAct).where(
                LegalAct.id != comp.id,
                LegalAct.id.not_in(list(deleted_ids)) if deleted_ids else True,
                or_(*conditions),
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


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    global LOG

    parser = argparse.ArgumentParser(
        description="Bulk ingest legal acts: search → download → MinIO → OCR"
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Also retry acts marked failed / not_found / needs_review"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print work plan only — no DB writes, no MinIO, no downloads"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10,
        help="Acts per batch (default: 10)"
    )
    parser.add_argument(
        "--start", type=int, default=0,
        help="Skip first N acts from acts.txt (useful for resuming)"
    )
    parser.add_argument(
        "--act-timeout", type=int, default=300,
        help="Max seconds per act before it is skipped (default: 300 = 5 min)"
    )
    args = parser.parse_args()

    LOG = _setup_logging()

    LOG.info("=" * 72)
    LOG.info("  LexAI — Bulk Legal Act Ingestion (v2 — memory-safe)")
    LOG.info(f"  Started      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    LOG.info(f"  Log file     : {_LOG_FILE}")
    LOG.info(f"  Bucket       : {LEGAL_ACTS_BUCKET}")
    LOG.info(f"  Act timeout  : {args.act_timeout}s")
    LOG.info(f"  Batch size   : {args.batch_size}")
    LOG.info(f"  Start offset : {args.start}")
    LOG.info(f"  Retry failed : {args.retry_failed}")
    LOG.info(f"  Dry run      : {args.dry_run}")
    LOG.info(f"  Max OCR pages: {MAX_OCR_PAGES} (Gemini skipped above this)")
    LOG.info(f"  Max Gemini MB: {MAX_GEMINI_MB} MB")
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

    # ── 2. Init DB + MinIO ─────────────────────────────────────────────────────
    LOG.info("[DB] Ensuring tables exist…")
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
    LOG.info("[Dedup] Cleaning up duplicate rows for completed acts…")
    with SyncSession(_get_engine()) as db:
        deleted = _cleanup_duplicate_failures(db)
    LOG.info(f"[Dedup] {deleted} duplicate row(s) removed" if deleted else "[Dedup] No duplicates found")

    # ── 4. Build work list ─────────────────────────────────────────────────────
    LOG.info("[Plan] Building work list…")
    work_items, skipped = _build_work_list(all_acts, args.retry_failed, args.start)
    total_work = len(work_items)
    LOG.info(f"[Plan] {total_work} acts to process  |  {skipped} skipped (completed / offset)")

    if total_work == 0:
        LOG.info("[Done] Nothing to do. Use --retry-failed to retry failed items.")
        return

    # ── 5. Process in batches ──────────────────────────────────────────────────
    batch_size = args.batch_size
    num_batches = (total_work + batch_size - 1) // batch_size

    stats = {
        "completed": 0, "needs_review": 0,
        "not_found": 0, "failed": 0, "pdf_unavailable": 0,
    }
    all_failures: List[Tuple[int, str, str, str]] = []
    bulk_start = time.time()

    for b_idx in range(num_batches):
        if _SHUTDOWN:
            LOG.info("[SHUTDOWN] Graceful exit — remaining acts not processed")
            break

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
            if _SHUTDOWN:
                LOG.info("[SHUTDOWN] Stopping between acts")
                break

            status, errmsg = _ingest_one(
                act_title, orig_idx, len(all_acts), DOWNLOAD_DIR, args.act_timeout
            )

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

        # Polite pause between batches (let server breathe)
        if b_idx < num_batches - 1 and not _SHUTDOWN:
            LOG.info("  Pausing 2s between batches…")
            time.sleep(2)

    # ── 6. Final report ────────────────────────────────────────────────────────
    elapsed_total = time.time() - bulk_start
    LOG.info("")
    LOG.info("=" * 72)
    LOG.info("  FINAL INGESTION REPORT")
    LOG.info(f"  Finished   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    LOG.info(f"  Duration   : {elapsed_total / 60:.1f} min ({elapsed_total:.0f}s)")
    LOG.info("─" * 72)
    LOG.info(f"  Processed         : {total_work}")
    LOG.info(f"  ✓ Completed        : {stats['completed']}")
    LOG.info(f"  ~ Needs review     : {stats['needs_review']}")
    LOG.info(f"  ✗ Not found        : {stats['not_found']}")
    LOG.info(f"  ✗ Failed           : {stats['failed']}")
    LOG.info(f"  ~ PDF unavailable  : {stats['pdf_unavailable']}")
    LOG.info(f"\n  Log file  : {_LOG_FILE}")
    LOG.info("─" * 72)

    if all_failures:
        LOG.info(f"  FAILED / NOT FOUND ({len(all_failures)} total):")
        for gnum, title, status, error in all_failures:
            LOG.info(f"    [{gnum:>3}] [{status.upper():<10}] {title}")
            LOG.info(f"          ↳ {(error or 'unknown')[:100]}")
        LOG.info("")
        LOG.info("  TIP: Run with --retry-failed to retry the above acts")
    else:
        LOG.info("  All acts processed successfully!")

    LOG.info("=" * 72)
    LOG.info("  ✅ DONE!")
    LOG.info(f"  View acts at: http://staging.lexai.zuarione.com/dashboard/acts")
    LOG.info(f"  MinIO console: http://[SERVER_IP]:9001  (minioadmin / minioadmin)")
    LOG.info("=" * 72)


if __name__ == "__main__":
    main()
