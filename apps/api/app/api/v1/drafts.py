"""
Contract drafting routes
Includes legacy direct-generation endpoint + new Word export endpoint.
The new agentic multi-stage flow is in draft_sessions.py.
"""
import io
import json
import re
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.draft import ContractDraft, DraftCreate, DraftRead, DraftDetail
from app.ai.workflows.draft_workflow import draft_workflow
from app.services.audit_service import log_action
from app.core.logging import get_logger
from app.services.draft_orchestrator import auto_insert_fix

logger = get_logger("drafts_endpoint")

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# POST / — Legacy direct generation (kept for backwards compatibility)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/", response_model=DraftDetail, status_code=201)
async def create_draft(
    body: DraftCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Legacy direct-generation endpoint (Gemini). Use /sessions for the new agentic flow."""
    logger.info("create_draft_started", contract_type=body.contract_type, user_id=current_user.id)
    try:
        state = await draft_workflow.ainvoke({
            "contract_type": body.contract_type,
            "structured_inputs": body.inputs,
            "required_clauses": [],
            "precedent_clauses": [],
            "graph_conflicts": "",
            "draft": {},
        })
        draft_data = state["draft"]
    except Exception as e:
        logger.error("draft_workflow_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {str(e)}")

    clauses = draft_data.get("clauses", [])
    html_content = _draft_to_html(draft_data)

    draft = ContractDraft(
        user_id=current_user.id,
        contract_type=body.contract_type,
        title=body.title or draft_data.get("title", f"Draft {body.contract_type.upper()}"),
        inputs_json=json.dumps(body.inputs),
        content=html_content,
        provenance_json=json.dumps([
            {"clause_number": c.get("clause_number"), "provenance": c.get("provenance"),
             "source": c.get("precedent_source")}
            for c in clauses
        ]),
        issues_json=json.dumps(draft_data.get("issues", [])),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    await log_action(session, current_user.id, "draft", "draft", draft.id)

    return DraftDetail(
        id=draft.id,
        contract_type=draft.contract_type,
        title=draft.title,
        status=draft.status,
        session_id=draft.session_id,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        content=draft.content,
        blocks_json=draft.blocks_json,
        provenance_json=draft.provenance_json,
        issues_json=draft.issues_json,
        sources_json=draft.sources_json,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET / — List drafts
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# GET /{id} — Get single draft
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /{id} — Update draft content / blocks
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/{draft_id}", response_model=DraftDetail)
async def update_draft(
    draft_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Update draft content, blocks, or title."""
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
    if "blocks_json" in body:
        draft.blocks_json = body["blocks_json"]
    draft.updated_at = datetime.utcnow()
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    await log_action(session, current_user.id, "edit", "draft", draft_id)
    return draft


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /{id} — Delete a draft
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{draft_id}")
async def delete_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Delete a contract draft."""
    result = await session.exec(select(ContractDraft).where(ContractDraft.id == draft_id))
    draft = result.first()
    if not draft or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    await session.delete(draft)
    await session.commit()
    await log_action(session, current_user.id, "delete", "draft", draft_id)
    return {"status": "deleted"}



# ─────────────────────────────────────────────────────────────────────────────
# POST /{id}/auto-insert-fix — Generate + apply adversarial fix to a specific clause
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{draft_id}/auto-insert-fix")
async def auto_insert_fix_endpoint(
    draft_id: str,
    body: dict,  # {finding: {...}, block_id: str}
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a corrected clause using Claude Sonnet based on an adversarial finding.
    The caller provides the finding dict and the block_id to fix.
    The endpoint:
      1. Loads the current block content from the draft.
      2. Calls Claude Sonnet (auto_insert_fix) to rewrite the clause.
      3. Updates the block in blocks_json in the database.
      4. Returns {block_id, corrected_content, change_summary}.
    """
    result = await session.exec(select(ContractDraft).where(ContractDraft.id == draft_id))
    draft = result.first()
    if not draft or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.status == "approved":
        raise HTTPException(status_code=400, detail="Approved drafts cannot be edited")

    finding = body.get("finding", {})
    block_id = body.get("block_id", "")
    if not finding or not block_id:
        raise HTTPException(status_code=400, detail="finding and block_id are required")

    # Load blocks and find target block
    blocks = json.loads(draft.blocks_json) if draft.blocks_json else []
    target_block = next((b for b in blocks if b.get("block_id") == block_id), None)
    if not target_block:
        raise HTTPException(status_code=404, detail=f"Block '{block_id}' not found in draft")

    original_content = target_block.get("content", "")
    contract_type = draft.contract_type

    # Generate the corrected clause via Claude Sonnet
    try:
        fix_result = await auto_insert_fix(
            finding=finding,
            original_block_content=original_content,
            contract_type=contract_type,
        )
    except Exception as e:
        logger.error("auto_insert_fix.failed", draft_id=draft_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Fix generation failed: {str(e)}")

    if fix_result.get("_parse_error"):
        raise HTTPException(status_code=500, detail="Claude could not generate a valid fix. Please try again.")

    corrected_content = fix_result.get("corrected_content", "")
    change_summary = fix_result.get("change_summary", "")

    if not corrected_content:
        raise HTTPException(status_code=500, detail="No corrected content returned")

    # Update the block in the draft
    for block in blocks:
        if block.get("block_id") == block_id:
            block["content"] = corrected_content
            block["needs_review"] = False  # fix applied, no longer needs review
            block["review_reason"] = f"Auto-fixed: {change_summary}"
            block["provenance"] = block.get("provenance", "generated")  # keep original provenance
            break

    draft.blocks_json = json.dumps(blocks)
    
    # Remove the resolved finding from the adversarial report
    updated_findings = []
    if draft.adversarial_report_json:
        try:
            report = json.loads(draft.adversarial_report_json)
            # Filter out the finding that matches the suggested_fix exactly, or matches block_id + problematic_text
            original_findings = report.get("findings", [])
            updated_findings = [
                f for f in original_findings 
                if not (f.get("block_id") == block_id and f.get("problematic_text") == finding.get("problematic_text"))
            ]
            report["findings"] = updated_findings
            draft.adversarial_report_json = json.dumps(report)
        except Exception as e:
            logger.warning("auto_insert_fix.failed_to_update_report", error=str(e))

    draft.updated_at = datetime.utcnow()
    session.add(draft)
    await log_action(session, current_user.id, "auto_insert_fix", "draft", draft_id)
    await session.commit()

    return {
        "block_id": block_id,
        "corrected_content": corrected_content,
        "change_summary": change_summary,
        "status": "applied",
        "updated_findings": updated_findings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /{id}/approve — Approve draft
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Approve a draft — only reviewers or admins can approve."""
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


# ─────────────────────────────────────────────────────────────────────────────
# POST /{id}/export — Export to Word (.docx)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{draft_id}/export")
async def export_draft_docx(
    draft_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Export the contract draft as a Word (.docx) document."""
    result = await session.exec(select(ContractDraft).where(ContractDraft.id == draft_id))
    draft = result.first()
    if not draft or draft.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        docx_bytes = _generate_docx(draft)
    except Exception as e:
        logger.error("export_docx.failed", draft_id=draft_id, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

    await log_action(session, current_user.id, "export", "draft", draft_id)

    safe_title = re.sub(r"[^\w\s\-]", "", draft.title)[:60].strip().replace(" ", "_")
    filename = f"{safe_title or 'contract'}.docx"

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _generate_docx(draft: ContractDraft) -> bytes:
    """Generate a Word document from the contract draft."""
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # ── Document styles ───────────────────────────────────────────────────────
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)

    # ── Cover / Title block ───────────────────────────────────────────────────
    title_para = doc.add_heading(draft.title, level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(
        f"Contract Type: {draft.contract_type.replace('_', ' ').title()}  |  "
        f"Status: {draft.status.upper()}  |  "
        f"Generated: {draft.created_at.strftime('%d %B %Y')}"
    ).font.size = Pt(10)
    doc.add_paragraph()

    # ── Watermark for draft status ────────────────────────────────────────────
    if draft.status == "draft":
        watermark_para = doc.add_paragraph("DRAFT — NOT FOR EXECUTION")
        watermark_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = watermark_para.runs[0]
        run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
        run.font.bold = True
        run.font.size = Pt(14)
        doc.add_paragraph()

    # ── Content from blocks (preferred) or raw HTML (fallback) ───────────────
    blocks_json = draft.blocks_json
    if blocks_json:
        blocks = json.loads(blocks_json)
        for block in blocks:
            # Clause heading
            heading_text = f"{block.get('clause_number', '')} {block.get('heading', '')}".strip()
            doc.add_heading(heading_text, level=2)

            # Review flag
            if block.get("needs_review"):
                flag = doc.add_paragraph()
                run = flag.add_run(f"⚠ REVIEW REQUIRED: {block.get('review_reason', '')}")
                run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
                run.font.italic = True
                run.font.size = Pt(10)

            # Clause content (strip HTML tags)
            content = _strip_html(block.get("content", ""))
            if content:
                doc.add_paragraph(content)

            # Source provenance note
            provenance = block.get("provenance", "generated")
            source = block.get("precedent_source", "")
            if provenance != "generated" and source:
                prov_para = doc.add_paragraph()
                run = prov_para.add_run(f"[Source: {provenance.capitalize()} — {source}]")
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

            doc.add_paragraph()

    elif draft.content:
        # Fallback: raw HTML content → strip tags and add as paragraphs
        text = _strip_html(draft.content)
        for para in text.split("\n"):
            para = para.strip()
            if para:
                doc.add_paragraph(para)

    # ── Issues section ────────────────────────────────────────────────────────
    issues_json = draft.issues_json
    if issues_json:
        issues = json.loads(issues_json)
        if issues:
            doc.add_heading("AI Policy Audit — Flagged Issues", level=1)
            for issue in issues:
                severity = issue.get("severity", "low").upper()
                desc = issue.get("description", "")
                para = doc.add_paragraph()
                run = para.add_run(f"[{severity}] {desc}")
                if severity == "HIGH":
                    run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
                elif severity == "MEDIUM":
                    run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
                else:
                    run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    # ── Adversarial Red-Team Report ───────────────────────────────────────────
    adversarial_json = draft.adversarial_report_json
    if adversarial_json:
        try:
            adv_report = json.loads(adversarial_json)
            findings = adv_report.get("findings", [])
            if findings:
                doc.add_heading("⚔ Adversarial Risk Report — Opposing Counsel Analysis", level=1)

                # Overall assessment
                overall_risk = adv_report.get("overall_risk", "")
                assessment = adv_report.get("overall_assessment", "")
                if assessment:
                    risk_para = doc.add_paragraph()
                    run = risk_para.add_run(f"Overall Risk Level: {overall_risk.upper()}  |  {assessment}")
                    run.font.size = Pt(10)
                    run.font.italic = True
                    if overall_risk == "critical":
                        run.font.color.rgb = RGBColor(0x8B, 0x00, 0x00)
                    elif overall_risk == "high":
                        run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
                    elif overall_risk == "medium":
                        run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
                    doc.add_paragraph()

                for i, finding in enumerate(findings, 1):
                    severity = finding.get("severity", "medium").upper()
                    clause_ref = finding.get("clause_ref", "")
                    problematic_text = finding.get("problematic_text", "")
                    exploit = finding.get("exploit", "")
                    suggested_fix = finding.get("suggested_fix", "")

                    # Finding heading
                    heading_para = doc.add_paragraph()
                    run = heading_para.add_run(f"[{severity}] Finding {i}: {clause_ref}")
                    run.bold = True
                    run.font.size = Pt(11)
                    if severity == "CRITICAL":
                        run.font.color.rgb = RGBColor(0x8B, 0x00, 0x00)
                    elif severity == "HIGH":
                        run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
                    else:
                        run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)

                    # Problematic text
                    if problematic_text:
                        prob_para = doc.add_paragraph()
                        run = prob_para.add_run("Vulnerable Text: ")
                        run.bold = True
                        run.font.size = Pt(10)
                        run2 = prob_para.add_run(f'"{problematic_text}"')
                        run2.font.size = Pt(10)
                        run2.font.italic = True

                    # Exploit
                    if exploit:
                        exp_para = doc.add_paragraph()
                        run = exp_para.add_run("How Opposing Counsel Exploits This: ")
                        run.bold = True
                        run.font.size = Pt(10)
                        run2 = exp_para.add_run(exploit)
                        run2.font.size = Pt(10)

                    # Suggested fix
                    if suggested_fix:
                        fix_para = doc.add_paragraph()
                        run = fix_para.add_run("Recommended Fix: ")
                        run.bold = True
                        run.font.size = Pt(10)
                        run.font.color.rgb = RGBColor(0x00, 0x66, 0x33)
                        run2 = fix_para.add_run(suggested_fix)
                        run2.font.size = Pt(10)
                        run2.font.color.rgb = RGBColor(0x00, 0x66, 0x33)

                    doc.add_paragraph()

                # Missing sections
                missing_sections = adv_report.get("missing_sections", [])
                if missing_sections:
                    ms_para = doc.add_paragraph()
                    run = ms_para.add_run("⚠ Missing Critical Sections: ")
                    run.bold = True
                    run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
                    run2 = ms_para.add_run(", ".join(missing_sections))
                    run2.font.size = Pt(10)
        except Exception:
            pass  # Don't fail export if adversarial report is malformed

    # ── Sources ───────────────────────────────────────────────────────────────
    sources_json = draft.sources_json
    if sources_json:
        sources = json.loads(sources_json)
        if sources:
            doc.add_heading("Research Sources", level=1)
            for i, src in enumerate(sources[:10], 1):
                para = doc.add_paragraph()
                para.add_run(f"[{i}] ").bold = True
                para.add_run(f"{src.get('source_name', 'Unknown Source')}")
                if src.get("url"):
                    para.add_run(f" — {src['url']}")
                para.style.font.size = Pt(10)

    # ── Footer note ───────────────────────────────────────────────────────────
    doc.add_page_break()
    footer_para = doc.add_paragraph()
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer_para.add_run(
        "Generated by LexAI — AI-Assisted Legal Drafting System\n"
        "This document is a draft prepared for review only and does not constitute legal advice.\n"
        "Review by qualified legal counsel is required before execution."
    )
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    run.font.italic = True

    # Serialize to bytes
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities."""
    import html as _html_module
    text = re.sub(r"<[^>]+>", "\n", html)
    text = _html_module.unescape(text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _draft_to_html(draft_data: dict) -> str:
    """Convert structured draft JSON to HTML (legacy path)."""
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
