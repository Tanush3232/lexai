"""
Contract drafting routes
"""
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.draft import ContractDraft, DraftCreate, DraftRead, DraftDetail
from app.ai.workflows.draft_workflow import draft_workflow
from app.services.audit_service import log_action
from app.core.logging import get_logger

logger = get_logger("drafts_endpoint")

router = APIRouter()


@router.post("/", response_model=DraftDetail, status_code=201)
async def create_draft(
    body: DraftCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Generate a first-draft contract using precedent retrieval and Gemini."""
    logger.info("create_draft_started", contract_type=body.contract_type, user_id=current_user.id)
    # Run LangGraph drafting workflow
    try:
        logger.info("invoking_draft_workflow")
        state = await draft_workflow.ainvoke({
            "contract_type": body.contract_type,
            "structured_inputs": body.inputs,
            "required_clauses": [],
            "precedent_clauses": [],
            "graph_conflicts": "",
            "draft": {},
        })
        draft_data = state["draft"]
        logger.info("draft_workflow_completed", clause_count=len(draft_data.get("clauses", [])))
    except Exception as e:
        logger.error("draft_workflow_failed", error=str(e), contract_type=body.contract_type, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {str(e)}")

    # Convert structured draft to rich text / HTML
    logger.debug("converting_draft_to_html")
    clauses = draft_data.get("clauses", [])
    html_content = _draft_to_html(draft_data)

    draft = ContractDraft(
        user_id=current_user.id,
        contract_type=body.contract_type,
        title=body.title or draft_data.get("title", f"Draft {body.contract_type.upper()}"),
        inputs_json=json.dumps(body.inputs),
        content=html_content,
        provenance_json=json.dumps([
            {"clause_number": c.get("clause_number"), "provenance": c.get("provenance"), "source": c.get("precedent_source")}
            for c in clauses
        ]),
        issues_json=json.dumps(draft_data.get("issues", [])),
    )
    logger.info("saving_draft_to_db")
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    logger.info("draft_saved", draft_id=draft.id)
    await log_action(session, current_user.id, "draft", "draft", draft.id)

    return DraftDetail(
        id=draft.id,
        contract_type=draft.contract_type,
        title=draft.title,
        status=draft.status,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        content=draft.content,
        provenance_json=draft.provenance_json,
        issues_json=draft.issues_json,
    )


@router.get("/", response_model=List[DraftRead])
async def list_drafts(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(
        select(ContractDraft)
        .where(ContractDraft.user_id == current_user.id)
        .order_by(ContractDraft.updated_at.desc())
    )
    return result.all()


@router.get("/{draft_id}", response_model=DraftDetail)
async def get_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(ContractDraft).where(ContractDraft.id == draft_id))
    draft = result.first()
    if not draft or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    await log_action(session, current_user.id, "read", "draft", draft_id)
    return draft


@router.patch("/{draft_id}", response_model=DraftDetail)
async def update_draft(
    draft_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Update draft content (rich text edits by legal team)."""
    from datetime import datetime
    result = await session.exec(select(ContractDraft).where(ContractDraft.id == draft_id))
    draft = result.first()
    if not draft or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "approved":
        raise HTTPException(status_code=400, detail="Approved drafts cannot be edited")
    if "content" in body:
        draft.content = body["content"]
    if "title" in body:
        draft.title = body["title"]
    draft.updated_at = datetime.utcnow()
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    await log_action(session, current_user.id, "edit", "draft", draft_id)
    return draft


@router.post("/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Approve a draft — only reviewers or admins can approve."""
    from datetime import datetime
    if current_user.role not in ["reviewer", "ops_admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Only reviewers and admins can approve drafts")

    result = await session.exec(select(ContractDraft).where(ContractDraft.id == draft_id))
    draft = result.first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft.status = "approved"
    draft.approved_by = current_user.id
    draft.approved_at = datetime.utcnow()
    session.add(draft)
    await session.commit()
    await log_action(session, current_user.id, "approve", "draft", draft_id)
    return {"status": "approved", "approved_by": current_user.full_name}


def _draft_to_html(draft_data: dict) -> str:
    """Convert structured draft JSON to HTML for TipTap editor."""
    import html as _html

    clauses = draft_data.get("clauses", [])
    html = f"<h1>{_html.escape(draft_data.get('title', 'Contract Draft'))}</h1>\n"

    for clause in clauses:
        num = _html.escape(clause.get("clause_number", ""))
        heading = _html.escape(clause.get("heading", ""))
        text = _html.escape(clause.get("text", ""))
        needs_review = clause.get("needs_review", False)
        provenance = _html.escape(clause.get("provenance", "generated"))

        review_class = ' class="needs-review"' if needs_review else ""
        prov_label = f'<span class="provenance-{provenance}">[{provenance.upper()}]</span> '

        html += f'<h2{review_class}>{num} {heading}</h2>\n'
        html += f"<p>{prov_label}{text}</p>\n"
        if needs_review:
            reason = _html.escape(clause.get("review_reason", ""))
            html += f'<blockquote class="review-note">⚠️ Review required: {reason}</blockquote>\n'

    issues = draft_data.get("issues", [])
    if issues:
        html += "<h2>Issues Flagged</h2><ul>\n"
        for issue in issues:
            severity = _html.escape(issue.get("severity", "low"))
            description = _html.escape(issue.get("description", ""))
            html += f'<li class="issue-{severity}">{description}</li>\n'
        html += "</ul>\n"

    return html
