"""
Neo4j graph database client and operations.
Stores document structure, clause logic, and cross-references.
"""
from typing import List, Dict, Any, Optional, Tuple
from neo4j import GraphDatabase, Driver
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("graph_db")

_driver: Optional[Driver] = None


def get_graph_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


def init_graph_db():
    """Test connection and create indexes."""
    try:
        driver = get_graph_driver()
        with driver.session() as session:
            # Constraints and indexes
            session.run("CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
            session.run("CREATE CONSTRAINT folder_id IF NOT EXISTS FOR (f:Folder) REQUIRE f.id IS UNIQUE")
            session.run("CREATE CONSTRAINT clause_id IF NOT EXISTS FOR (c:Clause) REQUIRE c.id IS UNIQUE")
            session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
            logger.info("graph_db.initialized")
    except Exception as e:
        logger.error("graph_db.init_failed", error=str(e))
        # Non-fatal exception for startup


def create_document_node(doc_id: str, name: str, folder_id: str, metadata: Dict):
    driver = get_graph_driver()
    with driver.session() as session:
        session.run(
            """
            MERGE (d:Document {id: $id})
            SET d.name = $name, d.folder_id = $folder_id,
                d.language = $language, d.doc_type = $doc_type,
                d.created_at = $created_at
            MERGE (f:Folder {id: $folder_id})
            MERGE (f)-[:CONTAINS]->(d)
            """,
            id=doc_id,
            name=name,
            folder_id=folder_id,
            language=metadata.get("language", "unknown"),
            doc_type=metadata.get("doc_type", "unknown"),
            created_at=metadata.get("created_at", ""),
        )


def create_clause_node_batch(doc_id: str, clauses: List[Dict]):
    """Bulk create clause nodes and link to document."""
    if not clauses:
        return
    driver = get_graph_driver()
    with driver.session() as session:
        session.run(
            """
            MATCH (d:Document {id: $doc_id})
            UNWIND $clauses AS c
            MERGE (clause:Clause {id: c.id})
            SET clause.clause_type = c.clause_type,
                clause.section_id = c.section_id,
                clause.section_heading = c.section_heading,
                clause.page_start = c.page_start,
                clause.text_preview = c.text_preview
            MERGE (d)-[:CONTAINS]->(clause)
            """,
            doc_id=doc_id,
            clauses=clauses,
        )


def create_reference_edges(ref_pairs: List[Tuple[str, str]]):
    """Bulk create :REFERENCES edges between clauses."""
    if not ref_pairs:
        return
    driver = get_graph_driver()
    edges = [{"from_id": src, "to_id": dst} for src, dst in ref_pairs]
    with driver.session() as session:
        session.run(
            """
            UNWIND $edges AS edge
            MATCH (src:Clause {id: edge.from_id})
            MATCH (dst:Clause {id: edge.to_id})
            MERGE (src)-[:REFERENCES]->(dst)
            """,
            edges=edges,
        )


def create_entity_nodes(doc_id: str, entities: List[Dict]):
    if not entities:
        return
    driver = get_graph_driver()
    with driver.session() as session:
        for entity in entities:
            session.run(
                """
                MERGE (e:Entity {id: $id})
                SET e.name = $name, e.entity_type = $entity_type
                WITH e
                MATCH (d:Document {id: $doc_id})
                MERGE (d)-[:MENTIONS]->(e)
                """,
                id=entity["id"],
                name=entity["name"],
                entity_type=entity.get("entity_type", "UNKNOWN"),
                doc_id=doc_id,
            )


def get_document_graph(doc_id: str) -> Dict:
    """Get full document graph for reasoning."""
    driver = get_graph_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Document {id: $doc_id})
            OPTIONAL MATCH (d)-[:CONTAINS]->(c:Clause)
            OPTIONAL MATCH (d)-[:MENTIONS]->(e:Entity)
            RETURN d, collect(DISTINCT c) as clauses, collect(DISTINCT e) as entities
            """,
            doc_id=doc_id,
        )
        record = result.single()
        if not record:
            return {}
        return {
            "document": dict(record["d"]),
            "clauses": [dict(c) for c in record["clauses"] if c],
            "entities": [dict(e) for e in record["entities"] if e],
        }


def get_cross_document_conflicts(folder_ids: List[str]) -> List[Dict]:
    """Find conflicting clauses across documents in given folders."""
    driver = get_graph_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (d:Document)-[:CONTAINS]->(c:Clause)
            WHERE d.folder_id IN $folder_ids
            WITH c.clause_type as ctype, collect({doc: d.name, text_preview: c.text_preview}) as clauses
            WHERE size(clauses) > 1
            RETURN ctype, clauses
            LIMIT 50
            """,
            folder_ids=folder_ids,
        )
        return [{"clause_type": r["ctype"], "occurrences": r["clauses"]} for r in result]

def delete_document_graph(doc_id: str):
    """Delete document node, all its clause nodes, and orphaned entity nodes."""
    driver = get_graph_driver()
    with driver.session() as session:
        # Delete document, clauses, and detach/delete entity nodes that are only
        # referenced by this document (to avoid orphaning nodes used by other docs).
        session.run(
            """
            MATCH (d:Document {id: $doc_id})
            OPTIONAL MATCH (d)-[:MENTIONS]->(e:Entity)
            WHERE NOT EXISTS { MATCH (other:Document)-[:MENTIONS]->(e) WHERE other.id <> $doc_id }
            DETACH DELETE e
            """,
            doc_id=doc_id,
        )
        session.run(
            """
            MATCH (d:Document {id: $doc_id})
            OPTIONAL MATCH (d)-[:CONTAINS]->(c:Clause)
            DETACH DELETE d, c
            """,
            doc_id=doc_id,
        )
        logger.info("graph_db.document_deleted", doc_id=doc_id)
