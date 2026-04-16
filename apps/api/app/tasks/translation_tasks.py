"""
Celery tasks for document translation.
"""
import asyncio
from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("translation_task")


@celery_app.task(bind=True, name="app.tasks.translation_tasks.run_translation")
def run_translation(self, job_id: str, document_id: str):
    """
    Run the translation workflow for a given job.
    1. Fetch document text from storage
    2. Run LangGraph translation workflow
    3. Store results in Postgres
    """
    logger.info("translation.task_started", job_id=job_id)

    async def _run():
        # ── Per-task engine with NullPool so no connections survive beyond this
        # event-loop instance.  asyncio.run() closes the loop when done; using
        # the module-level pooled engine would leave stale asyncpg connections
        # whose internal _loop._proactor is None on the next task invocation.
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.core.config import settings
        from app.core.storage import download_file
        from app.models.translation import TranslationJob
        from app.models.document import Document
        from app.ai.workflows.translation_workflow import translation_workflow
        from sqlmodel import select
        from datetime import datetime
        import json

        task_engine = create_async_engine(
            settings.POSTGRES_URL,
            poolclass=NullPool,
            echo=False,
        )
        TaskSession = async_sessionmaker(
            bind=task_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        try:
            async with TaskSession() as session:
                # Fetch job
                result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
                job = result.first()
                if not job:
                    raise ValueError(f"Translation job {job_id} not found")

                result = await session.exec(select(Document).where(Document.id == document_id))
                doc = result.first()
                if not doc:
                    raise ValueError(f"Document {document_id} not found")

                try:
                    job.status = "processing"
                    session.add(job)
                    await session.commit()

                    # Download document and extract plain text (handles PDF, DOCX, TXT)
                    file_bytes = await download_file(doc.storage_key)
                    from app.ingestion.pipeline import DocumentParser, parse_with_gemini
                    parser = DocumentParser()
                    parsed = parser.parse(file_bytes, doc.name)
                    
                    # If PyMuPDF extracted nothing (scanned/image PDF), use Gemini OCR fallback
                    if not parsed.get("pages"):
                        logger.info("translation.pymupdf_empty_falling_back_to_gemini", doc_id=document_id)
                        parsed = await parse_with_gemini(file_bytes, doc.name)
                    
                    text = "\n".join(p["text"] for p in parsed.get("pages", []))

                    # ── Detect language eagerly so frontend can show it during processing ──
                    from app.ai.workflows.translation_workflow import detect_language as _detect_language, TranslationState
                    lang_state: TranslationState = {
                        "document_id": document_id,
                        "document_text": text,
                        "source_language": "",
                        "target_language": job.target_language,
                        "structure_map": [],
                        "result": {},
                    }
                    lang_state = await _detect_language(lang_state)
                    detected_language = lang_state["source_language"]

                    job.source_language = detected_language
                    session.add(job)
                    await session.commit()
                    logger.info("translation.language_committed", job_id=job_id, lang=detected_language)

                    # ── Run full workflow (skips re-detection since language is pre-set) ──
                    state = await translation_workflow.ainvoke({
                        "document_id": document_id,
                        "document_text": text,
                        "source_language": detected_language,
                        "target_language": job.target_language,
                        "structure_map": [],
                        "result": {},
                    })

                    translation_result = state["result"]

                    # Reconstruct full translated text
                    translated_full = ""
                    for s in translation_result.get("translated_sections", []):
                        if s.get("translated_heading"):
                            translated_full += f"\n\n{s['translated_heading']}\n"
                        translated_full += s.get("translated_text", "") + "\n"

                    original_full = ""
                    for s in translation_result.get("translated_sections", []):
                        if s.get("original_heading"):
                            original_full += f"\n\n{s['original_heading']}\n"
                        original_full += s.get("original_text", "") + "\n"

                    # ── Safety Check ──
                    # Refresh job from DB to see if it was cancelled by the API while we were working
                    await session.refresh(job)
                    if job.status == "error":
                        logger.info("translation.aborted_early", job_id=job_id)
                        return

                    job.status = "done"
                    job.source_language = translation_result.get("source_language", job.source_language)
                    job.original_text = original_full
                    job.translated_text = translated_full
                    job.structure_map = json.dumps(translation_result.get("translated_sections", []))
                    job.uncertainty_flags = json.dumps(translation_result.get("uncertainty_flags", []))
                    job.completed_at = datetime.utcnow()
                    session.add(job)
                    await session.commit()

                    logger.info("translation.completed", job_id=job_id)

                except Exception as e:
                    logger.error("translation.failed", job_id=job_id, error=str(e))
                    try:
                        await session.rollback()
                        job.status = "error"
                        job.error_message = str(e)[:500]
                        session.add(job)
                        await session.commit()
                    except Exception as commit_err:
                        logger.error("translation.status_update_failed", job_id=job_id, error=str(commit_err))
                    raise

        finally:
            # Dispose engine INSIDE the event loop so asyncpg can cleanly close
            # all connections before asyncio.run() shuts the loop down.
            await task_engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error("translation.loop_error", job_id=job_id, error=str(e))
        raise
