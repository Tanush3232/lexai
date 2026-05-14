"""
Web Search models — SQLModel tables and Pydantic schemas
for the LexAI Legal Web Search feature.
"""
import uuid
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column
from enum import Enum


class SearchMode(str, Enum):
    FAST = "fast"
    PRO = "pro"
    DEEP = "deep"


class SearchStatus(str, Enum):
    PENDING = "pending"
    SEARCHING = "searching"
    COMPLETED = "completed"
    FAILED = "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Database Tables
# ─────────────────────────────────────────────────────────────────────────────

class WebSearchSession(SQLModel, table=True):
    __tablename__ = "web_search_sessions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    query: str = Field(sa_column=Column(Text))
    mode: str = Field(default=SearchMode.FAST.value)
    status: str = Field(default=SearchStatus.PENDING.value)
    answer_summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    full_answer: Optional[str] = Field(default=None, sa_column=Column(Text))       # markdown answer
    reasoning_steps: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON array
    search_plan: Optional[str] = Field(default=None, sa_column=Column(Text))      # JSON
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WebSearchCitation(SQLModel, table=True):
    __tablename__ = "web_search_citations"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="web_search_sessions.id", index=True)
    source_name: str
    url: str = Field(sa_column=Column(Text))
    snippet: Optional[str] = Field(default=None, sa_column=Column(Text))
    domain: str = Field(default="")
    relevance_score: float = Field(default=0.0)
    jurisdiction: Optional[str] = Field(default=None)
    citation_type: Optional[str] = Field(default=None)  # act | judgement | circular | notification | book
    turn_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Schemas
# ─────────────────────────────────────────────────────────────────────────────

class WebSearchRequest(SQLModel):
    query: str
    mode: SearchMode = SearchMode.FAST
    plan: Optional[List[str]] = None
    session_id: Optional[str] = None

class PlanRequest(SQLModel):
    query: str
    mode: str = "deep"


class CitationRead(SQLModel):
    id: str
    source_name: str
    url: str
    snippet: Optional[str] = None
    domain: str
    relevance_score: float
    jurisdiction: Optional[str] = None
    citation_type: Optional[str] = None
    turn_index: int = 0


class ReasoningStep(SQLModel):
    step: str
    detail: Optional[str] = None
    timestamp: Optional[str] = None


class WebSearchSessionRead(SQLModel):
    id: str
    query: str
    mode: str
    status: str
    answer_summary: Optional[str] = None
    full_answer: Optional[str] = None
    reasoning_steps: Optional[List[ReasoningStep]] = None
    search_plan: Optional[dict] = None
    citations: Optional[List[CitationRead]] = None
    error_message: Optional[str] = None
    created_at: datetime


class WebSearchSessionListItem(SQLModel):
    id: str
    query: str
    mode: str
    status: str
    answer_summary: Optional[str] = None
    created_at: datetime
