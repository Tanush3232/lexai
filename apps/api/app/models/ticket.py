"""
Legal Ticketing System — Ticket, TicketUser, EmailThread, EmailLog models

Ticket        → central entity for each legal support request
TicketUser    → many-to-many: user ↔ ticket with a role (creator|assignee|watcher)
EmailThread   → maps an external Outlook conversation ID to a ticket
EmailLog      → audit trail for every email event on a ticket
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column, UniqueConstraint


# ─── Ticket ──────────────────────────────────────────────────────────────────

class TicketBase(SQLModel):
    title: str
    status: str = Field(default="open")     # open | in_progress | closed
    priority: str = Field(default="medium")  # low | medium | high | urgent
    request_id: Optional[str] = Field(default=None, index=True, unique=True, description="External SharePoint RequestID")


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

class AttachmentBase(SQLModel):
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    file_name: str
    file_url: str
    entity: Optional[str] = Field(default=None)

class Attachment(AttachmentBase, table=True):
    __tablename__ = "attachments"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AttachmentRead(AttachmentBase):
    id: str
    created_at: datetime

