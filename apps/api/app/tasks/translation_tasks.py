"""
Celery tasks for document translation.

Upgraded pipeline:
  1. Download raw file bytes from MinIO
  2. Run new layout-aware translation workflow (Gemini Pro, block-by-block)
  3. Store full block-level result in Postgres for structure-preserving PDF generation
"""
import asyncio
from app.celery_app import celery_app
from app.core.logging import get_logger

logger = get_logger("translation_task")


@celery_app.task(bind=True, name="app.tasks.translation_tasks.run_translation")
def run_translation(self, job_id: str, document_id: str):
    """
    Run the structured translation workflow for a given job.
    Steps:
      1. Fetch document bytes from MinIO (raw, not plain text)
      2. Detect language (Flash)
      3. Parse layout structure (PyMuPDF / python-docx)
      4. Translate block-by-block (Gemini Pro)
      5. Validate translation accuracy (Gemini Pro)
      6. Reconstruct document structure
      7. Store results in Postgres
    """
    logger.info("translation.task_started", job_id=job_id)

    async def _run():
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.core.config import settings
        from app.core.storage import download_file
        from app.models.translation import TranslationJob
        from app.models.document import Document
        from app.ai.workflows.translation_workflow import translation_workflow, detect_language as _detect_language, TranslationState
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
                # ── Fetch job and document ──
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

                    # ── Step 1: Download raw file bytes ──
                    file_bytes = await download_file(doc.storage_key)
                    logger.info(
                        "translation.file_downloaded",
                        job_id=job_id,
                        doc_id=document_id,
                        size=len(file_bytes),
                        filename=doc.name,
                    )

                    # ── Step 2: Build initial state with raw bytes for layout-aware parsing ──
                    # _file_bytes and _filename are internal workflow keys (not part of TypedDict schema
                    # but passed as extra keys which LangGraph preserves in state dict).
                    initial_state: TranslationState = {
                        "job_id": job_id,
                        "document_id": document_id,
                        "document_text": "",     # Will be populated as fallback in parse_structure
                        "source_language": "",   # Will be detected by detect_language node
                        "target_language": job.target_language,
                        "structure_map": [],
                        "translated_blocks": [],
                        "validation_report": {},
                        "result": {},
                        "_file_bytes": file_bytes,   # fed to layout-aware parser
                        "_filename": doc.name,
                    }

                    # ── Step 3: Extract plain text for language detection ──
                    # Try native parser first; fall back to Gemini structured OCR.
                    # We need plain text here just for language detection (cheap Flash call).
                    from app.ingestion.pipeline import DocumentParser, parse_with_gemini
                    from app.ingestion.document_structure_parser import (
                        DocumentStructureParser,
                        _blocks_from_gemini_json,
                    )

                    plain_text = ""
                    parser = DocumentParser()
                    parsed = parser.parse(file_bytes, doc.name)
                    if parsed.get("pages"):
                        plain_text = "\n".join(p["text"] for p in parsed["pages"])
                        logger.info(
                            "translation.text_extracted_native",
                            job_id=job_id,
                            chars=len(plain_text),
                        )
                    else:
                        # Native parser yielded nothing → use Gemini OCR for text
                        logger.info(
                            "translation.text_extract_gemini_fallback",
                            doc_id=document_id,
                        )
                        try:
                            parsed_gemini = await parse_with_gemini(file_bytes, doc.name)
                            plain_text = "\n".join(
                                p["text"] for p in parsed_gemini.get("pages", [])
                            )
                        except Exception as text_e:
                            logger.warning(
                                "translation.text_extract_failed",
                                job_id=job_id,
                                error=str(text_e),
                            )

                    initial_state["document_text"] = plain_text

                    # Run language detection only (not full workflow)
                    lang_state: TranslationState = {
                        **initial_state,
                        "source_language": "",
                    }
                    lang_state = await _detect_language(lang_state)
                    detected_language = lang_state["source_language"]

                    # Commit detected language early
                    job.source_language = detected_language
                    session.add(job)
                    await session.commit()
                    logger.info(
                        "translation.language_committed",
                        job_id=job_id,
                        lang=detected_language,
                    )

                    # ── Step 4: Run full translation pipeline ──
                    initial_state["source_language"] = detected_language
                    state = await translation_workflow.ainvoke(initial_state)

                    translation_result = state["result"]

                    # ── Step 5: Safety check — abort if cancelled during processing ──
                    await session.refresh(job)
                    if job.status == "error":
                        logger.info("translation.aborted_early", job_id=job_id)
                        return

                    # ── Step 6: Reconstruct display text from sections ──
                    translated_full = _build_display_text(
                        translation_result.get("translated_sections", []),
                        key="translated",
                    )
                    original_full = _build_display_text(
                        translation_result.get("translated_sections", []),
                        key="original",
                    )

                    # ── Step 7: Persist to Postgres ──
                    job.status = "done"
                    job.source_language = translation_result.get(
                        "source_language", job.source_language
                    )
                    job.original_text = original_full
                    job.translated_text = translated_full
                    # Store the FULL rich block data for structure-preserving PDF
                    job.structure_map = json.dumps(translation_result.get("translated_sections", []))
                    # Store validation + block data as separate JSON for save endpoint
                    job.uncertainty_flags = json.dumps({
                        "flags": translation_result.get("uncertainty_flags", []),
                        "warnings": translation_result.get("dropped_text_warnings", []),
                        "validation": translation_result.get("validation_report", {}),
                        "translated_blocks": translation_result.get("translated_blocks", []),
                    })
                    job.completed_at = datetime.utcnow()
                    session.add(job)
                    await session.commit()

                    logger.info(
                        "translation.completed",
                        job_id=job_id,
                        sections=len(translation_result.get("translated_sections", [])),
                        blocks=len(translation_result.get("translated_blocks", [])),
                        confidence=translation_result.get("overall_confidence", "?"),
                    )

                except Exception as e:
                    if isinstance(e, RuntimeError) and "cancelled or missing" in str(e):
                        logger.info("translation.aborted_gracefully", job_id=job_id)
                        return
                    
                    logger.error("translation.failed", job_id=job_id, error=str(e))
                    try:
                        await session.rollback()
                        job.status = "error"
                        job.error_message = str(e)[:500]
                        session.add(job)
                        await session.commit()
                    except Exception as commit_err:
                        logger.error(
                            "translation.status_update_failed",
                            job_id=job_id,
                            error=str(commit_err),
                        )
                    raise

        finally:
            await task_engine.dispose()

    try:
        asyncio.run(_run())
    except Exception as e:
        if isinstance(e, RuntimeError) and "cancelled or missing" in str(e):
            logger.info("translation.task_terminated_safely", job_id=job_id)
            return

        logger.error("translation.loop_error", job_id=job_id, error=str(e))
        raise


def _build_display_text(sections: list, key: str) -> str:
    """Build a plain-text display string from translated_sections list."""
    parts = []
    heading_key = f"{key}_heading"
    text_key = f"{key}_text"
    for s in sections:
        if s.get(heading_key):
            parts.append(f"\n\n{s[heading_key]}\n")
        t = s.get(text_key, "")
        if t:
            parts.append(t + "\n")
    return "".join(parts)
