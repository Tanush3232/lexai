"""
Contract draft models
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column


class ContractDraft(SQLModel, table=True):
    __tablename__ = "contract_drafts"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    contract_type: str  # NDA | CNF | software_license | lease | vendor | purchase_order
    title: str
    status: str = Field(default="draft")  # draft | in_review | approved | rejected
    inputs_json: str = Field(sa_column=Column(Text))  # structured form inputs as JSON
    content: Optional[str] = Field(default=None, sa_column=Column(Text))  # rich text HTML
    provenance_json: Optional[str] = Field(default=None, sa_column=Column(Text))  # clause provenance
    issues_json: Optional[str] = Field(default=None, sa_column=Column(Text))  # flagged issues
    approved_by: Optional[str] = Field(default=None, foreign_key="users.id")
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DraftCreate(SQLModel):
    contract_type: str
    title: str
    inputs: dict


class DraftRead(SQLModel):
    id: str
    contract_type: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class DraftDetail(DraftRead):
    content: Optional[str]
    provenance_json: Optional[str]
    issues_json: Optional[str]
