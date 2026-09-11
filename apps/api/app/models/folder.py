"""
Folder model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class FolderBase(SQLModel):
    name: str
    description: Optional[str] = None
    owner_id: str = Field(foreign_key="users.id", index=True)
    is_global: bool = Field(default=False)
    parent_id: Optional[str] = Field(default=None, foreign_key="folders.id", index=True)


class Folder(FolderBase, table=True):
    __tablename__ = "folders"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FolderCreate(SQLModel):
    name: str
    description: Optional[str] = None
    is_global: bool = False
    parent_id: Optional[str] = None


class FolderRead(FolderBase):
    id: str
    created_at: datetime
    document_count: int = 0
