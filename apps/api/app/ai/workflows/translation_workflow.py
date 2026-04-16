"""
Translation LangGraph workflow.
Language detection → structure extraction → structure-preserving Gemini translation.
"""
import json
import asyncio
from typing import Any, Dict, List, TypedDict
from langgraph.graph import StateGraph, END

from app.ai.gemini_client import generate_structured, generate_text
from app.ai.prompts import TRANSLATION_PROMPT, LANGUAGE_DETECTION_PROMPT
from app.core.logging import get_logger

logger = get_logger("translation_workflow")

MAX_CHARS_PER_SECTION = 10000


class TranslationState(TypedDict):
    document_id: str
    document_text: str
    source_language: str
    target_language: str
    structure_map: List[Dict]
    result: Dict


async def detect_language(state: TranslationState) -> TranslationState:
    """Detect the source language of the document. Skips if already set by caller."""
    if state.get("source_language"):
        return state
    sample = state["document_text"][:3000]
    prompt = LANGUAGE_DETECTION_PROMPT.format(text_sample=sample)
    try:
        result = await generate_structured(prompt, schema={}, feature_name="language_detection")
        state["source_language"] = result.get("language_name", "Unknown")
        logger.info("translation.language_detected", lang=state["source_language"])
    except Exception as e:
        logger.warning("translation.detect_failed", error=str(e))
        state["source_language"] = "Unknown"
    return state


async def extract_structure(state: TranslationState) -> TranslationState:
    """
    Extract document structure (sections, headings, clause numbers).
    Splits text into translatable sections preserving hierarchy.
    """
    text = state["document_text"]
    lines = text.split("\n")
    sections = []
    current_section = {"id": "s0", "heading": "", "content": [], "index": 0}
    section_index = 0

    for line in lines:
        stripped = line.strip()
        # Heuristic heading detection: ALL CAPS or ends with ':' or starts with number+period
        is_heading = (
            (stripped.isupper() and len(stripped) > 3 and len(stripped) < 100)
            or (stripped and stripped[0].isdigit() and "." in stripped[:5])
            or (stripped.endswith(":") and len(stripped) < 80)
        )
        if is_heading and current_section["content"]:
            sections.append({
                "section_id": f"s{section_index}",
                "heading": current_section["heading"],
                "text": "\n".join(current_section["content"]).strip(),
            })
            section_index += 1
            current_section = {"id": f"s{section_index}", "heading": stripped, "content": [], "index": section_index}
        elif is_heading:
            current_section["heading"] = stripped
        else:
            current_section["content"].append(line)

    # Flush last section
    if current_section["content"]:
        sections.append({
            "section_id": f"s{section_index}",
            "heading": current_section["heading"],
            "text": "\n".join(current_section["content"]).strip(),
        })

    # If no sections detected, treat whole doc as one section
    if not sections:
        sections = [{"section_id": "s0", "heading": "Document", "text": text}]

    state["structure_map"] = sections
    return state


async def translate_document(state: TranslationState) -> TranslationState:
    """Translate all sections in parallel to maximise throughput."""
    sections = [s for s in state["structure_map"] if s.get("text", "").strip()]
    sem = asyncio.Semaphore(10)  # max 10 concurrent Gemini Flash calls

    async def _translate_one(section: Dict) -> Dict:
        prompt = TRANSLATION_PROMPT.format(
            source_language=state["source_language"],
            target_language=state["target_language"],
            structure_map=json.dumps([{
                "section_id": section["section_id"],
                "heading": section["heading"],
            }]),
            original_text=section["text"][:MAX_CHARS_PER_SECTION],
        )
        async with sem:
            try:
                return await generate_structured(prompt, schema={}, use_pro=False, temperature=0.1, feature_name="translation")
            except Exception as e:
                logger.error("translation.section_failed", section_id=section["section_id"], error=str(e))
                return {
                    "translated_sections": [{
                        "section_id": section["section_id"],
                        "original_heading": section["heading"],
                        "translated_heading": section["heading"],
                        "original_text": section["text"],
                        "translated_text": f"[TRANSLATION FAILED: {e}]",
                        "is_approximate": True,
                        "translator_notes": [f"Section translation failed: {e}"],
                    }]
                }

    results = await asyncio.gather(*[_translate_one(s) for s in sections])

    translated_sections: List[Dict] = []
    uncertainty_flags: List = []
    dropped_warnings: List = []
    for r in results:
        translated_sections.extend(r.get("translated_sections", []))
        uncertainty_flags.extend(r.get("uncertainty_flags", []))
        dropped_warnings.extend(r.get("dropped_text_warnings", []))

    state["result"] = {
        "translated_sections": translated_sections,
        "source_language": state["source_language"],
        "target_language": state["target_language"],
        "overall_confidence": _compute_confidence(uncertainty_flags, translated_sections),
        "uncertainty_flags": uncertainty_flags,
        "dropped_text_warnings": dropped_warnings,
    }
    return state


def _compute_confidence(flags: List, sections: List) -> str:
    if not sections:
        return "low"
    approximate_count = sum(1 for s in sections if s.get("is_approximate"))
    ratio = approximate_count / len(sections)
    if ratio < 0.1 and len(flags) < 3:
        return "high"
    elif ratio < 0.3:
        return "medium"
    return "low"


def build_translation_workflow() -> Any:
    graph = StateGraph(TranslationState)
    graph.add_node("detect_language", detect_language)
    graph.add_node("extract_structure", extract_structure)
    graph.add_node("translate_document", translate_document)

    graph.set_entry_point("detect_language")
    graph.add_edge("detect_language", "extract_structure")
    graph.add_edge("extract_structure", "translate_document")
    graph.add_edge("translate_document", END)

    return graph.compile()


translation_workflow = build_translation_workflow()
