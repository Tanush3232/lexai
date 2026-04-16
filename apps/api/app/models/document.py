"""
Document model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, JSON, Column
from sqlalchemy import Text, UniqueConstraint


class DocumentBase(SQLModel):
    name: str
    folder_id: str = Field(foreign_key="folders.id", index=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    content_type: str = "application/pdf"
    size_bytes: int = 0
    language: Optional[str] = None
    page_count: int = 0
    doc_type: Optional[str] = None  # NDA | contract | policy | etc.


class Document(DocumentBase, table=True):
    __tablename__ = "documents"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    storage_key: str  # MinIO object key
    status: str = Field(default="uploaded")  # uploaded | parsing | indexed | error
    parsed_at: Optional[datetime] = None
    indexed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # Parsed metadata stored as JSON
    extracted_metadata: Optional[str] = Field(default=None, sa_column=Column(Text))


class DocumentRead(DocumentBase):
    id: str
    status: str
    page_count: int
    language: Optional[str]
    created_at: datetime
    storage_key: str


class DocumentChunk(SQLModel, table=True):
    """Metadata mirror of Qdrant chunks stored in Postgres for audit."""
    __tablename__ = "document_chunks"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    folder_id: str = Field(index=True)
    chunk_index: int
    chunk_type: str  # section | clause | table | paragraph
    section: Optional[str] = None
    clause_number: Optional[str] = None
    page_number: int = 0
    text: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DocumentTree(SQLModel, table=True):
    """
    Page-Index tree for a document.
    Stores AI-generated executive summary + hierarchical section tree as JSON.
    Built during ingestion (async, non-blocking) and used by:
      1. Document Viewer Modal (left-panel summary + section nav)
      2. Page-Index RAG agent path (large doc navigation)
    """
    __tablename__ = "document_trees"
    __table_args__ = (UniqueConstraint("document_id", name="uq_document_tree_doc_id"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    # AI-generated executive summary of the full document
    summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    # JSON string: list of TreeNode dicts — see TREE_BUILD_PROMPT schema
    tree_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    # pending | ready | error
    status: str = Field(default="pending")
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
