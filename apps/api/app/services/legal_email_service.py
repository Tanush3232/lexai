"""
emailService.py (Legal Ticketing) — Legal Ticketing System

Stub service for all outbound email operations via Outlook / Office 365.

Transport is SMTP + STARTTLS using credentials from settings:
    settings.EMAIL_HOST  → smtp.office365.com
    settings.EMAIL_PORT  → 587
    settings.EMAIL_USER  → app.info@adventz.com
    settings.EMAIL_PASS  → from .env

Business logic and HTML templates will be implemented in a follow-up.
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession


async def send_ticket_created(
    session: AsyncSession,
    ticket_id: str,
    to_emails: list[str],
):
    """
    Notify assigned counsel/reviewer that a new ticket has been created.
    Logs an EmailLog(event_type='sent') on success.
    """
    raise NotImplementedError


async def send_ticket_status_changed(
    session: AsyncSession,
    ticket_id: str,
    to_emails: list[str],
    new_status: str,
):
    """
    Notify stakeholders when a ticket's status changes.
    Logs an EmailLog(event_type='sent') on success.
    """
    raise NotImplementedError


async def send_message_notification(
    session: AsyncSession,
    ticket_id: str,
    message_id: str,
    to_emails: list[str],
):
    """
    Notify watchers/assignees when a new message is posted on a ticket.
    Logs an EmailLog(event_type='sent') on success.
    """
    raise NotImplementedError


async def ingest_email_reply(
    session: AsyncSession,
    conversation_id: str,
    sender_email: str,
    content: str,
    raw_headers: Optional[dict] = None,
):
    """
    Process an inbound email reply from Outlook webhook.
    - Looks up EmailThread by conversation_id → resolves ticket
    - Creates a Message(source='email') on that ticket
    - Logs an EmailLog(event_type='received')
    Returns: Message
    """
    raise NotImplementedError


async def log_email_event(
    session: AsyncSession,
    ticket_id: str,
    user_id: Optional[str],
    event_type: str,
    email_metadata: Optional[dict] = None,
):
    """
    Write a raw EmailLog entry (used internally by all send_* functions).
    event_type: sent | received | bounced | opened | failed
    """
    raise NotImplementedError
