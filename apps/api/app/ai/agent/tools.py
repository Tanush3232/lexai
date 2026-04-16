"""
LexAI Agent Tools
9 Tools available to the ReAct agent for deep legal reasoning.
"""
from typing import Dict, List, Any, Optional
from app.core.database import AsyncSessionLocal
from app.core.vector_store import hybrid_search_clauses
from app.core.graph_db import get_graph_driver
from app.core.elasticsearch import keyword_search
from app.ai.gemini_client import embed_query, generate_structured
from app.models.clause import Clause
from sqlmodel import select
from app.core.logging import get_logger

logger = get_logger("agent.tools")


async def search_clauses(query: str, doc_ids: List[str], clause_types: List[str] = None, limit: int = 10) -> List[Dict]:
    """1. Hybrid retrieval across Qdrant + Elasticsearch."""
    try:
        dense_vec = await embed_query(query)
        hits = await hybrid_search_clauses(
            query=query,
            dense_vector=dense_vec,
            filter_doc_ids=doc_ids if doc_ids else None,
            filter_clause_types=clause_types if clause_types else None,
            limit=limit,
        )
        return hits
    except Exception as e:
        logger.error("tool.search_clauses.failed", error=str(e))
        return []


async def get_document_outline(doc_id: str) -> List[Dict]:
    """2. Fetch the table of contents / sections of a document."""
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Clause.section_id, Clause.section_heading)
            .where(Clause.doc_id == doc_id)
            .distinct()
        )
        sections = result.all()
        # Deduplicate while preserving order
        seen = set()
        outline = []
        for sid, head in sections:
            if sid not in seen:
                seen.add(sid)
                outline.append({"section_id": sid, "heading": head})
        return outline


async def get_section(doc_id: str, section_id: str) -> List[Dict]:
    """3. Fetch all clauses within a specific section sequentially."""
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Clause)
            .where(Clause.doc_id == doc_id, Clause.section_id == section_id)
            .order_by(Clause.position_in_doc)
        )
        clauses = result.all()
        return [c.model_dump() for c in clauses]


def traverse_references(clause_id: str, depth: int = 1) -> List[Dict]:
    """4. Follow Neo4j graph :REFERENCES edges to find related clauses."""
    driver = get_graph_driver()
    with driver.session() as session:
        query = """
        MATCH p=(src:Clause {id: $clause_id})-[:REFERENCES*1..%d]->(dst:Clause)
        RETURN dst.id AS id, dst.clause_type AS type, dst.text_preview AS text, dst.section_heading AS section
        """ % max(1, min(depth, 3))
        
        result = session.run(query, clause_id=clause_id)
        return [{"id": r["id"], "type": r["type"], "text": r["text"], "section": r["section"]} for r in result]


async def search_definitions(term: str, doc_ids: List[str]) -> List[Dict]:
    """5. Targeted BM25 search specifically looking for definitions."""
    return await keyword_search(
        query=term,
        filter_doc_ids=doc_ids,
        filter_clause_types=["definition"],
        limit=5,
    )


async def extract_fields(doc_id: str, fields: List[str]) -> Dict:
    """6. Structured extraction via Gemini."""
    # Build text from top 30 clauses (start of doc)
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Clause.text)
            .where(Clause.doc_id == doc_id)
            .order_by(Clause.position_in_doc)
            .limit(30)
        )
        # result.all() returns a list of scalar strings when selecting a single column
        clause_texts: List[str] = [row for row in result.all()]
        doc_text = "\n".join(clause_texts)

    if not doc_text.strip():
        logger.warning("tool.extract_fields.no_text", doc_id=doc_id)
        return {"error": f"No text found for document {doc_id}"}

    prompt = (
        f"Extract the following fields accurately from the legal document text below.\n"
        f"Do not guess or infer values not explicitly stated. Return null for missing fields.\n"
        f"Fields to extract: {', '.join(fields)}\n\nDocument text:\n{doc_text[:8000]}"
    )
    try:
        result = await generate_structured(prompt, use_pro=True, feature_name="field_extraction")
        logger.info("tool.extract_fields.success", doc_id=doc_id, fields=fields)
        return result
    except Exception as e:
        logger.error("tool.extract_fields.failed", doc_id=doc_id, error=str(e), exc_info=True)
        return {"error": f"Extraction failed: {str(e)}"}


async def compare_clauses(clause_id_a: str, clause_id_b: str) -> Dict:
    """7. Compare two clauses semantically."""
    # Access .text INSIDE the session to avoid DetachedInstanceError
    async with AsyncSessionLocal() as session:
        res_a = await session.exec(select(Clause).where(Clause.id == clause_id_a))
        res_b = await session.exec(select(Clause).where(Clause.id == clause_id_b))
        ca = res_a.first()
        cb = res_b.first()

        if not ca or not cb:
            logger.warning("tool.compare_clauses.not_found", id_a=clause_id_a, id_b=clause_id_b)
            return {"error": "One or both clauses not found"}

        # Read attributes while session is still open
        text_a = ca.text
        text_b = cb.text

    prompt = (
        "Compare these two legal clauses. Are they equivalent, compatible, or conflicting?\n"
        f"Clause A (id={clause_id_a}):\n{text_a}\n\n"
        f"Clause B (id={clause_id_b}):\n{text_b}\n\n"
        'Return JSON with keys: "status" (equivalent|compatible|conflicting), '
        '"explanation", "key_differences"'
    )
    try:
        result = await generate_structured(prompt, use_pro=True, feature_name="clause_comparison")
        logger.info("tool.compare_clauses.success", id_a=clause_id_a, id_b=clause_id_b)
        return result
    except Exception as e:
        logger.error("tool.compare_clauses.failed", error=str(e), exc_info=True)
        return {"error": f"Comparison failed: {str(e)}"}


async def get_full_document(doc_id: str) -> Optional[str]:
    """8. Return full document text if under size limit (for vectorless mode).
    Returns:
        None  — document is too large; use hybrid retrieval
        ""    — document has 0 clauses; not yet indexed by Celery
        str   — full clause text concatenated
    """
    from app.core.config import settings
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Clause)
            .where(Clause.doc_id == doc_id)
            .order_by(Clause.position_in_doc)
        )
        clauses = result.all()

        if not clauses:
            return ""  # Sentinel: not yet indexed

        # Approximate 300 words a page
        total_words = sum(len(c.text.split()) for c in clauses)
        pages_approx = total_words / 300

        if pages_approx > settings.VECTORLESS_MAX_PAGES:
            return None  # Signal to use retrieval instead

        return "\n".join([f"[{c.section_heading} - Clause {c.id}]\n{c.text}" for c in clauses])


async def get_document_tree_data(doc_id: str) -> Optional[Dict]:
    """9. Fetch the page-index tree for a document (used by page-index RAG path).
    Returns None if no tree exists or it is not yet ready.
    """
    import json as _json
    from app.models.document import DocumentTree
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(DocumentTree).where(DocumentTree.document_id == doc_id)
        )
        tree = result.first()
        if not tree or tree.status != "ready":
            return None
        return {
            "summary": tree.summary,
            "tree": _json.loads(tree.tree_json) if tree.tree_json else [],
        }


async def get_sections_by_page_range(doc_id: str, page_start: int, page_end: int) -> str:
    """10. Fetch clause text for a given page range (used by page-index RAG path).
    Returns concatenated text of all clauses whose page range overlaps [page_start, page_end].
    """
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Clause)
            .where(
                Clause.doc_id == doc_id,
                Clause.page_start <= page_end,
                Clause.page_end >= page_start,
            )
            .order_by(Clause.position_in_doc)
        )
        clauses = result.all()
        if not clauses:
            return ""
        return "\n".join([f"[{c.section_heading} - Clause {c.id}]\n{c.text}" for c in clauses])


# Tool #11: verify_claim is handled at the validation layer, not directly by the ReAct loop
