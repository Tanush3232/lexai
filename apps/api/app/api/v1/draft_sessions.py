"""
Draft Sessions API Router
=========================
Enterprise agentic contract drafting — SSE streaming orchestration.

Endpoints:
  POST   /sessions              — start a new drafting session (SSE stream)
  GET    /sessions/{id}         — get session state
  PATCH  /sessions/{id}/context — submit follow-up answers
  POST   /sessions/{id}/approve-sources — approve sources, trigger assembly (SSE stream)
"""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.user import User
from app.models.draft import (
    DraftSession, DraftSessionCreate, DraftSessionRead,
    ContextSubmit, SourceApproval, ContractDraft,
)
from app.services.draft_orchestrator import run_draft_orchestration, run_assembly_phase, auto_insert_fix
from app.services.audit_service import log_action

router = APIRouter()
logger = get_logger("draft_sessions")


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# POST /sessions — Start a new drafting session with SSE stream
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sessions")
async def create_draft_session(
    body: DraftSessionCreate,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new draft session and stream the orchestration as SSE events.
    Phase 1: intent analysis → optional questions → redaction → research → sources_ready.
    """
    # Create DB session record
    session = DraftSession(
        user_id=current_user.id,
        original_prompt=body.prompt,
        stage="analyzing_intent",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    session_id = session.id

    await db.close()

    async def event_stream():
        yield _sse_event({"type": "session_created", "session_id": session_id})

        async for event in run_draft_orchestration(
            prompt=body.prompt,
            context={},
            session_id=session_id,
        ):
            yield _sse_event(event)

            # Persist state changes as they happen
            evt_type = event.get("type")
            try:
                from app.core.database import AsyncSessionLocal
                async with AsyncSessionLocal() as persist_db:
                    res = await persist_db.exec(
                        select(DraftSession).where(DraftSession.id == session_id)
                    )
                    row = res.first()
                    if not row:
                        continue

                    if evt_type == "intent_analyzed":
                        row.intent_json = json.dumps(event.get("intent", {}))
                        row.stage = "intent_analyzed"

                    elif evt_type == "needs_input":
                        row.questions_json = json.dumps(event.get("questions", []))
                        row.stage = "needs_input"

                    elif evt_type == "redaction_complete":
                        row.stage = "redacting"

                    elif evt_type == "sources_ready":
                        row.sources_json = json.dumps({
                            "sources": event.get("sources", []),
                            "all_sources": event.get("all_sources", []),
                            "ranking_summary": event.get("ranking_summary", ""),
                            "sufficient": event.get("sufficient", True),
                        })
                        row.stage = "sources_ready"

                    elif evt_type == "error":
                        row.stage = "failed"
                        row.error_message = event.get("message", "Unknown error")

                    row.updated_at = datetime.utcnow()
                    persist_db.add(row)
                    await persist_db.commit()

            except Exception as e:
                logger.warning("session_persist.failed", error=str(e))

        # Audit log
        try:
            from app.core.database import AsyncSessionLocal
            async with AsyncSessionLocal() as audit_db:
                await log_action(audit_db, current_user.id, "draft_session", "draft", session_id)
        except Exception:
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /sessions/{id} — Retrieve session state (for reload recovery)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}")
async def get_session_state(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch the current state of a drafting session.
    Used by the frontend to restore state upon page reload.
    """
    res = await db.exec(select(DraftSession).where(DraftSession.id == session_id))
    session = res.first()
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "id": session.id,
        "stage": session.stage,
        "original_prompt": session.original_prompt,
        "intent": json.loads(session.intent_json) if session.intent_json else None,
        "questions": json.loads(session.questions_json) if session.questions_json else [],
        "sources_data": json.loads(session.sources_json) if session.sources_json else None,
    }

# ─────────────────────────────────────────────────────────────────────────────
# PATCH /sessions/{id}/context — Submit follow-up answers, resume orchestration
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/sessions/{session_id}/context")
async def submit_context(
    session_id: str,
    body: ContextSubmit,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Submit answers to follow-up questions.
    Re-runs the orchestration from the beginning with the enriched context.
    """
    res = await db.exec(select(DraftSession).where(DraftSession.id == session_id))
    session = res.first()
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Merge answers into context
    existing_context = {}
    if session.context_json:
        existing_context = json.loads(session.context_json)
    new_context = {**existing_context, **body.answers, "_skip_questions": True}
    session.context_json = json.dumps(new_context)
    session.stage = "analyzing_intent"
    session.updated_at = datetime.utcnow()
    db.add(session)
    await db.commit()

    # Reload to get prompt
    original_prompt = session.original_prompt
    await db.close()

    async def event_stream():
        yield _sse_event({"type": "resuming", "session_id": session_id,
                          "message": "Resuming with your answers…"})

        async for event in run_draft_orchestration(
            prompt=original_prompt,
            context=new_context,
            session_id=session_id,
        ):
            yield _sse_event(event)

            evt_type = event.get("type")
            try:
                from app.core.database import AsyncSessionLocal
                async with AsyncSessionLocal() as persist_db:
                    res2 = await persist_db.exec(
                        select(DraftSession).where(DraftSession.id == session_id)
                    )
                    row = res2.first()
                    if not row:
                        continue

                    if evt_type == "intent_analyzed":
                        row.intent_json = json.dumps(event.get("intent", {}))
                        row.stage = "intent_analyzed"
                    elif evt_type == "sources_ready":
                        row.sources_json = json.dumps({
                            "sources": event.get("sources", []),
                            "all_sources": event.get("all_sources", []),
                            "ranking_summary": event.get("ranking_summary", ""),
                        })
                        row.stage = "sources_ready"
                    elif evt_type == "error":
                        row.stage = "failed"
                        row.error_message = event.get("message", "")

                    row.updated_at = datetime.utcnow()
                    persist_db.add(row)
                    await persist_db.commit()
            except Exception as e:
                logger.warning("context_persist.failed", error=str(e))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /sessions/{id}/approve-sources — User approves, trigger assembly
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/approve-sources")
async def approve_sources(
    session_id: str,
    body: SourceApproval,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    User approves research sources.
    Triggers Claude Opus block assembly SSE stream → creates ContractDraft on completion.
    """
    res = await db.exec(select(DraftSession).where(DraftSession.id == session_id))
    session = res.first()
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Load state
    intent = json.loads(session.intent_json) if session.intent_json else {}
    context = json.loads(session.context_json) if session.context_json else {}
    sources_data = json.loads(session.sources_json) if session.sources_json else {}
    redaction_map = json.loads(session.redaction_map_json) if session.redaction_map_json else {}

    all_sources = sources_data.get("all_sources", sources_data.get("sources", []))
    ranked_sources = sources_data.get("sources", [])

    # Filter to approved sources only
    approved_ids = set(body.approved_source_ids)
    if approved_ids:
        approved_sources = [s for s in ranked_sources if s.get("source_id") in approved_ids]
    else:
        # If no specific IDs, use all recommended sources
        approved_sources = [s for s in ranked_sources if s.get("include", True)]

    internal_sources = [s for s in all_sources if s.get("domain") == "internal"]

    # Update session state
    session.approved_source_ids_json = json.dumps(body.approved_source_ids)
    session.stage = "assembling"
    session.updated_at = datetime.utcnow()
    db.add(session)
    await db.commit()
    await db.close()

    async def event_stream():
        yield _sse_event({"type": "assembly_started", "session_id": session_id,
                          "approved_count": len(approved_sources),
                          "message": "Sources approved — starting assembly with Claude Opus…"})

        draft_id = None
        adversarial_report: dict = {}

        async for event in run_assembly_phase(
            session_id=session_id,
            intent=intent,
            context=context,
            token_map=redaction_map,
            approved_sources=approved_sources,
            internal_sources=internal_sources,
        ):
            yield _sse_event(event)

            evt_type = event.get("type")

            # Capture adversarial report to persist with the draft
            if evt_type == "red_team_complete":
                adversarial_report = {
                    "findings": event.get("findings", []),
                    "overall_risk": event.get("overall_risk", ""),
                    "overall_assessment": event.get("overall_assessment", ""),
                    "missing_sections": event.get("missing_sections", []),
                }

            if evt_type == "draft_ready":
                # Persist the completed draft
                try:
                    from app.core.database import AsyncSessionLocal
                    async with AsyncSessionLocal() as persist_db:
                        title = event.get("title", f"Draft {intent.get('contract_type', 'CONTRACT').upper()}")
                        contract_type = event.get("contract_type", intent.get("contract_type", "other"))
                        html_content = event.get("html_content", "")
                        blocks = event.get("blocks", [])
                        issues = event.get("issues", [])

                        # Provenance from blocks
                        provenance = [
                            {
                                "clause_number": b.get("clause_number"),
                                "provenance": b.get("provenance", "generated"),
                                "source": b.get("precedent_source"),
                            }
                            for b in blocks
                        ]

                        draft = ContractDraft(
                            user_id=current_user.id,
                            session_id=session_id,
                            contract_type=contract_type,
                            title=title,
                            status="draft",
                            inputs_json=json.dumps(context),
                            content=html_content,
                            blocks_json=json.dumps(blocks),
                            sources_json=json.dumps(approved_sources),
                            provenance_json=json.dumps(provenance),
                            issues_json=json.dumps(issues),
                            adversarial_report_json=json.dumps(adversarial_report) if adversarial_report else None,
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                        persist_db.add(draft)
                        await persist_db.flush()
                        draft_id = draft.id

                        # Update session
                        res2 = await persist_db.exec(
                            select(DraftSession).where(DraftSession.id == session_id)
                        )
                        sess_row = res2.first()
                        if sess_row:
                            sess_row.draft_id = draft_id
                            sess_row.stage = "complete"
                            sess_row.updated_at = datetime.utcnow()
                            persist_db.add(sess_row)

                        await persist_db.commit()

                        yield _sse_event({
                            "type": "saved",
                            "draft_id": draft_id,
                            "session_id": session_id,
                            "message": "Draft saved successfully",
                        })

                except Exception as e:
                    logger.error("draft_save.failed", error=str(e), exc_info=True)
                    yield _sse_event({"type": "error", "message": f"Failed to save draft: {str(e)}"})

            elif evt_type == "error":
                try:
                    from app.core.database import AsyncSessionLocal
                    async with AsyncSessionLocal() as err_db:
                        res3 = await err_db.exec(
                            select(DraftSession).where(DraftSession.id == session_id)
                        )
                        row = res3.first()
                        if row:
                            row.stage = "failed"
                            row.error_message = event.get("message", "")
                            row.updated_at = datetime.utcnow()
                            err_db.add(row)
                            await err_db.commit()
                except Exception:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /sessions/{id} — Session state
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}", response_model=DraftSessionRead)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    res = await db.exec(select(DraftSession).where(DraftSession.id == session_id))
    session = res.first()
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions", response_model=List[DraftSessionRead])
async def list_sessions(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    res = await db.exec(
        select(DraftSession)
        .where(DraftSession.user_id == current_user.id)
        .order_by(DraftSession.created_at.desc())
        .limit(20)
    )
    return res.all()
