"""
Legal Acts API routes — search, seed, confirm, list, detail, status, upload, review.
"""
import io
import os
import re
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, UploadFile, File, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func, col

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.storage import get_storage_client, upload_file as upload_to_minio
from app.core.config import settings
from app.core.logging import get_logger
from app.core.title_matching import normalize_legal_act_title
from app.models.user import User
from app.models.audit import AuditLog
from app.models.legal_act import (
    LegalAct,
    LegalActSeedLog,
    LegalActRead,
    LegalActDetail,
    LegalActStatusResponse,
    LegalActCandidate,
)
from app.services.audit_service import log_action

logger = get_logger("acts_router")

router = APIRouter()

# ── Acts list file path (relative to api app root) ──
ACTS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "acts.txt")


def _parse_acts_file() -> List[str]:
    """Parse the acts.txt file and return clean act names."""
    acts_path = os.path.normpath(ACTS_FILE)
    if not os.path.exists(acts_path):
        raise HTTPException(status_code=404, detail="acts.txt file not found")

    with open(acts_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    act_names = []
    for line in lines:
        # Strip numbering, bullets, whitespace
        name = line.strip()
        if not name:
            continue
        # Skip header lines
        if name.lower().startswith("here is") or name.lower().startswith("---"):
            continue
        # Strip leading numbers like "1. " or "1) "
        name = re.sub(r'^\d+[\.\)]\s*', '', name)
        name = name.strip()
        if len(name) > 5:  # skip very short/empty lines
            act_names.append(name)

    return act_names


def _normalize_title(title: str) -> str:
    """Normalize for dedup matching."""
    return normalize_legal_act_title(title)


def _clean_user_act_query(title: str) -> str:
    """Clean noisy suffixes commonly seen in manually entered act names."""
    t = title.strip()
    # Keep the primary act name; strip catalog/explanatory suffixes.
    t = re.sub(r'\s*-\s*.*$', '', t)
    t = re.sub(r'\s+along\s+with\s+.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+with\s+.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+including\s+.*$', '', t, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', t).strip()


# ─── POST /api/v1/acts/seed ─────────────────────────────────


@router.post("/seed")
async def seed_acts(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Parse acts.txt, insert pending rows for each act, enqueue Celery tasks.
    Idempotent — skips acts whose normalized_title already exists.
    """
    act_names = _parse_acts_file()

    from app.tasks.act_ingestion_tasks import ingest_legal_act

    queued = 0
    skipped = 0

    for name in act_names:
        normalized = _normalize_title(name)

        # Check if already exists by normalized title OR exact raw title
        existing = await session.exec(
            select(LegalAct).where(
                (LegalAct.normalized_title == normalized) | (LegalAct.title == name)
            )
        )
        if existing.first():
            skipped += 1
            continue

        act = LegalAct(
            title=name,
            normalized_title=normalized,
            ingestion_status="pending",
        )
        session.add(act)
        try:
            await session.flush()
        except IntegrityError:
            # title unique constraint — already exists
            await session.rollback()
            skipped += 1
            continue

        # Enqueue celery task
        ingest_legal_act.delay(act.id, name)
        queued += 1

    await session.commit()
    await log_action(session, current_user.id, "seed_acts", "legal_act", details={"queued": queued, "skipped": skipped})

    logger.info("acts.seed_complete", queued=queued, skipped=skipped, total=len(act_names))
    return {"queued": queued, "skipped": skipped, "total": len(act_names)}


# ─── POST /api/v1/acts/add ──────────────────────────────────


@router.post("/add")
async def add_act(
    body: dict,
    current_user: User = Depends(get_current_user),
):
    """
    Search IndiaCode for an act name. Returns top 3 candidates.
    Does NOT ingest — use /confirm to trigger ingestion.
    """
    act_name = body.get("act_name", "").strip()
    if not act_name:
        raise HTTPException(status_code=400, detail="act_name is required")

    cleaned_name = _clean_user_act_query(act_name)
    if len(cleaned_name) < 3:
        raise HTTPException(status_code=400, detail="act_name is too short")

    from app.services.indiacode_scraper import search_and_get_candidates

    candidates = search_and_get_candidates(cleaned_name, top_n=3)

    return [
        LegalActCandidate(
            title=c.title,
            handle_id=c.handle_id,
            act_number=c.act_number,
            enactment_date=c.enactment_date,
            confidence_score=c.confidence_score,
        )
        for c in candidates
    ]


# ─── POST /api/v1/acts/confirm ──────────────────────────────


@router.post("/confirm")
async def confirm_act(
    body: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Confirm a candidate and trigger ingestion (download → OCR).
    """
    handle_id = body.get("handle_id")
    title = body.get("title", "")
    act_number = body.get("act_number")
    enactment_date = body.get("enactment_date")

    if not handle_id or not title:
        raise HTTPException(status_code=400, detail="handle_id and title are required")

    cleaned_title = _clean_user_act_query(title)
    normalized_title = _normalize_title(cleaned_title)

    # Idempotent check by handle_id first.
    existing_by_handle = await session.exec(
        select(LegalAct).where(LegalAct.handle_id == handle_id)
    )
    existing_handle_act = existing_by_handle.first()
    if existing_handle_act:
        return {
            "id": existing_handle_act.id,
            "status": existing_handle_act.ingestion_status,
            "already_exists": True,
            "message": "Act with this handle_id already exists",
        }

    # Secondary idempotent check by normalized title.
    existing_by_title = await session.exec(
        select(LegalAct).where(LegalAct.normalized_title == normalized_title)
    )
    existing_title_act = existing_by_title.first()
    if existing_title_act:
        return {
            "id": existing_title_act.id,
            "status": existing_title_act.ingestion_status,
            "already_exists": True,
            "message": "Act with this title already exists",
        }

    act = LegalAct(
        title=cleaned_title,
        normalized_title=normalized_title,
        handle_id=handle_id,
        # act_number is intentionally NOT set here. 
        # PostgreSQL's default expression ('LEX-' || nextval('lex_act_no_seq'))
        # will automatically assign the next unique LEX-XXXXX system ID.
        enactment_date=enactment_date,
        ingestion_status="pending",
    )
    session.add(act)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

        # Resolve race conditions cleanly for UI callers.
        existing = await session.exec(
            select(LegalAct).where(
                (LegalAct.handle_id == handle_id) | (LegalAct.normalized_title == normalized_title)
            )
        )
        winner = existing.first()
        if winner:
            return {
                "id": winner.id,
                "status": winner.ingestion_status,
                "already_exists": True,
                "message": "Act already exists (created in parallel)",
            }
        raise HTTPException(status_code=409, detail="Act could not be created due to duplicate key")

    await session.refresh(act)
    await log_action(session, current_user.id, "confirm_act", "legal_act", act.id)

    from app.tasks.act_ingestion_tasks import ingest_legal_act
    ingest_legal_act.delay(act.id, title, handle_id)

    return {"id": act.id, "status": "pending", "already_exists": False}


# ─── GET /api/v1/acts ───────────────────────────────────────

_NOT_COMPLETED_STATUSES = [
    "pending", "searching", "downloading", "ocr_processing",
    "failed", "not_found", "needs_review", "pdf_unavailable",
]


@router.get("/")
async def list_acts(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = None,
    review: Optional[str] = None,
    search: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Paginated list of legal acts with total count.
    status filter: 'completed', 'not_completed', or omit for all.
    review filter: 'reviewed' to show only manually reviewed acts.
    """
    base = select(LegalAct)
    count_q = select(func.count()).select_from(LegalAct)

    if status == "completed":
        base = base.where(LegalAct.ingestion_status == "completed")
        count_q = count_q.where(LegalAct.ingestion_status == "completed")
    elif status == "not_completed":
        base = base.where(col(LegalAct.ingestion_status).in_(_NOT_COMPLETED_STATUSES))
        count_q = count_q.where(col(LegalAct.ingestion_status).in_(_NOT_COMPLETED_STATUSES))
    elif status:
        base = base.where(LegalAct.ingestion_status == status)
        count_q = count_q.where(LegalAct.ingestion_status == status)

    if review == "reviewed":
        base = base.where(LegalAct.review_status == "manually_reviewed")
        count_q = count_q.where(LegalAct.review_status == "manually_reviewed")

    if search:
        term = f"%{search}%"
        base = base.where(
            col(LegalAct.title).ilike(term)
            | col(LegalAct.ministry).ilike(term)
            | col(LegalAct.review_status).ilike(term)
        )
        count_q = count_q.where(
            col(LegalAct.title).ilike(term)
            | col(LegalAct.ministry).ilike(term)
            | col(LegalAct.review_status).ilike(term)
        )

    total_result = await session.exec(count_q)
    total = total_result.one()

    base = base.order_by(LegalAct.created_at.desc())
    base = base.offset((page - 1) * limit).limit(limit)
    result = await session.exec(base)
    items = result.all()

    return {
        "items": [LegalActRead.model_validate(a) for a in items],
        "total": total,
        "page": page,
        "limit": limit,
    }


# ─── GET /api/v1/acts/search ────────────────────────────────


@router.get("/search")
async def search_acts(
    q: str = Query(..., min_length=2),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Full-text search on completed acts (title + pdf_text)."""
    query = (
        select(LegalAct)
        .where(LegalAct.ingestion_status == "completed")
        .where(
            col(LegalAct.title).ilike(f"%{q}%")
            | col(LegalAct.pdf_text).ilike(f"%{q}%")
        )
        .limit(10)
    )
    result = await session.exec(query)
    acts = result.all()

    return [LegalActRead.model_validate(a) for a in acts]


# ─── GET /api/v1/acts/{act_id} ──────────────────────────────


@router.get("/{act_id}", response_model=LegalActDetail)
async def get_act(
    act_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
    act = result.first()
    if not act:
        raise HTTPException(status_code=404, detail="Act not found")
    return act


# ─── GET /api/v1/acts/{act_id}/status ────────────────────────


@router.get("/{act_id}/status", response_model=LegalActStatusResponse)
async def get_act_status(
    act_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
    act = result.first()
    if not act:
        raise HTTPException(status_code=404, detail="Act not found")
    return LegalActStatusResponse(
        id=act.id,
        ingestion_status=act.ingestion_status,
        error_message=act.error_message,
    )


# ─── GET /api/v1/acts/{act_id}/pdf-url ──────────────────────


@router.get("/{act_id}/pdf-url")
async def get_act_pdf_url(
    act_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
    act = result.first()
    if not act or not act.minio_path:
        raise HTTPException(status_code=404, detail="Act or PDF not found")

    bucket = act.minio_bucket or "legal-acts"

    # Strip embedded bucket prefix if accidentally stored in path.
    # e.g. "legal-acts/acts/19949/original.pdf" → "acts/19949/original.pdf"
    path = act.minio_path.lstrip("/")
    if path.startswith(f"{bucket}/"):
        path = path[len(f"{bucket}/"):]

    logger.info("acts.pdf_url", bucket=bucket, path=path)

    # Generate a presigned URL (valid 4 hours).
    # Presigned URLs are signed by MinIO — no bucket policy needed, bypasses Access Denied.
    # We then replace MinIO's internal Docker hostname with the public-facing server hostname
    # so the browser can open it directly.
    from datetime import timedelta
    from urllib.parse import urlparse, urlunparse

    client_host = request.url.hostname  # e.g. staging.lexai.zuarione.com or LAN IP

    try:
        client = get_storage_client()
        presigned = client.presigned_get_object(
            bucket_name=bucket,
            object_name=path,
            expires=timedelta(hours=4),
        )
        # Replace the internal minio:9000 host with the public-facing host
        parsed = urlparse(presigned)
        url = urlunparse(parsed._replace(
            scheme="http",
            netloc=f"{client_host}:9000",
        ))
        logger.info("acts.pdf_presigned_url", url=url)
    except Exception as e:
        # Fallback to direct public URL (bucket must be public)
        logger.warning("acts.presigned_url_failed", error=str(e))
        url = f"http://{client_host}:9000/{bucket}/{path}"

    return {"url": url}



# ─── POST /api/v1/acts/{act_id}/upload-pdf ───────────────────


@router.post("/{act_id}/upload-pdf")
async def upload_act_pdf(
    act_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Upload a PDF for a not-completed act → OCR → mark completed."""
    result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
    act = result.first()
    if not act:
        raise HTTPException(status_code=404, detail="Act not found")
    if act.ingestion_status == "completed":
        raise HTTPException(status_code=400, detail="Act is already completed")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = await file.read()
    if len(pdf_bytes) < 100:
        raise HTTPException(status_code=400, detail="File is too small to be a valid PDF")

    # Use act id as folder if no handle_id
    folder = act.handle_id or act.id
    minio_path = f"acts/{folder}/original.pdf"

    # Upload to MinIO
    act.ingestion_status = "ocr_processing"
    act.updated_at = datetime.utcnow()
    session.add(act)
    await session.commit()

    try:
        await upload_to_minio(minio_path, pdf_bytes, "application/pdf")
    except Exception as e:
        act.ingestion_status = "failed"
        act.error_message = f"MinIO upload failed: {e}"
        act.updated_at = datetime.utcnow()
        session.add(act)
        await session.commit()
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    # OCR
    from app.services.ocr_service import extract_text_from_pdf
    try:
        ocr_result = await extract_text_from_pdf(pdf_bytes)
    except Exception as e:
        ocr_result = {"text": "", "total_pages": 0, "has_text_layer": False}

    act.minio_path = minio_path
    act.minio_bucket = settings.MINIO_BUCKET
    act.pdf_text = ocr_result["text"]
    act.total_pages = ocr_result["total_pages"]
    act.has_text_layer = ocr_result["has_text_layer"]
    act.ingestion_status = "completed"
    act.error_message = None
    act.updated_at = datetime.utcnow()
    session.add(act)
    await session.commit()
    await session.refresh(act)

    await log_action(session, current_user.id, "upload_act_pdf", "legal_act", act.id)
    await session.commit()
    logger.info("acts.manual_upload_complete", act_id=act_id, pages=act.total_pages)
    return {"id": act.id, "status": "completed", "total_pages": act.total_pages}


# ─── DELETE /api/v1/acts/{act_id}/pdf ───────────────────────


@router.delete("/{act_id}/pdf")
async def remove_act_pdf(
    act_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Remove a manually uploaded PDF unless review is permanently locked."""
    result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
    act = result.first()
    if not act:
        raise HTTPException(status_code=404, detail="Act not found")

    if act.review_locked:
        raise HTTPException(status_code=400, detail="Cannot remove PDF after review is locked")

    if act.ingestion_status != "completed" or not act.minio_path:
        raise HTTPException(status_code=400, detail="No completed PDF available to remove")

    client = get_storage_client()
    buckets_to_try = [act.minio_bucket or settings.MINIO_BUCKET, settings.MINIO_BUCKET]
    tried = set()
    for bucket in buckets_to_try:
        if not bucket or bucket in tried:
            continue
        tried.add(bucket)
        try:
            client.remove_object(bucket, act.minio_path)
        except Exception:
            # Ignore object-store delete errors and continue cleanup of DB state.
            pass

    act.minio_path = None
    act.pdf_text = None
    act.total_pages = None
    act.has_text_layer = None
    act.ingestion_status = "pdf_unavailable"
    act.error_message = "PDF removed manually by user"
    act.updated_at = datetime.utcnow()
    session.add(act)
    await session.commit()

    await log_action(session, current_user.id, "remove_act_pdf", "legal_act", act.id)
    await session.commit()
    return {"id": act.id, "status": act.ingestion_status}


# ─── PATCH /api/v1/acts/{act_id}/review ─────────────────────


@router.patch("/{act_id}/review")
async def update_review_status(
    act_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Update the manual review status. Once locked, cannot be changed."""
    result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
    act = result.first()
    if not act:
        raise HTTPException(status_code=404, detail="Act not found")

    if act.review_locked:
        raise HTTPException(status_code=400, detail="Review is permanently locked")

    review_status = body.get("review_status")
    lock = body.get("lock", False)

    valid_statuses = {None, "none", "manually_reviewed", "flagged"}
    if review_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid review_status: {review_status}")

    if review_status == "none":
        review_status = None

    act.review_status = review_status
    if lock and review_status == "manually_reviewed":
        if not act.minio_path:
            raise HTTPException(status_code=400, detail="Upload a PDF before locking the review")
        act.review_locked = True
    act.updated_at = datetime.utcnow()
    session.add(act)
    await session.commit()

    await log_action(session, current_user.id, "update_review", "legal_act", act.id,
                     details={"review_status": review_status, "locked": act.review_locked})
    return {"id": act.id, "review_status": act.review_status, "review_locked": act.review_locked}


# ─── GET /api/v1/acts/stats ─────────────────────────────────


@router.get("/stats/summary")
async def acts_stats(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Return summary counts for the acts dashboard (single query)."""
    result = await session.exec(
        select(
            func.count().label("total"),
            func.count().filter(LegalAct.ingestion_status == "completed").label("completed"),
            func.count().filter(LegalAct.review_status == "manually_reviewed").label("reviewed"),
        ).select_from(LegalAct)
    )
    row = result.one()
    total, completed, reviewed = row[0], row[1], row[2]
    return {
        "total": total,
        "completed": completed,
        "not_completed": total - completed,
        "reviewed": reviewed,
    }
