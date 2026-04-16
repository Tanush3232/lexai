"""
Qdrant vector store initialization and operations.
Handles dense vector storage and retrieval. Combines with ES for hybrid search.
"""
from typing import List, Optional, Dict, Any
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
    NamedVector,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.elasticsearch import keyword_search

logger = get_logger("vector_store")

_client: Optional[AsyncQdrantClient] = None


def get_vector_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.QDRANT_URL, timeout=10)
    return _client


async def init_vector_store():
    """Create Qdrant collection if not exists. Only for dense vectors now."""
    client = get_vector_client()
    col = settings.QDRANT_COLLECTION
    try:
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        if col not in names:
            await client.create_collection(
                collection_name=col,
                vectors_config={
                    "dense": VectorParams(
                        size=settings.EMBEDDING_DIMENSION,
                        distance=Distance.COSINE,
                    )
                },
            )
            logger.info("vector_store.collection_created", collection=col)
        else:
            logger.info("vector_store.collection_exists", collection=col)
    except Exception as e:
        logger.error("vector_store.init_error", error=str(e), exc_info=True)
        logger.warning(
            "vector_store.unavailable",
            hint="Qdrant is unreachable. Clause indexing and retrieval will fail until resolved.",
        )


async def upsert_clauses(points: List[PointStruct]):
    """Upsert clause dense embeddings into Qdrant."""
    client = get_vector_client()
    await client.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=points,
        wait=True,
    )


async def search_dense(
    dense_vector: List[float],
    filter_doc_ids: Optional[List[str]] = None,
    filter_folder_ids: Optional[List[str]] = None,
    filter_clause_types: Optional[List[str]] = None,
    limit: int = 20,
) -> List[Dict]:
    """Pure dense vector search."""
    client = get_vector_client()
    conditions = []
    
    if filter_doc_ids:
        conditions.append(FieldCondition(key="doc_id", match=MatchAny(any=filter_doc_ids)))
    if filter_folder_ids:
        conditions.append(FieldCondition(key="folder_id", match=MatchAny(any=filter_folder_ids)))
    if filter_clause_types:
        conditions.append(FieldCondition(key="clause_type", match=MatchAny(any=filter_clause_types)))
        
    query_filter = Filter(must=conditions) if conditions else None

    try:
        hits = await client.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=NamedVector(name="dense", vector=dense_vector),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            {
                "id": str(hit.id),
                "score": hit.score,
                "payload": hit.payload,
            }
            for hit in hits
        ]
    except Exception as e:
        logger.error("vector_store.search_dense_error", error=str(e))
        return []


async def hybrid_search_clauses(
    query: str,
    dense_vector: List[float],
    filter_doc_ids: Optional[List[str]] = None,
    filter_folder_ids: Optional[List[str]] = None,
    filter_clause_types: Optional[List[str]] = None,
    limit: int = 20,
) -> List[Dict]:
    """
    Perform hybrid retrieval combing Qdrant (dense) + Elasticsearch (BM25 keyword).
    Returns top-k merged hits.
    """
    import asyncio
    
    # Run both searches concurrently
    dense_task = search_dense(
        dense_vector, filter_doc_ids, filter_folder_ids, filter_clause_types, limit=limit*2
    )
    keyword_task = keyword_search(
        query, filter_doc_ids, filter_folder_ids, filter_clause_types, limit=limit*2
    )
    
    dense_hits, keyword_hits = await asyncio.gather(dense_task, keyword_task)
    
    return _reciprocal_rank_fusion(dense_hits, keyword_hits, limit=limit)


def _reciprocal_rank_fusion(dense_hits: List[Dict], keyword_hits: List[Dict], k: int = 60, limit: int = 20) -> List[Dict]:
    """Combine dense and keyword results using RRF scoring."""
    scores: Dict[str, float] = {}
    hit_map: Dict[str, Any] = {}

    for rank, hit in enumerate(dense_hits):
        sid = str(hit["id"])
        scores[sid] = scores.get(sid, 0) + 1.0 / (k + rank + 1)
        hit_map[sid] = hit["payload"]

    for rank, hit in enumerate(keyword_hits):
        sid = str(hit["clause_id"])
        # BM25 scores can be large, RRF uses rank so we just ignore raw ES score
        scores[sid] = scores.get(sid, 0) + 1.0 / (k + rank + 1)
        # ES highlights are useful, but payload structure might slightly differ, 
        # but we indexed the exact same fields in both
        if sid not in hit_map:
            # Reconstruct payload exactly as it was indexed
            payload = {k_val: v_val for k_val, v_val in hit.items() if k_val not in ("score", "highlight")}
            hit_map[sid] = payload

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {
            "id": sid,
            "score": score,
            "payload": hit_map[sid],
        }
        for sid, score in ranked[:limit]
    ]


# Backward compatibility shim for legacy workflows (draft_workflow.py, chat_workflow.py)
# that call hybrid_search with old positional/keyword arguments.
async def hybrid_search(*args, **kwargs):
    """
    Compat shim: maps legacy keyword args to the new hybrid_search_clauses signature.
    Legacy callers pass: dense_vector, sparse_indices, sparse_values, filter_dict, limit.
    """
    if "dense_vector" in kwargs and "query" not in kwargs:
        kwargs["query"] = "legacy search fallback"
        logger.warning(
            "vector_store.hybrid_search_compat_fallback",
            hint="Caller should provide query= explicitly to get accurate BM25 results.",
        )
    # Drop sparse params — not used by the current dense+BM25 implementation
    kwargs.pop("sparse_indices", None)
    kwargs.pop("sparse_values", None)

    # Map filter_dict to typed filter params
    if "filter_dict" in kwargs:
        fdict = kwargs.pop("filter_dict") or {}
        if "document_id" in fdict:
            val = fdict["document_id"]
            kwargs["filter_doc_ids"] = val if isinstance(val, list) else [val]
        if "folder_id" in fdict:
            val = fdict["folder_id"]
            kwargs["filter_folder_ids"] = val if isinstance(val, list) else [val]
        # chunk_type / clause_type filter
        if "chunk_type" in fdict:
            val = fdict["chunk_type"]
            kwargs["filter_clause_types"] = val if isinstance(val, list) else [val]

    return await hybrid_search_clauses(*args, **kwargs)


async def delete_document_clauses(doc_id: str):
    """Delete all clauses for a document."""
    client = get_vector_client()
    try:
        await client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
    except Exception as e:
        logger.error("vector_store.delete_error", doc_id=doc_id, error=str(e))
