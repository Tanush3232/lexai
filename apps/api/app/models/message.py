"""
Legal Ticketing System — Message model

Each message belongs to one ticket. The `source` field distinguishes between
messages typed in the app UI and events ingested from SharePoint.

SharePoint Events List column mapping:
  RequestID       → ticket.request_id (FK resolved to ticket_id)
  EventID         → event_id          (SharePoint Events list row ID)
  MessageID       → message_id        (SharePoint/Outlook message ID)
  Timestamp       → timestamp
  Sender          → sender_email      (resolved to sender_id FK; stored raw for audit)
  Body            → content
  Direction       → direction         ('inbound' | 'outbound')
  HasAttachment   → has_attachment
  AttachmentNames → attachment_names  (pipe-separated list, e.g. "doc1.pdf|doc2.pdf")
  AttachmentLinks → attachment_links  (pipe-separated SharePoint direct links)
  ToEmails        → to_emails         (pipe-separated recipient list)
  CCEmails        → cc_emails
  BccEmails       → bcc_emails
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column, Boolean


class MessageBase(SQLModel):
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    sender_id: str = Field(foreign_key="users.id", index=True)
    source: str = Field(default="app")  # app | sharepoint
    content: str = Field(sa_column=Column(Text))

    # ── SharePoint Events List columns ────────────────────────────────────────
    event_id: Optional[str] = Field(
        default=None, index=True,
        description="SharePoint Events List → EventID (unique row identifier in SP)"
    )
    message_id: Optional[str] = Field(
        default=None, index=True,
        description="SharePoint Events List → MessageID (Outlook/Exchange message ID)"
    )
    sender_email: Optional[str] = Field(
        default=None,
        description="SharePoint Events List → Sender (raw email; retained for audit even if user deleted)"
    )
    direction: Optional[str] = Field(
        default=None,
        description="SharePoint Events List → Direction ('inbound' | 'outbound')"
    )
    has_attachment: Optional[bool] = Field(
        default=None,
        description="SharePoint Events List → HasAttachment"
    )
    attachment_names: Optional[str] = Field(
        default=None, sa_column=Column(Text),
        description="SharePoint Events List → AttachmentNames (pipe-separated)"
    )
    attachment_links: Optional[str] = Field(
        default=None, sa_column=Column(Text),
        description="SharePoint Events List → AttachmentLinks (pipe-separated SharePoint URLs)"
    )
    to_emails: Optional[str] = Field(
        default=None, sa_column=Column(Text),
        description="SharePoint Events List → ToEmails (pipe-separated)"
    )
    cc_emails: Optional[str] = Field(
        default=None, sa_column=Column(Text),
        description="SharePoint Events List → CCEmails (pipe-separated)"
    )
    bcc_emails: Optional[str] = Field(
        default=None, sa_column=Column(Text),
        description="SharePoint Events List → BccEmails (pipe-separated)"
    )


class Message(MessageBase, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Optional: link back to an email thread when source == "email"
    email_thread_id: Optional[str] = Field(default=None, foreign_key="email_threads.id", index=True)


class MessageCreate(SQLModel):
    ticket_id: str
    content: str
    source: str = "app"


class MessageRead(SQLModel):
    id: str
    ticket_id: str
    sender_id: str
    sender_email: Optional[str]
    content: str
    source: str
    direction: Optional[str]
    has_attachment: Optional[bool]
    attachment_names: Optional[str]
    attachment_links: Optional[str]
    to_emails: Optional[str]
    cc_emails: Optional[str]
    bcc_emails: Optional[str]
    event_id: Optional[str]
    message_id: Optional[str]
    timestamp: datetime
    email_thread_id: Optional[str]
