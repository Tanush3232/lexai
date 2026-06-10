"""
sharepoint_ingestion_service.py — Legal Ticketing System

Handles all database writes from Power Automate webhook payloads.

Three public coroutines:
  process_request    → UPSERT Ticket from SharePoint Requests List
  process_event      → INSERT Message from SharePoint Events List
  process_attachment → UPSERT Attachment from SharePoint LegalAttachments Library

Design principles:
  - Idempotent: repeated webhook calls with same IDs produce no duplicates.
  - Non-lossy: if the sender email does not map to a LexAI user we fall back
    to a super_admin account so that no conversation is silently dropped.
  - All SharePoint column values are persisted verbatim so the frontend can
    render them without further API calls.
"""
import logging
from typing import Optional
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlmodel import select

from app.models.ticket import Ticket, Attachment
from app.models.message import Message
from app.models.user import User
from app.models.ticket import TicketUser
from app.core.config import settings

log = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO-8601 timestamp string into a naive UTC datetime.
    Returns None silently if ts is blank or unparseable.
    """
    if not ts:
        return None
    try:
        # Replace Z suffix so fromisoformat works on Python < 3.11
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # Normalise to naive UTC for DB storage
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, AttributeError):
        log.warning("[SharePoint] Could not parse timestamp %r — falling back to utcnow()", ts)
        return None


async def _resolve_user(session: AsyncSession, email: Optional[str]) -> Optional[User]:
    """
    Attempt to resolve an email address to a LexAI User row.
    Returns None if not found; callers decide the fallback strategy.
    """
    if not email:
        return None
    # Case-insensitive: lower() both sides so "Vivek.Kumar@X.com" matches "vivek.kumar@x.com"
    addr = email.lower().strip()
    result = await session.exec(select(User).where(func.lower(User.email) == addr))
    return result.first()


async def _get_admin_fallback(session: AsyncSession) -> User:
    """
    Return the first active super_admin as a last-resort sender.
    Raises ValueError when the database has no super_admin (mis-configured deployment).
    """
    result = await session.exec(select(User).where(User.role == "super_admin"))
    admin = result.first()
    if not admin:
        raise ValueError("No super_admin user found — cannot ingest event without a valid sender.")
    return admin


async def _sync_watchers_from_emails(
    session: AsyncSession,
    ticket_id: str,
    email_fields: list,
    exclude_emails: Optional[list] = None,
) -> None:
    """
    For every semicolon-separated (or pipe-separated) email address in
    *email_fields*, if there is a matching active LexAI User, insert a
    TicketUser(role='watcher') row.

    SharePoint sends addresses as semicolons: "a@x.com;b@y.com"
    We also accept pipe-separated for backward compatibility.

    exclude_emails: list of lowercase addresses to skip (e.g. the system
    mailbox that receives everything but is not a real participant).

    Idempotent: skips addresses already in ticket_users for this ticket.
    """
    skip_set = {e.lower().strip() for e in (exclude_emails or [])}

    for field_val in email_fields:
        if not field_val:
            continue
        # Normalise separators: treat both ; and | as delimiters
        normalised = field_val.replace("|", ";")
        for raw_email in normalised.split(";"):
            addr = raw_email.strip().lower()
            if not addr or addr in skip_set:
                continue
            # Case-insensitive lookup — func.lower() on DB side, addr already lowercased
            u_result = await session.exec(select(User).where(func.lower(User.email) == addr))
            found_user = u_result.first()
            if not found_user:
                continue
            # Check if already a participant for this ticket
            tu_result = await session.exec(
                select(TicketUser).where(
                    TicketUser.ticket_id == ticket_id,
                    TicketUser.user_id == found_user.id,
                )
            )
            if tu_result.first():
                continue   # already present, skip
            tu = TicketUser(ticket_id=ticket_id, user_id=found_user.id, role="watcher")
            session.add(tu)
            log.info(
                "[SharePoint] Auto-added %s as watcher on ticket %s (from to/cc/bcc)",
                addr, ticket_id,
            )
    await session.commit()



# ─── process_request ─────────────────────────────────────────────────────────

async def process_request(session: AsyncSession, payload: dict) -> Ticket:
    """
    UPSERT a Ticket from a SharePoint Requests List payload.

    SharePoint columns consumed:
      requestId       → Ticket.request_id    (unique UPSERT key)
      subject         → Ticket.title
      conversationId  → Ticket.conversation_id
      entity          → Ticket.entity

    Optional email-recipient fields (semicolon-separated, SharePoint format):
      toEmails, ccEmails, bccEmails  → auto-add matching LexAI users as watchers
      The system mailbox (EMAIL_USER) is automatically excluded.

    Returns the created or updated Ticket.
    Raises ValueError when requestId or subject are missing.
    """
    request_id: Optional[str] = payload.get("requestId", "").strip() or None
    subject: str = (payload.get("subject") or "No Subject").strip()
    conversation_id: Optional[str] = payload.get("conversationId") or None
    entity: Optional[str] = payload.get("entity") or None
    to_emails: Optional[str] = payload.get("toEmails") or None
    cc_emails: Optional[str] = payload.get("ccEmails") or None
    bcc_emails: Optional[str] = payload.get("bccEmails") or None

    if not request_id:
        raise ValueError("requestId is required and must not be blank.")
    if not subject:
        raise ValueError("subject is required and must not be blank.")

    # Try to find an existing ticket keyed on request_id
    result = await session.exec(select(Ticket).where(Ticket.request_id == request_id))
    ticket = result.first()

    if ticket:
        # ── Update existing ──────────────────────────────────────────────────
        changed = False
        if ticket.title != subject:
            ticket.title = subject
            changed = True
        if conversation_id and ticket.conversation_id != conversation_id:
            ticket.conversation_id = conversation_id
            changed = True
        if entity and ticket.entity != entity:
            ticket.entity = entity
            changed = True
        if changed:
            ticket.updated_at = datetime.utcnow()
            session.add(ticket)
        log.info("[SharePoint] Updated Ticket %s (request_id=%s)", ticket.id, request_id)
    else:
        # ── Create new ───────────────────────────────────────────────────────
        ticket = Ticket(
            title=subject,
            request_id=request_id,
            conversation_id=conversation_id,
            entity=entity,
            status="open",
            priority="medium",
        )
        session.add(ticket)
        log.info("[SharePoint] Created Ticket (request_id=%s)", request_id)

    await session.commit()
    await session.refresh(ticket)

    # ── Auto-sync watchers from To / CC / BCC ─────────────────────────────────
    # Any LexAI user found in these fields is automatically added as a watcher
    # so the ticket appears under their "Involved In" tab.
    # The system mailbox is excluded — it is not a real participant.
    _system_email = [settings.EMAIL_USER] if settings.EMAIL_USER else []
    await _sync_watchers_from_emails(
        session, ticket.id,
        [to_emails, cc_emails, bcc_emails],
        exclude_emails=_system_email,
    )

    return ticket


# ─── process_event ───────────────────────────────────────────────────────────

async def process_event(session: AsyncSession, payload: dict) -> Optional[Message]:
    """
    INSERT a Message from a SharePoint Events List payload.

    SharePoint columns consumed:
      requestId        → resolves to ticket_id via Ticket.request_id
      eventId          → Message.event_id            (SP Events row ID)
      messageId        → Message.message_id           (Outlook message ID)
      timestamp        → Message.timestamp
      sender           → Message.sender_email + resolved to Message.sender_id
      body             → Message.content
      direction        → Message.direction
      hasAttachment    → Message.has_attachment
      attachmentNames  → Message.attachment_names
      attachmentLinks  → Message.attachment_links
      toEmails         → Message.to_emails
      ccEmails         → Message.cc_emails
      bccEmails        → Message.bcc_emails

    Idempotency:
      1. If eventId is provided, an exact eventId match blocks duplicate inserts.
      2. Otherwise falls back to content + ticket_id + source deduplication.

    Returns the Message (existing if duplicate, newly created otherwise).
    Raises ValueError if the parent Ticket cannot be resolved.
    """
    request_id: Optional[str] = (payload.get("requestId") or "").strip() or None
    if not request_id:
        raise ValueError("requestId is required.")

    event_id: Optional[str] = payload.get("eventId") or None
    message_id: Optional[str] = payload.get("messageId") or None
    sender_email: str = (payload.get("sender") or "").lower().strip()
    body: str = (payload.get("body") or "").strip()
    timestamp_str: Optional[str] = payload.get("timestamp")
    direction: Optional[str] = payload.get("direction") or None
    has_attachment: Optional[bool] = payload.get("hasAttachment")
    attachment_names: Optional[str] = payload.get("attachmentNames") or None
    attachment_links: Optional[str] = payload.get("attachmentLinks") or None
    to_emails: Optional[str] = payload.get("toEmails") or None
    cc_emails: Optional[str] = payload.get("ccEmails") or None
    bcc_emails: Optional[str] = payload.get("bccEmails") or None

    # ── Resolve Ticket ────────────────────────────────────────────────────────
    result = await session.exec(select(Ticket).where(Ticket.request_id == request_id))
    ticket = result.first()
    if not ticket:
        log.error("[SharePoint] Cannot append event — Ticket not found (request_id=%s)", request_id)
        raise ValueError(f"Ticket not found for requestId={request_id!r}. Create the Request first.")

    # ── Idempotency guard ─────────────────────────────────────────────────────
    if event_id:
        dup_result = await session.exec(
            select(Message).where(
                Message.ticket_id == ticket.id,
                Message.event_id == event_id,
            )
        )
        duplicate = dup_result.first()
        if duplicate:
            log.info(
                "[SharePoint] Duplicate event dropped (ticket_id=%s, event_id=%s)",
                ticket.id, event_id,
            )
            return duplicate
    else:
        # Content-level dedup when eventId is absent
        dup_result = await session.exec(
            select(Message).where(
                Message.ticket_id == ticket.id,
                Message.content == body,
                Message.source == "sharepoint",
            )
        )
        duplicate = dup_result.first()
        if duplicate:
            log.info("[SharePoint] Duplicate event dropped by content (ticket_id=%s)", ticket.id)
            return duplicate

    # ── Resolve Sender ────────────────────────────────────────────────────────
    user = await _resolve_user(session, sender_email)
    if not user:
        user = await _get_admin_fallback(session)
        log.warning(
            "[SharePoint] Sender %r not found in LexAI — falling back to admin %s",
            sender_email, user.email,
        )

    # ── Parse Timestamp ───────────────────────────────────────────────────────
    timestamp = _parse_iso(timestamp_str) or datetime.utcnow()

    # ── Insert Message ────────────────────────────────────────────────────────
    msg = Message(
        ticket_id=ticket.id,
        sender_id=user.id,
        sender_email=sender_email or None,
        source="sharepoint",
        content=body,
        timestamp=timestamp,
        event_id=event_id,
        message_id=message_id,
        direction=direction,
        has_attachment=has_attachment,
        attachment_names=attachment_names,
        attachment_links=attachment_links,
        to_emails=to_emails,
        cc_emails=cc_emails,
        bcc_emails=bcc_emails,
    )
    session.add(msg)

    # Bump ticket updated_at so list views refresh ordering
    ticket.updated_at = datetime.utcnow()
    session.add(ticket)

    await session.commit()
    await session.refresh(msg)

    # ── Auto-sync watchers from To / CC / BCC ─────────────────────────────────
    _system_email = [settings.EMAIL_USER] if settings.EMAIL_USER else []
    await _sync_watchers_from_emails(
        session, ticket.id,
        [to_emails, cc_emails, bcc_emails],
        exclude_emails=_system_email,
    )

    log.info(
        "[SharePoint] Appended message to ticket %s (request_id=%s, event_id=%s)",
        ticket.id, request_id, event_id,
    )
    return msg


# ─── process_attachment ───────────────────────────────────────────────────────

async def process_attachment(session: AsyncSession, payload: dict) -> Attachment:
    """
    UPSERT an Attachment from a SharePoint LegalAttachments Library payload.

    SharePoint columns consumed:
      requestId    → resolves to ticket_id via Ticket.request_id
      fileName     → Attachment.file_name   (SP 'Name' column)
      fileUrl      → Attachment.file_url    (direct SP link)
      eventId      → Attachment.event_id    (SP LegalAttachments → EventID)
      documentId   → Attachment.document_id (SP LegalAttachments → DocumentID)
      entity       → Attachment.entity
      modifiedBy   → Attachment.modified_by (SP 'Modified By' display name)
      spModifiedAt → Attachment.sp_modified_at (SP 'Modified' timestamp)

    Idempotency keyed on (ticket_id, file_url).
    Returns the Attachment (existing if duplicate, newly created otherwise).
    Raises ValueError when required fields are missing or the parent Ticket
    cannot be resolved.
    """
    request_id: Optional[str] = (payload.get("requestId") or "").strip() or None
    file_name: Optional[str] = (payload.get("fileName") or "").strip() or None
    file_url: Optional[str] = (payload.get("fileUrl") or "").strip() or None

    if not request_id:
        raise ValueError("requestId is required.")
    if not file_name:
        raise ValueError("fileName is required.")
    if not file_url:
        raise ValueError("fileUrl is required.")

    event_id: Optional[str] = payload.get("eventId") or None
    document_id: Optional[str] = payload.get("documentId") or None
    entity: Optional[str] = payload.get("entity") or None
    modified_by: Optional[str] = payload.get("modifiedBy") or None
    sp_modified_at: Optional[datetime] = _parse_iso(payload.get("spModifiedAt"))

    # ── Resolve Ticket ────────────────────────────────────────────────────────
    result = await session.exec(select(Ticket).where(Ticket.request_id == request_id))
    ticket = result.first()
    if not ticket:
        log.error("[SharePoint] Cannot attach file — Ticket not found (request_id=%s)", request_id)
        raise ValueError(f"Ticket not found for requestId={request_id!r}. Create the Request first.")

    # ── Idempotency guard (keyed on file_url) ─────────────────────────────────
    dup_result = await session.exec(
        select(Attachment).where(
            Attachment.ticket_id == ticket.id,
            Attachment.file_url == file_url,
        )
    )
    existing_att = dup_result.first()
    if existing_att:
        log.info(
            "[SharePoint] Duplicate attachment dropped (ticket_id=%s, file_url=%s)",
            ticket.id, file_url,
        )
        return existing_att

    # ── Insert Attachment ─────────────────────────────────────────────────────
    att = Attachment(
        ticket_id=ticket.id,
        request_id=request_id,
        file_name=file_name,
        file_url=file_url,
        event_id=event_id,
        document_id=document_id,
        entity=entity,
        modified_by=modified_by,
        sp_modified_at=sp_modified_at,
    )
    session.add(att)

    # Bump ticket updated_at
    ticket.updated_at = datetime.utcnow()
    session.add(ticket)

    await session.commit()
    await session.refresh(att)
    log.info(
        "[SharePoint] Attached %r to ticket %s (request_id=%s, document_id=%s)",
        file_name, ticket.id, request_id, document_id,
    )
    return att
