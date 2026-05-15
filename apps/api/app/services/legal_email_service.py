"""
legal_email_service.py — Legal Ticketing System

Service for all outbound email operations via Outlook / Office 365.
Transport is SMTP + STARTTLS.
"""
import logging
import json
from typing import Optional
from email.message import EmailMessage
import aiosmtplib

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from app.core.config import settings
from app.models.ticket import EmailLog, Ticket
from app.models.user import User

log = logging.getLogger(__name__)

async def _send_smtp_email(to_emails: list[str], subject: str, html_content: str) -> bool:
    if not settings.EMAIL_USER or not settings.EMAIL_PASS:
        log.warning("[Email] SMTP credentials not set, skipping email.")
        return False

    msg = EmailMessage()
    msg["From"] = settings.EMAIL_USER
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    msg.set_content(html_content, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.EMAIL_HOST,
            port=settings.EMAIL_PORT,
            username=settings.EMAIL_USER,
            password=settings.EMAIL_PASS,
            start_tls=True,
        )
        return True
    except Exception as e:
        log.error("[Email] Failed to send email to %s: %s", to_emails, e)
        return False

async def log_email_event(
    session: AsyncSession,
    ticket_id: str,
    user_id: Optional[str],
    event_type: str,
    email_metadata: Optional[dict] = None,
):
    entry = EmailLog(
        ticket_id=ticket_id,
        user_id=user_id,
        event_type=event_type,
        email_metadata=json.dumps(email_metadata) if email_metadata else None,
    )
    session.add(entry)
    await session.commit()

async def send_ticket_assignment(
    session: AsyncSession,
    ticket_id: str,
    to_emails: list[str],
    assigned_by_name: str,
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        return

    subject = f"You have been assigned to Ticket: {ticket.title}"
    link = f"{settings.FRONTEND_URL}/dashboard/tickets/{ticket.id}"
    
    html = f"""
    <h2>Ticket Assignment</h2>
    <p><strong>{assigned_by_name}</strong> has assigned you to the following ticket:</p>
    <p><strong>Title:</strong> {ticket.title}</p>
    <p><strong>Status:</strong> {ticket.status}</p>
    <br/>
    <a href="{link}" style="padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px;">View Ticket in LexAI</a>
    """
    
    success = await _send_smtp_email(to_emails, subject, html)
    if success:
        await log_email_event(session, ticket_id, None, "sent", {"type": "assignment", "to": to_emails})

async def send_message_notification(
    session: AsyncSession,
    ticket_id: str,
    to_emails: list[str],
    sender_name: str,
    is_mention: bool = False
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        return

    subject_prefix = "[Mention] " if is_mention else "[New Message] "
    subject = f"{subject_prefix}Ticket: {ticket.title}"
    link = f"{settings.FRONTEND_URL}/dashboard/tickets/{ticket.id}"
    
    html = f"""
    <h2>New Activity on Ticket</h2>
    <p><strong>{sender_name}</strong> has {'mentioned you' if is_mention else 'posted a new message'} on:</p>
    <p><strong>Title:</strong> {ticket.title}</p>
    <br/>
    <a href="{link}" style="padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px;">View Chat in LexAI</a>
    """
    
    success = await _send_smtp_email(to_emails, subject, html)
    if success:
        await log_email_event(session, ticket_id, None, "sent", {"type": "mention" if is_mention else "notification", "to": to_emails})
