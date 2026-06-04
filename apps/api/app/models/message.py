"""
Legal Ticketing System — Message model

Each message belongs to one ticket. The `source` field distinguishes between
emails ingested from Outlook and messages typed directly in the app UI.
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import Text, Column


class MessageBase(SQLModel):
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    sender_id: str = Field(foreign_key="users.id", index=True)
    source: str = Field(default="app")  # app | email | sharepoint
    content: str = Field(sa_column=Column(Text))


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
    content: str
    source: str
    timestamp: datetime
    email_thread_id: Optional[str]
