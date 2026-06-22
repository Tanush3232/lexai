"""
Chat/Document Intelligence routes with scope-aware ReAct Agent retrieval
"""
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.user import User
from app.models.chat import (
    ChatSession, ChatMessage, ChatSessionCreate,
    ChatMessageCreate, ChatMessageRead, SourceRef, ScopeType
)
from app.models.folder import Folder
from app.models.document import Document
from app.ai.agent.react_agent import ReActAgent
from app.services.audit_service import log_action

router = APIRouter()
logger = get_logger("chat_endpoint")


@router.post("/sessions", response_model=dict, status_code=201)
async def create_session(
    body: ChatSessionCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Create a new chat session with a specified scope."""
    chat_session = ChatSession(
        user_id=current_user.id,
        title=body.title or "New Chat",
        scope_type=body.scope_type.value,
        scope_folder_ids=",".join(body.scope_folder_ids),
        scope_document_ids=",".join(body.scope_document_ids),
    )
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    
    return {"id": chat_session.id, "title": chat_session.title, "scope_type": chat_session.scope_type}


@router.get("/sessions", response_model=List[dict])
async def list_sessions(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
    )
    sessions = result.all()

    out = []
    for s in sessions:
        # Fetch the last assistant message for preview
        msg_result = await session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == s.id)
            .order_by(ChatMessage.created_at.asc())
        )
        msgs = msg_result.all()
        msg_count = len(msgs)
        last_user_msg = next((m.content for m in reversed(msgs) if m.role == "user"), None)

        out.append({
            "id": s.id,
            "title": s.title,
            "scope_type": s.scope_type,
            "message_count": msg_count,
            "last_query": last_user_msg,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        })
    return out


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a chat session and all its messages."""
    result = await session.exec(select(ChatSession).where(ChatSession.id == session_id))
    chat_session = result.first()
    if not chat_session or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete all messages first (FK constraint)
    msg_result = await session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id))
    for msg in msg_result.all():
        await session.delete(msg)

    await session.delete(chat_session)
    await session.commit()


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageRead)
async def send_message(
    session_id: str,
    body: ChatMessageCreate,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Send a message and get a source-grounded answer using ReAct agent."""
    # Verify session ownership
    result = await db.exec(select(ChatSession).where(ChatSession.id == session_id))
    chat_session = result.first()
    if not chat_session or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Build scope
    folder_ids = [f for f in chat_session.scope_folder_ids.split(",") if f]
    doc_ids = [d for d in chat_session.scope_document_ids.split(",") if d]

    logger.info("chat.request.start", session_id=session_id, folder_count=len(folder_ids), doc_count=len(doc_ids))

    # Save user message
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.content,
    )
    db.add(user_msg)
    await db.commit()

    # Fetch previous messages for context (up to 20 to cover multi-turn sessions)
    msg_result = await db.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(20)
    )
    previous_msgs = msg_result.all()
    history = "\n".join([f"{m.role}: {m.content}" for m in previous_msgs[:-1]])  # exclude the one we just added

    # Accumulate evidence from ALL previous assistant turns in this session.
    # This means if turn-1 found Harvey AI clauses and turn-2 found sick-leave clauses,
    # turn-3 starts with BOTH sets already loaded — THINK can decide to SYNTHESIZE
    # immediately for follow-ups or do a targeted new search for genuinely new topics.
    MAX_PRIOR_EVIDENCE = 60  # cap to keep THINK prompt manageable
    prior_evidence: List = []
    seen_clause_ids: set = set()
    for msg in previous_msgs[:-1]:  # chronological — accumulate in order
        if msg.role != "assistant" or not msg.retrieval_trace:
            continue
        try:
            turn_evidence = json.loads(msg.retrieval_trace).get("evidence", [])
            for item in turn_evidence:
                cid = item.get("clause_id") or item.get("id")
                if cid:
                    if cid not in seen_clause_ids:
                        prior_evidence.append(item)
                        seen_clause_ids.add(cid)
                else:
                    # structural items (outline, extraction) — include as-is
                    prior_evidence.append(item)
                if len(prior_evidence) >= MAX_PRIOR_EVIDENCE:
                    break
        except Exception:
            pass
        if len(prior_evidence) >= MAX_PRIOR_EVIDENCE:
            break
    if prior_evidence:
        logger.info("chat.evidence_carried_over", session_id=session_id,
                    items=len(prior_evidence), unique_ids=len(seen_clause_ids))

    # Release the DB connection back to the pool BEFORE the long LLM call.
    # Holding it open for 60-120 seconds causes the connection to go stale,
    # making the post-agent commit hang indefinitely.
    await db.close()

    logger.info("chat.agent.init", session_id=session_id)
    # Run ReAct Agent workflow
    try:
        agent = ReActAgent(doc_ids=doc_ids, folder_ids=folder_ids,
                           prior_evidence=prior_evidence, question=body.content)
        logger.info("chat.agent.run.start", session_id=session_id)
        answer_data = await agent.run(body.content, previous_messages=history)
        logger.info("chat.agent.run.done", session_id=session_id, keys=list(answer_data.keys()))
    except Exception as e:
        logger.error("chat.agent.run.error", session_id=session_id, error=str(e), exc_info=True)
        answer_data = {
            "answer": f"An error occurred during agent reasoning: {str(e)}",
            "sources": [],
            "confidence": "low",
        }

    logger.info("chat.building_sources", session_id=session_id)
    # Build source refs with rich metadata
    sources = []
    agent_sources = answer_data.get("sources", [])
    
    if agent_sources:
        # Fetch document names in bulk for these citations
        cited_doc_ids = list({s.get("doc_id") for s in agent_sources if s.get("doc_id")})
        doc_map = {}
        if cited_doc_ids:
            dr = await db.exec(select(Document).where(Document.id.in_(cited_doc_ids)))
            for d in dr.all():
                doc_map[d.id] = {"name": d.name, "folder_id": d.folder_id}
                
        # Optional: fetch folder names if needed, but for simplicity we rely on doc_map
        for s in agent_sources:
            did = s.get("doc_id", "")
            d_info = doc_map.get(did, {})
            
            sources.append(SourceRef(
                document_id=did,
                document_name=d_info.get("name", "Unknown Document"),
                folder_name="Document Folder", # Could join with folders if desired
                page_number=s.get("page", 1) or 1,
                section=s.get("section_id"),
                clause_id=s.get("clause_id"),
                snippet=s.get("snippet", ""),
                relevance_score=s.get("score", 0.0),
            ))

    # Save assistant message
    assistant_msg = ChatMessage(
        session_id=session_id,
        role="assistant",
        content=answer_data.get("answer", ""),
        sources=json.dumps([s.model_dump() for s in sources]),
        retrieval_trace=json.dumps({
            "confidence": answer_data.get("confidence", "low"),
            "tool_log": agent.tool_log if 'agent' in locals() else [],
            # Store raw agent evidence (all clauses found this turn) so future
            # turns can accumulate across topics without re-searching.
            "evidence": agent.evidence if 'agent' in locals() else [],
        }),
    )
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(assistant_msg)
    logger.info("assistant_message_saved", session_id=session_id, message_id=assistant_msg.id)

    await log_action(db, current_user.id, "chat", "chat", session_id)

    return ChatMessageRead(
        id=assistant_msg.id,
        role="assistant",
        content=assistant_msg.content,
        sources=sources,
        created_at=assistant_msg.created_at,
    )


@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageRead])
async def get_messages(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await db.exec(select(ChatSession).where(ChatSession.id == session_id))
    chat_session = result.first()
    if not chat_session or chat_session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_result = await db.exec(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = msg_result.all()

    out = []
    for m in messages:
        sources = None
        if m.sources:
            try:
                sources = [SourceRef(**s) for s in json.loads(m.sources)]
            except Exception:
                pass
        out.append(ChatMessageRead(id=m.id, role=m.role, content=m.content, sources=sources, created_at=m.created_at))
    return out


@router.post("/improve")
async def improve_prompt(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """Improve a user's query using the Flash model for better legal specificity."""
    from app.ai.gemini_client import generate_text
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")

    prompt = f"""You are a legal AI prompt engineer. Rewrite this query to be more precise and effective for legal document analysis.

Original query: {content}

Rules:
- Keep the same intent but make it specific and detailed
- Mention desired output format if useful (e.g. table, summary, list)
- Add legal context if relevant
- Be concise — return only the improved query, nothing else

Improved query:"""
    improved = await generate_text(prompt, use_pro=False, temperature=0.3, feature_name="query_improvement")
    return {"improved": improved}

