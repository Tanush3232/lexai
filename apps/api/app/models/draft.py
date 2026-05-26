"""
Contract draft models — enterprise agentic drafting system.

DraftSession   — orchestration session (one per drafting flow)
ContractDraft  — the actual saved draft document (block-based)
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration Session (one per new-draft flow)
# ─────────────────────────────────────────────────────────────────────────────

class DraftSession(SQLModel, table=True):
    """
    Tracks the multi-stage drafting orchestration.
    Stage flow:
      analyzing_intent → needs_input? → redacting → researching
      → sources_ready → (user approves) → assembling → complete
    """
    __tablename__ = "draft_sessions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)

    # Raw user prompt (pre-redaction)
    original_prompt: str = Field(sa_column=Column(Text))

    # Stage tracking
    stage: str = Field(default="analyzing_intent")
    # analyzing_intent | needs_input | redacting | researching |
    # sources_ready | awaiting_approval | assembling | complete | failed

    # Context collected from user follow-up answers (JSON)
    context_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Redaction token map: {"{{COMPANY_1}}": "Acme Corp"} — kept for restore
    redaction_map_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Intent analysis result (JSON)
    intent_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Research results per agent (JSON)
    sources_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Approved source IDs (JSON list)
    approved_source_ids_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Questions asked to user (JSON list of {id, question, answer?})
    questions_json: Optional[str] = Field(default=None, sa_column=Column(Text))

    # Link to resulting draft
    draft_id: Optional[str] = Field(default=None, foreign_key="contract_drafts.id")

    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Contract Draft (the saved document)
# ─────────────────────────────────────────────────────────────────────────────

class ContractDraft(SQLModel, table=True):
    __tablename__ = "contract_drafts"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    session_id: Optional[str] = Field(default=None)  # FK to draft_sessions.id (not enforced — session may be deleted)

    contract_type: str  # NDA | CNF | software_license | lease | vendor | purchase_order
    title: str
    status: str = Field(default="draft")  # draft | in_review | approved | rejected

    inputs_json: str = Field(sa_column=Column(Text))          # structured form inputs as JSON
    content: Optional[str] = Field(default=None, sa_column=Column(Text))  # full HTML (assembled from blocks)
    blocks_json: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON list of ContractBlock dicts
    provenance_json: Optional[str] = Field(default=None, sa_column=Column(Text))  # clause provenance
    issues_json: Optional[str] = Field(default=None, sa_column=Column(Text))       # flagged issues
    sources_json: Optional[str] = Field(default=None, sa_column=Column(Text))      # approved research sources
    adversarial_report_json: Optional[str] = Field(default=None, sa_column=Column(Text))  # Phase 6.5 red-team findings

    approved_by: Optional[str] = Field(default=None, foreign_key="users.id")
    approved_at: Optional[datetime] = None
    export_path: Optional[str] = Field(default=None)  # MinIO path for .docx export

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class DraftSessionCreate(SQLModel):
    prompt: str


class DraftSessionRead(SQLModel):
    id: str
    stage: str
    intent_json: Optional[str]
    questions_json: Optional[str]
    sources_json: Optional[str]
    draft_id: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime


class ContextSubmit(SQLModel):
    answers: dict  # {question_id: answer_text}


class SourceApproval(SQLModel):
    approved_source_ids: list[str]


class DraftCreate(SQLModel):
    contract_type: str
    title: str
    inputs: dict


class DraftRead(SQLModel):
    id: str
    contract_type: str
    title: str
    status: str
    session_id: Optional[str]
    created_at: datetime
    updated_at: datetime


class DraftDetail(DraftRead):
    content: Optional[str]
    blocks_json: Optional[str]
    provenance_json: Optional[str]
    issues_json: Optional[str]
    sources_json: Optional[str]
    adversarial_report_json: Optional[str] = None
