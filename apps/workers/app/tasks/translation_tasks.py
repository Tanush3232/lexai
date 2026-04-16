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
        from app.core.database import AsyncSessionLocal
        from app.core.storage import download_file
        from app.models.translation import TranslationJob
        from app.models.document import Document
        from app.ai.workflows.translation_workflow import translation_workflow
        from sqlmodel import select
        from datetime import datetime
        import json

        async with AsyncSessionLocal() as session:
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

                # Download document
                file_bytes = await download_file(doc.storage_key)
                text = file_bytes.decode("utf-8", errors="replace")

                # Run translation workflow
                state = await translation_workflow.ainvoke({
                    "document_id": document_id,
                    "document_text": text,
                    "source_language": job.source_language or "",
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
                job.status = "error"
                job.error_message = str(e)
                session.add(job)
                await session.commit()
                raise

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
    finally:
        loop.close()
