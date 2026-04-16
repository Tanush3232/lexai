"""
Contract Drafting LangGraph workflow.
Retrieves precedent clauses → graph conflict check → structured draft generation.
"""
import json
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, END

from app.ai.gemini_client import generate_structured, embed_query
from app.ai.prompts import CONTRACT_DRAFTING_PROMPT
from app.core.vector_store import hybrid_search
from app.core.graph_db import get_cross_document_conflicts
from app.core.logging import get_logger

logger = get_logger("draft_workflow")

# Default policy constraints applied to all drafts
DEFAULT_POLICY_CONSTRAINTS = """
- Governing law must be explicitly stated.
- Indemnification clauses must be mutual unless explicitly waived by both parties.
- Force majeure provisions are required for contracts >12 months.
- All defined terms must be used consistently throughout the document.
- Payment terms must specify currency, due dates, and late payment consequences.
- Confidentiality obligations must specify duration.
"""

CLAUSE_TEMPLATES = {
    "NDA": ["recitals", "definitions", "confidentiality", "exclusions", "obligations", "term", "governing_law", "dispute_resolution", "signatures"],
    "software_license": ["recitals", "definitions", "license_grant", "restrictions", "ip_ownership", "payment", "warranties", "indemnification", "limitation_of_liability", "term_termination", "governing_law", "signatures"],
    "vendor_contract": ["recitals", "definitions", "scope_of_services", "payment", "delivery", "ip", "confidentiality", "indemnification", "term_termination", "governing_law", "signatures"],
    "purchase_order": ["parties", "goods_description", "price", "delivery", "payment_terms", "warranties", "governing_law"],
    "lease": ["parties", "premises", "term", "rent", "security_deposit", "use", "maintenance", "default", "governing_law"],
    "CNF": ["recitals", "definitions", "confirmation_terms", "payment", "delivery", "liability", "governing_law"],
}


class DraftState(TypedDict):
    contract_type: str
    structured_inputs: Dict
    required_clauses: List[str]
    precedent_clauses: List[Dict]
    graph_conflicts: str
    draft: Dict  # final structured draft


async def determine_required_clauses(state: DraftState) -> DraftState:
    contract_type = state["contract_type"]
    state["required_clauses"] = CLAUSE_TEMPLATES.get(contract_type, CLAUSE_TEMPLATES["vendor_contract"])
    return state


async def retrieve_precedent_clauses(state: DraftState) -> DraftState:
    """Retrieve relevant precedent clauses from vector store."""
    precedent_chunks = []
    for clause_type in state["required_clauses"][:5]:  # top 5 clause types
        query = f"{state['contract_type']} {clause_type} clause"
        dense_vec = await embed_query(query)

        # Simple sparse
        words = query.split()
        sparse_indices = [abs(hash(w)) % 30000 for w in set(words)]
        sparse_values = [1.0] * len(sparse_indices)

        hits = await hybrid_search(
            dense_vector=dense_vec,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            filter_dict={"chunk_type": "clause"},
            limit=3,
        )
        for hit in hits:
            p = hit.get("payload", {})
            precedent_chunks.append({
                "clause_type": clause_type,
                "text": p.get("text", ""),
                "source_document": p.get("document_name", "Unknown"),
                "section": p.get("section", ""),
                "page": p.get("page_number", 0),
            })

    state["precedent_clauses"] = precedent_chunks
    return state


async def check_graph_conflicts(state: DraftState) -> DraftState:
    """Check for known conflicts in related documents."""
    try:
        # For drafting, check across all available documents
        conflicts = get_cross_document_conflicts([])
        state["graph_conflicts"] = (
            json.dumps(conflicts[:5]) if conflicts else "No pre-existing conflicts detected."
        )
    except Exception:
        state["graph_conflicts"] = "Graph conflict check unavailable."
    return state


async def generate_draft(state: DraftState) -> DraftState:
    """Generate the structured contract draft."""
    precedent_text = ""
    for p in state["precedent_clauses"]:
        precedent_text += f"""
[{p['clause_type'].upper()} — from {p['source_document']}, page {p['page']}]
{p['text'][:800]}
---
"""

    prompt = CONTRACT_DRAFTING_PROMPT.format(
        contract_type=state["contract_type"],
        structured_inputs=json.dumps(state["structured_inputs"], indent=2),
        precedent_clauses=precedent_text or "No precedent clauses retrieved.",
        policy_constraints=DEFAULT_POLICY_CONSTRAINTS,
        graph_conflicts=state.get("graph_conflicts", ""),
    )

    try:
        result = await generate_structured(prompt, schema={}, use_pro=True, temperature=0.15, feature_name="contract_draft")
        state["draft"] = result
    except Exception as e:
        logger.error("draft_generation.failed", error=str(e))
        state["draft"] = {
            "title": f"DRAFT {state['contract_type'].upper()}",
            "clauses": [],
            "issues": [{"severity": "high", "description": f"Draft generation failed: {str(e)}", "clause_affected": None}],
            "defined_terms": [],
            "missing_clauses": state["required_clauses"],
        }
    return state


def build_draft_workflow() -> Any:
    graph = StateGraph(DraftState)
    graph.add_node("determine_clauses", determine_required_clauses)
    graph.add_node("retrieve_precedent", retrieve_precedent_clauses)
    graph.add_node("check_conflicts", check_graph_conflicts)
    graph.add_node("generate_draft", generate_draft)

    graph.set_entry_point("determine_clauses")
    graph.add_edge("determine_clauses", "retrieve_precedent")
    graph.add_edge("retrieve_precedent", "check_conflicts")
    graph.add_edge("check_conflicts", "generate_draft")
    graph.add_edge("generate_draft", END)

    return graph.compile()


draft_workflow = build_draft_workflow()
