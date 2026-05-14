"""
Translation routes.

IMPORTANT: Static routes (/document/{id}) must be registered BEFORE
dynamic routes (/{job_id}) so FastAPI does not treat "document" as a job_id.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.models.user import User
from app.models.translation import TranslationJob, TranslationRequest, TranslationRead, TranslationResult
from app.models.document import Document
from app.services.audit_service import log_action
from app.celery_app import celery_app

router = APIRouter()
logger = get_logger("translations_endpoint")


@router.post("/", response_model=TranslationRead, status_code=201)
async def start_translation(
    body: TranslationRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Start a translation job for a document."""
    result = await session.exec(select(Document).where(Document.id == body.document_id))
    doc = result.first()
    if not doc or doc.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    job = TranslationJob(
        document_id=body.document_id,
        user_id=current_user.id,
        source_language="",  # Will be detected by workflow
        target_language=body.target_language,
        status="pending",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    try:
        from app.tasks.translation_tasks import run_translation
        task = run_translation.delay(job_id=job.id, document_id=body.document_id)
        job.celery_task_id = task.id
        session.add(job)
        await session.commit()
        await session.refresh(job)
        logger.info("translation.task_queued", job_id=job.id, task_id=task.id)
    except Exception as e:
        logger.error(
            "translation.dispatch_failed",
            job_id=job.id,
            document_id=body.document_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Translation task could not be queued: {str(e)}"
        )

    await log_action(session, current_user.id, "translate", "translation", job.id)
    return job


# Static route MUST come before the dynamic /{job_id} route.
@router.get("/document/{document_id}", response_model=list)
async def get_document_translations(
    document_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(
        select(TranslationJob)
        .where(TranslationJob.document_id == document_id)
        .where(TranslationJob.user_id == current_user.id)
        .order_by(TranslationJob.created_at.desc())
    )
    return result.all()


@router.get("/{job_id}", response_model=TranslationResult)
async def get_translation(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
    job = result.first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Translation job not found")
    return job


@router.post("/{job_id}/cancel")
async def cancel_translation(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Cancel an ongoing translation job."""
    result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
    job = result.first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Translation job not found")

    if job.status in ["done", "error"]:
        raise HTTPException(status_code=400, detail="Cannot cancel a completed or failed job")

    job.status = "error"
    job.error_message = "Translation cancelled by user"

    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGKILL")
        logger.info("translation.task_revoked", job_id=job_id, task_id=job.celery_task_id)

    session.add(job)
    await session.commit()

    await log_action(session, current_user.id, "cancel_translation", "translation", job_id)
    return {"message": "Translation cancelled successfully"}


@router.post("/action/cancel-all")
async def cancel_all_translations(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Cancel all processing translations for the current user."""
    result = await session.exec(select(TranslationJob).where(
        TranslationJob.user_id == current_user.id,
        TranslationJob.status.in_(["pending", "processing"])
    ))

    count = 0
    for job in result.all():
        job.status = "error"
        job.error_message = "Translation cancelled by user"
        if job.celery_task_id:
            celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGKILL")
        session.add(job)
        count += 1

    await session.commit()
    return {"message": f"Cancelled {count} jobs"}


# ──────────────────────────────────────────────────────────────────────────────
# Output format helpers
# ──────────────────────────────────────────────────────────────────────────────

# Maps input extension → (output extension, content-type)
_FORMAT_MAP = {
    ".pdf":  (".pdf",  "application/pdf"),
    ".docx": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".doc":  (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".txt":  (".txt",  "text/plain; charset=utf-8"),
    ".md":   (".md",   "text/markdown; charset=utf-8"),
}
_PDF_EXTS  = {".pdf"}
_DOCX_EXTS = {".docx", ".doc"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".rtf"}


def _resolve_format(original_name: str):
    """
    Return (out_ext, content_type) matching the original file format.
    Unknown types fall back to PDF.
    """
    from pathlib import Path
    ext = Path(original_name).suffix.lower()
    return _FORMAT_MAP.get(ext, (".pdf", "application/pdf"))


# ──────────────────────────────────────────────────────────────────────────────
# Save endpoint — format-matched output
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/{job_id}/save")
async def save_translated_document(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Save the translated document in the SAME FORMAT as the original input.

    - Input PDF  → Output PDF  (structure-preserving, headings/tables/lists)
    - Input DOCX → Output DOCX (heading styles, real tables, bold/italic)
    - Input DOC  → Output DOCX (upgraded to modern format)
    - Input TXT  → Output TXT  (plain text with structure markers)
    - Other      → Output PDF  (safe fallback)
    """
    import uuid
    import json
    from pathlib import Path
    from app.core.storage import upload_file
    from app.models.document import Document
    from app.models.folder import Folder

    result = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
    job = result.first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Translation job not found")
    if job.status != "done":
        raise HTTPException(status_code=400, detail="Translation is not completed yet")
    if job.saved_storage_key:
        return {
            "message": "Already saved",
            "document_id": job.saved_document_id,
            "storage_key": job.saved_storage_key,
        }

    doc_result = await session.exec(select(Document).where(Document.id == job.document_id))
    original_doc = doc_result.first()
    if not original_doc:
        raise HTTPException(status_code=404, detail="Original document not found")

    # ── Resolve output format from original file extension ──
    out_ext, content_type = _resolve_format(original_doc.name)

    # ── Extract rich block data and metadata ──
    translated_blocks = []
    translated_metadata = {}
    try:
        if job.uncertainty_flags:
            flags_data = json.loads(job.uncertainty_flags)
            translated_blocks = flags_data.get("translated_blocks", [])
            translated_metadata = flags_data.get("translated_metadata", {})
    except Exception:
        pass

    sections = []
    try:
        if job.structure_map:
            sections = json.loads(job.structure_map)
    except Exception:
        pass

    if not translated_blocks and not sections and not job.translated_text:
        raise HTTPException(status_code=400, detail="No translated content to save")

    orig_name = Path(original_doc.name).stem  # filename without extension
    src_lang  = job.source_language or "Auto"
    tgt_lang  = job.target_language

    # ── Generate output in matching format ──
    try:
        if out_ext == ".pdf":
            file_bytes = _build_pdf(translated_blocks, sections,
                                    job.translated_text or "", orig_name,
                                    src_lang, tgt_lang,
                                    metadata=translated_metadata)

        elif out_ext == ".docx":
            file_bytes = _build_docx(translated_blocks, sections,
                                     job.translated_text or "", orig_name,
                                     src_lang, tgt_lang,
                                     metadata=translated_metadata)

        else:
            # Plain-text fallback (.txt / .md / unknown)
            file_bytes = _build_text(translated_blocks, sections,
                                     job.translated_text or "", orig_name,
                                     src_lang, tgt_lang).encode("utf-8")

    except Exception as e:
        logger.error(f"Failed to generate {out_ext} for translation {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate output file: {e}")

    # ── Upload ──
    folder_result = await session.exec(
        select(Folder).where(Folder.name == "Translations", Folder.owner_id == current_user.id)
    )
    translations_folder = folder_result.first()
    if not translations_folder:
        translations_folder = Folder(
            id=str(uuid.uuid4()),
            name="Translations",
            description="Saved documents from language translations.",
            owner_id=current_user.id,
        )
        session.add(translations_folder)
        await session.flush()

    new_filename = f"translated_{orig_name}{out_ext}"
    new_doc_id   = str(uuid.uuid4())
    storage_key  = f"documents/{translations_folder.id}/{new_doc_id}/{new_filename}"

    await upload_file(storage_key, file_bytes, content_type)

    new_doc = Document(
        id=new_doc_id,
        name=new_filename,
        folder_id=translations_folder.id,
        owner_id=current_user.id,
        content_type=content_type,
        size_bytes=len(file_bytes),
        storage_key=storage_key,
        status="uploaded",
    )
    session.add(new_doc)

    from datetime import datetime as _dt
    job.saved_storage_key = storage_key
    job.saved_document_id = new_doc_id
    job.saved_at = _dt.utcnow()
    session.add(job)

    await session.commit()
    await log_action(session, current_user.id, "save_translation", "translation",
                     job_id, details={"saved_doc_id": new_doc_id})
    logger.info("translation.saved", job_id=job_id, doc_id=new_doc_id,
                key=storage_key, format=out_ext)

    return {
        "message": "Translated document saved successfully",
        "document_id": new_doc_id,
        "storage_key": storage_key,
        "filename": new_filename,
        "format": out_ext,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Shared helper
# ──────────────────────────────────────────────────────────────────────────────

def _xml_escape(text: str) -> str:
    # 1. Standard XML escaping FIRST to prevent breaking on original doc chars
    text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .strip()
    )

    # 2. Strip junk Unicode symbols
    import re as _re
    text = _re.sub(r'[\u25A0-\u25FF\u25CF\u2022\u00B7]', '', text)
    text = _re.sub(r'[-_]{3,}', '', text)
    
    # 3. Convert high-fidelity Markdown to ReportLab tags (injecting safe tags)
    # Headers: ### Title -> <font color=\"#1E3A5F\"><b>TITLE</b></font>
    def _repl_h(m):
        return f'<br/><br/><font color="#1E3A5F"><b>{m.group(1).strip().upper()}</b></font><br/>'
    text = _re.sub(r'^###\s*(.*)$', _repl_h, text, flags=_re.M)
    
    # Bold: **text** -> <b>text</b>
    text = _re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    
    # Simple line breaks: ensure \n becomes <br/>
    text = text.replace("\n", "<br/>")
    
    return text


def _iter_blocks(translated_blocks, sections, fallback_text):
    """
    Unified iterator: yields translated_blocks if available,
    otherwise synthesises pseudo-blocks from sections or fallback_text.
    Each yielded item is a dict with at minimum {"type", "translated_content"}.
    """
    import re as _re_ib

    if translated_blocks:
        for b in translated_blocks:
            btype = b.get("type", "paragraph")
            txt = b.get("translated_content") or b.get("content") or ""
            
            # If a text block contains a <table> OR a Markdown pipe table, split it out
            has_html_table = "<table" in txt.lower()
            has_pipe_table = "|" in txt and "--|" in txt
            
            if btype not in ("table", "html_table", "kv_table") and (has_html_table or has_pipe_table):
                # Regex to find either <table>...</table> OR lines that look like a pipe table
                # For pipe tables, we look for blocks of lines containing '|'
                import re as _re_ib_local
                
                # First handle HTML tables
                if has_html_table:
                    parts = _re_ib_local.split(r"(<table[\s\S]*?</table>)", txt, flags=_re_ib_local.I)
                else:
                    # Very basic split for pipe tables: look for double newlines
                    parts = txt.split("\n\n")

                for part in parts:
                    part = part.strip()
                    if not part: continue
                    
                    if has_html_table and _re_ib_local.match(r"<table", part, _re_ib_local.I):
                        yield {"type": "html_table", "translated_content": part, 
                               "translated_table_rows": [], "table_rows": []}
                    elif "|" in part and "--|" in part:
                        # Convert pipe table to row data immediately for the builder
                        rows = []
                        for line in part.split("\n"):
                            if "--|" in line: continue
                            cells = [c.strip() for c in line.split("|") if c.strip() or "|" in line]
                            # Filter out empty first/last cells from | cell | cell | format
                            if line.startswith("|"): cells = cells[1:]
                            if line.endswith("|"): cells = cells[:-1]
                            if cells: rows.append(cells)
                        
                        yield {"type": "table", "translated_table_rows": rows, "table_rows": rows}
                    else:
                        nb = b.copy()
                        nb["translated_content"] = part
                        yield nb
            else:
                yield b
        return

    if sections:
        for s in sections:
            hd = (s.get("translated_heading") or "").strip()
            tx = (s.get("translated_text") or "").strip()
            if hd:
                yield {"type": "heading", "heading_level": 2,
                       "translated_content": hd, "style": {"bold": True}}
            if tx:
                # Split on HTML table boundaries so tables get proper block treatment
                parts = _re_ib.split(r"(<table[\s\S]*?</table>)", tx, flags=_re_ib.I)
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    if _re_ib.match(r"<table", part, _re_ib.I):
                        # Yield as a table block so PDF/DOCX builders render it correctly
                        yield {"type": "html_table", "translated_content": part,
                               "translated_table_rows": [], "table_rows": []}
                    else:
                        yield {"type": "paragraph", "translated_content": part, "style": {}}
        return

    # last resort
    for para in fallback_text.split("\n\n"):
        para = para.strip()
        if para:
            yield {"type": "paragraph", "translated_content": para, "style": {}}




# ──────────────────────────────────────────────────────────────────────────────
# Unicode Font Registration (needed for non-Latin scripts like Devanagari)
# ──────────────────────────────────────────────────────────────────────────────

def _get_unicode_fonts() -> tuple:
    """
    Attempt to register a Unicode TrueType font that can render Devanagari, 
    Arabic, CJK and other non-Latin scripts in ReportLab PDFs.
    Returns (regular_font_name, bold_font_name) — falls back to Helvetica if unavailable.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    CANDIDATES = [
        # Windows — Arial has broad Unicode coverage including Devanagari
        ("C:/Windows/Fonts/arial.ttf",   "C:/Windows/Fonts/arialbd.ttf"),
        # Windows — Noto (if user has installed it)
        ("C:/Windows/Fonts/NotoSans-Regular.ttf", "C:/Windows/Fonts/NotoSans-Bold.ttf"),
        # Linux / Docker — Noto Sans (most distros)
        ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
         "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        # Linux — Liberation Sans (common fallback)
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        # Linux — DejaVu Sans
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        # Linux — FreeSans
        ("/usr/share/fonts/truetype/freefont/FreeSans.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ]

    for reg_path, bold_path in CANDIDATES:
        if os.path.exists(reg_path):
            try:
                pdfmetrics.registerFont(TTFont("LexUnicode", reg_path))
                if os.path.exists(bold_path):
                    pdfmetrics.registerFont(TTFont("LexUnicode-Bold", bold_path))
                    return "LexUnicode", "LexUnicode-Bold"
                return "LexUnicode", "LexUnicode"
            except Exception:
                continue

    # Nothing found — fall back to Helvetica (Latin-only, may show boxes for Hindi)
    return "Helvetica", "Helvetica-Bold"


# ──────────────────────────────────────────────────────────────────────────────
# PDF BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def _build_pdf(
    translated_blocks, sections, fallback_text,
    orig_name, src_lang, tgt_lang,
    metadata=None,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, white
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak,
    )
    from reportlab.lib.enums import TA_JUSTIFY
    import io as _io

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=25 * mm, bottomMargin=22 * mm,
        title=f"Translation: {orig_name}",
        author="LexAI",
        subject=f"{src_lang} \u2192 {tgt_lang}",
    )

    story = []
    ss = getSampleStyleSheet()
    NAVY  = HexColor("#1E3A5F")
    BLUE2 = HexColor("#2E6DA4")
    BLUE3 = HexColor("#4A90C4")
    GREY  = HexColor("#666666")
    THDR  = HexColor("#D6E4F0")
    TALT  = HexColor("#F4F8FC")
    TBDR  = HexColor("#B8CFE6")
    RULE  = HexColor("#C8D8E8")

    # ── Use a Unicode-capable font so Devanagari/non-Latin chars render correctly ──
    BASE_FONT, BASE_FONT_BOLD = _get_unicode_fonts()

    hs = {
        1: ParagraphStyle("h1", parent=ss["Normal"], fontName=BASE_FONT_BOLD,
                          fontSize=15, leading=20, spaceAfter=8, spaceBefore=14, textColor=NAVY),
        2: ParagraphStyle("h2", parent=ss["Normal"], fontName=BASE_FONT_BOLD,
                          fontSize=13, leading=18, spaceAfter=6, spaceBefore=10, textColor=BLUE2),
        3: ParagraphStyle("h3", parent=ss["Normal"], fontName=BASE_FONT_BOLD,
                          fontSize=11.5, leading=16, spaceAfter=4, spaceBefore=8, textColor=BLUE2),
        4: ParagraphStyle("h4", parent=ss["Normal"], fontName=BASE_FONT_BOLD,
                          fontSize=10.5, leading=14, spaceAfter=3, spaceBefore=6, textColor=BLUE3),
        5: ParagraphStyle("h5", parent=ss["Normal"], fontName=BASE_FONT_BOLD,
                          fontSize=10, leading=13, spaceAfter=3, spaceBefore=5, textColor=BLUE3),
        6: ParagraphStyle("h6", parent=ss["Normal"], fontName=BASE_FONT,
                          fontSize=10, leading=13, spaceAfter=2, spaceBefore=4, textColor=GREY),
    }
    body   = ParagraphStyle("body", parent=ss["Normal"], fontName=BASE_FONT,
                             fontSize=10, leading=15, spaceAfter=5, spaceBefore=2,
                             alignment=TA_JUSTIFY, wordWrap="CJK")
    b_bold = ParagraphStyle("bb", parent=body, fontName=BASE_FONT_BOLD)
    b_ital = ParagraphStyle("bi", parent=body, fontName=BASE_FONT)
    lst_s  = ParagraphStyle("ls", parent=ss["Normal"], fontName=BASE_FONT,
                             fontSize=10, leading=14, spaceAfter=3, leftIndent=16)
    meta_s  = ParagraphStyle("mt", parent=ss["Normal"], fontName=BASE_FONT,
                             fontSize=8.5, leading=12, textColor=GREY)
    label_s = ParagraphStyle("lb", parent=body, fontName=BASE_FONT_BOLD, fontSize=9)

    # ── Metadata Table (Phase 1/2 requirement) ──
    if metadata:
        meta_grid = []
        # Map technician keys to human-readable labels
        key_map = {
            "certificate_no": "Certificate No.",
            "issued_date": "Issued Date",
            "stamp_duty_amount": "Stamp Duty Amount",
            "article": "Article",
            "account_reference": "Account Reference",
            "unique_doc_ref": "Unique Doc Ref"
        }
        for k, label in key_map.items():
            val = metadata.get(k)
            if val:
                meta_grid.append([Paragraph(label, label_s), Paragraph(_xml_escape(str(val)), body)])
        
        if meta_grid:
            mtbl = Table(meta_grid, colWidths=[40*mm, 120*mm])
            mtbl.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, TBDR),
                ('BACKGROUND', (0,0), (0,-1), TALT),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('LEFTPADDING', (0,0), (-1,-1), 8),
                ('RIGHTPADDING', (0,0), (-1,-1), 8),
            ]))
            story.append(mtbl)
            story.append(Spacer(1, 8 * mm))

    def _draw_table(table_rows):
        if not table_rows:
            return
        grid = []
        for r_idx, row in enumerate(table_rows):
            cells = []
            for cell in row:
                if isinstance(cell, dict):
                    txt = cell.get("translated_text", cell.get("text", ""))
                    hdr = cell.get("is_header", r_idx == 0)
                else:
                    txt = str(cell); hdr = r_idx == 0
                
                # Sanitize table cell text
                clean_txt = _xml_escape(txt or "")
                if not clean_txt:
                    clean_txt = " " # space to keep cell borders
                cells.append(Paragraph(clean_txt, b_bold if hdr else body))
            if cells:
                grid.append(cells)
        if not grid:
            return
        nc = max(len(r) for r in grid)
        cw = (A4[0] - 44 * mm) / max(nc, 1)
        t = Table(grid, colWidths=[cw] * nc, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), THDR),
            ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, TALT]),
            ("GRID", (0, 0), (-1, -1), 0.4, TBDR),
            ("BOX", (0, 0), (-1, -1), 0.8, TBDR),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(t)
        story.append(Spacer(1, 4 * mm))

    for block in _iter_blocks(translated_blocks, sections, fallback_text):
        btype = block.get("type", "paragraph")

        if btype == "page_break":
            story.append(PageBreak())
            continue

        # ── Table blocks ──────────────────────────────────────────────────────
        if btype in ("table", "html_table", "kv_table"):
            # PRIORITY: Use translated HTML first (contains correctly translated English text).
            # translated_table_rows is a backup that may still contain original-language text,
            # so we only fall back to it if no HTML is available.
            html_tbl = (
                block.get("translated_html_table")
                or block.get("translated_content", "")
            )
            
            if html_tbl and "<table" in html_tbl.lower():
                # Parse the HTML into rows for ReportLab
                import re as _re2
                rows_html: list = []
                for tr in _re2.findall(r'<tr[^>]*>(.*?)</tr>', html_tbl, _re2.I | _re2.S):
                    cells_raw = _re2.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, _re2.I | _re2.S)
                    cells_clean = []
                    for c in cells_raw:
                        c_no_br = _re2.sub(r'<br\s*/?>', '\n', c, flags=_re2.I)
                        c_no_tags = _re2.sub(r'<[^>]+>', '', c_no_br).strip()
                        cells_clean.append(c_no_tags)
                    if cells_clean:
                        rows_html.append(cells_clean)
                if rows_html:
                    _draw_table(rows_html)
                    story.append(Spacer(1, 4))
                    continue

            # Fallback: use structured translated_table_rows
            rows = block.get("translated_table_rows") or block.get("table_rows", [])
            if rows:
                _draw_table(rows)
            story.append(Spacer(1, 4))
            continue

        txt = block.get("translated_content", block.get("content", ""))
        if not txt.strip():
            continue

        # Convert Markdown to ReportLab-friendly XML
        clean_txt = _xml_escape(txt)

        if btype == "signatures":
            story.append(Spacer(1, 10))
            story.append(HRFlowable(width="60%", thickness=0.5, color=GREY, hAlign="LEFT"))
            story.append(Paragraph(clean_txt, body))
            continue

        # Normal paragraph (contains headers and bold numbering in markdown tags now)
        story.append(Paragraph(clean_txt, body))
        story.append(Spacer(1, 4))


    doc.build(story)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# DOCX BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def _build_docx(
    translated_blocks, sections, fallback_text,
    orig_name, src_lang, tgt_lang,
    metadata=None,
) -> bytes:
    """
    Build a Word DOCX file that mirrors the original document structure.
    Uses python-docx which is already a project dependency.
    """
    from docx import Document as DocxDocument
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    import io as _io

    docx = DocxDocument()

    # ── Page margins (mirror A4 legal standard) ──
    section = docx.sections[0]
    section.left_margin   = Inches(0.87)   # ~ 22 mm
    section.right_margin  = Inches(0.87)
    section.top_margin    = Inches(0.98)   # ~ 25 mm
    section.bottom_margin = Inches(0.87)

    # ── Document title header ──
    title_para = docx.add_heading(f"Translation: {orig_name}", level=1)
    title_para.runs[0].font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)

    # ── Metadata Table (Phase 1/2) ──
    if metadata:
        meta_table = docx.add_table(rows=0, cols=2)
        meta_table.style = 'Table Grid'
        key_map = {
            "certificate_no": "Certificate No.",
            "issued_date": "Issued Date",
            "stamp_duty_amount": "Stamp Duty Amount",
            "article": "Article",
            "account_reference": "Account Reference",
            "unique_doc_ref": "Unique Doc Ref"
        }
        for k, label in key_map.items():
            val = metadata.get(k)
            if val:
                row = meta_table.add_row().cells
                row[0].text = label
                row[1].text = str(val)
                # Make label bold
                row[0].paragraphs[0].runs[0].bold = True
        docx.add_paragraph() # space

    # ── Heading colour map ──
    _HEADING_COLORS = {
        1: RGBColor(0x1E, 0x3A, 0x5F),  # deep navy
        2: RGBColor(0x2E, 0x6D, 0xA4),  # mid blue
        3: RGBColor(0x2E, 0x6D, 0xA4),
        4: RGBColor(0x4A, 0x90, 0xC4),
        5: RGBColor(0x4A, 0x90, 0xC4),
        6: RGBColor(0x66, 0x66, 0x66),
    }

    def _add_heading(text: str, level: int):
        level = min(max(level, 1), 6)
        h = docx.add_heading(text.strip(), level=level)
        if h.runs:
            color = _HEADING_COLORS.get(level, RGBColor(0x1E, 0x3A, 0x5F))
            h.runs[0].font.color.rgb = color
        return h

    def _add_paragraph(text: str, bold=False, italic=False):
        p = docx.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run = p.add_run(text.strip())
        run.font.size = Pt(10)
        run.bold = bold
        run.italic = italic
        p.paragraph_format.space_after = Pt(5)
        return p

    def _add_list_item(text: str, numbered: bool = False, idx: int = 0):
        style = "List Number" if numbered else "List Bullet"
        try:
            p = docx.add_paragraph(text.strip(), style=style)
        except Exception:
            # Style may not exist in all environments — fall back to indented para
            p = docx.add_paragraph()
            prefix = f"{idx + 1}." if numbered else "\u2022"
            run = p.add_run(f"{prefix}  {text.strip()}")
            run.font.size = Pt(10)
            p.paragraph_format.left_indent = Inches(0.25)
        return p

    def _add_table(table_rows: list):
        if not table_rows:
            return
        # Filter rows with at least one non-empty cell
        valid_rows = [
            row for row in table_rows
            if any(
                (cell.get("text") or cell.get("translated_text") or "").strip()
                if isinstance(cell, dict) else str(cell).strip()
                for cell in row
            )
        ]
        if not valid_rows:
            return

        nc = max(len(r) for r in valid_rows)
        tbl = docx.add_table(rows=len(valid_rows), cols=nc)
        tbl.style = "Table Grid"

        for r_idx, row in enumerate(valid_rows):
            for c_idx, cell in enumerate(row):
                if c_idx >= nc:
                    break
                if isinstance(cell, dict):
                    txt = (cell.get("translated_text") or cell.get("text") or "").strip()
                    is_hdr = cell.get("is_header", r_idx == 0)
                else:
                    txt = str(cell).strip()
                    is_hdr = r_idx == 0

                docx_cell = tbl.cell(r_idx, c_idx)
                p = docx_cell.paragraphs[0]
                run = p.add_run(txt)
                run.font.size = Pt(9.5)
                run.bold = is_hdr
                if is_hdr:
                    run.font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)
                    # Light blue header background
                    tc = docx_cell._tc
                    tcPr = tc.get_or_add_tcPr()
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:val"), "clear")
                    shd.set(qn("w:color"), "auto")
                    shd.set(qn("w:fill"), "D6E4F0")
                    tcPr.append(shd)

        # Space after table
        docx.add_paragraph()

    # ── Render blocks ──
    for block in _iter_blocks(translated_blocks, sections, fallback_text):
        btype = block.get("type", "paragraph")

        if btype == "page_break":
            docx.add_page_break()
            continue

        if btype in ("heading", "document_title"):
            txt = block.get("translated_content", block.get("content", ""))
            if txt.strip():
                lvl = 1 if btype == "document_title" else int(block.get("heading_level", 2))
                clean_txt = txt.strip()
                if btype == "document_title":
                    clean_txt = clean_txt.upper()
                _add_heading(clean_txt, lvl)
            continue

        if btype in ("table", "html_table", "kv_table"):
            # PRIORITY: prefer translated HTML (English) over translated_table_rows (may be original lang)
            import re as _re2
            strip_tags = lambda s: _re2.sub(r'<[^>]+>', '', s).strip()
            
            html_tbl = block.get("translated_html_table") or block.get("translated_content", "")
            if html_tbl and "<table" in html_tbl.lower():
                rows_html = []
                for tr in _re2.findall(r'<tr[^>]*>(.*?)</tr>', html_tbl, _re2.I | _re2.S):
                    cells_raw = _re2.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, _re2.I | _re2.S)
                    cells_clean = [strip_tags(c) for c in cells_raw]
                    if cells_clean:
                        rows_html.append(cells_clean)
                if rows_html:
                    _add_table(rows_html)
                    continue

            # Fallback: structured row data
            rows = block.get("translated_table_rows") or block.get("table_rows", [])
            if rows:
                _add_table(rows)
            continue


        if btype == "list":
            items = block.get("list_items", [])
            if not items:
                raw = block.get("translated_content", block.get("content", ""))
                items = [line.strip() for line in raw.splitlines() if line.strip()]
            
            is_num = block.get("list_style", "numbered") == "numbered"
            for i, item in enumerate(items):
                _add_list_item(item, numbered=is_num, idx=i)
            continue

        if btype == "signatures":
            txt = block.get("translated_content", block.get("content", ""))
            if txt.strip():
                docx.add_paragraph("-" * 30)
                _add_paragraph(txt, italic=True)
            continue

        # Paragraph / default
        txt = block.get("translated_content", block.get("content", ""))
        if not txt.strip():
            continue
        
        # Handle **1.** bolding pattern from Gemini (Phase 2 requirement)
        import re as _re
        has_bold_num = _re.match(r'^\s*\*\*(\d+\.?)\*\*', txt)
        if has_bold_num:
            clean_txt = _re.sub(r'^\s*\*\*(\d+\.?)\*\*', r'\1', txt)
            p = _add_paragraph(clean_txt)
            # Bold the prefix number
            if p.runs: p.runs[0].bold = True
        else:
            bst = block.get("style", {})
            _add_paragraph(txt, bold=bst.get("bold", False), italic=bst.get("italic", False))

    buf = _io.BytesIO()
    docx.save(buf)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# PLAIN TEXT BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def _build_text(
    translated_blocks, sections, fallback_text,
    orig_name, src_lang, tgt_lang,
) -> str:
    """
    Build a clean plain-text representation of the translated document.
    Headings use underlines or ## prefix, tables use ASCII art borders.
    """
    lines = []

    # Header
    title = f"Translation: {orig_name}"
    lines.append(title)
    lines.append("=" * len(title))
    lines.append(f"Source: {src_lang}  →  Target: {tgt_lang}")
    lines.append(f"Generated by LexAI")
    lines.append("")
    lines.append("-" * 60)
    lines.append("")

    _UNDERLINE = {1: "=", 2: "-", 3: "~", 4: "^", 5: "'", 6: "`"}

    def _table_to_text(table_rows) -> str:
        if not table_rows:
            return ""
        # Build cell text grid
        grid = []
        for row in table_rows:
            cells = []
            for cell in row:
                if isinstance(cell, dict):
                    txt = (cell.get("translated_text") or cell.get("text") or "").strip()
                else:
                    txt = str(cell).strip()
                cells.append(txt)
            grid.append(cells)
        if not grid:
            return ""
        nc = max(len(r) for r in grid)
        # Pad to same width
        col_widths = [0] * nc
        for row in grid:
            for i, cell in enumerate(row[:nc]):
                col_widths[i] = max(col_widths[i], len(cell))
        sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
        result_lines = [sep]
        for r_idx, row in enumerate(grid):
            cells_padded = []
            for i in range(nc):
                cell = row[i] if i < len(row) else ""
                cells_padded.append(f" {cell:<{col_widths[i]}} ")
            result_lines.append("|" + "|".join(cells_padded) + "|")
            if r_idx == 0:  # separator after header
                result_lines.append(sep)
        result_lines.append(sep)
        return "\n".join(result_lines)

    for block in _iter_blocks(translated_blocks, sections, fallback_text):
        btype = block.get("type", "paragraph")

        if btype == "page_break":
            lines.append("\n" + "─" * 60 + "\n")
            continue

        if btype == "heading":
            txt = block.get("translated_content", block.get("content", ""))
            if txt.strip():
                lvl = block.get("heading_level", 2)
                lines.append("")
                lines.append(txt.strip())
                ul_char = _UNDERLINE.get(lvl, "-")
                lines.append(ul_char * len(txt.strip()))
                lines.append("")
            continue

        if btype in ("table", "html_table", "kv_table"):
            rows = block.get("translated_table_rows") or block.get("table_rows", [])
            if not rows:
                html_tbl = block.get("translated_html_table") or block.get("translated_content", "")
                if html_tbl:
                    import re as _re2
                    cell_texts = _re2.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", html_tbl, _re2.I | _re2.S)
                    col_count = len(_re2.findall(r"<t[dh]", html_tbl.split("</tr>")[0], _re2.I)) if "</tr>" in html_tbl else 1
                    if cell_texts and col_count:
                        rows = [[c.strip() for c in r] for r in [cell_texts[i:i+col_count] for i in range(0, len(cell_texts), col_count)]]
            txt = _table_to_text(rows)
            if txt:
                lines.append("")
                lines.append(txt)
                lines.append("")
            continue

        if btype == "list":
            items = block.get("list_items", [])
            if not items:
                raw = block.get("translated_content", block.get("content", ""))
                items = [raw] if raw.strip() else []
            is_num = block.get("list_style", "bullet") == "numbered"
            for i, item in enumerate(items):
                if item.strip():
                    prefix = f"{i + 1}." if is_num else "\u2022"
                    lines.append(f"  {prefix} {item.strip()}")
            lines.append("")
            continue

        # Paragraph / default
        txt = block.get("translated_content", block.get("content", ""))
        if txt.strip():
            lines.append(txt.strip())
            lines.append("")

    return "\n".join(lines)
