"""
Celery tasks for legal act ingestion.
Pipeline: Search IndiaCode → Download PDF → Upload to MinIO → OCR → Update DB.
"""
import asyncio
import sys
import traceback

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.celery_app import celery_app
from app.core.logging import get_logger
from app.core.database import engine

logger = get_logger("act_ingestion_task")


@celery_app.task(
    bind=True,
    name="app.tasks.act_ingestion_tasks.ingest_legal_act",
    max_retries=1,
    acks_late=True,
)
def ingest_legal_act(self, act_id: str, act_title: str, handle_id: str = None):
    """
    Full act ingestion pipeline:
    1. Search IndiaCode (if no handle_id provided)
    2. Download PDF
    3. Upload to MinIO
    4. OCR / text extraction
    5. Update DB record
    """
    logger.info("act_ingestion.started", act_id=act_id, title=act_title)

    async def _run():
        from app.core.database import AsyncSessionLocal
        from app.core.storage import upload_to_bucket
        from app.models.legal_act import LegalAct, LegalActSeedLog
        from app.services.indiacode_scraper import search_act, download_act_pdf, _normalize_title
        from app.services.ocr_service import extract_text_from_pdf
        from sqlmodel import select
        from datetime import datetime

        async with AsyncSessionLocal() as session:
            # Load the act record
            result = await session.exec(select(LegalAct).where(LegalAct.id == act_id))
            act = result.first()
            if not act:
                logger.error("act_ingestion.not_found_in_db", act_id=act_id)
                return

            try:
                # ── Step 1: Search IndiaCode ──
                if not handle_id:
                    act.ingestion_status = "searching"
                    act.updated_at = datetime.utcnow()
                    session.add(act)
                    await session.commit()

                    search_result = search_act(act_title)

                    if search_result.status == "not_found":
                        act.ingestion_status = "not_found"
                        act.error_message = search_result.error_message
                        act.indiacode_url = search_result.indiacode_url
                        session.add(act)
                        await session.commit()
                        await _log_seed(session, act_title, None, None, 0, "not_found", search_result.error_message)
                        return

                    if search_result.status == "needs_review":
                        # Low confidence match — but still ATTEMPT download.
                        # We do NOT stop here: if the PDF downloads fine, we mark
                        # completed. If it fails, we fall through to failed.
                        best = search_result.best_match
                        existing_owner = await session.exec(
                            select(LegalAct).where(
                                LegalAct.handle_id == best.handle_id,
                                LegalAct.id != act.id,
                            )
                        )
                        owner = existing_owner.first()
                        if owner:
                            # CONFLICT — genuinely cannot use this handle
                            act.ingestion_status = "needs_review"
                            act.confidence_score = best.confidence_score
                            act.handle_id = None
                            if not (act.act_number and str(act.act_number).startswith("LEX-")):
                                act.act_number = best.act_number
                            act.enactment_date = best.enactment_date
                            act.indiacode_url = search_result.indiacode_url
                            act.error_message = (
                                f"Fuzzy match score {best.confidence_score} — candidate handle "
                                f"{best.handle_id} already linked to '{owner.title}'"
                            )
                            session.add(act)
                            await session.commit()
                            await _log_seed(
                                session, act_title, best.title, None,
                                best.confidence_score, "needs_review", act.error_message,
                            )
                            return
                        # No conflict — set handle_id and continue to download
                        act.handle_id = best.handle_id
                        if not (act.act_number and str(act.act_number).startswith("LEX-")):
                            act.act_number = best.act_number
                        act.enactment_date = best.enactment_date
                        act.confidence_score = best.confidence_score
                        act.indiacode_url = search_result.indiacode_url
                        session.add(act)
                        await session.commit()

                    if search_result.status == "failed":
                        act.ingestion_status = "failed"
                        act.error_message = search_result.error_message
                        session.add(act)
                        await session.commit()
                        await _log_seed(session, act_title, None, None, 0, "failed", search_result.error_message)
                        return

                    # Matched
                    best = search_result.best_match
                    existing_owner = await session.exec(
                        select(LegalAct).where(
                            LegalAct.handle_id == best.handle_id,
                            LegalAct.id != act.id,
                        )
                    )
                    owner = existing_owner.first()
                    if owner:
                        act.ingestion_status = "needs_review"
                        act.confidence_score = best.confidence_score
                        act.handle_id = None
                        # Never overwrite our custom LEX-XXXXX unique IDs with IndiaCode numbers
                        if not (act.act_number and str(act.act_number).startswith("LEX-")):
                            act.act_number = best.act_number
                        act.enactment_date = best.enactment_date
                        act.indiacode_url = search_result.indiacode_url
                        act.error_message = (
                            f"Matched handle {best.handle_id} already linked to '{owner.title}' — manual review required"
                        )
                        session.add(act)
                        await session.commit()
                        await _log_seed(session, act_title, best.title, None, best.confidence_score, "needs_review", act.error_message)
                        return

                    act.handle_id = best.handle_id
                    # Never overwrite our custom LEX-XXXXX unique IDs with IndiaCode numbers
                    if not (act.act_number and str(act.act_number).startswith("LEX-")):
                        act.act_number = best.act_number
                    act.enactment_date = best.enactment_date
                    act.confidence_score = best.confidence_score
                    act.indiacode_url = search_result.indiacode_url
                    session.add(act)
                    await session.commit()

                    current_handle_id = best.handle_id
                else:
                    current_handle_id = handle_id

                # ── Step 2: Download PDF ──
                act.ingestion_status = "downloading"
                act.updated_at = datetime.utcnow()
                session.add(act)
                await session.commit()

                dl_result = download_act_pdf(current_handle_id)

                if dl_result.status == "pdf_unavailable":
                    act.ingestion_status = "pdf_unavailable"
                    act.error_message = dl_result.error_message or "PDF not available – Act under updation"
                    act.bitstream_url = dl_result.bitstream_url
                    act.indiacode_url = dl_result.indiacode_url
                    act.updated_at = datetime.utcnow()
                    session.add(act)
                    await session.commit()
                    await _log_seed(session, act_title, act.title, current_handle_id,
                                   act.confidence_score, "pdf_unavailable", act.error_message)
                    logger.info("act_ingestion.pdf_unavailable", act_id=act_id, handle=current_handle_id)
                    return

                if dl_result.status != "completed" or not dl_result.pdf_bytes:
                    act.ingestion_status = "failed"
                    act.error_message = dl_result.error_message or "PDF download returned no data"
                    act.bitstream_url = dl_result.bitstream_url
                    session.add(act)
                    await session.commit()
                    await _log_seed(session, act_title, act.title, current_handle_id, act.confidence_score, "failed", act.error_message)
                    return

                act.bitstream_url = dl_result.bitstream_url

                # ── Step 3: Upload to MinIO ──
                BUCKET = "legal-acts"
                minio_path = f"acts/{current_handle_id}/original.pdf"
                await upload_to_bucket(BUCKET, minio_path, dl_result.pdf_bytes, "application/pdf")
                act.minio_path = minio_path
                act.minio_bucket = BUCKET
                session.add(act)
                await session.commit()

                logger.info("act_ingestion.uploaded_to_minio", act_id=act_id, path=minio_path)

                # ── Step 4: OCR / Text extraction ──
                act.ingestion_status = "ocr_processing"
                act.updated_at = datetime.utcnow()
                session.add(act)
                await session.commit()

                ocr_result = await extract_text_from_pdf(dl_result.pdf_bytes)
                act.pdf_text = ocr_result["text"]
                act.total_pages = ocr_result["total_pages"]
                act.has_text_layer = ocr_result["has_text_layer"]

                # ── Step 5: Complete ──
                act.ingestion_status = "completed"
                act.updated_at = datetime.utcnow()
                session.add(act)
                await session.commit()

                await _log_seed(session, act_title, act.title, current_handle_id, act.confidence_score, "completed", None)
                logger.info("act_ingestion.completed", act_id=act_id, pages=act.total_pages)

            except Exception as e:
                act.ingestion_status = "failed"
                act.error_message = f"{str(e)}\n{traceback.format_exc()}"
                act.updated_at = datetime.utcnow()
                session.add(act)
                await session.commit()
                await _log_seed(session, act_title, None, None, 0, "failed", str(e))
                logger.error("act_ingestion.failed", act_id=act_id, error=str(e), exc_info=True)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


async def _log_seed(session, raw_name, matched_title, handle_id, confidence, status, error):
    """Insert a seed log entry."""
    from app.models.legal_act import LegalActSeedLog
    log = LegalActSeedLog(
        raw_name=raw_name,
        matched_title=matched_title,
        handle_id=handle_id,
        confidence_score=confidence,
        status=status,
        error=error,
    )
    session.add(log)
    await session.commit()
