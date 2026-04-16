"""
Celery tasks for document ingestion.
Called asynchronously after document upload.
"""
import asyncio
import json
from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("ingestion_task")


@celery_app.task(bind=True, name="app.tasks.ingestion_tasks.ingest_document")
def ingest_document(self, document_id: str, storage_key: str, document_name: str, folder_id: str, folder_name: str):
    """
    Full ingestion pipeline for an uploaded document:
    1. Download from MinIO
    2. Parse with Docling
    3. Chunk semantically
    4. Extract metadata with Gemini
    5. Embed and store in Qdrant
    6. Create graph nodes in Neo4j
    7. Update document status in Postgres
    """
    logger.info("ingestion.started", doc_id=document_id, key=storage_key)
    self.update_state(state="STARTED", meta={"step": "downloading"})

    async def _run():
        from app.core.storage import download_file
        from app.core.database import AsyncSessionLocal
        from app.ingestion.pipeline import (
            DocumentParser,
            chunk_document,
            extract_document_metadata,
            embed_and_store_chunks,
        )
        from app.core.graph_db import (
            create_document_node,
            create_clause_nodes,
            create_entity_nodes,
        )
        from sqlmodel import select
        from app.models.document import Document, DocumentChunk
        from datetime import datetime

        async with AsyncSessionLocal() as session:
            try:
                # 1. Download
                file_bytes = await download_file(storage_key)

                # 2. Parse
                parser = DocumentParser()
                parsed = parser.parse(file_bytes, document_name)

                # 3. Chunk
                chunks = chunk_document(parsed, document_id, folder_id)

                # 4. Extract metadata
                metadata = await extract_document_metadata(
                    parsed["full_text"], document_id, document_name
                )

                # 5. Embed + store in Qdrant
                await embed_and_store_chunks(chunks, document_name, folder_name)

                # 6. Create graph nodes
                create_document_node(
                    doc_id=document_id,
                    name=document_name,
                    folder_id=folder_id,
                    metadata={
                        "language": metadata.get("language", "unknown"),
                        "doc_type": metadata.get("document_type", "unknown"),
                        "created_at": str(datetime.utcnow()),
                    },
                )
                clause_nodes = [
                    {
                        "id": c["id"],
                        "text": c["text"],
                        "clause_type": c.get("chunk_type", "general"),
                        "section": c.get("section", ""),
                        "page": c.get("page_number", 0),
                    }
                    for c in chunks
                    if c["chunk_type"] == "clause"
                ]
                if clause_nodes:
                    create_clause_nodes(document_id, clause_nodes)

                entity_nodes = []
                for party in metadata.get("parties", []):
                    entity_nodes.append({
                        "id": f"entity_{document_id}_{party.get('name','').replace(' ','_')}",
                        "name": party.get("name", ""),
                        "entity_type": "PARTY",
                    })
                if entity_nodes:
                    create_entity_nodes(document_id, entity_nodes)

                # 7. Save chunks to Postgres + update document status
                for chunk in chunks:
                    db_chunk = DocumentChunk(
                        id=chunk["id"],
                        document_id=chunk["document_id"],
                        folder_id=chunk["folder_id"],
                        chunk_index=chunk["chunk_index"],
                        chunk_type=chunk["chunk_type"],
                        section=chunk.get("section"),
                        clause_number=chunk.get("clause_number"),
                        page_number=chunk.get("page_number", 0),
                        text=chunk["text"],
                    )
                    session.add(db_chunk)

                # Update document record
                result = await session.exec(select(Document).where(Document.id == document_id))
                doc = result.first()
                if doc:
                    doc.status = "indexed"
                    doc.page_count = parsed.get("page_count", 0)
                    doc.language = metadata.get("language", "unknown")
                    doc.doc_type = metadata.get("document_type")
                    doc.extracted_metadata = json.dumps(metadata)
                    doc.indexed_at = datetime.utcnow()
                    doc.parsed_at = datetime.utcnow()
                    session.add(doc)

                await session.commit()
                logger.info("ingestion.completed", doc_id=document_id, chunks=len(chunks))

            except Exception as e:
                logger.error("ingestion.failed", doc_id=document_id, error=str(e))
                # Update document to error state
                result = await session.exec(select(Document).where(Document.id == document_id))
                doc = result.first()
                if doc:
                    doc.status = "error"
                    doc.error_message = str(e)
                    session.add(doc)
                await session.commit()
                raise

    # Run async in sync Celery context
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
    finally:
        loop.close()
