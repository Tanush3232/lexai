"""
Clauses API routes
"""
from typing import List, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.clause import Clause, ClauseRead
from app.core.graph_db import get_graph_driver
from app.ai.agent.tools import compare_clauses

router = APIRouter()


@router.get("/{clause_id}", response_model=ClauseRead)
async def get_clause(
    clause_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get full details of a specific clause."""
    result = await session.exec(select(Clause).where(Clause.id == clause_id))
    clause = result.first()
    if not clause:
        raise HTTPException(status_code=404, detail="Clause not found")
    return clause


@router.get("/{clause_id}/references")
async def get_clause_references(
    clause_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get clauses referenced by this clause (Neo4j traversal)."""
    driver = get_graph_driver()
    with driver.session() as driver_session:
        query = """
        MATCH (src:Clause {id: $clause_id})-[:REFERENCES]->(dst:Clause)
        RETURN dst.id AS id, dst.clause_type AS type, dst.text_preview AS text, dst.section_heading AS section
        """
        result = driver_session.run(query, clause_id=clause_id)
        return [{"id": r["id"], "type": r["type"], "text": r["text"], "section": r["section"]} for r in result]


@router.post("/compare")
async def compare_clauses_endpoint(
    body: dict,  # Expects {"clause_id_a": "...", "clause_id_b": "..."}
    current_user: User = Depends(get_current_user),
):
    """Compare two clauses semantically."""
    ca = body.get("clause_id_a")
    cb = body.get("clause_id_b")
    if not ca or not cb:
        raise HTTPException(status_code=400, detail="Both clause_id_a and clause_id_b are required")
        
    result = await compare_clauses(ca, cb)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
        
    return result


@router.get("/document/{doc_id}/outline")
async def get_document_outline(
    doc_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get document structure (sections and clauses)"""
    result = await session.exec(
        select(Clause.section_id, Clause.section_heading, Clause.id, Clause.clause_type, Clause.position_in_doc)
        .where(Clause.doc_id == doc_id)
        .order_by(Clause.position_in_doc)
    )
    
    outline = {}
    for sid, sheading, cid, ctype, pos in result.all():
        if sid not in outline:
            outline[sid] = {"heading": sheading, "clauses": []}
        outline[sid]["clauses"].append({"id": cid, "type": ctype, "position": pos})
        
    return {"doc_id": doc_id, "sections": list(outline.values())}
