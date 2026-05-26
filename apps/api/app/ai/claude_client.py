"""
Claude AI client for Contract Drafting.
Uses Claude claude-opus-4-5 for complex drafting tasks (main assembly, source ranking)
and Claude claude-sonnet-4-5 for fast tasks (intent analysis, query generation, redaction check).

This module is EXCLUSIVE to the contract drafting feature.
All other LexAI features use the Gemini client.
"""
import asyncio
import json
import re
from typing import Any, Dict, List, Optional

import anthropic

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("claude_client")

# Model aliases — use the best available
CLAUDE_OPUS = "claude-opus-4-5"       # Max intelligence — main draft assembly + source ranking
CLAUDE_SONNET = "claude-sonnet-4-5"   # Fast + smart — intent analysis, query gen, redaction

_client: Optional[anthropic.AsyncAnthropic] = None


def get_claude_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


async def claude_generate_structured(
    prompt: str,
    system: str = "",
    use_opus: bool = False,
    temperature: float = 0.1,
    max_tokens: int = 8000,
    feature_name: str = "contract_draft",
) -> Dict[str, Any]:
    """
    Call Claude and parse the JSON response.
    Falls back to raw text parsing if JSON extraction fails.
    """
    client = get_claude_client()
    model = CLAUDE_OPUS if use_opus else CLAUDE_SONNET

    system_msg = system or (
        "You are an expert Indian legal drafting AI. "
        "Always respond with valid JSON only — no preamble, no code fences, no markdown."
    )

    logger.info("claude_generate_structured.called", model=model, feature=feature_name)

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_msg,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text if response.content else ""
    logger.info(
        "claude_generate_structured.completed",
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    return _parse_json(raw, feature_name)


async def claude_generate_text(
    prompt: str,
    system: str = "",
    use_opus: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 4000,
) -> str:
    """Call Claude and return raw text (for non-JSON outputs like clause text)."""
    client = get_claude_client()
    model = CLAUDE_OPUS if use_opus else CLAUDE_SONNET
    system_msg = system or "You are an expert Indian legal drafting AI."

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_msg,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text if response.content else ""


async def claude_web_search(
    queries: List[str],
    contract_type: str,
    approved_domains: List[str],
) -> List[Dict[str, Any]]:
    """
    Perform real web search via Claude's built-in web_search tool.
    Runs up to 3 queries in sequence, restricted to the approved Indian legal domain list.
    Returns a deduplicated list of source dicts compatible with the drafting pipeline.

    Each source dict contains:
        source_id, source_name, url, domain, snippet,
        source_type, jurisdiction, relevance_note
    """
    client = get_claude_client()

    domain_list = "\n".join(f"  • {d}" for d in approved_domains)
    system_msg = (
        "You are an expert Indian legal research assistant. "
        "Use your web_search tool to find authoritative information. "
        "ONLY cite sources from the following approved Indian legal domains:\n"
        f"{domain_list}\n\n"
        "For each query, search and return a JSON array of sources found. "
        "Never fabricate URLs, case names, or section numbers. "
        "If a source is not from the approved domains, exclude it."
    )

    all_sources: List[Dict] = []
    seen_urls: set = set()

    for i, query in enumerate(queries[:3]):  # cap at 3 queries to control cost
        # ── Rate-limit guard: pause between queries to stay under 30k TPM ─────
        if i > 0:
            await asyncio.sleep(2.0)

        try:
            # Truncate query to ~120 chars to control input token cost
            short_query = query[:120]
            logger.info("claude_web_search.query", index=i, query=short_query[:80])

            response = await client.messages.create(
                model=CLAUDE_SONNET,
                max_tokens=2000,  # reduced — we only need URLs + snippets
                system=system_msg,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for: {short_query}\n\n"
                        f"Contract type: {contract_type}, Indian law.\n\n"
                        "Return a JSON array of sources (max 4):\n"
                        "[{\"source_name\": \"...\", \"url\": \"...\", "
                        "\"snippet\": \"...\", \"source_type\": \"statute|case_law|regulation\"}]\n\n"
                        "Return ONLY the JSON array."
                    ),
                }],
            )

            # ── Extract text from the response ───────────────────────────────
            # IMPORTANT: Claude tool-use responses can contain multiple block types:
            #   TextBlock (.text = str), ToolUseBlock (.input), ToolResultBlock
            # We must check `.text is not None` — the attribute exists on all
            # blocks but is None on non-text blocks, causing the NoneType crash.
            result_text = ""
            for block in response.content:
                text_val = getattr(block, "text", None)
                if text_val is not None:  # explicit None check, not just truthiness
                    result_text += text_val

            if not result_text.strip():
                logger.info("claude_web_search.no_text", query_index=i)
                continue

            # Parse JSON array from the result
            sources_raw = _parse_json_array(result_text)
            for j, src in enumerate(sources_raw):
                url = src.get("url") or ""  # coerce None to ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)

                # Determine domain from URL if not explicitly given
                domain = src.get("domain") or ""
                if not domain and url:
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(url).netloc.lstrip("www.")
                    except Exception:
                        domain = url.split("/")[2] if "/" in url else url

                # Guard all string fields against None
                all_sources.append({
                    "source_id": f"web_{i}_{j}",
                    "source_name": src.get("source_name") or f"Source {i}_{j}",
                    "url": url,
                    "domain": domain,
                    "snippet": (src.get("snippet") or "")[:400],
                    "source_type": src.get("source_type") or "reference",
                    "jurisdiction": "India",
                    "relevance_note": src.get("relevance_note") or "",
                })

            logger.info("claude_web_search.results", query_index=i, sources_found=len(sources_raw))

        except anthropic.BadRequestError as e:
            # web_search tool may not be enabled on this API tier — fall back gracefully
            logger.warning("claude_web_search.not_available", error=str(e), query=query[:60])
            break
        except anthropic.RateLimitError as e:
            # Hit 30k TPM cap — stop and return what we have so far
            logger.warning("claude_web_search.rate_limited", query_index=i, error=str(e))
            break
        except Exception as e:
            logger.error("claude_web_search.error", query=query[:60], error=str(e))
            continue

    return all_sources


def _strip_code_fences(text: str) -> str:
    """
    Remove markdown code fences from Claude's response.
    Handles all these formats Claude may return:
      ```json\n{...}\n```
      ```\n{...}\n```
      ```json{...}```
      {... (no fences)}
    """
    s = text.strip()

    # Pattern: optional ```json or ``` at the start, content, ``` at the end
    # Use a greedy strip of the outer fences
    fence_match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```\s*$", s)
    if fence_match:
        return fence_match.group(1).strip()

    # Fallback: strip just the opening fence line
    s = re.sub(r"^```(?:json)?\s*", "", s)
    # Strip any trailing fence (``` possibly preceded by whitespace on its own line)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _parse_json_array(text: str) -> List[Dict]:
    """Extract a JSON array from Claude's response text."""
    clean = _strip_code_fences(text)

    # Try direct parse as array
    try:
        result = json.loads(clean)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "sources" in result:
            return result["sources"]
    except json.JSONDecodeError:
        pass

    # Try to find first JSON array in the text
    match = re.search(r"\[.*\]", clean, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return []


def _parse_json(raw: str, feature_name: str) -> Dict[str, Any]:
    """Extract JSON from Claude response — handles code fences and stray text."""
    clean = _strip_code_fences(raw)

    # Try direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Try to find first JSON object in the text
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning("claude_parse_json.failed", feature=feature_name, raw_preview=raw[:200])
    return {"_raw": raw, "_parse_error": True}
