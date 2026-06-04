"""
sharepoint_ingestion_service.py — Legal Ticketing System

Service for ingesting tickets, events, and attachments from SharePoint via Power Automate webhooks.
"""
import logging
from typing import Optional
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.ticket import Ticket, Attachment
from app.models.message import Message
from app.models.user import User

log = logging.getLogger(__name__)

async def process_request(session: AsyncSession, payload: dict) -> Ticket:
    """
    Process a Request payload from SharePoint.
    UPSERT logic based on request_id.
    """
    request_id = payload.get("requestId")
    subject = payload.get("subject", "No Subject")
    # conversation_id = payload.get("conversationId")
    # entity = payload.get("entity")

    if not request_id:
        raise ValueError("requestId is required")

    # Check if exists
    existing = await session.exec(select(Ticket).where(Ticket.request_id == request_id))
    ticket = existing.first()

    if ticket:
        # Update existing
        ticket.title = subject
        log.info(f"[SharePoint] Updated Ticket for request_id={request_id}")
    else:
        # Create new
        ticket = Ticket(
            title=subject,
            request_id=request_id,
            status="open",
            priority="medium"
        )
        session.add(ticket)
        log.info(f"[SharePoint] Created Ticket for request_id={request_id}")

    await session.commit()
    await session.refresh(ticket)
    return ticket


async def process_event(session: AsyncSession, payload: dict) -> Optional[Message]:
    """
    Process an Event payload from SharePoint (appends a message).
    """
    request_id = payload.get("requestId")
    sender_email = payload.get("sender", "").lower()
    body = payload.get("body", "")
    timestamp_str = payload.get("timestamp")

    if not request_id:
        raise ValueError("requestId is required")

    # Find ticket
    existing = await session.exec(select(Ticket).where(Ticket.request_id == request_id))
    ticket = existing.first()
    if not ticket:
        log.error(f"[SharePoint] Cannot append event, Ticket not found for request_id={request_id}")
        raise ValueError(f"Ticket not found for request_id={request_id}")

    # Resolve user
    user = None
    if sender_email:
        user_result = await session.exec(select(User).where(User.email == sender_email))
        user = user_result.first()

    if not user:
        # We need a user ID for the foreign key. Fall back to super_admin or log error.
        admin_result = await session.exec(select(User).where(User.role == "super_admin"))
        user = admin_result.first()
        if not user:
            log.error(f"[SharePoint] No user found for {sender_email} and no super_admin fallback.")
            raise ValueError(f"User not found for sender={sender_email}")
        log.warning(f"[SharePoint] Sender {sender_email} not found, falling back to admin user {user.email}")

    # Parse timestamp if available, else use current
    timestamp = datetime.utcnow()
    if timestamp_str:
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass

    # Simple duplicate protection: Check if message with same content and timestamp exists
    existing_msg = await session.exec(
        select(Message).where(
            Message.ticket_id == ticket.id,
            Message.content == body,
            Message.source == "sharepoint"
        )
    )
    if existing_msg.first():
        log.info(f"[SharePoint] Duplicate event dropped for request_id={request_id}")
        return existing_msg.first()

    msg = Message(
        ticket_id=ticket.id,
        sender_id=user.id,
        content=body,
        source="sharepoint",
        timestamp=timestamp
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    
    log.info(f"[SharePoint] Appended event to ticket {ticket.id} (request_id={request_id})")
    return msg


async def process_attachment(session: AsyncSession, payload: dict) -> Attachment:
    """
    Process an Attachment payload from SharePoint.
    """
    request_id = payload.get("requestId")
    file_name = payload.get("fileName")
    file_url = payload.get("fileUrl")
    entity = payload.get("entity")

    if not request_id or not file_name or not file_url:
        raise ValueError("requestId, fileName, and fileUrl are required")

    # Find ticket
    existing = await session.exec(select(Ticket).where(Ticket.request_id == request_id))
    ticket = existing.first()
    if not ticket:
        log.error(f"[SharePoint] Cannot attach file, Ticket not found for request_id={request_id}")
        raise ValueError(f"Ticket not found for request_id={request_id}")

    # Idempotent check (same file_url)
    existing_att = await session.exec(
        select(Attachment).where(
            Attachment.ticket_id == ticket.id,
            Attachment.file_url == file_url
        )
    )
    if existing_att.first():
        log.info(f"[SharePoint] Duplicate attachment dropped for request_id={request_id}")
        return existing_att.first()

    att = Attachment(
        ticket_id=ticket.id,
        file_name=file_name,
        file_url=file_url,
        entity=entity
    )
    session.add(att)
    await session.commit()
    await session.refresh(att)

    log.info(f"[SharePoint] Attached {file_name} to ticket {ticket.id} (request_id={request_id})")
    return att
