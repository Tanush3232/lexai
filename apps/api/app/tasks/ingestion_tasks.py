"""
Celery tasks for document ingestion.
Full pipeline: Download → Parse → Segment → Resolve refs → Index all stores → Update status.
"""
import asyncio
import json
import sys

# Ensure UTF-8 output so Unicode characters in document text never crash the worker.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging import get_logger
from app.core.database import engine

logger = get_logger("ingestion_task")


@celery_app.task(bind=True, name="app.tasks.ingestion_tasks.ingest_document", max_retries=2)
def ingest_document(self, document_id: str, storage_key: str, document_name: str, folder_id: str, folder_name: str):
    """
    Full ingestion pipeline:
    1. Download from MinIO
    2. Parse (PyMuPDF / DOCX / text)
    3. Segment into atomic clauses
    4. Resolve cross-references
    5. Extract metadata with Gemini
    6. Embed + store in Qdrant (dense)
    7. BM25 index in Elasticsearch
    8. Graph nodes in Neo4j
    9. Save Clause records in Postgres
    10. Update Document status
    """
    logger.info("ingestion.started", doc_id=document_id, key=storage_key)
    self.update_state(state="STARTED", meta={"step": "downloading"})

    async def _run():
        from app.core.storage import download_file
        from app.core.database import AsyncSessionLocal
        from app.ingestion.pipeline import (
            DocumentParser,
            ClauseSegmenter,
            CrossReferenceResolver,
            extract_document_metadata,
            embed_and_store_clauses,
            index_clauses_to_elasticsearch,
            write_graph_nodes,
        )
        from app.models.document import Document
        from app.models.clause import Clause
        from sqlmodel import select
        from datetime import datetime

        async with AsyncSessionLocal() as session:
            try:
                doc_res = await session.exec(select(Document).where(Document.id == document_id))
                doc_rec = doc_res.first()
                is_global = doc_rec.is_global if doc_rec else False

                # ── 1. Download ──────────────────────────────────
                self.update_state(state="PROGRESS", meta={"step": "downloading"})
                file_bytes = await download_file(storage_key)
                logger.info("ingestion.downloaded", doc_id=document_id, bytes=len(file_bytes))

                # ── 2. Parse ─────────────────────────────────────
                self.update_state(state="PROGRESS", meta={"step": "parsing"})
                parser = DocumentParser()
                parsed = parser.parse(file_bytes, document_name)
                logger.info("ingestion.parsed", doc_id=document_id, pages=parsed["page_count"], parser=parsed["parser"])

                # ── 2b. Gemini fallback when primary parser yields no text ───────
                #   Covers: scanned PDFs, image-only PDFs, PyMuPDF unavailable,
                #   corrupt DOCX, binary files with wrong extension, etc.
                if not parsed.get("pages"):
                    logger.info(
                        "ingestion.gemini_fallback.start",
                        doc_id=document_id,
                        primary_parser=parsed.get("parser", "unknown"),
                    )
                    self.update_state(state="PROGRESS", meta={"step": "gemini_parse"})
                    from app.ingestion.pipeline import parse_with_gemini
                    parsed = await parse_with_gemini(file_bytes, document_name)
                    logger.info("ingestion.gemini_fallback.done", doc_id=document_id, pages=parsed["page_count"])

                # ── 3. Segment into clauses ───────────────────────
                self.update_state(state="PROGRESS", meta={"step": "segmenting"})
                segmenter = ClauseSegmenter()
                clauses = segmenter.segment(parsed, document_id, folder_id, is_global=is_global)
                logger.info("ingestion.segmented", doc_id=document_id, clauses=len(clauses))

                if not clauses:
                    raise ValueError("No clauses extracted — document may be empty or unreadable.")

                # ── 4. Resolve cross-references ───────────────────
                resolver = CrossReferenceResolver()
                clauses = resolver.resolve(clauses)

                # ── 5. Extract metadata ───────────────────────────
                self.update_state(state="PROGRESS", meta={"step": "extracting_metadata"})
                full_text = "\n".join(p["text"] for p in parsed.get("pages", []))
                metadata = await extract_document_metadata(full_text, document_id)

                # ── 6. Qdrant (dense embeddings) ──────────────────
                self.update_state(state="PROGRESS", meta={"step": "embedding"})
                qdrant_ok = False
                try:
                    await embed_and_store_clauses(clauses, document_name, folder_name)
                    qdrant_ok = True
                except Exception as qdrant_err:
                    logger.error(
                        "ingestion.qdrant_failed",
                        doc_id=document_id,
                        error=str(qdrant_err),
                        exc_info=True,
                    )
                    # Non-fatal: continue so ES + graph still get indexed

                # ── 7. Elasticsearch (BM25) ───────────────────────
                self.update_state(state="PROGRESS", meta={"step": "indexing_es"})
                es_ok = False
                try:
                    indexed_count = await index_clauses_to_elasticsearch(clauses, document_name, folder_name)
                    es_ok = indexed_count > 0
                    if not es_ok:
                        logger.warning("ingestion.es_indexed_zero", doc_id=document_id)
                except Exception as es_err:
                    logger.error(
                        "ingestion.es_failed",
                        doc_id=document_id,
                        error=str(es_err),
                        exc_info=True,
                    )

                # ── 8. Neo4j graph ────────────────────────────────
                self.update_state(state="PROGRESS", meta={"step": "graph"})
                try:
                    write_graph_nodes(document_id, document_name, folder_id, clauses, metadata)
                except Exception as graph_err:
                    logger.warning("ingestion.graph_failed", doc_id=document_id, error=str(graph_err))
                    # Graph is non-fatal — continue

                # ── 9. Save Clause records in Postgres ───────────
                self.update_state(state="PROGRESS", meta={"step": "saving_clauses"})
                for c in clauses:
                    db_clause = Clause(
                        id=c.clause_id,
                        doc_id=c.doc_id,
                        folder_id=c.folder_id,
                        section_id=c.section_id,
                        section_heading=c.section_heading,
                        clause_type=c.clause_type,
                        text=c.text,
                        page_start=c.page_start,
                        page_end=c.page_end,
                        position_in_doc=c.position_in_doc,
                        parties_involved=json.dumps(c.parties_involved),
                        references=json.dumps(c.resolved_references),
                        qdrant_indexed=qdrant_ok,
                        es_indexed=es_ok,
                    )
                    session.add(db_clause)

                # ── 10. Update Document status ────────────────────
                doc_result = await session.exec(select(Document).where(Document.id == document_id))
                doc = doc_result.first()
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
                logger.info("ingestion.completed", doc_id=document_id, clauses=len(clauses))

                # ── 11. Build Page-Index Tree (non-fatal) ─────────
                self.update_state(state="PROGRESS", meta={"step": "building_tree"})
                try:
                    from app.ingestion.pipeline import build_page_index_tree
                    from app.models.document import DocumentTree
                    from sqlmodel import select as sql_select

                    tree_data = await build_page_index_tree(document_id, full_text)

                    # Upsert: replace existing tree record if any
                    tree_q = await session.exec(
                        sql_select(DocumentTree).where(DocumentTree.document_id == document_id)
                    )
                    existing_tree = tree_q.first()
                    if existing_tree:
                        existing_tree.summary = tree_data["summary"]
                        existing_tree.tree_json = json.dumps(tree_data["tree"])
                        existing_tree.status = "ready"
                        existing_tree.error_message = None
                        session.add(existing_tree)
                    else:
                        session.add(DocumentTree(
                            document_id=document_id,
                            summary=tree_data["summary"],
                            tree_json=json.dumps(tree_data["tree"]),
                            status="ready",
                        ))
                    await session.commit()
                    logger.info("ingestion.tree_built", doc_id=document_id)
                except Exception as tree_err:
                    logger.error(
                        "ingestion.tree_failed",
                        doc_id=document_id,
                        error=str(tree_err),
                    )
                    # Tree failure is non-fatal — write an error status so the modal
                    # can gracefully degrade to showing a fallback message.
                    try:
                        from app.models.document import DocumentTree as _DT
                        from sqlmodel import select as _sel
                        _tq = await session.exec(
                            _sel(_DT).where(_DT.document_id == document_id)
                        )
                        _existing = _tq.first()
                        if _existing:
                            _existing.status = "error"
                            _existing.error_message = str(tree_err)[:500]
                            session.add(_existing)
                        else:
                            session.add(_DT(
                                document_id=document_id,
                                status="error",
                                error_message=str(tree_err)[:500],
                            ))
                        await session.commit()
                    except Exception:
                        pass

            except Exception as e:
                logger.error("ingestion.failed", doc_id=document_id, error=str(e))
                import traceback
                logger.error("ingestion.traceback", doc_id=document_id, tb=traceback.format_exc())

                # Rollback the failed transaction before issuing a new query.
                try:
                    await session.rollback()
                except Exception:
                    pass

                doc_result = await session.exec(select(Document).where(Document.id == document_id))
                doc = doc_result.first()
                if doc:
                    doc.status = "error"
                    doc.error_message = str(e)[:500]
                    session.add(doc)
                await session.commit()
                raise

    async def _run_wrapper():
        try:
            await _run()
        finally:
            # Crucial: Dispose engine connections to avoid loop mismatch in next task
            await engine.dispose()

    try:
        # Use asyncio.run for robust loop management (handles cleanup better)
        asyncio.run(_run_wrapper())
    except Exception as e:
        logger.error("ingestion.loop_error", doc_id=document_id, error=str(e))
        raise


@celery_app.task(bind=True, name="app.tasks.ingestion_tasks.build_tree_for_document", max_retries=1)
def build_tree_for_document(self, document_id: str):
    """
    On-demand task: build the page-index tree for an already-indexed document.
    Called by GET /documents/{id}/tree when the tree record is missing.
    """
    from sqlmodel import select as _sel
    from app.models.document import Document, DocumentTree
    from app.models.clause import Clause

    async def _run():
        from sqlalchemy.ext.asyncio import AsyncSession
        async with AsyncSession(engine) as session:
            doc_q = await session.exec(_sel(Document).where(Document.id == document_id))
            doc = doc_q.first()
            if not doc or doc.status != "indexed":
                return

            # Check not already building
            tq = await session.exec(
                _sel(DocumentTree).where(DocumentTree.document_id == document_id)
            )
            existing = tq.first()
            if existing and existing.status in ("ready", "pending"):
                return

            # Create a pending record so repeated calls don't double-fire
            if not existing:
                session.add(DocumentTree(document_id=document_id, status="pending"))
                await session.commit()

            # Collect clauses to build full text
            cq = await session.exec(
                _sel(Clause)
                .where(Clause.doc_id == document_id)
                .order_by(Clause.position_in_doc.asc())
            )
            clauses = cq.all()
            full_text = "\n\n".join(c.text for c in clauses if c.text)
            if not full_text.strip():
                return

            try:
                from app.ingestion.pipeline import build_page_index_tree
                tree_data = await build_page_index_tree(document_id, full_text)

                tq2 = await session.exec(
                    _sel(DocumentTree).where(DocumentTree.document_id == document_id)
                )
                rec = tq2.first()
                if rec:
                    rec.summary = tree_data["summary"]
                    rec.tree_json = json.dumps(tree_data["tree"])
                    rec.status = "ready"
                    rec.error_message = None
                    session.add(rec)
                else:
                    session.add(DocumentTree(
                        document_id=document_id,
                        summary=tree_data["summary"],
                        tree_json=json.dumps(tree_data["tree"]),
                        status="ready",
                    ))
                await session.commit()
                logger.info("tree.built_on_demand", doc_id=document_id)
            except Exception as err:
                logger.error("tree.on_demand_failed", doc_id=document_id, error=str(err))
                tq3 = await session.exec(
                    _sel(DocumentTree).where(DocumentTree.document_id == document_id)
                )
                rec = tq3.first()
                if rec:
                    rec.status = "error"
                    rec.error_message = str(err)[:500]
                    session.add(rec)
                    await session.commit()

    async def _wrapper():
        try:
            await _run()
        finally:
            await engine.dispose()

    try:
        asyncio.run(_wrapper())
    except Exception as e:
        logger.error("tree.on_demand_loop_error", doc_id=document_id, error=str(e))

