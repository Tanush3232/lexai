"""
Audit log model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: Optional[str] = Field(default=None, foreign_key="users.id", index=True)
    action: str  # upload | read | chat | draft | translate | approve | delete
    resource_type: str  # document | folder | draft | translation | chat
    resource_id: Optional[str] = None
    details: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditLogRead(SQLModel):
    id: str
    user_id: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    details: Optional[str]
    created_at: datetime
