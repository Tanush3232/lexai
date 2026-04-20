"""
Gemini API client — single entry point for all LLM calls in LexAI.
Enforces: one API key, one model family, structured outputs, no hallucination.
Embedding calls are run in a thread executor to avoid blocking the event loop.
"""
import json
import asyncio
from typing import Any, Dict, List, Optional
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("gemini")

# Initialize Gemini client once
genai.configure(api_key=settings.GOOGLE_API_KEY)

_flash_model: genai.GenerativeModel | None = None
_pro_model: genai.GenerativeModel | None = None

# ─────────────────────────────────────────────
# Safety Settings — Legal Documents
# ─────────────────────────────────────────────
# Set to BLOCK_NONE to prevent false-positives on standard legal
# terminology (e.g., party disputes, harassment-adjacent terms).
SAFETY_SETTINGS = {
    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
    "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
}


def get_flash_model() -> genai.GenerativeModel:
    global _flash_model
    if _flash_model is None:
        _flash_model = genai.GenerativeModel(settings.GEMINI_MODEL)
    return _flash_model


def get_pro_model() -> genai.GenerativeModel:
    global _pro_model
    if _pro_model is None:
        _pro_model = genai.GenerativeModel(settings.GEMINI_PRO_MODEL)
    return _pro_model


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def generate_structured(
    prompt: str,
    use_pro: bool = False,
    temperature: float = 0.1,
    schema: Optional[Dict] = None,  # accepted for call-site compatibility; Gemini enforces JSON via mime type
    feature_name: str = "unknown",
) -> Dict:
    """
    Generate a structured JSON response from Gemini.
    Uses response_mime_type=application/json to enforce JSON output.
    The `schema` parameter is accepted but unused — Gemini enforces structure via the prompt itself.
    """
    model = get_pro_model() if use_pro else get_flash_model()
    config = GenerationConfig(
        temperature=temperature,
        response_mime_type="application/json",
    )
    # Pro model (synthesis/analysis): allow 300s for large docs. Flash (routing/think): 60s.
    timeout = 300 if use_pro else 60
    loop = asyncio.get_running_loop()
    raw = ""
    try:
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                prompt,
                generation_config=config,
                safety_settings=SAFETY_SETTINGS,
                request_options={"timeout": timeout},
            ),
        )

        # ── Check for blocked responses ──────────────────────────────────────
        if not response.candidates:
            reason = getattr(response.prompt_feedback, "block_reason", "UNKNOWN")
            logger.error("gemini.blocked_prompt", reason=reason, feature=feature_name)
            raise ValueError(f"Gemini blocked the prompt (Reason: {reason}). Please check safety settings.")
        # ── Token tracking ────────────────────────────────────────────────────
        try:
            from app.services.usage_tracker import log_usage
            usage = getattr(response, "usage_metadata", None)
            input_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage else len(prompt) // 4
            output_tokens = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
            model_name = settings.GEMINI_PRO_MODEL if use_pro else settings.GEMINI_MODEL
            await log_usage(model_name, feature_name, input_tokens, output_tokens)
        except Exception:
            pass  # tracking must never break the primary path
        # ─────────────────────────────────────────────────────────────────────
        raw = response.text.strip()
        # Strip markdown code fences robustly
        if raw.startswith("```"):
            import re
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except asyncio.TimeoutError:
        logger.error("gemini.generate_structured_timeout", prompt_len=len(prompt))
        raise
    except json.JSONDecodeError as e:
        logger.error("gemini.json_parse_error", error=str(e), raw_preview=raw[:200])
        raise
    except Exception as e:
        logger.error("gemini.generate_structured_error", error=str(e), exc_info=True)
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def generate_text(
    prompt: str,
    use_pro: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    feature_name: str = "unknown",
) -> str:
    """Generate free-form text from Gemini."""
    model = get_pro_model() if use_pro else get_flash_model()
    config = GenerationConfig(temperature=temperature, max_output_tokens=max_tokens)
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(
            prompt,
            generation_config=config,
            safety_settings=SAFETY_SETTINGS,
            request_options={"timeout": 120},
        ),
    )

    if not response.candidates:
        reason = getattr(response.prompt_feedback, "block_reason", "UNKNOWN")
        logger.error("gemini.blocked_prompt", reason=reason, feature=feature_name)
        return f"[BLOCKED BY SAFETY FILTER: {reason}]"
    # ── Token tracking ────────────────────────────────────────────────────
    try:
        from app.services.usage_tracker import log_usage
        usage = getattr(response, "usage_metadata", None)
        input_tokens = (getattr(usage, "prompt_token_count", 0) or 0) if usage else len(prompt) // 4
        output_tokens = (getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        model_name = settings.GEMINI_PRO_MODEL if use_pro else settings.GEMINI_MODEL
        await log_usage(model_name, feature_name, input_tokens, output_tokens)
    except Exception:
        pass  # tracking must never break the primary path
    # ─────────────────────────────────────────────────────────────────────
    return response.text.strip()


# ─────────────────────────────────────────────
# Embeddings — always run synchronously in executor
# to avoid blocking asyncio event loop
# ─────────────────────────────────────────────

def _embed_texts_sync(texts: List[str]) -> List[List[float]]:
    """Synchronous batch embedding — never call directly from async code."""
    embeddings: List[List[float]] = []
    batch_size = 50
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        result = genai.embed_content(
            model=settings.EMBEDDING_MODEL,
            content=batch,
            task_type="retrieval_document",
        )
        raw = result.get("embedding", [])
        if raw and isinstance(raw[0], (int, float)):
            embeddings.append(raw)  # single item
        else:
            embeddings.extend(raw)  # batch
    return embeddings


def _embed_query_sync(text: str) -> List[float]:
    """Synchronous single query embedding."""
    result = genai.embed_content(
        model=settings.EMBEDDING_MODEL,
        content=text,
        task_type="retrieval_query",
    )
    return result["embedding"]


async def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """Non-blocking batch embedding via thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_texts_sync, texts)


async def extract_text_from_file(file_bytes: bytes, mime_type: str) -> str:
    """
    Send raw document bytes to Gemini and extract all text.
    Works with application/pdf, image/png, image/jpeg, etc.
    Single API call for the entire document — no per-page rendering needed.
    """
    model = get_flash_model()  # gemini-flash is multimodal
    prompt = (
        "You are a legal document OCR and text-extraction system.\n"
        "Extract ALL text from this document exactly as it appears.\n"
        "Preserve the original structure: headings, numbering, sub-clauses, "
        "paragraphs, tables, and lists.\n"
        "For each page, prefix the content with a line: --- Page N ---\n"
        "Return ONLY the extracted text — no commentary, no markdown fences, no labels."
    )
    loop = asyncio.get_running_loop()
    try:
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                [{"mime_type": mime_type, "data": file_bytes}, prompt],
                safety_settings=SAFETY_SETTINGS,
                request_options={"timeout": 300},
            ),
        )
        # Track usage for extract_text_from_file
        try:
            from app.services.usage_tracker import log_usage, normalise_model_name
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0) or 0
            output_tokens = getattr(usage, "candidates_token_count", 0) or 0
            model_name = normalise_model_name(getattr(response, "model_version", "") or "gemini-2.5-flash")
            await log_usage(model_name, "doc_text_extraction", input_tokens, output_tokens)
        except Exception:
            pass  # never break extraction for tracking
        return (response.text or "").strip()
    except Exception as e:
        logger.error("gemini.extract_text_from_file_failed", error=str(e), mime=mime_type)
        return ""


async def embed_query(text: str) -> List[float]:
    """Non-blocking single query embedding via thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _embed_query_sync, text)
