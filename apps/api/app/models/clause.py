"""
Clause model — the atomic unit of the Document Intelligence system.
Every factual claim is grounded to a specific clause.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column


class Clause(SQLModel, table=True):
    __tablename__ = "clauses"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    doc_id: str = Field(foreign_key="documents.id", index=True)
    folder_id: str = Field(index=True)
    section_id: str = Field(index=True)       # e.g. "doc_abc_s3"
    section_heading: str = Field(default="")

    # Clause classification
    clause_type: str = Field(default="miscellaneous")
    # definition|obligation|right|condition|penalty|termination|warranty|
    # indemnity|representation|payment|liability|confidentiality|
    # data_protection|notice|miscellaneous

    # Content
    text: str = Field(sa_column=Column(Text))
    page_start: int = Field(default=1)
    page_end: int = Field(default=1)
    position_in_doc: int = Field(default=0)  # ordinal position

    # Structured metadata (stored as JSON strings)
    parties_involved: Optional[str] = Field(default="[]", sa_column=Column(Text))   # JSON array
    references: Optional[str] = Field(default="[]", sa_column=Column(Text))          # JSON array of clause_ids

    # Index tracking
    qdrant_indexed: bool = Field(default=False)
    es_indexed: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)


class ClauseRead(SQLModel):
    id: str
    doc_id: str
    folder_id: str
    section_id: str
    section_heading: str
    clause_type: str
    text: str
    page_start: int
    page_end: int
    position_in_doc: int
    parties_involved: Optional[str]
    references: Optional[str]
    created_at: datetime
