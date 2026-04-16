"""
Translation routes.

IMPORTANT: Static routes (/document/{id}) must be registered BEFORE
dynamic routes (/{job_id}) so FastAPI does not treat "document" as a job_id.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.user import User
from app.models.translation import TranslationJob, TranslationRequest, TranslationRead, TranslationResult
from app.models.document import Document
from app.services.audit_service import log_action
from app.celery_app import celery_app

router = APIRouter()
logger = get_logger("translations_endpoint")


@router.post("/", response_model=TranslationRead, status_code=201)
async def start_translation(
    body: TranslationRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Start a translation job for a document."""
    result = await session.exec(select(Document).where(Document.id == body.document_id))
    doc = result.first()
    if not doc or doc.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    job = TranslationJob(
        document_id=body.document_id,
        user_id=current_user.id,
        source_language="",  # Will be detected by workflow
        target_language=body.target_language,
        status="pending",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Queue translation task
    try:
        from app.tasks.translation_tasks import run_translation
        task = run_translation.delay(job_id=job.id, document_id=body.document_id)
        job.celery_task_id = task.id
        session.add(job)
        await session.commit()
        await session.refresh(job)
        logger.info("translation.task_queued", job_id=job.id, task_id=task.id)
    except Exception as e:
        logger.error(
            "translation.dispatch_failed",
            job_id=job.id,
            document_id=body.document_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Translation task could not be queued: {str(e)}"
        )

    await log_action(session, current_user.id, "translate", "translation", job.id)
    return job


# Static route MUST come before the dynamic /{job_id} route.
@router.get("/document/{document_id}", response_model=list)
async def get_document_translations(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(
        select(TranslationJob)
        .where(TranslationJob.document_id == document_id)
        .where(TranslationJob.user_id == current_user.id)
        .order_by(TranslationJob.created_at.desc())
    )
    return result.all()


@router.get("/{job_id}", response_model=TranslationResult)
async def get_translation(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
    job = result.first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Translation job not found")
    return job

@router.post("/{job_id}/cancel")
async def cancel_translation(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Cancel an ongoing translation job."""
    result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
    job = result.first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Translation job not found")
    
    if job.status in ["done", "error"]:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed or failed job")

    job.status = "error"
    job.error_message = "Translation cancelled by user"
    
    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True, signal='SIGKILL')
        logger.info("translation.task_revoked", job_id=job_id, task_id=job.celery_task_id)

    session.add(job)
    await session.commit()
    
    await log_action(session, current_user.id, "cancel_translation", "translation", job_id)
    return {"message": "Translation cancelled successfully"}


@router.post("/action/cancel-all")
async def cancel_all_translations(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Cancel all processing translations for the current user."""
    result = await session.exec(select(TranslationJob).where(
        TranslationJob.user_id == current_user.id,
        TranslationJob.status.in_(["pending", "processing"])
    ))
    
    count = 0
    for job in result.all():
        job.status = "error"
        job.error_message = "Translation cancelled by user"
        if job.celery_task_id:
            celery_app.control.revoke(job.celery_task_id, terminate=True, signal='SIGKILL')
        session.add(job)
        count += 1
        
    await session.commit()
    return {"message": f"Cancelled {count} jobs"}


@router.post("/{job_id}/save")
async def save_translated_document(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Save the translated document as a PDF in the same folder as the original.
    Filename: translated_<original_name>.pdf
    Creates a new Document record and uploads to MinIO.
    """
    import uuid
    import json
    from datetime import datetime
    from app.core.storage import upload_file
    from app.models.document import Document
    from app.models.folder import Folder

    result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
    job = result.first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Translation job not found")
    if job.status != "done":
        raise HTTPException(status_code=400, detail="Translation is not completed yet")
    if job.saved_storage_key:
        # Already saved — return the existing document
        return {"message": "Already saved", "document_id": job.saved_document_id, "storage_key": job.saved_storage_key}

    # Fetch original document
    doc_result = await session.exec(select(Document).where(Document.id == job.document_id))
    original_doc = doc_result.first()
    if not original_doc:
        raise HTTPException(status_code=404, detail="Original document not found")

    # Build translated text from structure_map (preferred) or translated_text
    translated_content = ""
    if job.structure_map:
        try:
            sections = json.loads(job.structure_map)
            parts = []
            for s in sections:
                if s.get("translated_heading"):
                    parts.append(f"\n{s['translated_heading']}\n{'='*len(s['translated_heading'])}\n")
                if s.get("translated_text"):
                    parts.append(s["translated_text"] + "\n")
            translated_content = "\n".join(parts)
        except Exception:
            translated_content = job.translated_text or ""
    else:
        translated_content = job.translated_text or ""

    if not translated_content.strip():
        raise HTTPException(status_code=400, detail="No translated content to save")

    # Find or create 'Translations' folder
    folder_result = await session.exec(
        select(Folder).where(Folder.name == "Translations", Folder.owner_id == current_user.id)
    )
    translations_folder = folder_result.first()
    if not translations_folder:
        translations_folder = Folder(
            id=str(uuid.uuid4()),
            name="Translations",
            description="Saved documents from language translations.",
            owner_id=current_user.id
        )
        session.add(translations_folder)
        await session.flush()

    # Generate PDF using reportlab
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.enums import TA_LEFT
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import io as _io

        buf = _io.BytesIO()
        doc_pdf = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=20*mm, rightMargin=20*mm,
            topMargin=24*mm, bottomMargin=20*mm,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "title", parent=styles["Heading1"],
            fontSize=14, spaceAfter=12, leading=18,
        )
        body_style = ParagraphStyle(
            "body", parent=styles["Normal"],
            fontSize=10, leading=15, spaceAfter=6,
            wordWrap="CJK",
        )
        meta_style = ParagraphStyle(
            "meta", parent=styles["Normal"],
            fontSize=8, leading=12, textColor="#888888",
        )
        story = []
        # Header
        orig_name = original_doc.name.rsplit(".", 1)[0]
        story.append(Paragraph(f"Translation: {orig_name}", title_style))
        story.append(Paragraph(
            f"Source: {job.source_language or 'Auto'} → Target: {job.target_language} | Generated by LexAI",
            meta_style,
        ))
        story.append(Spacer(1, 6*mm))

        # Content — split on double newlines for paragraphs
        for para in translated_content.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            # Escape XML special chars for reportlab
            para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if para.startswith("=") or (len(para) < 80 and para.endswith("===")):
                continue  # Skip separator lines
            story.append(Paragraph(para, body_style))
            story.append(Spacer(1, 3))

        doc_pdf.build(story)
        pdf_bytes = buf.getvalue()
        ext = ".pdf"
        content_type = "application/pdf"

    except Exception as e:
        logger.error(f"Failed to generate PDF for translation {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {e}")

    # Build filename and storage key
    base_name = original_doc.name.rsplit(".", 1)[0]
    new_filename = f"translated_{base_name}{ext}"
    new_doc_id = str(uuid.uuid4())
    storage_key = f"documents/{translations_folder.id}/{new_doc_id}/{new_filename}"

    # Upload to MinIO
    await upload_file(storage_key, pdf_bytes, content_type)

    # Create Document record in Translations folder
    new_doc = Document(
        id=new_doc_id,
        name=new_filename,
        folder_id=translations_folder.id,
        owner_id=current_user.id,
        content_type=content_type,
        size_bytes=len(pdf_bytes),
        storage_key=storage_key,
        status="uploaded",  # Not indexed — it's a translation output
    )
    session.add(new_doc)

    # Mark job as saved
    from datetime import datetime as _dt
    job.saved_storage_key = storage_key
    job.saved_document_id = new_doc_id
    job.saved_at = _dt.utcnow()
    session.add(job)

    await session.commit()
    await log_action(session, current_user.id, "save_translation", "translation", job_id, details={"saved_doc_id": new_doc_id})
    logger.info("translation.saved", job_id=job_id, doc_id=new_doc_id, key=storage_key)

    return {
        "message": "Translated document saved successfully",
        "document_id": new_doc_id,
        "storage_key": storage_key,
        "filename": new_filename,
    }

