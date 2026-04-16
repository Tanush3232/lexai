"""
Elasticsearch client — BM25 keyword search for clause retrieval.
Complements Qdrant dense vector search in the hybrid retrieval engine.

NOTE: elasticsearch-py >= 8.0 deprecated the `body=` keyword argument.
All queries use explicit keyword arguments instead.
"""
from typing import Any, Dict, List, Optional
from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("elasticsearch")

_client: Optional[AsyncElasticsearch] = None

INDEX_MAPPINGS = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "legal_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "stop", "stemmer"],
                }
            },
            "filter": {
                "stemmer": {"type": "stemmer", "language": "english"}
            },
        },
    },
    "mappings": {
        "properties": {
            "clause_id":       {"type": "keyword"},
            "doc_id":          {"type": "keyword"},
            "folder_id":       {"type": "keyword"},
            "section_id":      {"type": "keyword"},
            "section_heading": {"type": "text", "analyzer": "legal_analyzer"},
            "document_name":   {"type": "keyword"},
            "folder_name":     {"type": "keyword"},
            "clause_type":     {"type": "keyword"},
            "text":            {"type": "text", "analyzer": "legal_analyzer"},
            "page_start":      {"type": "integer"},
            "page_end":        {"type": "integer"},
            "position_in_doc": {"type": "integer"},
        }
    },
}


def get_es_client() -> AsyncElasticsearch:
    global _client
    if _client is None:
        _client = AsyncElasticsearch(
            hosts=[settings.ELASTICSEARCH_URL],
            request_timeout=30,
            retry_on_timeout=True,
            max_retries=3,
        )
    return _client


async def init_elasticsearch():
    """Create clause index if not exists."""
    client = get_es_client()
    idx = settings.ELASTICSEARCH_INDEX
    try:
        exists = await client.indices.exists(index=idx)
        if not exists:
            await client.indices.create(
                index=idx,
                mappings=INDEX_MAPPINGS["mappings"],
                settings=INDEX_MAPPINGS["settings"],
            )
            logger.info("elasticsearch.index_created", index=idx)
        else:
            logger.info("elasticsearch.index_exists", index=idx)
    except Exception as e:
        logger.error("elasticsearch.init_error", error=str(e), exc_info=True)
        logger.warning(
            "elasticsearch.unavailable",
            hint="Elasticsearch is unreachable. Keyword search will fail until resolved.",
        )


async def bulk_index_clauses(clause_docs: List[Dict]) -> int:
    """Bulk index clause documents into Elasticsearch."""
    client = get_es_client()
    idx = settings.ELASTICSEARCH_INDEX

    actions = [
        {
            "_index": idx,
            "_id": doc["clause_id"],
            "_source": doc,
        }
        for doc in clause_docs
    ]

    try:
        ok, errors = await async_bulk(client, actions, raise_on_error=False)
        if errors:
            logger.warning("elasticsearch.bulk_errors", count=len(errors), first_error=errors[0] if errors else None)
        logger.info("elasticsearch.bulk_indexed", count=ok)
        return ok
    except Exception as e:
        logger.error("elasticsearch.bulk_error", error=str(e), exc_info=True)
        return 0


async def keyword_search(
    query: str,
    filter_doc_ids: Optional[List[str]] = None,
    filter_folder_ids: Optional[List[str]] = None,
    filter_clause_types: Optional[List[str]] = None,
    limit: int = 20,
) -> List[Dict]:
    """BM25 keyword search with optional filters."""
    client = get_es_client()
    idx = settings.ELASTICSEARCH_INDEX

    must_filters = []
    if filter_doc_ids:
        must_filters.append({"terms": {"doc_id": filter_doc_ids}})
    if filter_folder_ids:
        must_filters.append({"terms": {"folder_id": filter_folder_ids}})
    if filter_clause_types:
        must_filters.append({"terms": {"clause_type": filter_clause_types}})

    es_query: Dict[str, Any] = {
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": ["text^3", "section_heading^2", "document_name"],
                            "type": "best_fields",
                            "operator": "or",
                            "minimum_should_match": "60%",
                        }
                    }
                ],
                "filter": must_filters,
            }
        },
        "size": limit,
        "_source": True,
        "highlight": {
            "fields": {"text": {"fragment_size": 200, "number_of_fragments": 1}},
            "pre_tags": [""],
            "post_tags": [""],
        },
    }

    try:
        response = await client.search(
            index=idx,
            query=es_query["query"],
            size=limit,
            source=True,
            highlight=es_query["highlight"],
        )
        hits = []
        for hit in response["hits"]["hits"]:
            src = hit["_source"]
            src["score"] = hit["_score"]
            src["highlight"] = hit.get("highlight", {}).get("text", [""])[0]
            hits.append(src)
        return hits
    except Exception as e:
        logger.error("elasticsearch.search_error", query=query[:100], error=str(e), exc_info=True)
        return []


async def delete_document_clauses_es(doc_id: str):
    """Delete all clauses for a document from Elasticsearch."""
    client = get_es_client()
    try:
        await client.delete_by_query(
            index=settings.ELASTICSEARCH_INDEX,
            query={"term": {"doc_id": doc_id}},
        )
        logger.info("elasticsearch.delete_success", doc_id=doc_id)
    except Exception as e:
        logger.error("elasticsearch.delete_error", doc_id=doc_id, error=str(e), exc_info=True)
