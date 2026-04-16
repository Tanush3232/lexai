"""
User model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class UserBase(SQLModel):
    email: str = Field(unique=True, index=True)
    full_name: str
    title: Optional[str] = None
    organization: Optional[str] = None
    role: str = Field(default="legal_team")  # legal_team | ops_admin | reviewer | super_admin
    is_active: bool = Field(default=True)


class User(UserBase, table=True):
    __tablename__ = "users"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserCreate(UserBase):
    password: str


class UserRead(UserBase):
    id: str
    created_at: datetime


class UserLogin(SQLModel):
    email: str
    password: str
