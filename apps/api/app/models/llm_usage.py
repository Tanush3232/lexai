"""
LLM Usage Log model — tracks every call made to any LLM provider.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class LLMUsageLog(SQLModel, table=True):
    __tablename__ = "llm_usage_logs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    provider: str = Field(index=True)           # gemini | openai | claude
    model: str = Field(index=True)              # e.g. gemini-2.5-pro
    feature_name: str = Field(index=True)       # translation | doc_analysis | contract_draft | …
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    cost: float = Field(default=0.0)            # USD
    user_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class LLMUsageLogRead(SQLModel):
    id: str
    provider: str
    model: str
    feature_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    user_id: Optional[str]
    created_at: datetime
