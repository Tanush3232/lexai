"""
Enterprise Contract Drafting Orchestrator
==========================================
Async generator that powers the SSE stream for the multi-stage agentic drafting pipeline.

Stage flow:
  analyzing_intent → [needs_input?] → redacting → researching
  → sources_ready → [awaiting_approval] → assembling → complete

Uses Claude (Opus for heavy reasoning, Sonnet for fast tasks).
"""
import asyncio
import json
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional, Any

from app.ai.claude_client import claude_generate_structured, claude_web_search, CLAUDE_OPUS, CLAUDE_SONNET
from app.ai.prompts import (
    INTENT_ANALYSIS_PROMPT,
    DRAFT_QUERY_AGENT_PROMPT,
    SOURCE_RANKING_PROMPT,
    BLOCK_ASSEMBLY_PROMPT,
    ADVERSARIAL_RED_TEAM_PROMPT,
    AUTO_INSERT_FIX_PROMPT,
)
from app.core.vector_store import hybrid_search_clauses
from app.ai.gemini_client import embed_query  # Embeddings still via Gemini
from app.services.draft_redaction import redact, restore
from app.ai.web_search_orchestrator import APPROVED_DOMAINS, LegalWebSearchOrchestrator
from app.core.logging import get_logger

logger = get_logger("draft_orchestrator")

# ─── Clause type templates per contract type ───────────────────────────────
CLAUSE_TEMPLATES: Dict[str, List[str]] = {
    "NDA": ["recitals", "definitions", "confidentiality_obligations", "permitted_disclosures",
            "exclusions", "obligations_of_receiving_party", "term", "return_of_information",
            "remedies", "governing_law", "dispute_resolution", "miscellaneous", "signatures"],
    "software_license": ["recitals", "definitions", "license_grant", "restrictions",
                         "ip_ownership", "payment_terms", "support_maintenance", "warranties",
                         "indemnification", "limitation_of_liability", "term_termination",
                         "governing_law", "dispute_resolution", "signatures"],
    "vendor_contract": ["recitals", "definitions", "scope_of_services", "delivery_milestones",
                        "payment_terms", "intellectual_property", "confidentiality",
                        "indemnification", "warranties", "force_majeure", "term_termination",
                        "governing_law", "dispute_resolution", "signatures"],
    "purchase_order": ["parties", "goods_description", "quantity_specifications", "price_payment",
                       "delivery_terms", "inspection_acceptance", "warranties", "governing_law"],
    "lease": ["parties", "premises_description", "term", "rent_deposit", "permitted_use",
              "maintenance_repairs", "alterations", "subletting", "default_remedies",
              "handover", "governing_law", "signatures"],
    "CNF": ["recitals", "definitions", "confirmation_terms", "payment_schedule",
            "delivery_conditions", "liability_cap", "governing_law", "signatures"],
    "employment": ["recitals", "definitions", "designation_duties", "compensation_benefits",
                   "working_hours", "leave_policy", "confidentiality", "ip_assignment",
                   "non_compete", "termination", "governing_law", "signatures"],
    "service_agreement": ["recitals", "definitions", "scope_of_services", "fees_payment",
                          "term_renewal", "warranties", "liability", "indemnification",
                          "confidentiality", "termination", "governing_law", "signatures"],
    "MoU": ["background", "definitions", "purpose", "scope_of_cooperation", "responsibilities",
             "financial_arrangements", "confidentiality", "term", "termination",
             "governing_law", "signatures"],
    "partnership": ["recitals", "definitions", "business_name", "capital_contributions",
                    "profit_loss_sharing", "management_control", "banking", "accounts",
                    "partner_duties", "dissolution", "governing_law", "signatures"],
}

# Fallback for unknown types
DEFAULT_CLAUSES = ["recitals", "definitions", "main_obligations", "payment_terms",
                   "confidentiality", "term_termination", "governing_law",
                   "dispute_resolution", "signatures"]


def _sse(event_type: str, **kwargs) -> Dict:
    """Build a standard SSE event dict."""
    return {"type": event_type, "timestamp": datetime.utcnow().isoformat(), **kwargs}


async def analyze_intent(prompt: str, context: Dict) -> Dict:
    """Stage 1: Use Claude Sonnet to extract structured intent from the user prompt."""
    result = await claude_generate_structured(
        prompt=INTENT_ANALYSIS_PROMPT.format(
            user_prompt=prompt,
            context_json=json.dumps(context, indent=2) if context else "{}",
        ),
        use_opus=False,
        temperature=0.1,
        feature_name="intent_analysis",
    )
    return result


async def _run_web_research(queries: List[str], contract_type: str) -> List[Dict]:
    """
    Real web research using the Gemini-grounded LegalWebSearchOrchestrator.
    This guarantees reliable, legal-focused research without 429 timeouts.

    Returns a list of source dicts with source_id, source_name, url, domain,
    snippet, source_type, jurisdiction, relevance_note.
    """
    try:
        primary_query = queries[0] if queries else f"{contract_type} contract India"
        logger.info("web_research.gemini_start", query=primary_query)
        
        orchestrator = LegalWebSearchOrchestrator(mode="pro", query=primary_query)
        complete_event = None
        async for event in orchestrator.run():
            if event.get("type") == "complete":
                complete_event = event
                break

        if complete_event:
            citations = complete_event.get("citations", [])
            sources = []
            for i, cit in enumerate(citations[:8]):
                sources.append({
                    "source_id": f"web_{i}",
                    "source_name": cit.get("source_name", ""),
                    "url": cit.get("url", ""),
                    "domain": cit.get("domain", ""),
                    "snippet": cit.get("snippet", "")[:400],
                    "source_type": cit.get("citation_type", "reference"),
                    "jurisdiction": cit.get("jurisdiction") or "India",
                    "relevance_note": f"Retrieved via legal research for {contract_type}",
                })
            logger.info("web_research.gemini_success", count=len(sources))
            return sources
    except Exception as e:
        logger.error("web_research.failed", error=str(e), exc_info=True)
    
    return []


async def _run_internal_research(queries: List[Dict]) -> List[Dict]:
    """Search internal document store (Qdrant + ES) for relevant precedent clauses."""
    results = []
    try:
        for q in queries[:6]:  # limit to 6 clause types
            query_text = q.get("query", "")
            if not query_text:
                continue
            dense_vec = await embed_query(query_text)
            hits = await hybrid_search_clauses(
                query=query_text,
                dense_vector=dense_vec,
                filter_clause_types=[q.get("clause_type")],
                limit=3,
            )
            for hit in hits:
                payload = hit.get("payload", {})
                results.append({
                    "source_id": f"internal_{hit.get('id', str(uuid.uuid4())[:8])}",
                    "source_name": payload.get("document_name", "Internal Document"),
                    "url": "",
                    "domain": "internal",
                    "snippet": payload.get("text", "")[:400],
                    "source_type": "precedent",
                    "jurisdiction": "India",
                    "clause_type": q.get("clause_type", ""),
                    "relevance_note": f"Internal precedent for {q.get('clause_type', 'clause')}",
                    "page": payload.get("page_start", 0),
                    "section": payload.get("section_heading", ""),
                })
    except Exception as e:
        logger.warning("internal_research.failed", error=str(e))
    return results


async def _run_acts_research(relevant_acts: List[str]) -> List[Dict]:
    """Search the legal_acts table for relevant Indian statutes."""
    results = []
    for act_name in relevant_acts[:4]:
        results.append({
            "source_id": f"act_{uuid.uuid4().hex[:8]}",
            "source_name": act_name,
            "url": "https://indiacode.nic.in",
            "domain": "indiacode.nic.in",
            "snippet": f"Provisions of {act_name} applicable to this contract type.",
            "source_type": "statute",
            "jurisdiction": "India",
            "relevance_note": f"Statutory framework: {act_name}",
        })
    return results


async def rank_sources(sources: List[Dict], contract_type: str, context: Dict) -> Dict:
    """Use Claude Opus to rank and assess sources."""
    result = await claude_generate_structured(
        prompt=SOURCE_RANKING_PROMPT.format(
            contract_type=contract_type,
            context_json=json.dumps(context, indent=2),
            sources_json=json.dumps(sources, indent=2),
        ),
        use_opus=True,
        temperature=0.1,
        feature_name="source_ranking",
    )
    return result


async def assemble_blocks(
    contract_type: str,
    context: Dict,
    approved_sources: List[Dict],
    precedent_clauses: List[Dict],
    redacted_context: Dict,
) -> Dict:
    """Use Claude Opus to assemble the block-based contract draft."""
    clause_list = CLAUSE_TEMPLATES.get(contract_type, DEFAULT_CLAUSES)
    jurisdiction = context.get("jurisdiction", "India")
    governing_law = context.get("governing_law", "Laws of India")

    result = await claude_generate_structured(
        prompt=BLOCK_ASSEMBLY_PROMPT.format(
            contract_type=contract_type,
            jurisdiction=jurisdiction,
            governing_law=governing_law,
            context_json=json.dumps(context, indent=2),
            required_clauses="\n".join(f"- {c}" for c in clause_list),
            approved_sources=json.dumps(approved_sources[:8], indent=2),
            precedent_clauses=json.dumps(precedent_clauses[:10], indent=2),
        ),
        use_opus=True,
        temperature=0.15,
        max_tokens=16000,
        feature_name="block_assembly",
    )
    return result


def blocks_to_html(blocks: List[Dict], preamble: str, signature_block: str) -> str:
    """Convert assembled blocks to a single HTML string for storage."""
    parts = [f"<div class='contract-preamble'>{preamble}</div>"]
    for block in blocks:
        review_flag = (
            "<span class='review-flag'>⚠ REVIEW REQUIRED</span>" if block.get("needs_review") else ""
        )
        parts.append(f"""
<div class='contract-block' data-block-id='{block.get("block_id", "")}' 
     data-locked='{str(block.get("is_locked", True)).lower()}'>
  <h3 class='clause-heading'>{block.get("clause_number", "")} {block.get("heading", "")}</h3>
  {review_flag}
  <div class='clause-content'>{block.get("content", "")}</div>
</div>""")
    parts.append(f"<div class='signature-block'>{signature_block}</div>")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Main Orchestrator Generator
# ─────────────────────────────────────────────────────────────────────────────

async def run_draft_orchestration(
    prompt: str,
    context: Dict,
    session_id: str,
) -> AsyncGenerator[Dict, None]:
    """
    Main SSE event generator for the enterprise contract drafting pipeline.
    Each yielded dict is one SSE event.
    """
    try:
        # ── STAGE 1: Analyze Intent ──────────────────────────────────────────
        yield _sse("stage", stage="analyzing_intent", label="Analyzing your request with Claude…",
                   progress=10)

        intent = await analyze_intent(prompt, context)

        if intent.get("_parse_error"):
            yield _sse("error", message="Intent analysis failed — please rephrase your request.")
            return

        yield _sse("intent_analyzed",
                   intent=intent,
                   stage="analyzing_intent",
                   progress=20)

        # ── STAGE 1b: Ask follow-up questions if needed ──────────────────────
        missing = intent.get("missing_critical_fields", [])
        # Filter out already answered fields
        answered_ids = set(context.keys())
        unanswered = [f for f in missing if f.get("id") not in answered_ids]

        if unanswered and not context.get("_skip_questions"):
            yield _sse("needs_input",
                       questions=unanswered,
                       intent=intent,
                       stage="needs_input",
                       label="A few details needed before we can proceed…",
                       progress=25)
            return  # FE will submit answers, re-trigger orchestration

        # ── STAGE 2: Redact PII (Role-Preserving) ───────────────────────────
        yield _sse("stage", stage="redacting",
                   label="Protecting sensitive information with role-preserving masking…", progress=30)

        full_context = {**intent, **context}
        context_str = json.dumps(full_context)

        # Build role_hints from intent parties for role-preserving token format
        # e.g. {"Acme Corp": "SERVICE_PROVIDER"} → {{PARTY_SERVICE_PROVIDER_1}}
        role_hints: Dict[str, str] = {}
        for party in intent.get("parties", []):
            name = party.get("name", "")
            role = party.get("role", "")
            if name and role:
                # Sanitize role for token: "Service Provider" → "SERVICE_PROVIDER"
                role_hints[name] = role.upper().replace(" ", "_")

        redacted_str, token_map = redact(context_str, role_hints=role_hints if role_hints else None)
        redacted_context = json.loads(redacted_str) if redacted_str else full_context

        yield _sse("redaction_complete",
                   tokens_masked=len(token_map),
                   stage="redacting",
                   progress=35)

        # ── STAGE 3: Parallel Research ───────────────────────────────────────
        yield _sse("stage", stage="researching",
                   label="Running parallel research agents…", progress=40)

        contract_type = intent.get("contract_type", "vendor_contract")
        clause_list = CLAUSE_TEMPLATES.get(contract_type, DEFAULT_CLAUSES)

        # Generate optimized search queries via Claude Sonnet
        query_plan = await claude_generate_structured(
            prompt=DRAFT_QUERY_AGENT_PROMPT.format(
                contract_type=contract_type,
                context_json=json.dumps(redacted_context, indent=2),
                clause_types=", ".join(clause_list),
            ),
            use_opus=False,
            temperature=0.1,
            feature_name="query_planning",
        )

        yield _sse("research_update", agent="query_planner",
                   label=f"Generated {len(query_plan.get('clause_queries', []))} search queries",
                   progress=45)

        # Run 3 research agents in parallel
        # Build a list of specific queries: primary + top clause queries
        clause_queries = query_plan.get("clause_queries", [])
        web_queries = [query_plan.get("primary_web_query", f"{contract_type} contract India")]
        # Add 2 most important clause-specific queries for richer web coverage
        for cq in clause_queries[:2]:
            if cq.get("query"):
                web_queries.append(cq["query"])

        web_task = _run_web_research(web_queries, contract_type)
        internal_task = _run_internal_research(query_plan.get("clause_queries", []))
        acts_task = _run_acts_research(query_plan.get("relevant_acts", []))

        web_sources, internal_sources, acts_sources = await asyncio.gather(
            web_task, internal_task, acts_task
        )

        yield _sse("research_update", agent="web", count=len(web_sources),
                   label=f"Web research: {len(web_sources)} Indian legal sources",
                   progress=55)
        yield _sse("research_update", agent="internal", count=len(internal_sources),
                   label=f"Internal precedents: {len(internal_sources)} clauses",
                   progress=60)
        yield _sse("research_update", agent="acts", count=len(acts_sources),
                   label=f"Statutes: {len(acts_sources)} Indian acts",
                   progress=63)

        # ── STAGE 4: Rank & Present Sources ─────────────────────────────────
        yield _sse("stage", stage="ranking_sources",
                   label="Ranking and assessing sources with Claude Opus…", progress=65)

        all_sources = web_sources + internal_sources + acts_sources
        ranking_result = await rank_sources(all_sources, contract_type, redacted_context)

        ranked = ranking_result.get("ranked_sources", [])
        # Attach full source data to ranking result
        source_map = {s["source_id"]: s for s in all_sources}
        for r in ranked:
            sid = r["source_id"]
            if sid in source_map:
                r.update(source_map[sid])

        yield _sse("sources_ready",
                   sources=ranked,
                   all_sources=all_sources,
                   ranking_summary=ranking_result.get("summary", ""),
                   sufficient=ranking_result.get("sufficient_for_drafting", True),
                   stage="sources_ready",
                   label="Research complete — please review and approve sources",
                   progress=70)
        # FE shows sources panel — user must click Approve & Draft
        # Execution resumes in a separate endpoint call (approve-sources)
        return

    except Exception as e:
        logger.error("draft_orchestration.failed", session_id=session_id, error=str(e),
                     exc_info=True)
        yield _sse("error", message=f"Orchestration failed: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Adversarial Red-Team Agent (Phase 6.5)
# ─────────────────────────────────────────────────────────────────────────────

async def run_adversarial_agent(
    blocks: List[Dict],
    contract_type: str,
    jurisdiction: str,
) -> Dict:
    """
    Phase 6.5: Hostile opposing counsel agent.
    Analyzes the assembled blocks to find loopholes, liability gaps, payment escapes,
    and ambiguous terms — before the user ever sees the draft.

    Uses Claude Sonnet (fast, pattern-matching task — no need for Opus here).
    Runs concurrently with de-redaction to add zero wall time.

    Returns the parsed adversarial report dict.
    """
    # Only send essential block data (exclude huge provenance fields)
    blocks_summary = [
        {
            "block_id": b.get("block_id"),
            "clause_number": b.get("clause_number"),
            "clause_type": b.get("clause_type"),
            "heading": b.get("heading"),
            "content": b.get("content", ""),  # Do NOT truncate — we need the full text to find loopholes
        }
        for b in blocks
    ]

    result = await claude_generate_structured(
        prompt=ADVERSARIAL_RED_TEAM_PROMPT.format(
            contract_type=contract_type,
            jurisdiction=jurisdiction,
            blocks_json=json.dumps(blocks_summary, indent=2),
        ),
        use_opus=False,  # Sonnet is faster + sufficient for adversarial scan
        temperature=0.3,  # slightly higher temp for more creative loophole thinking
        max_tokens=6000,
        feature_name="adversarial_red_team",
    )
    return result


async def auto_insert_fix(
    finding: Dict,
    original_block_content: str,
    contract_type: str,
) -> Dict:
    """
    Generate a corrected clause content for a specific adversarial finding.
    Called when the user clicks 'Insert Fix' on a red-team finding card.

    Uses Claude Sonnet for speed (this is a targeted rewrite, not full assembly).
    Returns {corrected_content, change_summary}.
    """
    result = await claude_generate_structured(
        prompt=AUTO_INSERT_FIX_PROMPT.format(
            contract_type=contract_type,
            original_content=original_block_content[:1200],
            exploit=finding.get("exploit", ""),
            suggested_fix=finding.get("suggested_fix", ""),
        ),
        use_opus=False,
        temperature=0.1,
        max_tokens=3000,
        feature_name="auto_insert_fix",
    )
    return result


async def run_assembly_phase(
    session_id: str,
    intent: Dict,
    context: Dict,
    token_map: Dict[str, str],
    approved_sources: List[Dict],
    internal_sources: List[Dict],
) -> AsyncGenerator[Dict, None]:
    """
    Second SSE phase: triggered after user approves sources.
    Assembles the block-based contract using Claude Opus.
    """
    try:
        yield _sse("stage", stage="assembling",
                   label="Claude Opus is assembling your contract…", progress=75)

        contract_type = intent.get("contract_type", "vendor_contract")
        jurisdiction = intent.get("jurisdiction", "India")
        context_combined = {**intent, **context}

        draft_data = await assemble_blocks(
            contract_type=contract_type,
            context=context_combined,
            approved_sources=approved_sources,
            precedent_clauses=internal_sources,
            redacted_context=context_combined,
        )

        if draft_data.get("_parse_error"):
            yield _sse("error", message="Block assembly failed — please try again.")
            return

        blocks = draft_data.get("blocks", [])

        # ── Phase 6.5: De-redact FIRST, then run adversarial agent ────────
        # This prevents the red team from quoting placeholders in its findings,
        # ensuring `auto_insert_fix` works natively on real values.
        yield _sse("stage", stage="red_teaming",
                   label="Adversarial agent stress-testing your draft for loopholes…", progress=85)

        # 1. De-redact synchronously
        for block in blocks:
            if not block.get("is_locked", True):
                block["content"] = restore(block.get("content", ""), token_map)
        
        preamble = restore(draft_data.get("preamble", ""), token_map)
        signature_block = restore(draft_data.get("signature_block", ""), token_map)
        title = restore(draft_data.get("title", f"Draft {contract_type.upper()}"), token_map)

        # 2. Run hostile agent on the fully restored real names
        adversarial_result = await run_adversarial_agent(blocks, contract_type, jurisdiction)

        # Parse adversarial findings
        adversarial_findings = adversarial_result.get("findings", [])
        overall_risk = adversarial_result.get("overall_risk", "medium")
        overall_assessment = adversarial_result.get("overall_assessment", "")
        missing_sections = adversarial_result.get("missing_sections", [])

        finding_count = len(adversarial_findings)
        critical_count = sum(1 for f in adversarial_findings if f.get("severity") == "critical")

        logger.info(
            "adversarial_agent.complete",
            session_id=session_id,
            findings=finding_count,
            critical=critical_count,
            overall_risk=overall_risk,
        )

        yield _sse(
            "red_team_complete",
            findings=adversarial_findings,
            overall_risk=overall_risk,
            overall_assessment=overall_assessment,
            missing_sections=missing_sections,
            finding_count=finding_count,
            critical_count=critical_count,
            label=f"Adversarial review complete — {finding_count} vulnerabilities found ({critical_count} critical)",
            progress=92,
        )

        yield _sse("stage", stage="finalizing",
                   label="Finalizing and running policy checks…", progress=95)

        # Build HTML content
        html_content = blocks_to_html(blocks, preamble, signature_block)

        issues = draft_data.get("issues", [])
        defined_terms = draft_data.get("defined_terms", [])
        missing = draft_data.get("missing_clauses", [])

        yield _sse("draft_ready",
                   stage="complete",
                   label="Your contract is ready for review",
                   progress=100,
                   title=title,
                   blocks=blocks,
                   html_content=html_content,
                   issues=issues,
                   defined_terms=defined_terms,
                   missing_clauses=missing,
                   contract_type=contract_type,
                   session_id=session_id,
                   adversarial_findings=adversarial_findings,
                   adversarial_risk=overall_risk,
                   adversarial_assessment=overall_assessment,
                   adversarial_missing_sections=missing_sections)

    except Exception as e:
        logger.error("assembly_phase.failed", session_id=session_id, error=str(e), exc_info=True)
        yield _sse("error", message=f"Assembly failed: {str(e)}")
