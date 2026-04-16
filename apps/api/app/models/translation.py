"""
Translation job model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column


class TranslationJob(SQLModel, table=True):
    __tablename__ = "translation_jobs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    source_language: str
    target_language: str
    status: str = Field(default="pending")  # pending | processing | done | error
    original_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    translated_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    structure_map: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON
    uncertainty_flags: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON
    error_message: Optional[str] = None
    celery_task_id: Optional[str] = None  # Track for revocation
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    # Save-translated-document flow
    saved_storage_key: Optional[str] = None   # MinIO object key of saved PDF
    saved_document_id: Optional[str] = None   # Document row ID created on save
    saved_at: Optional[datetime] = None


class TranslationRequest(SQLModel):
    document_id: str
    target_language: str = "English"


class TranslationRead(SQLModel):
    id: str
    document_id: str
    source_language: str
    target_language: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]


class TranslationResult(TranslationRead):
    original_text: Optional[str]
    translated_text: Optional[str]
    structure_map: Optional[str]
    uncertainty_flags: Optional[str]
    error_message: Optional[str] = None
    saved_storage_key: Optional[str] = None
    saved_document_id: Optional[str] = None
    saved_at: Optional[datetime] = None
