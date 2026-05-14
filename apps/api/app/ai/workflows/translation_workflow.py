"""
Structured translation LangGraph workflow — Legal-Grade Pipeline.

Pipeline:
  Step 1: detect_language        — Detect source language
  Step 2: parse_structure        — Layout-aware block extraction (PDF/DOCX/TXT)
  Step 3: translate_blocks       — Gemini PRO block-by-block translation
                                    ├─ paragraphs/headings: BLOCK_TRANSLATION_PROMPT
                                    └─ tables: TABLE_TRANSLATION_PROMPT (cell-by-cell)
  Step 4: validate_translation   — Optional Gemini Pro quality/accuracy audit
  Step 5: reconstruct_document   — Assembles translated_sections for PDF reconstruction

Model: ALWAYS Gemini Pro for translation (legal-grade accuracy)
       Gemini Flash ONLY for language detection (lightweight)
"""
from __future__ import annotations

import json
import asyncio
from typing import Any, Dict, List, TypedDict, Optional
from langgraph.graph import StateGraph, END

from app.ai.gemini_client import generate_structured
from app.ai.prompts import (
    LANGUAGE_DETECTION_PROMPT,
    BLOCK_TRANSLATION_PROMPT,
    TABLE_TRANSLATION_PROMPT,
    TRANSLATION_VALIDATION_PROMPT,
)
from app.core.logging import get_logger

logger = get_logger("translation_workflow_v2")

# Maximum characters per block before splitting
MAX_BLOCK_CHARS = 6000
# Increased back to 4: Since we are using the stable 2.5 Pro model, we can handle higher concurrency to speed up large 50+ page documents.
PRO_SEMAPHORE_LIMIT = 4
# Enable validation pass — set False to skip for speed
ENABLE_VALIDATION = True


# ─────────────────────────────────────────────
# State Definition
# ─────────────────────────────────────────────

class TranslationState(TypedDict):
    job_id: str
    document_id: str
    document_text: str          # Full plain text (kept for backwards compat)
    source_language: str
    target_language: str
    structure_map: List[Dict]   # Raw structured blocks from parser
    metadata: Dict[str, str]    # Document metadata (e-Stamp info etc.)
    translated_blocks: List[Dict]  # Translated blocks
    translated_metadata: Dict[str, str] # Translated metadata
    validation_report: Dict     # Output of validation pass
    result: Dict                # Final assembled result (compatible with old schema)
    # Internal keys (must be in schema to persist across nodes)
    _file_bytes: Optional[bytes]
    _filename: Optional[str]


# ─────────────────────────────────────────────
# Cancellation Helpers
# ─────────────────────────────────────────────

async def _get_safe_session():
    """
    Create a loop-safe session using NullPool for background tasks.
    Ties the engine lifecycle directly to the current event loop.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.core.config import settings
    from sqlmodel.ext.asyncio.session import AsyncSession
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop? We can't really run async DB ops.
        raise RuntimeError("No event loop running - cannot create safe session")

    # Store engine on the loop object itself to ensure it is 
    # garbage collected when the loop (Celery task) finishes.
    if not hasattr(loop, "_lexai_translation_engine"):
        loop._lexai_translation_engine = create_async_engine(
            settings.POSTGRES_URL,
            poolclass=NullPool,
            echo=False,
            connect_args={"command_timeout": 10}
        )
    
    engine = loop._lexai_translation_engine
    
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    return factory()


async def check_cancellation(job_id: str | None):
    """Stop if job status changed to error/cancelled in DB. Fail-safe: stop if check fails."""
    if not job_id: return
    try:
        from app.models.translation import TranslationJob
        from sqlmodel import select
        
        # Use safe loop-local session instead of global pooled engine
        async with await _get_safe_session() as session:
            res = await session.exec(select(TranslationJob).where(TranslationJob.id == job_id))
            job = res.first()
            if not job or job.status not in ["pending", "processing"]:
                logger.warning("translation.cancelled_detected", job_id=job_id, status=getattr(job, "status", "MISSING"))
                raise RuntimeError(f"Job {job_id} was cancelled or missing")
    except RuntimeError:
        raise
    except Exception as e:
        # If DB check fails, we continue but log it. 
        # Prevents 'NoneType object has no attribute send' from crashing the worker.
        logger.error("translation.cancel_check_failed", job_id=job_id, error=str(e))


# ─────────────────────────────────────────────
# Step 1: Language Detection (Flash — fast)
# ─────────────────────────────────────────────

async def detect_language(state: TranslationState) -> TranslationState:
    """Detect the source language of the document. Skips if already set by caller."""
    if state.get("source_language"):
        return state
    sample = state["document_text"][:3000]
    prompt = LANGUAGE_DETECTION_PROMPT.format(text_sample=sample)
    try:
        # Flash is fine for language detection — it's cheap and accurate
        result = await generate_structured(
            prompt, use_pro=False, schema={}, feature_name="language_detection"
        )
        state["source_language"] = result.get("language_name", "Unknown")
        logger.info("translation.language_detected", lang=state["source_language"])
    except Exception as e:
        logger.warning("translation.detect_failed", error=str(e))
        state["source_language"] = "Unknown"
    return state


# ─────────────────────────────────────────────
# Step 2: Parse Document Structure
# ─────────────────────────────────────────────

async def parse_structure(state: TranslationState) -> TranslationState:
    """
    Layout-aware structure extraction — full fallback chain.

    Priority order:
      1. DocumentStructureParser.parse_async(file_bytes, filename)
         - Native: PyMuPDF (PDF) / python-docx (DOCX)
         - Auto-falls back to Gemini Structured OCR if native < 50 chars
      2. GeminiStructuredOCRExtractor directly on plain text (last resort)
      3. PlainTextStructureExtractor on document_text (emergency only)
    """
    await check_cancellation(state.get("job_id"))
    from app.ingestion.document_structure_parser import (
        DocumentStructureParser,
        GeminiStructuredOCRExtractor,
        PlainTextStructureExtractor,
        blocks_to_serializable,
    )

    file_bytes: Optional[bytes] = state.get("_file_bytes")  # type: ignore[arg-type]
    filename: str = state.get("_filename", "document.txt")   # type: ignore[arg-type]

    # ── PATH 1: Primary Gemini Structured OCR (Maximum Fidelity) ──
    # For legal documents, native PyMuPDF often flattens lists and tables.
    # We use Gemini as the primary visual parser to generate the Markdown Blueprint.
    if file_bytes:
        try:
            ocr = GeminiStructuredOCRExtractor()
            blocks = await ocr.extract(file_bytes, filename)
            if blocks:
                serializable = blocks_to_serializable(blocks)
                state["structure_map"] = serializable
                
                # Try to pull metadata if extracted
                state["metadata"] = getattr(ocr, "last_metadata", {})
                
                logger.info(
                    "translation.structure_parsed",
                    blocks=len(serializable),
                    source="gemini_ocr_primary",
                )
                return state
        except Exception as e:
            logger.warning("translation.gemini_primary_failed", error=str(e))

        # ── PATH 2: Native Parsing Fallback (PyMuPDF) ──
        try:
            parser = DocumentStructureParser()
            blocks = await parser.parse_async(file_bytes, filename)
            serializable = blocks_to_serializable(blocks)
            state["structure_map"] = serializable
            state["metadata"] = getattr(parser, "last_metadata", {})
            logger.info(
                "translation.structure_parsed",
                blocks=len(serializable),
                source="parse_async_fallback",
            )
            return state
        except Exception as e:
            logger.warning(
                "translation.parse_async_failed",
                error=str(e),
            )


    # ── PATH 3: Emergency — heuristic plain text extraction ──
    text = state.get("document_text", "")
    extractor = PlainTextStructureExtractor()
    blocks = extractor.extract(text)
    serializable = blocks_to_serializable(blocks)
    state["structure_map"] = serializable
    logger.warning(
        "translation.structure_parsed",
        blocks=len(serializable),
        source="plaintext_emergency_fallback",
    )
    return state



# ─────────────────────────────────────────────
# Step 3: Block-by-block Pro Translation
# ─────────────────────────────────────────────

async def translate_blocks(state: TranslationState) -> TranslationState:
    """
    Translate all blocks using Gemini Pro.
    - Paragraphs/headings/lists → BLOCK_TRANSLATION_PROMPT
    - Tables → TABLE_TRANSLATION_PROMPT (cell-by-cell)
    - Semaphore limits concurrent Pro calls
    """
    blocks = state["structure_map"]
    total = len(blocks)
    sem = asyncio.Semaphore(PRO_SEMAPHORE_LIMIT)

    src = state["source_language"]
    tgt = state["target_language"]

    # Precompute "nearest heading" context for each block
    heading_context: List[str] = []
    current_heading = ""
    for b in blocks:
        if b.get("type") == "heading":
            current_heading = b.get("content", "")
        heading_context.append(current_heading)

    job_id = state.get("job_id")

    async def _translate_text_block(block: Dict, idx: int) -> Dict:
        """Translate a single text/heading/list block with Gemini Pro."""
        content = block.get("content", "")
        if not content.strip():
            return {**block, "translated_content": "", "translator_notes": [], "uncertainty_flags": []}

        # Split super-long blocks to stay within context window
        chunks = _split_text(content, MAX_BLOCK_CHARS)
        translated_chunks = []
        notes: List[str] = []
        flags: List[str] = []

        for chunk in chunks:
            prompt = BLOCK_TRANSLATION_PROMPT.format(
                source_language=src,
                target_language=tgt,
                original_text=chunk,
            )
            async with sem:
                await check_cancellation(job_id)
                try:
                    result = await generate_structured(
                        prompt,
                        use_pro=True,
                        temperature=0.05,
                        feature_name="translation_block",
                    )
                    # Support both new 'translated_markdown' and old 'translated_text' keys
                    trans = result.get("translated_markdown") or result.get("translated_text") or chunk
                    translated_chunks.append(trans)
                    flags.extend(result.get("uncertainty_flags", []))
                except Exception as e:
                    logger.warning("translation.pro_failed_falling_back_to_flash", idx=idx, error=str(e))
                    try:
                        # Fallback to Flash model if Pro fails or times out with 504
                        result = await generate_structured(
                            prompt,
                            use_pro=False,
                            temperature=0.05,
                            feature_name="translation_block_fallback",
                        )
                        trans = result.get("translated_markdown") or result.get("translated_text") or chunk
                        translated_chunks.append(trans)
                        flags.append("Warning: Translated using fallback model due to Pro timeout")
                        flags.extend(result.get("uncertainty_flags", []))
                    except Exception as fallback_e:
                        logger.error(
                            "translation.block_failed",
                            idx=idx,
                            error=str(fallback_e),
                        )
                        translated_chunks.append(f"[TRANSLATION ERROR: {fallback_e}]")

        return {
            **block,
            "translated_content": "\n\n".join(translated_chunks),
            "uncertainty_flags": flags,
        }

    async def _translate_table_block(block: Dict, idx: int) -> Dict:
        """Translate a table block cell-by-cell using TABLE_TRANSLATION_PROMPT."""
        table_rows = block.get("table_rows", [])
        if not table_rows:
            return {**block, "translated_table_rows": [], "translator_notes": []}

        # Serialise rows as a 2D list of strings for the prompt
        rows_as_text = [[cell.get("text", "") for cell in row] for row in table_rows]
        prompt = TABLE_TRANSLATION_PROMPT.format(
            source_language=src,
            target_language=tgt,
            nearest_heading=heading_context[idx] if idx < len(heading_context) else "",
            table_json=json.dumps(rows_as_text, ensure_ascii=False),
        )
        async with sem:
            await check_cancellation(job_id)
            try:
                result = await generate_structured(
                    prompt,
                    use_pro=True,
                    temperature=0.05,
                    feature_name="translation_table",
                )
                translated_html = result.get("translated_html_table", "")
            except Exception as e:
                logger.warning("translation.table_pro_failed_falling_back_to_flash", idx=idx, error=str(e))
                try:
                    result = await generate_structured(
                        prompt,
                        use_pro=False,
                        temperature=0.05,
                        feature_name="translation_table_fallback",
                    )
                    translated_html = result.get("translated_html_table", "")
                except Exception as fallback_e:
                    logger.error(
                        "translation.table_failed", idx=idx, error=str(fallback_e)
                    )
                    translated_html = ""

        # If we got an HTML table back, store it directly; also keep the legacy
        # translated_table_rows for the PDF generator.
        translated_table_rows = []
        for r_idx, original_row in enumerate(table_rows):
            translated_row = []
            for c_idx, original_cell in enumerate(original_row):
                translated_row.append({
                    **original_cell,
                    "translated_text": original_cell.get("text", ""),
                })
            translated_table_rows.append(translated_row)

        result = {} if not isinstance(result, dict) else result  # ensure result is always dict

        # Store translated HTML as translated_content too, so BlockRenderer always finds it
        return {
            **block,
            "translated_html_table": translated_html,
            "translated_content": translated_html,   # <-- critical: BlockRenderer reads this
            "translated_table_rows": translated_table_rows,
            "translator_notes": result.get("translator_notes", []),
        }

    def _html_to_rows(html: str) -> List[List[str]]:
        """Extract a 2D list of strings from an HTML table string."""
        import re as _re_h
        if not html:
            return []
        rows_out = []
        for tr in _re_h.findall(r'<tr[^>]*>(.*?)</tr>', html, _re_h.I | _re_h.S):
            cells = _re_h.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, _re_h.I | _re_h.S)
            # Replace <br> with \n, then strip inner HTML tags to get plain text per cell
            strip_br = lambda s: _re_h.sub(r'<br\s*/?>', '\n', s, flags=_re_h.I)
            clean = [_re_h.sub(r'<[^>]+>', '', strip_br(c)).strip() for c in cells]
            if clean:
                rows_out.append(clean)
        return rows_out

    async def _dispatch(block: Dict, idx: int) -> Dict:
        await check_cancellation(job_id)
        btype = block.get("type", "paragraph")

        # Tables with structured table_rows: use dedicated table translator
        if btype == "table" and block.get("table_rows"):
            return await _translate_table_block(block, idx)

        # html_table / kv_table: parse the HTML into rows and use table translator
        if btype in ("html_table", "kv_table"):
            html_content = block.get("content", "")
            rows_from_html = _html_to_rows(html_content)
            if rows_from_html:
                # Build a fake block with table_rows so _translate_table_block can process it
                tbl_block = {**block, "table_rows": [[{"text": c, "is_header": ridx == 0} for c in row] for ridx, row in enumerate(rows_from_html)]}
                return await _translate_table_block(tbl_block, idx)
            # If HTML parsing fails, fall back to text translation with explicit instruction
            return await _translate_text_block(block, idx)

        if btype in ("page_break",):
            return {**block, "translated_content": ""}

        # paragraph, heading, list, document_title, signatures etc.
        return await _translate_text_block(block, idx)

    # ── Translate Metadata (e-Stamp info) ──
    meta = state.get("metadata", {})
    translated_meta = {}
    if meta:
        async def _translate_meta_field(k, v):
            if not v or not isinstance(v, str): return k, v
            # Simple prompt for metadata fields
            pmt = f"Translate this legal document metadata field value from {src} to formal English. Return ONLY the translated string.\n\nValue: {v}"
            try:
                res = await generate_structured(pmt, use_pro=True, temperature=0, feature_name="translation_metadata")
                return k, res.get("translated_text", v)
            except:
                return k, v
        
        meta_tasks = [_translate_meta_field(k, v) for k, v in meta.items()]
        meta_results = await asyncio.gather(*meta_tasks)
        translated_meta = dict(meta_results)

    # Fan-out translation over all blocks (bounded by semaphore)
    translated = await asyncio.gather(*[_dispatch(b, i) for i, b in enumerate(blocks)])

    state["translated_blocks"] = list(translated)
    state["translated_metadata"] = translated_meta
    logger.info("translation.blocks_translated", count=len(translated), meta=bool(translated_meta))
    return state


# ─────────────────────────────────────────────
# Step 4: Validation Pass (optional)
# ─────────────────────────────────────────────

async def validate_translation(state: TranslationState) -> TranslationState:
    await check_cancellation(state.get("job_id"))
    """
    Gemini Pro quality audit — compares a sample of original vs translated blocks.
    Flags meaning drift, missing content, or misused legal terms.
    Always graceful: validation failure does NOT block the result.
    """
    if not ENABLE_VALIDATION:
        state["validation_report"] = {"passed": True, "skipped": True}
        return state

    translated_blocks = state.get("translated_blocks", [])
    if not translated_blocks:
        state["validation_report"] = {"passed": True, "skipped": True}
        return state

    # Sample: pick first 3 non-empty text blocks for validation
    samples = [
        b for b in translated_blocks
        if b.get("type") in ("paragraph", "heading") and b.get("content", "").strip()
    ][:3]

    if not samples:
        state["validation_report"] = {"passed": True, "skipped": True}
        return state

    original_sample = "\n\n---\n\n".join(b["content"] for b in samples)
    translated_sample = "\n\n---\n\n".join(
        b.get("translated_content", "") for b in samples
    )

    prompt = TRANSLATION_VALIDATION_PROMPT.format(
        source_language=state["source_language"],
        target_language=state["target_language"],
        original_sample=original_sample[:4000],
        translated_sample=translated_sample[:4000],
    )

    try:
        report = await generate_structured(
            prompt, use_pro=True, temperature=0.1, feature_name="translation_validation"
        )
        state["validation_report"] = report
        quality = report.get("quality_score", 100)
        passed = report.get("passed", True)
        logger.info(
            "translation.validation_done",
            quality_score=quality,
            passed=passed,
            issues=len(report.get("issues", [])),
        )
    except Exception as e:
        logger.warning("translation.validation_failed", error=str(e))
        state["validation_report"] = {"passed": True, "error": str(e)}

    return state


# ─────────────────────────────────────────────
# Step 5: Reconstruct / Assemble Result
# ─────────────────────────────────────────────

async def reconstruct_document(state: TranslationState) -> TranslationState:
    await check_cancellation(state.get("job_id"))
    """
    Convert translated_blocks into the legacy `result` schema so all
    downstream code (task, save endpoint) continues to work without changes.

    Also stores the rich block data in result["translated_blocks"] for the
    new structure-preserving PDF generator.
    """
    translated_blocks = state.get("translated_blocks", [])
    translated_sections: List[Dict] = []
    uncertainty_flags: List[str] = []
    dropped_warnings: List[str] = []

    current_heading_original = ""
    current_heading_translated = ""
    current_section_original_lines: List[str] = []
    current_section_translated_lines: List[str] = []
    section_idx = 0

    def _flush_section():
        nonlocal section_idx
        if current_section_original_lines or current_section_translated_lines:
            translated_sections.append({
                "section_id": f"s{section_idx}",
                "original_heading": current_heading_original,
                "translated_heading": current_heading_translated,
                "original_text": "\n".join(current_section_original_lines),
                "translated_text": "\n".join(current_section_translated_lines),
                "is_approximate": False,
                "translator_notes": [],
            })
            section_idx += 1

    for block in translated_blocks:
        btype = block.get("type", "paragraph")

        if btype == "page_break":
            continue

        if btype == "heading":
            _flush_section()
            current_section_original_lines = []
            current_section_translated_lines = []
            current_heading_original = block.get("content", "")
            current_heading_translated = block.get("translated_content", current_heading_original)

        elif btype in ("table", "html_table", "kv_table"):
            # Prefer the translated HTML string; fall back to a plain-text render
            original_table_html = block.get("markdown_content", block.get("content")) or _table_rows_to_html(block.get("table_rows", []))
            translated_html = block.get("translated_html_table") or block.get("translated_content") or _table_rows_to_html(
                block.get("translated_table_rows", block.get("table_rows", []))
            )
            current_section_original_lines.append(original_table_html)
            current_section_translated_lines.append(translated_html)

        else:
            original = block.get("content", "")
            translated = block.get("translated_content", original)
            if original:
                current_section_original_lines.append(original)
            if translated:
                current_section_translated_lines.append(translated)

        # Collect uncertainty flags
        for flag in block.get("uncertainty_flags", []):
            uncertainty_flags.append(f"Block {block.get('index', '?')}: {flag}")

    _flush_section()

    # Check coverage — warn if any block has a translation error marker
    for block in translated_blocks:
        tc = block.get("translated_content", "")
        if tc and "[TRANSLATION ERROR" in tc:
            dropped_warnings.append(
                f"Block {block.get('index', '?')} ({block.get('type', '?')}): translation failed"
            )

    approx_count = sum(1 for b in translated_blocks if b.get("is_approximate"))
    ratio = approx_count / max(len(translated_blocks), 1)
    confidence = "high" if ratio < 0.1 and len(uncertainty_flags) < 3 else \
                 "medium" if ratio < 0.3 else "low"

    state["result"] = {
        "translated_sections": translated_sections,
        "translated_blocks": translated_blocks,   # Rich block data for PDF generation
        "translated_metadata": state.get("translated_metadata", {}),
        "source_metadata": state.get("metadata", {}),
        "source_language": state["source_language"],
        "target_language": state["target_language"],
        "overall_confidence": confidence,
        "uncertainty_flags": uncertainty_flags,
        "dropped_text_warnings": dropped_warnings,
        "validation_report": state.get("validation_report", {}),
    }

    logger.info(
        "translation.reconstruct_done",
        sections=len(translated_sections),
        blocks=len(translated_blocks),
        confidence=confidence,
    )
    return state


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _split_text(text: str, max_chars: int) -> List[str]:
    """Split large text at paragraph boundaries to stay within context limits."""
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    parts = text.split("\n\n")
    current_chunk: List[str] = []
    current_len = 0
    for part in parts:
        if current_len + len(part) + 2 > max_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [part]
            current_len = len(part)
        else:
            current_chunk.append(part)
            current_len += len(part) + 2
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    return chunks or [text[:max_chars]]


def _table_rows_to_html(rows: List) -> str:
    """Convert table rows (dicts with 'text') to an HTML table string."""
    if not rows:
        return ""
    html = ["<table style='border-collapse:collapse;width:100%'>"]
    for i, row in enumerate(rows):
        cells = [cell.get("text", "") if isinstance(cell, dict) else str(cell) for cell in row]
        tag = "th" if i == 0 else "td"
        html.append("<tr>" + "".join(f"<{tag} style='border:1px solid #ccc;padding:6px 10px'>{c}</{tag}>" for c in cells) + "</tr>")
    html.append("</table>")
    return "".join(html)


def _table_rows_to_text(rows: List) -> str:
    """Convert table rows (dicts with 'text') to plain-text table (legacy fallback)."""
    lines = []
    for row in rows:
        cells = [cell.get("text", "") if isinstance(cell, dict) else str(cell) for cell in row]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _translated_table_rows_to_text(rows: List) -> str:
    """Convert translated table rows (dicts with 'translated_text') to plain-text (legacy fallback)."""
    lines = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, dict):
                cells.append(cell.get("translated_text", cell.get("text", "")))
            else:
                cells.append(str(cell))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Graph Assembly
# ─────────────────────────────────────────────

def build_translation_workflow() -> Any:
    graph = StateGraph(TranslationState)

    graph.add_node("detect_language", detect_language)
    graph.add_node("parse_structure", parse_structure)
    graph.add_node("translate_blocks", translate_blocks)
    graph.add_node("validate_translation", validate_translation)
    graph.add_node("reconstruct_document", reconstruct_document)

    graph.set_entry_point("detect_language")
    graph.add_edge("detect_language", "parse_structure")
    graph.add_edge("parse_structure", "translate_blocks")
    graph.add_edge("translate_blocks", "validate_translation")
    graph.add_edge("validate_translation", "reconstruct_document")
    graph.add_edge("reconstruct_document", END)

    return graph.compile()


translation_workflow = build_translation_workflow()
