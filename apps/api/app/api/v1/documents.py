"""
Document upload, listing, and retrieval routes
"""
import uuid
import json
import asyncio
from typing import TYPE_CHECKING, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session, engine, AsyncSessionLocal
from app.core.auth import get_current_user
from app.core.storage import upload_file
from app.models.user import User
from app.models.folder import Folder
from app.models.document import Document, DocumentRead, DocumentTree
from app.services.audit_service import log_action
from app.core.logging import get_logger

logger = get_logger("documents_endpoint")

router = APIRouter()

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "text/plain": ".txt",
}

# Keep references to background tasks so they don't get garbage-collected
_active_tree_tasks: set = set()


async def _build_tree_background(doc_id: str):
    """Build AI tree for a document — runs as asyncio task in the event loop."""
    from app.models.document import Document as _Doc, DocumentTree as _DT
    from app.models.clause import Clause
    from app.ingestion.pipeline import build_page_index_tree
    from sqlmodel import select as _sel

    logger.info("tree.bg_task_started", doc_id=doc_id)
    try:
        async with AsyncSessionLocal() as bg_session:
            # Verify document exists and is indexed
            dq = await bg_session.exec(_sel(_Doc).where(_Doc.id == doc_id))
            d = dq.first()
            if not d:
                logger.warning("tree.bg_doc_not_found", doc_id=doc_id)
                return
            logger.info("tree.bg_doc_status", doc_id=doc_id, status=d.status)
            if d.status != "indexed":
                logger.warning("tree.bg_doc_not_indexed", doc_id=doc_id, status=d.status)
                return

            # Collect clauses for full text
            cq = await bg_session.exec(
                _sel(Clause).where(Clause.doc_id == doc_id)
                .order_by(Clause.position_in_doc.asc())
            )
            clauses = cq.all()
            logger.info("tree.bg_clauses_loaded", doc_id=doc_id, count=len(clauses))
            full_text = "\n\n".join(c.text for c in clauses if c.text)
            if not full_text.strip():
                logger.warning("tree.bg_no_text", doc_id=doc_id)
                return

            logger.info("tree.bg_calling_gemini", doc_id=doc_id, text_len=len(full_text))
            tree_data = await build_page_index_tree(doc_id, full_text)
            logger.info(
                "tree.bg_gemini_done",
                doc_id=doc_id,
                summary_len=len(tree_data.get("summary", "")),
                nodes=len(tree_data.get("tree", [])),
            )

            tq = await bg_session.exec(_sel(_DT).where(_DT.document_id == doc_id))
            rec = tq.first()
            if rec:
                rec.summary = tree_data["summary"]
                rec.tree_json = json.dumps(tree_data["tree"])
                rec.status = "ready"
                rec.error_message = None
            else:
                rec = _DT(
                    document_id=doc_id,
                    summary=tree_data["summary"],
                    tree_json=json.dumps(tree_data["tree"]),
                    status="ready",
                )
            bg_session.add(rec)
            await bg_session.commit()
            logger.info("tree.bg_saved_ready", doc_id=doc_id)
    except Exception as err:
        logger.error("tree.bg_failed", doc_id=doc_id, error=str(err), exc_info=True)
        try:
            async with AsyncSessionLocal() as err_session:
                tq2 = await err_session.exec(
                    select(DocumentTree).where(DocumentTree.document_id == doc_id)
                )
                rec2 = tq2.first()
                if rec2:
                    rec2.status = "error"
                    rec2.error_message = str(err)[:500]
                    err_session.add(rec2)
                    await err_session.commit()
        except Exception as inner_err:
            logger.error("tree.bg_error_save_failed", doc_id=doc_id, error=str(inner_err))


def _fire_tree_build(doc_id: str):
    """Schedule tree build as asyncio task with proper reference tracking."""
    task = asyncio.create_task(_build_tree_background(doc_id))
    _active_tree_tasks.add(task)
    task.add_done_callback(_active_tree_tasks.discard)
    logger.info("tree.task_scheduled", doc_id=doc_id)


@router.post("/upload", response_model=DocumentRead, status_code=201)
async def upload_document(
    folder_id: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    logger.info("upload_started", folder_id=folder_id, filename=file.filename, user_id=current_user.id)
    # Verify folder ownership or global access
    folder_result = await session.exec(select(Folder).where(Folder.id == folder_id))
    folder = folder_result.first()
    if not folder or (not folder.is_global and folder.owner_id != current_user.id):
        logger.warning("upload_failed_folder_not_found", folder_id=folder_id, user_id=current_user.id)
        raise HTTPException(status_code=404, detail="Folder not found")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES and not file.filename.endswith((".pdf", ".docx", ".doc", ".txt")):
        logger.warning("upload_failed_unsupported_type", content_type=content_type, filename=file.filename)
        raise HTTPException(status_code=400, detail=f"File type not supported: {content_type}")

    file_bytes = await file.read()
    file_size = len(file_bytes)
    logger.debug("file_read_completed", filename=file.filename, bytes=file_size)
    if file_size > 50 * 1024 * 1024:  # 50MB limit
        logger.warning("upload_failed_file_too_large", filename=file.filename, size=file_size)
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    doc_id = str(uuid.uuid4())
    storage_key = f"documents/{folder_id}/{doc_id}/{file.filename}"

    # Upload to MinIO
    logger.info("uploading_to_minio", storage_key=storage_key)
    await upload_file(storage_key, file_bytes, content_type)
    logger.info("minio_upload_success", storage_key=storage_key)

    # Create document record
    doc = Document(
        id=doc_id,
        name=file.filename,
        folder_id=folder_id,
        owner_id=current_user.id,
        content_type=content_type,
        size_bytes=len(file_bytes),
        storage_key=storage_key,
        status="uploaded",
        is_global=folder.is_global,
    )
    logger.info("saving_document_record", doc_id=doc_id)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    logger.debug("document_record_saved", doc_id=doc_id)

    # Queue ingestion task
    try:
        logger.info("queueing_ingestion_task", doc_id=doc_id, storage_key=storage_key)
        from app.tasks.ingestion_tasks import ingest_document
        task = ingest_document.delay(
            document_id=doc_id,
            storage_key=storage_key,
            document_name=file.filename,
            folder_id=folder_id,
            folder_name=folder.name,
        )
        logger.info("ingestion_task_queued", task_id=task.id)
    except Exception as e:
        # Log the failure but don't fail the upload — task will be retried
        logger.error("ingestion_dispatch.failed", doc_id=doc_id, error=str(e), exc_info=True)

    await log_action(session, current_user.id, "upload", "document", doc_id, details={"filename": file.filename})
    return doc


@router.get("/folder/{folder_id}", response_model=List[DocumentRead])
async def list_documents(
    folder_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    folder_result = await session.exec(select(Folder).where(Folder.id == folder_id))
    folder = folder_result.first()
    if not folder or (not folder.is_global and folder.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Folder not found")

    result = await session.exec(select(Document).where(Document.folder_id == folder_id))
    docs = result.all()
    await log_action(session, current_user.id, "read", "document", folder_id)
    return docs


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(Document).where(Document.id == document_id))
    doc = result.first()
    if not doc or (not doc.is_global and doc.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    from fastapi.responses import Response
    from app.core.storage import download_file

    result = await session.exec(select(Document).where(Document.id == document_id))
    doc = result.first()
    if not doc or (not doc.is_global and doc.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")

    file_bytes = await download_file(doc.storage_key)
    await log_action(session, current_user.id, "download", "document", document_id)
    return Response(
        content=file_bytes,
        media_type=doc.content_type,
        headers={"Content-Disposition": f'inline; filename="{doc.name}"'},
    )


@router.get("/{document_id}/file-url")
async def get_document_file_url(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Return a direct public MinIO URL for the original uploaded file.
    Uses the same dynamic host detection as the legal acts viewer so
    the URL works from both localhost and LAN clients.
    """

    result = await session.exec(select(Document).where(Document.id == document_id))
    doc = result.first()
    if not doc or (not doc.is_global and doc.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")

    # Return the backend streaming endpoint instead of a direct MinIO URL.
    # A direct MinIO URL (port 9000) only works locally; in production it is
    # blocked by the firewall/HTTPS mixed-content policy.
    # The /download endpoint streams bytes through FastAPI → Nginx → browser
    # and is fully authenticated, so this is also more secure.
    return {"url": f"/api/v1/documents/{document_id}/download", "content_type": doc.content_type, "name": doc.name}



@router.get("/{document_id}/tree")
async def get_document_tree(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Return the AI-generated page-index tree + executive summary for the Document Viewer Modal.
    If the tree doesn't exist yet for an indexed document, kicks off an async background build.
    """
    doc_result = await session.exec(select(Document).where(Document.id == document_id))
    doc = doc_result.first()
    if not doc or (not doc.is_global and doc.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")

    tree_result = await session.exec(
        select(DocumentTree).where(DocumentTree.document_id == document_id)
    )
    tree = tree_result.first()

    if not tree:
        if doc.status == "indexed":
            new_tree = DocumentTree(document_id=document_id, status="pending")
            session.add(new_tree)
            await session.commit()
            _fire_tree_build(document_id)
        return {
            "status": "pending",
            "summary": None,
            "tree": [],
            "document_name": doc.name,
            "doc_type": getattr(doc, "doc_type", None),
        }

    if tree.status == "pending":
        return {
            "status": "pending",
            "summary": None,
            "tree": [],
            "document_name": doc.name,
            "doc_type": getattr(doc, "doc_type", None),
        }

    # If a previous attempt errored, auto-retry
    if tree.status == "error":
        logger.warning("tree.error_auto_retry", doc_id=document_id, prev_error=tree.error_message)
        tree.status = "pending"
        tree.error_message = None
        session.add(tree)
        await session.commit()
        _fire_tree_build(document_id)
        return {
            "status": "pending",
            "summary": None,
            "tree": [],
            "document_name": doc.name,
            "doc_type": getattr(doc, "doc_type", None),
        }

    tree_nodes = json.loads(tree.tree_json) if tree.tree_json else []
    logger.info("tree.returning_ready", doc_id=document_id, nodes=len(tree_nodes))
    return {
        "status": "ready",
        "summary": tree.summary,
        "tree": tree_nodes,
        "document_name": doc.name,
        "doc_type": doc.doc_type,
    }


@router.get("/{document_id}/text-content")
async def get_document_text_content(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Return formatted document text (from Postgres clauses) for the Document Viewer center panel.
    Groups clauses by section_heading so the content renders with section headers.
    """
    from app.models.clause import Clause
    from collections import OrderedDict

    doc_result = await session.exec(select(Document).where(Document.id == document_id))
    doc = doc_result.first()
    if not doc or (not doc.is_global and doc.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status not in ("indexed", "error"):
        return {"content": "", "status": doc.status, "sections": []}

    clauses_result = await session.exec(
        select(Clause)
        .where(Clause.doc_id == document_id)
        .order_by(Clause.position_in_doc.asc())
    )
    clauses = clauses_result.all()

    if not clauses:
        return {"content": "", "status": doc.status, "sections": []}

    # Build structured sections for richer rendering
    sections = OrderedDict()
    for c in clauses:
        heading = c.section_heading.strip() if c.section_heading else ""
        key = heading or "__preamble__"
        if key not in sections:
            sections[key] = {"heading": heading, "texts": [], "page_start": c.page_start}
        sections[key]["texts"].append(c.text)

    sections_list = [
        {
            "heading": v["heading"],
            "text": "\n\n".join(v["texts"]),
            "page_start": v["page_start"],
        }
        for v in sections.values()
    ]

    # Also produce a flat content string (fallback for simple rendering)
    content_parts = []
    for s in sections_list:
        if s["heading"]:
            content_parts.append(f"## {s['heading']}")
        content_parts.append(s["text"])

    return {
        "content": "\n\n".join(content_parts),
        "status": doc.status,
        "sections": sections_list,
    }


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    from app.core.storage import delete_file
    from app.core.vector_store import delete_document_clauses
    from app.core.elasticsearch import delete_document_clauses_es
    from app.core.graph_db import delete_document_graph
    from app.models.clause import Clause
    from app.models.document import DocumentChunk
    from app.models.translation import TranslationJob
    from sqlalchemy import delete

    # 1. Fetch document and check ownership
    result = await session.exec(select(Document).where(Document.id == document_id))
    doc = result.first()
    if not doc or doc.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    # 2. Delete from all external stores (non-transactional, but we do them before DB)
    try:
        await delete_file(doc.storage_key)
    except Exception as e:
        logger.warning("delete.minio_failed", doc_id=document_id, error=str(e))

    try:
        await delete_document_clauses(document_id)  # Qdrant
    except Exception as e:
        logger.warning("delete.qdrant_failed", doc_id=document_id, error=str(e))

    try:
        await delete_document_clauses_es(document_id)  # Elasticsearch
    except Exception as e:
        logger.warning("delete.es_failed", doc_id=document_id, error=str(e))

    try:
        delete_document_graph(document_id)  # Neo4j (sync)
    except Exception as e:
        logger.warning("delete.neo4j_failed", doc_id=document_id, error=str(e))

    # 3. Cascading delete from database (Postgres)
    # Order matters: Dependent tables first due to FK constraints
    try:
        # Delete translation jobs
        await session.execute(delete(TranslationJob).where(TranslationJob.document_id == document_id))
        # Delete clauses and legacy chunks
        await session.execute(delete(Clause).where(Clause.doc_id == document_id))
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        # Delete AI tree (FK to documents)
        await session.execute(delete(DocumentTree).where(DocumentTree.document_id == document_id))
        
        # Finally delete document
        await log_action(session, current_user.id, "delete", "document", document_id)
        await session.delete(doc)
        await session.commit()
        logger.info("delete.completed", doc_id=document_id)
    except Exception as e:
        await session.rollback()
        logger.error("delete.db_failed", doc_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Database deletion failed: {str(e)}")
