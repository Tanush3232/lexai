"""
Legal Act models — for the India Code act ingestion system.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text


class LegalAct(SQLModel, table=True):
    __tablename__ = "legal_acts"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    title: str
    normalized_title: Optional[str] = None
    act_number: Optional[str] = None
    enactment_date: Optional[str] = None
    ministry: Optional[str] = None
    handle_id: Optional[str] = Field(default=None, unique=True)
    indiacode_url: Optional[str] = None
    bitstream_url: Optional[str] = None
    minio_bucket: str = "legal-acts"
    minio_path: Optional[str] = None
    pdf_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    has_text_layer: Optional[bool] = None
    total_pages: Optional[int] = None
    ingestion_status: str = Field(default="pending", index=True)
    # pending | searching | downloading | ocr_processing | completed | failed | not_found | needs_review | pdf_unavailable
    confidence_score: Optional[float] = None
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    # Manual review tracking
    review_status: Optional[str] = Field(default=None, index=True)  # none | manually_reviewed | flagged
    review_locked: bool = Field(default=False)  # True = permanently locked after tick
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LegalActSeedLog(SQLModel, table=True):
    __tablename__ = "legal_acts_seed_log"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    raw_name: Optional[str] = None
    matched_title: Optional[str] = None
    handle_id: Optional[str] = None
    confidence_score: Optional[float] = None
    status: Optional[str] = None
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Read schemas ─────────────────────────────────────────────


class LegalActRead(SQLModel):
    id: str
    title: str
    act_number: Optional[str]
    enactment_date: Optional[str]
    ministry: Optional[str]
    ingestion_status: str
    minio_path: Optional[str]
    has_text_layer: Optional[bool]
    confidence_score: Optional[float]
    error_message: Optional[str]
    review_status: Optional[str]
    review_locked: bool
    created_at: datetime


class LegalActDetail(LegalActRead):
    normalized_title: Optional[str]
    handle_id: Optional[str]
    indiacode_url: Optional[str]
    bitstream_url: Optional[str]
    minio_bucket: str
    pdf_text: Optional[str]
    total_pages: Optional[int]
    review_status: Optional[str]
    review_locked: bool
    updated_at: datetime


class LegalActStatusResponse(SQLModel):
    id: str
    ingestion_status: str
    error_message: Optional[str]


class LegalActCandidate(SQLModel):
    title: str
    handle_id: str
    act_number: Optional[str]
    enactment_date: Optional[str]
    confidence_score: float
