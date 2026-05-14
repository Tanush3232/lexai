"""
ticket_ingestion_service.py — Legal Ticketing System

Core email-to-ticket ingestion pipeline.

Pipeline:
  1. validate_email_payload  — subject tag check + at-least-one-known-user check
  2. resolve_participants    — map email addresses → DB User objects
  3. ingest_email            — thread routing → create ticket OR append message

FORCE RULE:
  Admins (ops_admin, super_admin) and reviewers are ALWAYS added as watchers,
  even if they were not on the email.

DO NOT:
  - send emails
  - assign tickets
  - create users
"""
import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.user import User
from app.models.ticket import Ticket, TicketUser, EmailThread, EmailLog
from app.models.message import Message

log = logging.getLogger(__name__)

# Roles that must ALWAYS be added as watchers on every ticket
_FORCE_WATCHER_ROLES = {"ops_admin", "super_admin", "reviewer"}

# The subject tag that marks an email as a legal ticket request
TICKET_TAG = "[TICKET]"


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Validation
# ══════════════════════════════════════════════════════════════════════════════

def validate_email_payload(payload: dict) -> tuple[bool, str]:
    """
    Returns (True, "") if the email should be processed,
    or (False, reason) if it should be silently dropped.

    Rules (STRICT):
      1. subject must contain "[TICKET]" (case-insensitive)
      2. at least one known user must appear in to + cc + from
         — this is checked AFTER DB lookup in resolve_participants
    """
    subject: str = payload.get("subject", "")
    if TICKET_TAG.lower() not in subject.lower():
        return False, f"Subject missing '{TICKET_TAG}' tag — dropped"

    # Verify at least one address is present (deep check happens post-DB)
    all_addrs = _collect_addresses(payload)
    if not all_addrs:
        return False, "No recipients at all — dropped"

    return True, ""


def _collect_addresses(payload: dict) -> list[str]:
    """Flatten sender + to + cc into a deduplicated lowercase list."""
    addrs: set[str] = set()

    sender = payload.get("sender", {}).get("emailAddress", {}).get("address", "")
    if sender:
        addrs.add(sender.lower())

    for field in ("toRecipients", "ccRecipients"):
        for r in payload.get(field, []):
            addr = r.get("emailAddress", {}).get("address", "")
            if addr:
                addrs.add(addr.lower())

    return list(addrs)


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Participant resolution
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_participants(
    session: AsyncSession,
    payload: dict,
) -> dict:
    """
    Maps every email address in the payload to a DB User.

    Returns a dict:
    {
        "sender":       User | None,
        "to_users":     list[User],    # known To recipients
        "cc_users":     list[User],    # known CC recipients
        "force_watchers": list[User],  # admins + reviewers always added
        "any_known":    bool,          # True if at least one address resolved
    }

    NEVER creates users. Unknown addresses are silently ignored.
    """
    all_emails = _collect_addresses(payload)
    if not all_emails:
        return {"sender": None, "to_users": [], "cc_users": [], "force_watchers": [], "any_known": False}

    # Bulk-fetch all matching users in a single query
    result = await session.exec(
        select(User).where(User.email.in_(all_emails), User.is_active == True)  # noqa: E712
    )
    known_users: list[User] = list(result.all())
    known_by_email: dict[str, User] = {u.email.lower(): u for u in known_users}

    # Sender
    sender_addr = payload.get("sender", {}).get("emailAddress", {}).get("address", "").lower()
    sender_user = known_by_email.get(sender_addr)

    # To recipients (known only)
    to_addrs = [
        r.get("emailAddress", {}).get("address", "").lower()
        for r in payload.get("toRecipients", [])
    ]
    to_users = [known_by_email[a] for a in to_addrs if a in known_by_email]

    # CC recipients (known only)
    cc_addrs = [
        r.get("emailAddress", {}).get("address", "").lower()
        for r in payload.get("ccRecipients", [])
    ]
    cc_users = [known_by_email[a] for a in cc_addrs if a in known_by_email]

    # Force-watcher fetch — ALL active admins + reviewers in the system
    force_result = await session.exec(
        select(User).where(User.role.in_(list(_FORCE_WATCHER_ROLES)), User.is_active == True)  # noqa: E712
    )
    force_watchers: list[User] = list(force_result.all())

    any_known = bool(sender_user or to_users or cc_users)

    return {
        "sender": sender_user,
        "to_users": to_users,
        "cc_users": cc_users,
        "force_watchers": force_watchers,
        "any_known": any_known,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Thread routing + ticket / message creation
# ══════════════════════════════════════════════════════════════════════════════

async def ingest_email(
    session: AsyncSession,
    payload: dict,
    participants: dict,
) -> Optional[Ticket]:
    """
    Main ingestion entry point called after validation and participant resolution.

    Thread routing logic:
      - IF conversationId already exists in email_threads → append a message
      - ELSE → create a new ticket + email_thread + first message

    Returns the Ticket that was created or updated. Returns None if aborted.
    """
    conversation_id: str = payload.get("conversationId", "")
    subject: str = payload.get("subject", "No Subject")
    body_content: str = (
        payload.get("body", {}).get("content", "")
        or payload.get("bodyPreview", "")
    )

    sender_user: Optional[User] = participants["sender"]

    # ── Abort if no known users at all ────────────────────────────────────────
    if not participants["any_known"]:
        log.warning("[Ingestion] No known users in email — dropping. subject=%r", subject)
        return None

    # ── Resolve existing thread ───────────────────────────────────────────────
    existing_thread: Optional[EmailThread] = None
    if conversation_id:
        thread_result = await session.exec(
            select(EmailThread).where(EmailThread.conversation_id == conversation_id)
        )
        existing_thread = thread_result.first()

    # ── CASE A: existing thread → append message ──────────────────────────────
    if existing_thread:
        ticket = await session.get(Ticket, existing_thread.ticket_id)
        if not ticket:
            log.error("[Ingestion] Orphan EmailThread %s — ticket %s missing", existing_thread.id, existing_thread.ticket_id)
            return None

        msg = Message(
            ticket_id=ticket.id,
            sender_id=sender_user.id if sender_user else _fallback_sender_id(participants),
            content=body_content,
            source="email",
            email_thread_id=existing_thread.id,
        )
        session.add(msg)
        await _log_email_event(session, ticket.id, sender_user, payload, "received")
        await session.commit()
        log.info("[Ingestion] Appended message to ticket %s (thread %s)", ticket.id, conversation_id)
        return ticket

    # ── CASE B: new ticket ────────────────────────────────────────────────────
    ticket = Ticket(title=subject, status="open", priority="medium")
    if sender_user:
        ticket.created_by = sender_user.id
    session.add(ticket)
    await session.flush()  # get ticket.id before relations

    # Email thread record
    if conversation_id:
        thread = EmailThread(conversation_id=conversation_id, ticket_id=ticket.id)
        session.add(thread)
        await session.flush()
        thread_id = thread.id
    else:
        thread_id = None

    # First message
    msg = Message(
        ticket_id=ticket.id,
        sender_id=sender_user.id if sender_user else _fallback_sender_id(participants),
        content=body_content,
        source="email",
        email_thread_id=thread_id,
    )
    session.add(msg)

    # Ticket participants
    await _add_ticket_participants(session, ticket.id, sender_user, participants)

    # Audit log
    await _log_email_event(session, ticket.id, sender_user, payload, "received")

    await session.commit()
    await session.refresh(ticket)
    log.info(
        "[Ingestion] New ticket created — id=%s title=%r conversation=%s",
        ticket.id, ticket.title, conversation_id,
    )
    return ticket


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _fallback_sender_id(participants: dict) -> str:
    """If sender is unknown, fall back to the first known participant."""
    for key in ("to_users", "cc_users", "force_watchers"):
        if participants.get(key):
            return participants[key][0].id
    raise RuntimeError("No known user available to use as sender fallback")


async def _add_ticket_participants(
    session: AsyncSession,
    ticket_id: str,
    sender_user: Optional[User],
    participants: dict,
) -> None:
    """
    Insert TicketUser rows for all participants.

    Roles:
      sender → creator
      to/cc  → watcher
      force_watchers (admins/reviewers) → watcher (always, no duplicates)
    """
    seen: set[str] = set()

    async def _upsert(user_id: str, role: str) -> None:
        if user_id in seen:
            return
        seen.add(user_id)
        # Check if already exists (e.g., force_watcher was also the sender)
        existing = await session.exec(
            select(TicketUser).where(
                TicketUser.ticket_id == ticket_id,
                TicketUser.user_id == user_id,
            )
        )
        if existing.first():
            return
        session.add(TicketUser(ticket_id=ticket_id, user_id=user_id, role=role))

    if sender_user:
        await _upsert(sender_user.id, "creator")

    for u in participants.get("to_users", []):
        await _upsert(u.id, "watcher")

    for u in participants.get("cc_users", []):
        await _upsert(u.id, "watcher")

    # Force-add admins/reviewers ALWAYS
    for u in participants.get("force_watchers", []):
        await _upsert(u.id, "watcher")

    await session.flush()


async def _log_email_event(
    session: AsyncSession,
    ticket_id: str,
    user: Optional[User],
    payload: dict,
    event_type: str,
) -> None:
    """Write an EmailLog entry for audit."""
    log_meta = {
        "subject": payload.get("subject"),
        "from": payload.get("sender", {}).get("emailAddress", {}).get("address"),
        "conversationId": payload.get("conversationId"),
        "hasAttachments": payload.get("hasAttachments", False),
    }
    entry = EmailLog(
        ticket_id=ticket_id,
        user_id=user.id if user else None,
        event_type=event_type,
        email_metadata=json.dumps(log_meta),
    )
    session.add(entry)
    await session.flush()
