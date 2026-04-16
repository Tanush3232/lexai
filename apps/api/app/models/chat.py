"""
Chat session and message models
"""
import uuid
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column
from enum import Enum


class ScopeType(str, Enum):
    SINGLE_FOLDER = "single_folder"
    MULTIPLE_FOLDERS = "multiple_folders"
    FILES_SINGLE_FOLDER = "files_single_folder"
    FILES_MULTI_FOLDER = "files_multi_folder"
    HYBRID = "hybrid"


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    title: str = Field(default="New Chat")
    scope_type: str  # ScopeType enum value
    scope_folder_ids: str = Field(default="")  # comma-separated folder ids
    scope_document_ids: str = Field(default="")  # comma-separated document ids
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="chat_sessions.id", index=True)
    role: str  # user | assistant
    content: str = Field(sa_column=Column(Text))
    sources: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON
    retrieval_trace: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatSessionCreate(SQLModel):
    title: Optional[str] = "New Chat"
    scope_type: ScopeType
    scope_folder_ids: List[str] = []
    scope_document_ids: List[str] = []


class ChatMessageCreate(SQLModel):
    content: str


class SourceRef(SQLModel):
    document_id: str
    document_name: str
    folder_name: str
    page_number: int
    section: Optional[str] = None
    clause_id: Optional[str] = None     # Added for the new architecture
    clause_number: Optional[str] = None
    snippet: str
    relevance_score: float


class ChatMessageRead(SQLModel):
    id: str
    role: str
    content: str
    sources: Optional[List[SourceRef]] = None
    created_at: datetime
