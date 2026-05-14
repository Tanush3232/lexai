"""
Web Search API Router
=====================
SSE streaming endpoint + CRUD for search history.
"""
import json
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import desc as sa_desc
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.user import User
from app.models.web_search import (
    WebSearchSession, WebSearchCitation,
    WebSearchRequest, PlanRequest, WebSearchSessionRead,
    WebSearchSessionListItem, CitationRead, ReasoningStep,
)
from app.ai.web_search_orchestrator import LegalWebSearchOrchestrator
from app.services.audit_service import log_action

router = APIRouter()
logger = get_logger("web_search_endpoint")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sse_event(data: dict) -> str:
    """Format a dict as an SSE event string."""
    return f"data: {json.dumps(data)}\n\n"


def _session_to_read(session_row: WebSearchSession, citations: List[WebSearchCitation]) -> WebSearchSessionRead:
    reasoning: Optional[List[ReasoningStep]] = None
    if session_row.reasoning_steps:
        try:
            steps_raw = json.loads(session_row.reasoning_steps)
            reasoning = [ReasoningStep(**s) for s in steps_raw]
        except Exception:
            pass

    search_plan = None
    if session_row.search_plan:
        try:
            search_plan = json.loads(session_row.search_plan)
        except Exception:
            pass

    cit_reads = [
        CitationRead(
            id=c.id,
            source_name=c.source_name,
            url=c.url,
            snippet=c.snippet,
            domain=c.domain,
            relevance_score=c.relevance_score,
            jurisdiction=c.jurisdiction,
            citation_type=c.citation_type,
        )
        for c in citations
    ]

    return WebSearchSessionRead(
        id=session_row.id,
        query=session_row.query,
        mode=session_row.mode,
        status=session_row.status,
        answer_summary=session_row.answer_summary,
        full_answer=session_row.full_answer,
        reasoning_steps=reasoning,
        search_plan=search_plan,
        citations=cit_reads,
        error_message=session_row.error_message,
        created_at=session_row.created_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Plan Endpoint (Deep Research)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/plan")
async def generate_deep_research_plan(
    body: PlanRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate a step-by-step research plan for deep search mode."""
    from app.ai.web_search_orchestrator import _planning_prompt
    from app.ai.gemini_client import generate_structured
    try:
        plan_raw = await generate_structured(
            _planning_prompt(body.query), use_pro=False,
            temperature=0.1, feature_name="web_search_deep_plan"
        )
        steps = plan_raw.get("search_strategy", [])
        if not steps:
            steps = [
                "Analyze query and identify key legal topics",
                "Search authoritative legal sources (Supreme Court, High Courts)",
                "Review relevant acts, regulations, and circulars",
                "Synthesize findings into a comprehensive legal answer"
            ]
        return {"steps": steps}
    except Exception as e:
        logger.error("plan_generation_error", error=str(e))
        return {"steps": [
            "Analyze query and identify key legal topics",
            "Search authoritative legal sources",
            "Synthesize findings into a comprehensive legal answer"
        ]}


# ─────────────────────────────────────────────────────────────────────────────
# SSE Search Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/search")
async def legal_web_search(
    body: WebSearchRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Stream a legal web search as Server-Sent Events.
    Each event is JSON: { type: 'thinking_step'|'complete'|'error', ... }
    """
    
    session_id = body.session_id
    is_follow_up = False
    
    if session_id:
        res = await db.exec(select(WebSearchSession).where(WebSearchSession.id == session_id))
        search_session = res.first()
        if not search_session or search_session.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Session not found")
        is_follow_up = True
        search_session.status = "searching"
        await db.commit()
    else:
        # Create session record
        search_session = WebSearchSession(
            user_id=current_user.id,
            query=body.query,
            mode=body.mode.value,
            status="searching",
        )
        db.add(search_session)
        await db.commit()
        await db.refresh(search_session)
        session_id = search_session.id

    # Release DB connection before long LLM call
    await db.close()

    async def event_stream():
        orchestrator = LegalWebSearchOrchestrator(mode=body.mode.value, query=body.query, plan_steps=body.plan)
        final_event = None

        try:
            # Send session ID first so the client knows where to save history
            yield _sse_event({"type": "session_created", "session_id": session_id})

            async for event in orchestrator.run():
                yield _sse_event(event)
                if event.get("type") == "complete":
                    final_event = event

            # Persist results to DB
            if final_event:
                answer = final_event.get("answer", "")
                citations_data = final_event.get("citations", [])
                read_not_used_data = final_event.get("read_but_not_used", [])
                reasoning_steps = final_event.get("reasoning_steps", [])
                search_plan = final_event.get("search_plan")

                # Re-open DB session for persistence
                from app.core.database import AsyncSessionLocal
                async with AsyncSessionLocal() as persist_db:
                    # Reload session
                    res = await persist_db.exec(
                        select(WebSearchSession).where(WebSearchSession.id == session_id)
                    )
                    row = res.first()
                    if row:
                        row.status = "completed"
                        
                        if is_follow_up:
                            sep = "\n\n---\n\n"
                            
                            actual_query = body.query
                            if "Follow-up question:" in actual_query:
                                actual_query = actual_query.split("Follow-up question:")[-1].strip()
                            
                            row.query = f"{row.query}\n|FOLLOWUP|\n{actual_query}"
                            row.full_answer = f"{row.full_answer or ''}{sep}{answer}"
                            
                            if row.reasoning_steps:
                                try:
                                    old_steps = json.loads(row.reasoning_steps)
                                    row.reasoning_steps = json.dumps(old_steps + reasoning_steps)
                                except Exception:
                                    row.reasoning_steps = json.dumps(reasoning_steps)
                            else:
                                row.reasoning_steps = json.dumps(reasoning_steps)
                        else:
                            row.full_answer = answer
                            row.answer_summary = answer[:300].strip() if answer else ""
                            row.reasoning_steps = json.dumps(reasoning_steps)
                            if search_plan:
                                row.search_plan = json.dumps(search_plan)
                        
                        row.updated_at = datetime.utcnow()
                        persist_db.add(row)

                        turn_idx = len(row.query.split("|FOLLOWUP|")) - 1

                        # Save citations
                        for c in citations_data:
                            citation = WebSearchCitation(
                                session_id=session_id,
                                source_name=c.get("source_name", ""),
                                url=c.get("url", ""),
                                snippet=c.get("snippet", ""),
                                domain=c.get("domain", ""),
                                relevance_score=c.get("relevance_score", 0.0),
                                jurisdiction=c.get("jurisdiction"),
                                citation_type=c.get("citation_type"),
                                turn_index=turn_idx,
                            )
                            persist_db.add(citation)

                        # Save read_but_not_used citations with negative score
                        for c in read_not_used_data:
                            citation = WebSearchCitation(
                                session_id=session_id,
                                source_name=c.get("source_name", ""),
                                url=c.get("url", ""),
                                snippet=c.get("snippet", ""),
                                domain=c.get("domain", ""),
                                relevance_score=-1.0,
                                jurisdiction=c.get("jurisdiction"),
                                citation_type=c.get("citation_type"),
                                turn_index=turn_idx,
                            )
                            persist_db.add(citation)

                        await persist_db.commit()

                        # Audit log
                        try:
                            await log_action(persist_db, current_user.id, "web_search", "web_search", session_id)
                        except Exception:
                            pass

        except Exception as e:
            logger.error("web_search_stream_error", error=str(e), exc_info=True)
            yield _sse_event({"type": "error", "message": str(e)})
            # Mark session as failed
            try:
                from app.core.database import AsyncSessionLocal
                async with AsyncSessionLocal() as err_db:
                    res = await err_db.exec(
                        select(WebSearchSession).where(WebSearchSession.id == session_id)
                    )
                    row = res.first()
                    if row:
                        row.status = "failed"
                        row.error_message = str(e)
                        err_db.add(row)
                        await err_db.commit()
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Search History
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sessions", response_model=List[WebSearchSessionListItem])
async def list_search_sessions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """List paginated search history for the current user."""
    offset = (page - 1) * limit
    result = await db.exec(
        select(WebSearchSession)
        .where(WebSearchSession.user_id == current_user.id)
        .order_by(sa_desc(WebSearchSession.created_at))
        .offset(offset)
        .limit(limit)
    )
    sessions = result.all()
    # Format query so it just shows the first one for the list view
    for s in sessions:
        if s.query and "|FOLLOWUP|" in s.query:
            s.query = s.query.split("|FOLLOWUP|")[0].strip()
            
    return [
        WebSearchSessionListItem(
            id=s.id,
            query=s.query,
            mode=s.mode,
            status=s.status,
            answer_summary=s.answer_summary,
            created_at=s.created_at,
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=WebSearchSessionRead)
async def get_search_session(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Get a full search session with all citations."""
    res = await db.exec(
        select(WebSearchSession).where(WebSearchSession.id == session_id)
    )
    session_row = res.first()
    if not session_row or session_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Search session not found")

    cit_res = await db.exec(
        select(WebSearchCitation)
        .where(WebSearchCitation.session_id == session_id)
        .order_by(sa_desc(WebSearchCitation.relevance_score))
    )
    citations = cit_res.all()

    return _session_to_read(session_row, citations)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_search_session(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a search session and its citations."""
    res = await db.exec(
        select(WebSearchSession).where(WebSearchSession.id == session_id)
    )
    session_row = res.first()
    if not session_row or session_row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Search session not found")

    # Delete citations first
    cit_res = await db.exec(
        select(WebSearchCitation).where(WebSearchCitation.session_id == session_id)
    )
    for cit in cit_res.all():
        await db.delete(cit)

    await db.delete(session_row)
    await db.commit()
