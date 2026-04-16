"""
Document Intelligence LangGraph workflow.
Deterministic retrieval → reranking → grounded answer generation.
"""
import json
from typing import Any, Dict, List, Optional, TypedDict
from langgraph.graph import StateGraph, END

from app.ai.gemini_client import generate_structured, embed_query
from app.ai.prompts import (
    DOCUMENT_INTELLIGENCE_PROMPT,
    RERANKING_PROMPT,
    QUERY_REWRITING_PROMPT,
)
from app.core.vector_store import hybrid_search
from app.core.graph_db import get_cross_document_conflicts
from app.core.logging import get_logger

logger = get_logger("chat_workflow")


class ChatState(TypedDict):
    question: str
    rewritten_question: str
    scope_filter: Dict[str, Any]
    folder_names: List[str]
    document_names: List[str]
    raw_chunks: List[Dict]
    reranked_chunks: List[Dict]
    graph_facts: str
    answer: Dict  # final structured answer


# ── Step 1: Rewrite query for better retrieval
async def rewrite_query(state: ChatState) -> ChatState:
    logger.info("workflow.rewrite_query.started", original_query=state["question"])
    prompt = QUERY_REWRITING_PROMPT.format(query=state["question"])
    try:
        result = await generate_structured(prompt, schema={}, feature_name="query_rewrite")
        state["rewritten_question"] = result.get("rewritten_query", state["question"])
        logger.info("workflow.rewrite_query.success", rewritten_query=state["rewritten_question"])
    except Exception as e:
        logger.warning("workflow.rewrite_query.failed", error=str(e))
        state["rewritten_question"] = state["question"]
    return state


# ── Step 2: Hybrid retrieval
async def retrieve_chunks(state: ChatState) -> ChatState:
    query_text = state["rewritten_question"]
    scope = state["scope_filter"]

    # Dense embedding
    dense_vec = await embed_query(query_text)

    # Sparse: simple TF approach — top N terms as indices
    # In production: use BM25/SPLADE encoder
    sparse_indices, sparse_values = _simple_sparse(query_text)

    # Build filter from scope
    filter_dict = {}
    if scope.get("folder_id"):
        filter_dict["folder_id"] = scope["folder_id"]
    if scope.get("document_id"):
        filter_dict["document_id"] = scope["document_id"]
    
    # Handle plural lists for hybrid/multi-selection
    if scope.get("folder_ids"):
        filter_dict["folder_id"] = scope["folder_ids"]
    if scope.get("document_ids"):
        filter_dict["document_id"] = scope["document_ids"]

    logger.debug("workflow.retrieve_chunks.searching", filter=filter_dict)
    chunks = await hybrid_search(
        dense_vector=dense_vec,
        sparse_indices=sparse_indices,
        sparse_values=sparse_values,
        filter_dict=filter_dict if filter_dict else None,
        limit=25,
    )
    logger.info("workflow.retrieve_chunks.completed", found_chunks=len(chunks))
    state["raw_chunks"] = chunks
    return state


# ── Step 3: Rerank
async def rerank_chunks(state: ChatState) -> ChatState:
    if not state["raw_chunks"]:
        state["reranked_chunks"] = []
        return state

    chunks_for_rerank = [
        {
            "chunk_id": c["id"],
            "text": c["payload"].get("text", "")[:500],
            "document": c["payload"].get("document_name", ""),
            "page": c["payload"].get("page_number", 0),
            "section": c["payload"].get("section", ""),
        }
        for c in state["raw_chunks"]
    ]

    prompt = RERANKING_PROMPT.format(
        question=state["question"],
        chunks_json=json.dumps(chunks_for_rerank, indent=2),
    )
    try:
        result = await generate_structured(prompt, schema={}, feature_name="doc_rerank")
        ranked = result.get("ranked_chunks", [])
        # Re-order raw_chunks by ranked order
        id_to_chunk = {c["id"]: c for c in state["raw_chunks"]}
        reranked = []
        for r in ranked[:10]:  # top 10 after reranking
            cid = r["chunk_id"]
            if cid in id_to_chunk:
                chunk = id_to_chunk[cid]
                chunk["relevance_score"] = r["relevance_score"]
                reranked.append(chunk)
        state["reranked_chunks"] = reranked
        logger.info("workflow.rerank_chunks.completed", top_k=len(reranked))
    except Exception as e:
        logger.warning("workflow.rerank.failed", error=str(e), exc_info=True)
        state["reranked_chunks"] = state["raw_chunks"][:10]
    return state


# ── Step 4: Fetch graph facts
async def fetch_graph_facts(state: ChatState) -> ChatState:
    scope = state["scope_filter"]
    folder_ids = list(scope.get("folder_ids", []) or [])
    # Support both singular and plural scope shapes.
    if scope.get("folder_id") and scope["folder_id"] not in folder_ids:
        folder_ids.append(scope["folder_id"])

    # If caller scoped only by document(s), infer folders from retrieved chunks.
    if not folder_ids:
        for chunk in state.get("reranked_chunks", []) or state.get("raw_chunks", []):
            payload = chunk.get("payload", {})
            fid = payload.get("folder_id")
            if fid and fid not in folder_ids:
                folder_ids.append(fid)

    if folder_ids:
        try:
            conflicts = get_cross_document_conflicts(folder_ids)
            state["graph_facts"] = json.dumps(conflicts) if conflicts else "No cross-document conflicts found."
        except Exception:
            state["graph_facts"] = "Graph lookup unavailable."
    else:
        state["graph_facts"] = "No graph scope specified."
    return state


# ── Step 5: Generate grounded answer
async def generate_answer(state: ChatState) -> ChatState:
    chunks = state["reranked_chunks"]
    if not chunks:
        state["answer"] = {
            "answer": "Insufficient evidence in the selected documents. No relevant chunks were retrieved. Please broaden your scope or add more documents.",
            "sources": [],
            "conflicts": [],
            "confidence": "low",
            "missing_evidence": "No relevant chunks retrieved from the selected scope.",
        }
        return state

    # Format retrieved chunks for prompt
    retrieved_text = ""
    for i, chunk in enumerate(chunks):
        p = chunk.get("payload", {})
        retrieved_text += f"""
[Chunk {i+1}]
Document: {p.get('document_name', 'Unknown')}
Folder: {p.get('folder_name', 'Unknown')}
Page: {p.get('page_number', '?')}
Section: {p.get('section', '')}
Clause: {p.get('clause_number', '')}
---
{p.get('text', '')}
"""

    prompt = DOCUMENT_INTELLIGENCE_PROMPT.format(
        retrieved_chunks=retrieved_text,
        graph_facts=state.get("graph_facts", ""),
        folder_names=", ".join(state["folder_names"]),
        document_names=", ".join(state["document_names"]),
        question=state["question"],
    )

    try:
        result = await generate_structured(prompt, schema={}, use_pro=True, feature_name="doc_analysis")
        state["answer"] = result
        logger.info("workflow.generate_answer.success")
    except Exception as e:
        logger.error("workflow.answer_generation.failed", error=str(e), exc_info=True)
        state["answer"] = {
            "answer": "An error occurred while generating the answer. Please try again.",
            "sources": [],
            "conflicts": [],
            "confidence": "low",
            "missing_evidence": str(e),
        }
    return state


def _simple_sparse(text: str):
    """
    Simple sparse representation: term hashes as indices.
    In production, replace with SPLADE or BM25 encoder.
    """
    words = text.lower().split()
    unique_words = list(set(words))
    indices = [abs(hash(w)) % 30000 for w in unique_words]
    values = [1.0] * len(indices)
    return indices, values


def build_chat_workflow() -> Any:
    """Build and compile the LangGraph chat workflow."""
    graph = StateGraph(ChatState)
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("retrieve_chunks", retrieve_chunks)
    graph.add_node("rerank_chunks", rerank_chunks)
    graph.add_node("fetch_graph_facts", fetch_graph_facts)
    graph.add_node("generate_answer", generate_answer)

    graph.set_entry_point("rewrite_query")
    graph.add_edge("rewrite_query", "retrieve_chunks")
    graph.add_edge("retrieve_chunks", "rerank_chunks")
    graph.add_edge("rerank_chunks", "fetch_graph_facts")
    graph.add_edge("fetch_graph_facts", "generate_answer")
    graph.add_edge("generate_answer", END)

    return graph.compile()


# Singleton workflow instance
chat_workflow = build_chat_workflow()
