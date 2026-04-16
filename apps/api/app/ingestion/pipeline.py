"""
Production-grade document ingestion pipeline.
Atomic unit: Clause (not chunk).
Multi-store indexing: Qdrant (dense) + Elasticsearch (BM25) + Neo4j (graph) + Postgres (metadata).
"""
import re
import uuid
import json
import asyncio
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("ingestion.pipeline")

# ─────────────────────────────────────────────
# Clause Data
# ─────────────────────────────────────────────

@dataclass
class ClauseData:
    clause_id: str
    doc_id: str
    folder_id: str
    section_id: str
    section_heading: str
    clause_type: str
    text: str
    page_start: int
    page_end: int
    position_in_doc: int
    parties_involved: List[str] = field(default_factory=list)
    raw_references: List[str] = field(default_factory=list)
    resolved_references: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# Heuristics
# ─────────────────────────────────────────────

CLAUSE_TYPE_HEURISTICS = {
    "definition":      [r'\bmeans\b', r'\bis defined as\b', r'\bshall mean\b', r'"[A-Z][^"]+"\s+means'],
    "obligation":      [r'\bshall\b(?!\s+mean)', r'\bmust\b', r'\bwill be responsible\b', r'\bhereby agrees to\b'],
    "right":           [r'\bmay\b(?!\s+not)', r'\bis entitled to\b', r'\bhas the right to\b', r'\bat its discretion\b'],
    "termination":     [r'\bterminate\b', r'\btermination\b', r'\bexpir', r'\bend date\b', r'\bcancel'],
    "payment":         [r'\bshall pay\b', r'\bpayment\b', r'\binvoice\b', r'\bcompensation\b', r'\bfee\b'],
    "confidentiality": [r'\bconfidential', r'\bnon-disclosure\b', r'\bproprietary\b', r'\btrade secret\b'],
    "liability":       [r'\bliabilit', r'\bliable\b', r'\bnot responsible\b', r'\blimit.*?liability\b'],
    "indemnity":       [r'\bindemnif', r'\bhold harmless\b'],
    "warranty":        [r'\bwarrant', r'\brepresents and warrants\b', r'\bguarante'],
    "penalty":         [r'\bpenalt', r'\bliquidated damages\b', r'\bfine\b'],
    "data_protection": [r'\bpersonal data\b', r'\bGDPR\b', r'\bdata protection\b', r'\bprivacy\b'],
    "notice":          [r'\bnotice\b.*\bshall\b', r'\bwritten notice\b', r'\bnotif'],
    "condition":       [r'\bsubject to\b', r'\bprovided that\b', r'\bupon condition\b'],
    "representation":  [r'\brepresents and warrants\b', r'\bcertifies\b', r'\backnowledges and agrees\b'],
}

HEADING_PATTERNS = [
    r'^\d+\.\s{1,3}[A-Z]',          # 1. Heading
    r'^\d+\.\d+\s{1,3}[A-Z]',       # 1.1 Heading
    r'^\d+\.\d+\.\d+\s{1,3}[A-Z]',  # 1.1.1 Heading
    r'^Article\s+\d+',               # Article 1
    r'^ARTICLE\s+[IVX\d]+',
    r'^Section\s+\d+',               # Section 1
    r'^SECTION\s+\d+',
    r'^PART\s+[IVX\d]+',             # PART I
    r'^SCHEDULE\s+\d+',              # SCHEDULE 1
    r'^EXHIBIT\s+[A-Z\d]+',          # EXHIBIT A
    r'^ANNEX\s+[A-Z\d]+',
    r'^[A-Z][A-Z\s]{4,}$',           # ALL CAPS SHORT LINE
]

CROSS_REF_PATTERNS = [
    r'(?:Section|Clause|Article|paragraph|Schedule|Exhibit)\s+(\d+(?:\.\d+)*)',
    r'(?:as defined in|defined in|set forth in|described in)\s+(?:Section|Clause)\s+(\d+(?:\.\d+)*)',
]


def _sanitize_text(text: str) -> str:
    """Remove characters that Postgres UTF-8 rejects (null bytes, lone surrogates)."""
    # Null byte is illegal in Postgres TEXT columns
    text = text.replace('\x00', '')
    # Re-encode to drop any lone surrogates introduced by errors="replace"
    text = text.encode('utf-8', errors='ignore').decode('utf-8')
    return text


def _detect_clause_type(text: str) -> str:
    for ctype, patterns in CLAUSE_TYPE_HEURISTICS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return ctype
    return "miscellaneous"


def _is_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 160:
        return False
    for pattern in HEADING_PATTERNS:
        if re.match(pattern, line):
            return True
    return False


def _extract_raw_references(text: str) -> List[str]:
    refs = []
    for pattern in CROSS_REF_PATTERNS:
        refs.extend(re.findall(pattern, text, re.IGNORECASE))
    return list(set(refs))


# ─────────────────────────────────────────────
# Document Parser
# ─────────────────────────────────────────────

# Mime-type map for Gemini file upload
_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def _split_gemini_text_into_pages(raw_text: str) -> List[Dict]:
    """
    Split Gemini's output (with --- Page N --- markers) into page dicts.
    If no markers found, treat the entire text as page 1.
    """
    import re as _re
    parts = _re.split(r'-{2,}\s*Page\s+(\d+)\s*-{2,}', raw_text)
    if len(parts) < 3:
        # No page markers — single page
        return [{"page": 1, "text": raw_text.strip()}] if raw_text.strip() else []
    pages = []
    # parts: [preamble, page_num, text, page_num, text, ...]
    for i in range(1, len(parts), 2):
        page_num = int(parts[i])
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if text:
            pages.append({"page": page_num, "text": text})
    return pages


class DocumentParser:
    """
    Parse document bytes into pages with text content.

    Strategy (fail-safe, accuracy-first):
      PDF:  PyMuPDF text extraction  →  Gemini direct PDF parse (fallback)
      DOCX: python-docx             →  Gemini direct file parse (fallback)
      Other: UTF-8 decode           →  Gemini direct file parse (fallback)

    The Gemini fallback handles scanned images, encrypted PDFs, corrupt DOCX,
    and any file type Gemini can read natively — with zero extra dependencies.
    """

    def parse(self, file_bytes: bytes, filename: str) -> Dict:
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            return self._parse_pdf(file_bytes)
        elif ext in (".docx", ".doc"):
            return self._parse_docx(file_bytes)
        else:
            return self._parse_text(file_bytes, ext)

    def _parse_pdf(self, file_bytes: bytes) -> Dict:
        """Try PyMuPDF first (fast, free). Return empty pages on failure — Gemini fallback runs later."""
        try:
            import fitz  # PyMuPDF
            pages = []
            with fitz.open(stream=file_bytes, filetype="pdf") as doc:
                for page_num, page in enumerate(doc, start=1):
                    text = page.get_text()
                    if text.strip():
                        pages.append({"page": page_num, "text": text})
            if pages:
                logger.info("parser.pymupdf.success", pages=len(pages))
                return {"pages": pages, "page_count": len(pages), "parser": "pymupdf"}
            # PyMuPDF opened the file but extracted no text — scanned/image PDF.
            logger.info("parser.pymupdf.no_text_extracted")
            return {"pages": [], "page_count": 0, "parser": "pymupdf_empty"}
        except Exception as e:
            # PyMuPDF unavailable or file unreadable — signal for Gemini fallback.
            logger.warning("parser.pymupdf_failed", error=str(e))
            return {"pages": [], "page_count": 0, "parser": "pymupdf_failed"}

    def _parse_docx(self, file_bytes: bytes) -> Dict:
        try:
            import io
            from docx import Document as DocxDocument
            doc = DocxDocument(io.BytesIO(file_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            full_text = "\n".join(paragraphs)
            if full_text.strip():
                return {"pages": [{"page": 1, "text": full_text}], "page_count": 1, "parser": "python-docx"}
            logger.info("parser.docx.no_text_extracted")
            return {"pages": [], "page_count": 0, "parser": "docx_empty"}
        except Exception as e:
            logger.warning("parser.docx_failed", error=str(e))
            return {"pages": [], "page_count": 0, "parser": "docx_failed"}

    def _parse_text(self, file_bytes: bytes, ext: str = ".txt") -> Dict:
        text = file_bytes.decode("utf-8", errors="replace")
        # Guard: if decoded text is mostly binary garbage, don\'t store it.
        printable_ratio = sum(1 for c in text[:2000] if c.isprintable() or c in "\n\r\t") / max(len(text[:2000]), 1)
        if printable_ratio < 0.8:
            logger.warning("parser.text.binary_garbage_detected", printable_ratio=round(printable_ratio, 2))
            return {"pages": [], "page_count": 0, "parser": "text_garbage"}
        return {"pages": [{"page": 1, "text": text}], "page_count": 1, "parser": "text"}


# ─────────────────────────────────────────────
# Clause Segmenter
# ─────────────────────────────────────────────

class ClauseSegmenter:
    """
    Heuristic-first clause segmentation.
    Converts parsed pages into atomic ClauseData objects.
    """

    def segment(self, parsed: Dict, doc_id: str, folder_id: str) -> List[ClauseData]:
        # Build line stream with page numbers
        lines_with_pages: List[Tuple[str, int]] = []
        for page_data in parsed.get("pages", []):
            page_num = page_data["page"]
            for line in page_data["text"].split("\n"):
                lines_with_pages.append((line, page_num))

        clauses: List[ClauseData] = []
        position = 0
        section_index = 0
        current_heading = ""
        current_section_id = f"{doc_id}_s0"

        buffer_lines: List[str] = []
        buffer_pages: List[int] = []

        def _flush():
            nonlocal position
            text = _sanitize_text(" ".join(buffer_lines).strip())
            if not text or len(text) < 15:
                return
            ctype = _detect_clause_type(text)
            raw_refs = _extract_raw_references(text)
            clauses.append(ClauseData(
                clause_id=str(uuid.uuid4()),
                doc_id=doc_id,
                folder_id=folder_id,
                section_id=current_section_id,
                section_heading=current_heading,
                clause_type=ctype,
                text=text,
                page_start=buffer_pages[0] if buffer_pages else 1,
                page_end=buffer_pages[-1] if buffer_pages else 1,
                position_in_doc=position,
                raw_references=raw_refs,
            ))
            position += 1

        for line, page_num in lines_with_pages:
            stripped = line.strip()
            if not stripped:
                continue

            if _is_heading(stripped):
                if buffer_lines:
                    _flush()
                    buffer_lines = []
                    buffer_pages = []
                section_index += 1
                current_section_id = f"{doc_id}_s{section_index}"
                current_heading = stripped
            else:
                buffer_lines.append(stripped)
                buffer_pages.append(page_num)
                # Hard boundary at 600 words to prevent mega-clauses
                if len(" ".join(buffer_lines).split()) > 600:
                    _flush()
                    buffer_lines = []
                    buffer_pages = []

        if buffer_lines:
            _flush()

        return clauses


# ─────────────────────────────────────────────
# Cross-Reference Resolver
# ─────────────────────────────────────────────

class CrossReferenceResolver:
    """Resolves raw cross-reference strings into clause_ids."""

    def resolve(self, clauses: List[ClauseData]) -> List[ClauseData]:
        # Build index: section_number → clause_id
        ref_index: Dict[str, str] = {}
        for c in clauses:
            m = re.match(r'^(\d+(?:\.\d+)*)', c.section_heading.strip())
            if m:
                ref_index[m.group(1)] = c.clause_id

        for c in clauses:
            resolved = [ref_index[r] for r in c.raw_references if r in ref_index]
            c.resolved_references = resolved

        return clauses


# ─────────────────────────────────────────────
# Embedding (non-blocking)
# ─────────────────────────────────────────────

def _embed_sync(texts: List[str]) -> List[List[float]]:
    """Synchronous embedding — delegates to gemini_client which has the API key configured."""
    from app.ai.gemini_client import _embed_texts_sync
    return _embed_texts_sync(texts)


async def generate_embeddings_async(texts: List[str]) -> List[List[float]]:
    """Non-blocking embedding via thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_sync, texts)


# ─────────────────────────────────────────────
# Metadata Extraction
# ─────────────────────────────────────────────

async def extract_document_metadata(full_text: str, doc_id: str) -> Dict:
    """Extract structured metadata using Gemini."""
    from app.ai.gemini_client import generate_structured
    from app.ai.prompts import METADATA_EXTRACTION_PROMPT
    try:
        prompt = METADATA_EXTRACTION_PROMPT.format(document_text=full_text[:10000])
        return await generate_structured(prompt, feature_name="metadata_extraction")
    except Exception as e:
        logger.warning("extraction.failed", doc_id=doc_id, error=str(e))
        return {"language": "en", "document_type": "unknown", "parties": []}


# ─────────────────────────────────────────────
# Multi-Store Indexer
# ─────────────────────────────────────────────

async def embed_and_store_clauses(clauses: List[ClauseData], document_name: str, folder_name: str):
    """Embed all clauses and upsert to Qdrant (dense vectors only)."""
    from qdrant_client.models import PointStruct
    from app.core.vector_store import upsert_clauses

    if not clauses:
        return

    texts = [c.text for c in clauses]
    embeddings = await generate_embeddings_async(texts)

    points = []
    for clause, embedding in zip(clauses, embeddings):
        point = PointStruct(
            id=clause.clause_id,
            vector={"dense": embedding},
            payload={
                "clause_id": clause.clause_id,
                "doc_id": clause.doc_id,
                "folder_id": clause.folder_id,
                "section_id": clause.section_id,
                "section_heading": clause.section_heading,
                "document_name": document_name,
                "folder_name": folder_name,
                "clause_type": clause.clause_type,
                "text": clause.text,
                "page_start": clause.page_start,
                "page_end": clause.page_end,
                "position_in_doc": clause.position_in_doc,
                "parties_involved": clause.parties_involved,
                "references": clause.resolved_references,
            },
        )
        points.append(point)

    await upsert_clauses(points)
    logger.info("ingestion.qdrant_stored", count=len(points))


async def index_clauses_to_elasticsearch(clauses: List[ClauseData], document_name: str, folder_name: str) -> int:
    """BM25 index all clauses to Elasticsearch. Returns the number of successfully indexed docs."""
    from app.core.elasticsearch import bulk_index_clauses

    docs = [
        {
            "clause_id": c.clause_id,
            "doc_id": c.doc_id,
            "folder_id": c.folder_id,
            "section_id": c.section_id,
            "section_heading": c.section_heading,
            "document_name": document_name,
            "folder_name": folder_name,
            "clause_type": c.clause_type,
            "text": c.text,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "position_in_doc": c.position_in_doc,
        }
        for c in clauses
    ]
    count = await bulk_index_clauses(docs)
    logger.info("ingestion.es_indexed", count=count, total=len(docs))
    return count


def write_graph_nodes(doc_id: str, document_name: str, folder_id: str, clauses: List[ClauseData], metadata: Dict):
    """Write clause nodes and reference edges to Neo4j."""
    from app.core.graph_db import (
        create_document_node,
        create_clause_node_batch,
        create_reference_edges,
        create_entity_nodes,
    )
    from datetime import datetime

    create_document_node(
        doc_id=doc_id,
        name=document_name,
        folder_id=folder_id,
        metadata={
            "language": metadata.get("language", "unknown"),
            "doc_type": metadata.get("document_type", "unknown"),
            "created_at": str(datetime.utcnow()),
        },
    )

    clause_dicts = [
        {
            "id": c.clause_id,
            "section_id": c.section_id,
            "clause_type": c.clause_type,
            "section_heading": c.section_heading,
            "page_start": c.page_start,
            "text_preview": c.text[:200],
        }
        for c in clauses
    ]
    create_clause_node_batch(doc_id, clause_dicts)

    ref_pairs = [
        (c.clause_id, ref_id)
        for c in clauses
        for ref_id in c.resolved_references
    ]
    if ref_pairs:
        create_reference_edges(ref_pairs)

    entity_nodes = []
    for party in metadata.get("parties", []):
        entity_nodes.append({
            "id": f"entity_{doc_id}_{party.get('name', '').replace(' ', '_')[:30]}",
            "name": party.get("name", ""),
            "entity_type": "PARTY",
        })
    if entity_nodes:
        create_entity_nodes(doc_id, entity_nodes)


# ─────────────────────────────────────────────
# Gemini Direct File Parsing (fallback for all formats)
# ─────────────────────────────────────────────

async def parse_with_gemini(file_bytes: bytes, filename: str) -> dict:
    """
    Universal fallback: send the raw file to Gemini and extract text.
    Works for scanned PDFs, image-only PDFs, corrupt DOCX, and any format
    Gemini can natively read (PDF, images, etc.).

    Single API call per document — no per-page rendering, no extra dependencies.
    Returns the same dict shape as DocumentParser.parse().
    """
    from pathlib import Path as _Path
    from app.ai.gemini_client import extract_text_from_file

    ext = _Path(filename).suffix.lower()
    mime = _EXT_TO_MIME.get(ext, "application/octet-stream")
    logger.info("pipeline.gemini_parse.start", filename=filename, mime=mime, size=len(file_bytes))

    raw_text = await extract_text_from_file(file_bytes, mime)
    if not raw_text.strip():
        raise ValueError(
            f"Gemini could not extract any text from '{filename}'. "
            "The file may be blank, fully redacted, or in an unsupported format."
        )

    pages = _split_gemini_text_into_pages(raw_text)
    logger.info("pipeline.gemini_parse.done", filename=filename, pages=len(pages), chars=len(raw_text))
    return {"pages": pages, "page_count": len(pages), "parser": "gemini_direct"}


# ─────────────────────────────────────────────
# Page-Index Tree Builder
# ─────────────────────────────────────────────

async def build_page_index_tree(doc_id: str, full_text: str) -> Dict:
    """
    Build a hierarchical page-index tree from document text using Gemini Pro.
    Returns: {"summary": str, "tree": List[TreeNode]}

    Called during ingestion (after Postgres clause records are saved) so the
    Document Viewer Modal can show AI summary + section navigation.
    Also used by the ReAct agent Page-Index RAG path for large documents.
    """
    from app.ai.gemini_client import generate_structured
    from app.ai.prompts import TREE_BUILD_PROMPT

    # Cap at 50k chars — enough to see full structure without overloading context
    prompt = TREE_BUILD_PROMPT.format(document_text=full_text[:50_000])
    try:
        result = await generate_structured(prompt, use_pro=True, feature_name="doc_tree_build")
        tree_nodes = result.get("tree", [])
        summary = result.get("summary", "")
        logger.info(
            "pipeline.tree_built",
            doc_id=doc_id,
            nodes=len(tree_nodes),
            summary_len=len(summary),
        )
        return {"summary": summary, "tree": tree_nodes}
    except Exception as e:
        logger.error("pipeline.tree_build_failed", doc_id=doc_id, error=str(e))
        raise

