"""
Layout-aware document structure parser for translation pipeline.

Extraction strategy (most accurate → least accurate):
  PDF:  1. PyMuPDF dict-mode (font sizes, bold/italic, bboxes)
             2. Gemini Pro Structured OCR (scanned/image PDFs, zero text layer)
  DOCX: 1. python-docx (paragraph styles, tables)
             2. Gemini Pro Structured OCR
  Other: PlainTextStructureExtractor (heuristic line parsing) →
             Gemini Pro Structured OCR

Gemini OCR is the FALLBACK and the real-world common path for:
  - Scanned PDFs (court filings, notarized documents, old acts)
  - Image-only PDFs
  - Encrypted / corrupted documents
  - Any file where the native extractor yields < MIN_CHARS

When Gemini runs, it returns fully structured JSON with typed blocks
(heading, paragraph, table, list) so structure preservation is maintained
just as well as the native path — it is NOT treated as flat text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("doc_structure_parser")


# ─────────────────────────────────────────────
# Block Types
# ─────────────────────────────────────────────

BLOCK_HEADING = "heading"
BLOCK_PARAGRAPH = "paragraph"
BLOCK_TABLE = "table"
BLOCK_LIST = "list"
BLOCK_PAGEBREAK = "page_break"


@dataclass
class BlockStyle:
    bold: bool = False
    italic: bool = False
    font_size: float = 10.0
    font_name: str = ""
    alignment: str = "left"  # left | center | right | justify


@dataclass
class TableCell:
    text: str
    col_span: int = 1
    row_span: int = 1
    is_header: bool = False


@dataclass
class StructuredBlock:
    index: int                           # Global order index in document
    block_type: str                      # heading | paragraph | table | list | page_break
    content: str                         # Plain text content (empty for tables)
    style: BlockStyle = field(default_factory=BlockStyle)
    heading_level: int = 0              # 1–6 for headings; 0 otherwise
    page_number: int = 1
    # Table-specific
    table_rows: List[List[TableCell]] = field(default_factory=list)
    # List-specific
    list_items: List[str] = field(default_factory=list)
    list_style: str = "bullet"           # bullet | numbered
    # Metadata
    section_id: str = ""
    original_heading: str = ""          # Nearest heading ancestor (for context)


# ─────────────────────────────────────────────
# Heading Level Heuristics
# ─────────────────────────────────────────────

_HEADING_RE = [
    # Level 1: ALLCAPS short lines, Article/Chapter/PART/SCHEDULE
    (1, re.compile(r'^(ARTICLE|CHAPTER|PART|SCHEDULE|EXHIBIT|ANNEX)\s+[IVX\d]+', re.I)),
    (1, re.compile(r'^[A-Z][A-Z\s]{3,60}$')),
    # Level 2: numbered headings like "1." / "Section 2"
    (2, re.compile(r'^\d+\.\s{1,4}[A-Z]')),
    (2, re.compile(r'^(Section|SECTION|Article)\s+\d+', re.I)),
    # Level 3: sub-numbered "1.1" / "1.1."
    (3, re.compile(r'^\d+\.\d+\.?\s{1,4}[A-Z]')),
    # Level 4: sub-sub-numbered "1.1.1"
    (4, re.compile(r'^\d+\.\d+\.\d+\.?\s{1,4}')),
    # Level 5: any line ending with ':' and short
    (5, re.compile(r'^.{4,79}:\s*$')),
]

_LIST_BULLET_RE = re.compile(r'^[\u2022\u2013\u2014\-\*]\s+')
_LIST_NUM_RE = re.compile(r'^(\d+[\.\)]\s+|[a-z][\.\)]\s+)')


def _detect_heading_level(text: str) -> int:
    text = text.strip()
    for level, pattern in _HEADING_RE:
        if pattern.match(text):
            return level
    return 0


def _is_list_item(text: str) -> tuple[bool, str]:
    """Returns (is_list, list_style)."""
    if _LIST_BULLET_RE.match(text):
        return True, "bullet"
    if _LIST_NUM_RE.match(text):
        return True, "numbered"
    return False, ""


# ─────────────────────────────────────────────
# PDF Structure Extractor (PyMuPDF)
# ─────────────────────────────────────────────

class PDFStructureExtractor:
    """
    Extracts structured blocks from a PDF using PyMuPDF's dict-based
    text block API, which gives us font size, bold/italic flags, and
    approximate bounding boxes for each text span.
    """

    # Font size thresholds for heading inference
    _H1_SIZE_THRESHOLD = 16.0
    _H2_SIZE_THRESHOLD = 13.0
    _H3_SIZE_THRESHOLD = 11.5

    def extract(self, file_bytes: bytes) -> List[StructuredBlock]:
        import fitz  # PyMuPDF

        blocks: List[StructuredBlock] = []
        idx = 0
        current_heading = ""

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page_num, page in enumerate(doc, start=1):
                # Insert logical page break marker (skip first page)
                if page_num > 1:
                    blocks.append(StructuredBlock(
                        index=idx,
                        block_type=BLOCK_PAGEBREAK,
                        content="",
                        page_number=page_num,
                    ))
                    idx += 1

                # ── Table detection via pdfplumber (optional, graceful fallback) ──
                table_bboxes = self._get_table_bboxes_plumber(file_bytes, page_num)

                # Get raw dict blocks from PyMuPDF
                raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                for raw_block in raw.get("blocks", []):
                    btype = raw_block.get("type", -1)

                    # Image block — skip
                    if btype == 1:
                        continue

                    if btype == 0:  # Text block
                        bbox = raw_block.get("bbox", (0, 0, 0, 0))

                        # Check if this block overlaps a detected table bbox
                        if self._overlaps_any_table(bbox, table_bboxes):
                            continue  # Will be handled by table extractor below

                        structured = self._parse_text_block(
                            raw_block, page_num, idx, current_heading
                        )
                        if structured:
                            if structured.block_type == BLOCK_HEADING:
                                current_heading = structured.content
                            blocks.append(structured)
                            idx += 1

                # ── Extract tables from this page ──
                page_tables = self._extract_tables_plumber(file_bytes, page_num, idx)
                for t in page_tables:
                    t.original_heading = current_heading
                    blocks.append(t)
                    idx += 1

        logger.info("pdf_parser.extracted", blocks=len(blocks))
        return blocks

    def _parse_text_block(
        self,
        raw_block: Dict,
        page_num: int,
        idx: int,
        current_heading: str,
    ) -> Optional[StructuredBlock]:
        """Parse a PyMuPDF text block dict into a StructuredBlock."""
        lines_text = []
        max_size = 0.0
        is_bold = False
        is_italic = False
        font_name = ""

        for line in raw_block.get("lines", []):
            line_str_parts = []
            for span in line.get("spans", []):
                span_text = span.get("text", "").strip()
                if not span_text:
                    continue
                span_size = span.get("size", 10.0)
                span_flags = span.get("flags", 0)
                span_font = span.get("font", "")
                line_str_parts.append(span_text)
                if span_size > max_size:
                    max_size = span_size
                    font_name = span_font
                # Bit 4 = bold, Bit 1 = italic in PyMuPDF flags
                if span_flags & (1 << 4):
                    is_bold = True
                if span_flags & (1 << 1):
                    is_italic = True
            if line_str_parts:
                lines_text.append(" ".join(line_str_parts))

        text = "\n".join(lines_text).strip()
        if not text or len(text) < 2:
            return None

        style = BlockStyle(
            bold=is_bold,
            italic=is_italic,
            font_size=max_size or 10.0,
            font_name=font_name,
        )

        # ── Determine block type ──
        heading_level = self._size_based_heading(max_size)
        if heading_level == 0:
            heading_level = _detect_heading_level(text)

        if heading_level > 0:
            style.bold = True  # headings are always rendered bold
            return StructuredBlock(
                index=idx,
                block_type=BLOCK_HEADING,
                content=text,
                style=style,
                heading_level=heading_level,
                page_number=page_num,
                original_heading=current_heading,
            )

        # List item detection
        is_list, list_style = _is_list_item(text)
        if is_list:
            # Collect as a single-item list block (will be merged later)
            clean_item = _LIST_BULLET_RE.sub("", text)
            clean_item = _LIST_NUM_RE.sub("", clean_item).strip()
            return StructuredBlock(
                index=idx,
                block_type=BLOCK_LIST,
                content=text,
                style=style,
                page_number=page_num,
                list_items=[clean_item],
                list_style=list_style,
                original_heading=current_heading,
            )

        # Default: paragraph
        return StructuredBlock(
            index=idx,
            block_type=BLOCK_PARAGRAPH,
            content=text,
            style=style,
            page_number=page_num,
            original_heading=current_heading,
        )

    def _size_based_heading(self, size: float) -> int:
        if size >= self._H1_SIZE_THRESHOLD:
            return 1
        if size >= self._H2_SIZE_THRESHOLD:
            return 2
        if size >= self._H3_SIZE_THRESHOLD:
            return 3
        return 0

    def _get_table_bboxes_plumber(
        self, file_bytes: bytes, page_num: int
    ) -> List[tuple]:
        """Return bounding boxes of tables on a page using pdfplumber."""
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                if page_num - 1 >= len(pdf.pages):
                    return []
                pg = pdf.pages[page_num - 1]
                return [t.bbox for t in pg.find_tables()]
        except Exception:
            return []

    def _extract_tables_plumber(
        self, file_bytes: bytes, page_num: int, start_idx: int
    ) -> List[StructuredBlock]:
        """Extract tables as StructuredBlock objects using pdfplumber."""
        result: List[StructuredBlock] = []
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                if page_num - 1 >= len(pdf.pages):
                    return []
                pg = pdf.pages[page_num - 1]
                for tbl in pg.extract_tables():
                    if not tbl:
                        continue
                    rows: List[List[TableCell]] = []
                    for r_idx, row in enumerate(tbl):
                        cells: List[TableCell] = []
                        for c_idx, cell in enumerate(row):
                            cell_text = (cell or "").strip()
                            cells.append(TableCell(
                                text=cell_text,
                                is_header=(r_idx == 0),
                            ))
                        if any(c.text for c in cells):
                            rows.append(cells)
                    if not rows:
                        continue
                    result.append(StructuredBlock(
                        index=start_idx + len(result),
                        block_type=BLOCK_TABLE,
                        content="",
                        page_number=page_num,
                        table_rows=rows,
                    ))
        except Exception as e:
            logger.warning("pdf_parser.table_extract_failed", page=page_num, error=str(e))
        return result

    def _overlaps_any_table(self, bbox: tuple, table_bboxes: List[tuple]) -> bool:
        if not table_bboxes:
            return False
        x0, y0, x1, y1 = bbox
        for tx0, ty0, tx1, ty1 in table_bboxes:
            # Simple overlap check
            if x0 < tx1 and x1 > tx0 and y0 < ty1 and y1 > ty0:
                return True
        return False


# ─────────────────────────────────────────────
# DOCX Structure Extractor
# ─────────────────────────────────────────────

class DOCXStructureExtractor:
    """
    Extract blocks from a DOCX file using python-docx.
    Preserves paragraph styles, bold/italic, lists, and tables.
    """

    def extract(self, file_bytes: bytes) -> List[StructuredBlock]:
        import io
        from docx import Document as DocxDocument
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        doc = DocxDocument(io.BytesIO(file_bytes))
        blocks: List[StructuredBlock] = []
        idx = 0
        current_heading = ""

        def _alignment_str(align) -> str:
            try:
                mapping = {
                    WD_ALIGN_PARAGRAPH.LEFT: "left",
                    WD_ALIGN_PARAGRAPH.CENTER: "center",
                    WD_ALIGN_PARAGRAPH.RIGHT: "right",
                    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
                }
                return mapping.get(align, "left")
            except Exception:
                return "left"

        for element in doc.element.body:
            tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

            if tag == "p":
                from docx.oxml.ns import qn
                from docx.text.paragraph import Paragraph as DocxParagraph
                para = DocxParagraph(element, doc)
                text = para.text.strip()
                if not text:
                    continue

                style_name = (para.style.name or "").lower()
                is_heading = "heading" in style_name
                heading_level = 0
                if is_heading:
                    # Extract level from style name like "Heading 1"
                    m = re.search(r'(\d+)', style_name)
                    heading_level = int(m.group(1)) if m else 1
                else:
                    heading_level = _detect_heading_level(text)

                # Bold/italic from first run
                is_bold = any(r.bold for r in para.runs if r.bold is not None)
                is_italic = any(r.italic for r in para.runs if r.italic is not None)
                font_size = 10.0
                try:
                    for r in para.runs:
                        if r.font.size:
                            font_size = r.font.size.pt
                            break
                except Exception:
                    pass

                style = BlockStyle(
                    bold=is_bold or heading_level > 0,
                    italic=is_italic,
                    font_size=font_size,
                    alignment=_alignment_str(para.alignment),
                )

                if heading_level > 0:
                    current_heading = text
                    blocks.append(StructuredBlock(
                        index=idx, block_type=BLOCK_HEADING, content=text,
                        style=style, heading_level=heading_level, page_number=1,
                        original_heading=current_heading,
                    ))
                else:
                    is_list, list_style = _is_list_item(text)
                    if is_list:
                        clean_item = _LIST_BULLET_RE.sub("", text)
                        clean_item = _LIST_NUM_RE.sub("", clean_item).strip()
                        blocks.append(StructuredBlock(
                            index=idx, block_type=BLOCK_LIST, content=text,
                            style=style, page_number=1,
                            list_items=[clean_item], list_style=list_style,
                            original_heading=current_heading,
                        ))
                    else:
                        blocks.append(StructuredBlock(
                            index=idx, block_type=BLOCK_PARAGRAPH, content=text,
                            style=style, page_number=1,
                            original_heading=current_heading,
                        ))
                idx += 1

            elif tag == "tbl":
                from docx.table import Table as DocxTable
                tbl = DocxTable(element, doc)
                rows: List[List[TableCell]] = []
                for r_idx, row in enumerate(tbl.rows):
                    cells = []
                    for c_idx, cell in enumerate(row.cells):
                        cells.append(TableCell(
                            text=cell.text.strip(),
                            is_header=(r_idx == 0),
                        ))
                    if any(c.text for c in cells):
                        rows.append(cells)
                if rows:
                    blocks.append(StructuredBlock(
                        index=idx, block_type=BLOCK_TABLE, content="",
                        page_number=1, table_rows=rows,
                        original_heading=current_heading,
                    ))
                    idx += 1

        logger.info("docx_parser.extracted", blocks=len(blocks))
        return blocks


# ─────────────────────────────────────────────
# Plain-Text Fallback Extractor
# ─────────────────────────────────────────────

class PlainTextStructureExtractor:
    """
    Heuristic structure extraction for plain text / TXT files.
    Falls back to line-by-line heading/paragraph detection.
    """

    def extract(self, text: str) -> List[StructuredBlock]:
        blocks: List[StructuredBlock] = []
        idx = 0
        current_heading = ""
        para_lines: List[str] = []

        def _flush_para():
            nonlocal idx
            if para_lines:
                content = " ".join(para_lines).strip()
                if content:
                    blocks.append(StructuredBlock(
                        index=idx, block_type=BLOCK_PARAGRAPH, content=content,
                        page_number=1, original_heading=current_heading,
                    ))
                    idx += 1
                para_lines.clear()

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                _flush_para()
                continue

            heading_level = _detect_heading_level(stripped)
            if heading_level > 0:
                _flush_para()
                current_heading = stripped
                blocks.append(StructuredBlock(
                    index=idx, block_type=BLOCK_HEADING, content=stripped,
                    style=BlockStyle(bold=True, font_size=12.0),
                    heading_level=heading_level, page_number=1,
                    original_heading=current_heading,
                ))
                idx += 1
            else:
                is_list, list_style = _is_list_item(stripped)
                if is_list:
                    _flush_para()
                    clean_item = _LIST_BULLET_RE.sub("", stripped)
                    clean_item = _LIST_NUM_RE.sub("", clean_item).strip()
                    blocks.append(StructuredBlock(
                        index=idx, block_type=BLOCK_LIST, content=stripped,
                        page_number=1, list_items=[clean_item], list_style=list_style,
                        original_heading=current_heading,
                    ))
                    idx += 1
                else:
                    para_lines.append(stripped)

        _flush_para()
        logger.info("text_parser.extracted", blocks=len(blocks))
        return blocks


# ─────────────────────────────────────────────
# Gemini Structured OCR Extractor
# (primary fallback — handles scanned/image PDFs, corrupted files, etc.)
# ─────────────────────────────────────────────

# Minimum extracted chars below which we consider native parsing "failed"
_MIN_CHARS_THRESHOLD = 50


class GeminiStructuredOCRExtractor:
    """
    Uses Gemini Pro's multimodal capabilities to extract document structure
    directly from raw file bytes.

    Returns fully typed StructuredBlock objects (heading, paragraph, table,
    list) — NOT flat text — so the translation pipeline preserves structure
    even for scanned PDFs and image-only documents.

    This is the production-grade fallback and will run in the majority of
    real-world cases (court filings, government acts, notarized documents).
    """

    # Maps file extension → Gemini mime type
    _EXT_TO_MIME = {
        ".pdf":  "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc":  "application/msword",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
        ".tif":  "image/tiff",
        ".bmp":  "image/bmp",
        ".webp": "image/webp",
    }

    async def extract(self, file_bytes: bytes, filename: str) -> List[StructuredBlock]:
        """
        Send raw file bytes to Gemini Pro and receive a structured block list.
        Returns StructuredBlock objects ready for the translation pipeline.
        """
        import asyncio
        from pathlib import Path as _Path
        from app.ai.gemini_client import generate_structured
        from app.ai.prompts import GEMINI_STRUCTURED_OCR_PROMPT

        ext = _Path(filename).suffix.lower()
        mime = self._EXT_TO_MIME.get(ext, "application/pdf")

        logger.info(
            "gemini_ocr.start",
            filename=filename,
            mime=mime,
            size_bytes=len(file_bytes),
        )

        # Gemini structured extraction — pass raw bytes as multimodal content
        loop = asyncio.get_event_loop()
        try:
            import google.generativeai as genai
            from app.core.config import settings
            from google.generativeai.types import GenerationConfig

            genai.configure(api_key=settings.GOOGLE_API_KEY)
            model = genai.GenerativeModel(settings.GEMINI_MODEL)
            config = GenerationConfig(
                temperature=0.1,
                response_mime_type="application/json",
            )

            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    [
                        {"mime_type": mime, "data": file_bytes},
                        GEMINI_STRUCTURED_OCR_PROMPT,
                    ],
                    generation_config=config,
                    request_options={"timeout": 600},  # generous for large docs
                ),
            )

            # Track usage
            try:
                from app.services.usage_tracker import log_usage
                usage = getattr(response, "usage_metadata", None)
                inp = (getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
                out = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
                await log_usage(settings.GEMINI_MODEL, "gemini_ocr_structured", inp, out)
            except Exception:
                pass

            raw = (response.text or "").strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                import re as _re
                raw = _re.sub(r"^```(?:json)?\s*", "", raw)
                raw = _re.sub(r"\s*```$", "", raw)

            import json as _json
            data = _json.loads(raw)
            # Handle new 'sections' schema or old 'blocks' fallback
            raw_sections = data.get("sections") or data.get("blocks", [])
            blocks = _blocks_from_gemini_markdown_json(raw_sections)

            # Store metadata in the parser context if present
            self.last_metadata = data.get("metadata", {})

            logger.info(
                "gemini_ocr.success",
                filename=filename,
                blocks=len(blocks),
                has_metadata=bool(self.last_metadata),
                mode="markdown_blueprint",
            )
            return blocks

        except Exception as e:
            logger.error("gemini_ocr.failed", filename=filename, error=str(e))
            raise


def _blocks_from_gemini_markdown_json(raw_sections: List[Dict]) -> List[StructuredBlock]:
    """
    Convert the new high-fidelity Markdown sections into StructuredBlock objects.
    Preserves the 'markdown_content' which already contains bold numbering and layout.
    """
    result: List[StructuredBlock] = []
    
    for idx, rs in enumerate(raw_sections):
        btype = (rs.get("type") or "paragraph").lower().strip()
        # Use markdown_content if available, fallback to content
        content = (rs.get("markdown_content") or rs.get("content") or "").strip()
        if not content:
            continue
            
        page = int(rs.get("page") or 1)
        
        # We treat the markdown content as a self-contained structural unit
        result.append(StructuredBlock(
            index=idx,
            block_type=btype,
            content=content, # This now contains the Markdown blueprint
            page_number=page,
            style=BlockStyle(bold=("###" in content)) # Hint for simple builders
        ))
        
    return result


def _blocks_from_gemini_json(data: Dict) -> List[StructuredBlock]:
    """
    Convert the Gemini structured OCR JSON response into StructuredBlock objects.
    Handles all block types: heading, paragraph, table, list, page_break.
    """
    raw_blocks = data.get("blocks", [])
    result: List[StructuredBlock] = []
    current_heading = ""

    for idx, rb in enumerate(raw_blocks):
        btype = (rb.get("type") or "paragraph").lower().strip()
        page = int(rb.get("page") or 1)
        style_data = rb.get("style", {})
        style = BlockStyle(
            bold=bool(style_data.get("bold", False)),
            italic=bool(style_data.get("italic", False)),
            font_size=float(style_data.get("font_size", 10.0)),
        )

        if btype in ("heading", "document_title"):
            content = (rb.get("content") or "").strip()
            if not content:
                continue
            level = 1 if btype == "document_title" else int(rb.get("heading_level") or _detect_heading_level(content) or 2)
            level = min(max(level, 1), 6)
            style.bold = True
            current_heading = content
            result.append(StructuredBlock(
                index=idx,
                block_type=btype if btype == "document_title" else BLOCK_HEADING,
                content=content,
                style=style,
                heading_level=level,
                page_number=page,
                original_heading=current_heading,
            ))

        elif btype == "paragraph":
            content = (rb.get("content") or "").strip()
            if not content:
                continue
            result.append(StructuredBlock(
                index=idx,
                block_type=BLOCK_PARAGRAPH,
                content=content,
                style=style,
                page_number=page,
                original_heading=current_heading,
            ))

        elif btype == "table":
            raw_rows = rb.get("table_rows", [])
            if not raw_rows:
                continue
            table_rows: List[List[TableCell]] = []
            for r_idx, row in enumerate(raw_rows):
                cells: List[TableCell] = []
                for cell in row:
                    cells.append(TableCell(
                        text=(cell or "").strip(),
                        is_header=(r_idx == 0),
                    ))
                if any(c.text for c in cells):
                    table_rows.append(cells)
            if not table_rows:
                continue
            result.append(StructuredBlock(
                index=idx,
                block_type=BLOCK_TABLE,
                content="",
                page_number=page,
                table_rows=table_rows,
                original_heading=current_heading,
            ))

        elif btype == "list":
            items = [str(i).strip() for i in rb.get("list_items", []) if i]
            if not items:
                # Fallback: try to parse content string
                raw_content = (rb.get("content") or "").strip()
                if raw_content:
                    items = [line.strip() for line in raw_content.splitlines() if line.strip()]
            if not items:
                continue
            lst_style = str(rb.get("list_style") or "bullet").lower()
            if lst_style not in ("bullet", "numbered"):
                lst_style = "bullet"
            result.append(StructuredBlock(
                index=idx,
                block_type=BLOCK_LIST,
                content="",
                page_number=page,
                list_items=items,
                list_style=lst_style,
                original_heading=current_heading,
            ))

        elif btype == "page_break":
            result.append(StructuredBlock(
                index=idx,
                block_type=BLOCK_PAGEBREAK,
                content="",
                page_number=page,
            ))

        elif btype == "signatures":
            content = (rb.get("content") or "").strip()
            if not content:
                continue
            result.append(StructuredBlock(
                index=idx,
                block_type="signatures",
                content=content,
                style=style,
                page_number=page,
                original_heading=current_heading,
            ))

        else:
            # Unknown type — treat as paragraph
            content = (rb.get("content") or "").strip()
            if content:
                result.append(StructuredBlock(
                    index=idx,
                    block_type=BLOCK_PARAGRAPH,
                    content=content,
                    style=style,
                    page_number=page,
                    original_heading=current_heading,
                ))

    return result



# ─────────────────────────────────────────────
# Unified Entry Point
# ─────────────────────────────────────────────

class DocumentStructureParser:
    """
    Main parser — full extraction chain with Gemini OCR as the proper fallback.

    Chain for PDFs:
      1. PyMuPDF dict-mode (font/bold/bbox-aware, fast, native text layer)
      2. Gemini Pro Structured OCR (scanned/image PDFs, image-only, no text layer)

    Chain for DOCX:
      1. python-docx (style-aware: Heading 1-6, bold, italic, tables)
      2. Gemini Pro Structured OCR (password-protected, corrupt, complex layout)

    Chain for everything else (TXT, unknown):
      1. PlainTextStructureExtractor (heuristic line parsing)
      2. Gemini Pro Structured OCR

    Gemini OCR is NOT a last-resort emergency dump — it returns structured
    typed blocks (heading/ paragraph/table/list) exactly like the native
    parsers, so downstream translation quality is identical regardless of
    which path ran.
    """

    # Native-parser output below this many chars → trigger Gemini OCR
    _GEMINI_FALLBACK_THRESHOLD = 50

    def parse_sync(self, file_bytes: bytes, filename: str) -> List[StructuredBlock]:
        """
        Synchronous entry point (for use outside async contexts).
        NOTE: This cannot call GeminiStructuredOCRExtractor (async).
        Use `parse_async` from an async context whenever possible.
        """
        ext = Path(filename).suffix.lower()
        blocks = self._try_native(file_bytes, filename, ext)
        if self._is_sufficient(blocks):
            return blocks
        # Cannot call Gemini from sync context — fall back to plain text
        logger.warning(
            "doc_parser.sync_gemini_unavailable",
            filename=filename,
            blocks=len(blocks),
        )
        return blocks  # return whatever native got (possibly empty)

    async def parse_async(self, file_bytes: bytes, filename: str) -> List[StructuredBlock]:
        """
        Async entry point — preferred path.
        Runs native extractor first; if result is insufficient, runs Gemini OCR.
        """
        ext = Path(filename).suffix.lower()
        blocks = self._try_native(file_bytes, filename, ext)

        if self._is_sufficient(blocks):
            logger.info(
                "doc_parser.native_success",
                filename=filename,
                blocks=len(blocks),
                ext=ext,
            )
            return blocks

        # ── Native parser failed / insufficient → Gemini OCR ──
        logger.info(
            "doc_parser.gemini_ocr_fallback",
            filename=filename,
            native_blocks=len(blocks),
            reason="insufficient_content",
        )
        try:
            gemini_blocks = await GeminiStructuredOCRExtractor().extract(file_bytes, filename)
            if gemini_blocks:
                return gemini_blocks
        except Exception as e:
            logger.error(
                "doc_parser.gemini_ocr_error",
                filename=filename,
                error=str(e),
            )

        # Last resort: return whatever native produced (may be empty)
        return blocks

    # ── Keep old `parse` method for backwards compat (sync) ──
    def parse(self, file_bytes: bytes, filename: str) -> List[StructuredBlock]:
        return self.parse_sync(file_bytes, filename)

    def _try_native(self, file_bytes: bytes, filename: str, ext: str) -> List[StructuredBlock]:
        """Run the appropriate native extractor. Returns [] on total failure."""
        try:
            if ext == ".pdf":
                return PDFStructureExtractor().extract(file_bytes)
            elif ext in (".docx", ".doc"):
                return DOCXStructureExtractor().extract(file_bytes)
            else:
                text = file_bytes.decode("utf-8", errors="replace")
                return PlainTextStructureExtractor().extract(text)
        except Exception as e:
            logger.warning(
                "doc_parser.native_failed",
                filename=filename,
                ext=ext,
                error=str(e),
            )
            return []

    def _is_sufficient(self, blocks: List[StructuredBlock]) -> bool:
        """Check if native extraction produced meaningful content."""
        total_chars = sum(
            len(b.content)
            + sum(len(c.text) for row in b.table_rows for c in row)
            + sum(len(i) for i in b.list_items)
            for b in blocks
        )
        return total_chars >= self._GEMINI_FALLBACK_THRESHOLD


def blocks_to_serializable(blocks: List[StructuredBlock]) -> List[Dict]:
    """Convert StructuredBlock list to JSON-serializable dicts for storage."""
    result = []
    for b in blocks:
        d: Dict[str, Any] = {
            "index": b.index,
            "type": b.block_type,
            "content": b.content, # Now containing markdown blueprint
            "page": b.page_number,
            "heading_level": b.heading_level,
            "original_heading": b.original_heading,
            "section_id": b.section_id,
            "style": {
                "bold": b.style.bold,
                "italic": b.style.italic,
                "font_size": b.style.font_size,
                "font_name": b.style.font_name,
                "alignment": b.style.alignment,
            },
        }
        if b.block_type == BLOCK_TABLE:
            # Check if content is markdown table, if not use table_rows
            if not ("|" in b.content and "---" in b.content):
                d["table_rows"] = [
                    [{"text": c.text, "is_header": c.is_header} for c in row]
                    for row in b.table_rows
                ]
            else:
                d["table_markdown"] = b.content
        if b.block_type == BLOCK_LIST:
            d["list_items"] = b.list_items
            d["list_style"] = b.list_style
        result.append(d)
    return result
