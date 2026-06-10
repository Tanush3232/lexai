"""
Legal Ticketing System — Ticket, TicketUser, EmailThread, EmailLog, Attachment models

Ticket        → central entity for each legal support request
TicketUser    → many-to-many: user ↔ ticket with a role (creator|assignee|watcher)
EmailThread   → maps an external Outlook conversation ID to a ticket
EmailLog      → audit trail for every email event on a ticket
Attachment    → file attached to a ticket, sourced from SharePoint LegalAttachments library

SharePoint column mapping:
  Requests List   → Ticket    (RequestID, ConversationID, Entity, Subject)
  Events List     → Message   (EventID, MessageID, Sender, Timestamp, Body, Direction,
                                HasAttachment, AttachmentNames, AttachmentLinks,
                                ToEmails, CCEmails, BccEmails)
  LegalAttachments→ Attachment (RequestID, EventID, DocumentID, Entity, Name,
                                Modified, Modified By)
"""
import uuid
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column, UniqueConstraint


# ─── Ticket ──────────────────────────────────────────────────────────────────

class TicketBase(SQLModel):
    title: str
    status: str = Field(default="open")      # open | in_progress | closed
    priority: str = Field(default="medium")   # low | medium | high | urgent

    # ── SharePoint Requests List columns ──────────────────────────────────────
    request_id: Optional[str] = Field(
        default=None, index=True, unique=True,
        description="SharePoint Requests List → RequestID (e.g. REQ-45)"
    )
    conversation_id: Optional[str] = Field(
        default=None, index=True,
        description="SharePoint Requests List → ConversationID (Outlook thread)"
    )
    entity: Optional[str] = Field(
        default=None,
        description="SharePoint Requests List → Entity (e.g. INPUT / OUTPUT / ZIL)"
    )


class Ticket(TicketBase, table=True):
    __tablename__ = "tickets"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_by: Optional[str] = Field(default=None, foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TicketCreate(TicketBase):
    pass


class TicketRead(TicketBase):
    id: str
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


# ─── TicketUser ───────────────────────────────────────────────────────────────

class TicketUser(SQLModel, table=True):
    """
    Explicit join table: links users to tickets with a declared role.
    A user may only hold one role per ticket (unique constraint).
    """
    __tablename__ = "ticket_users"
    __table_args__ = (UniqueConstraint("ticket_id", "user_id", name="uq_ticket_users_ticket_user"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    role: str  # creator | assignee | watcher
    assigned_at: datetime = Field(default_factory=datetime.utcnow)


class TicketUserRead(SQLModel):
    id: str
    ticket_id: str
    user_id: str
    role: str
    assigned_at: datetime


# ─── EmailThread ──────────────────────────────────────────────────────────────

class EmailThread(SQLModel, table=True):
    """
    Maps an external email conversation (e.g. Outlook conversation_id) to a
    ticket so all replies in that thread are routed to the same ticket.
    """
    __tablename__ = "email_threads"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    conversation_id: str = Field(index=True, unique=True)  # External Outlook conversation ID
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EmailThreadRead(SQLModel):
    id: str
    conversation_id: str
    ticket_id: str
    created_at: datetime


# ─── EmailLog ─────────────────────────────────────────────────────────────────

class EmailLog(SQLModel, table=True):
    """
    Audit trail for every email event associated with a ticket.
    event_type examples: sent | received | bounced | opened | failed
    """
    __tablename__ = "email_logs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    user_id: Optional[str] = Field(default=None, foreign_key="users.id", index=True)
    event_type: str  # sent | received | bounced | opened | failed
    email_metadata: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON — subject, to, from, etc.
    sent_at: datetime = Field(default_factory=datetime.utcnow)


class EmailLogRead(SQLModel):
    id: str
    ticket_id: str
    user_id: Optional[str]
    event_type: str
    email_metadata: Optional[str]
    sent_at: datetime


# ─── Attachment ───────────────────────────────────────────────────────────────
# Maps to SharePoint LegalAttachments document library columns:
#   Name, Modified, ModifiedBy, RequestID, EventID, DocumentID, Entity

class AttachmentBase(SQLModel):
    # Foreign keys
    ticket_id: str = Field(foreign_key="tickets.id", index=True)

    # ── SharePoint LegalAttachments Library columns ────────────────────────────
    file_name: str = Field(
        description="SharePoint LegalAttachments → Name (filename with extension)"
    )
    file_url: str = Field(
        description="SharePoint direct link to the document"
    )
    request_id: Optional[str] = Field(
        default=None, index=True,
        description="SharePoint LegalAttachments → RequestID (links to Requests List)"
    )
    event_id: Optional[str] = Field(
        default=None, index=True,
        description="SharePoint LegalAttachments → EventID (links to Events List row)"
    )
    document_id: Optional[str] = Field(
        default=None,
        description="SharePoint LegalAttachments → DocumentID (unique doc identifier)"
    )
    entity: Optional[str] = Field(
        default=None,
        description="SharePoint LegalAttachments → Entity (e.g. INPUT / ZIL)"
    )
    modified_by: Optional[str] = Field(
        default=None,
        description="SharePoint LegalAttachments → Modified By (uploader display name)"
    )


class Attachment(AttachmentBase, table=True):
    __tablename__ = "attachments"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # 'modified' tracks the last SharePoint-side modification timestamp
    sp_modified_at: Optional[datetime] = Field(
        default=None,
        description="SharePoint LegalAttachments → Modified (last modified timestamp from SP)"
    )


class AttachmentRead(AttachmentBase):
    id: str
    created_at: datetime
    sp_modified_at: Optional[datetime]
