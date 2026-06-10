"""
ticket_service.py — Legal Ticketing System

Business logic for managing tickets.
Enforces RBAC and participant visibility.

Ticket numbering:
  Tickets are numbered TKT-0001, TKT-0002, … in order of creation.
  The number is computed at query-time via a stable ordered ranking so that
  no additional DB column is needed. SharePoint requestId / conversationId are
  stored but never exposed to the frontend — only TKT-XXXX is shown.
"""
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func
from fastapi import HTTPException

from app.models.ticket import Ticket, TicketUser, EmailThread, Attachment
from app.models.message import Message
from app.models.user import User
from app.services.legal_email_service import send_ticket_assignment, send_message_notification

_ADMIN_ROLES = {"ops_admin", "super_admin", "reviewer"}


def _is_admin(user: User) -> bool:
    return user.role in _ADMIN_ROLES


def _fmt_ticket_number(n: int) -> str:
    """Return a human-readable, zero-padded ticket number: TKT-0001."""
    return f"TKT-{n:04d}"


async def _get_ticket_number_map(session: AsyncSession) -> Dict[str, int]:
    """
    Build a UUID → sequential-number mapping ordered by creation date.
    Older tickets receive lower numbers (stable assignment as long as
    rows are never deleted). O(N) — perfectly acceptable for legal ops volumes.
    """
    result = await session.exec(select(Ticket.id).order_by(Ticket.created_at.asc()))
    all_ids = result.all()
    return {tid: idx + 1 for idx, tid in enumerate(all_ids)}


async def check_ticket_access(session: AsyncSession, ticket_id: str, user: User) -> bool:
    """Check if user is allowed to access this ticket."""
    if _is_admin(user):
        return True
    result = await session.exec(
        select(TicketUser).where(
            TicketUser.ticket_id == ticket_id,
            TicketUser.user_id == user.id,
        )
    )
    return result.first() is not None


async def get_ticket_with_details(
    session: AsyncSession, ticket_id: str, current_user: User
) -> Dict[str, Any]:
    """
    Fetch ticket details, participants, messages, and attachments.
    Enforces visibility rules.
    Returns all SharePoint-sourced fields so the frontend can render them
    without additional API calls.
    """
    has_access = await check_ticket_access(session, ticket_id, current_user)
    if not has_access:
        raise HTTPException(status_code=403, detail="You do not have access to this ticket.")

    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    # ── Ticket number ─────────────────────────────────────────────────────────
    number_map = await _get_ticket_number_map(session)
    ticket_number = _fmt_ticket_number(number_map.get(ticket_id, 0))

    # ── Participants ──────────────────────────────────────────────────────────
    users_result = await session.exec(
        select(TicketUser, User)
        .join(User, TicketUser.user_id == User.id)
        .where(TicketUser.ticket_id == ticket_id)
    )
    participants = []
    for tu, u in users_result.all():
        participants.append(
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": tu.role,
                "assigned_at": tu.assigned_at,
            }
        )

    # ── Messages — all SharePoint Events List columns ─────────────────────────
    msgs_result = await session.exec(
        select(Message, User)
        .join(User, Message.sender_id == User.id)
        .where(Message.ticket_id == ticket_id)
        .order_by(Message.timestamp.asc())
    )
    messages = []
    for m, u in msgs_result.all():
        messages.append(
            {
                "id": m.id,
                "content": m.content,
                "source": m.source,           # "sharepoint" | "app"
                "timestamp": m.timestamp,
                "sender_id": u.id,
                "sender_name": u.full_name,
                # ── SharePoint Events List columns ────────────────────────────
                "sender_email": m.sender_email,
                "event_id": m.event_id,
                "message_id": m.message_id,
                "direction": m.direction,     # "inbound" | "outbound"
                "has_attachment": m.has_attachment,
                "attachment_names": m.attachment_names,   # pipe-separated
                "attachment_links": m.attachment_links,   # pipe-separated
                "to_emails": m.to_emails,                 # pipe-separated
                "cc_emails": m.cc_emails,                 # pipe-separated
                "bcc_emails": m.bcc_emails,               # pipe-separated
            }
        )

    # ── Attachments — all SharePoint LegalAttachments Library columns ─────────
    atts_result = await session.exec(
        select(Attachment)
        .where(Attachment.ticket_id == ticket_id)
        .order_by(Attachment.created_at.asc())
    )
    attachments = []
    for a in atts_result.all():
        attachments.append(
            {
                "id": a.id,
                "file_name": a.file_name,
                "file_url": a.file_url,
                "entity": a.entity,
                "created_at": a.created_at,
                # ── SharePoint LegalAttachments Library columns ───────────────
                "event_id": a.event_id,
                "document_id": a.document_id,
                "modified_by": a.modified_by,
                "sp_modified_at": a.sp_modified_at,
            }
        )

    ticket_dict = ticket.model_dump()
    ticket_dict["ticket_number"] = ticket_number

    return {
        "ticket": ticket_dict,
        "participants": participants,
        "messages": messages,
        "attachments": attachments,
    }


async def list_tickets(
    session: AsyncSession,
    current_user: User,
    view: str = "all",         # all | assigned | involved
    status: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    List tickets enforcing visibility rules.

    Admin / Reviewer → can see all tickets (view = "all")
    Counsel (legal_team) → only tickets they are a participant in

    Supports:
      - view: all | assigned | involved
      - status / priority filters
      - search: ILIKE on ticket title (server-side, case-insensitive)
    """
    query = select(Ticket)

    # ── Access control ────────────────────────────────────────────────────────
    if _is_admin(current_user):
        # Admins see ALL tickets — no tab separation needed, no join required
        # (view param is intentionally ignored for admins)
        pass
    else:
        # Non-admins: must be in ticket_users. Split by role based on view.
        if view == "assigned":
            # Tickets explicitly assigned to me by a boss/admin
            query = query.join(TicketUser, TicketUser.ticket_id == Ticket.id).where(
                TicketUser.user_id == current_user.id,
                TicketUser.role == "assignee",
            )
        elif view == "involved":
            # Tickets I'm a watcher on (auto-added from to/cc/bcc via SharePoint)
            query = query.join(TicketUser, TicketUser.ticket_id == Ticket.id).where(
                TicketUser.user_id == current_user.id,
                TicketUser.role == "watcher",
            )
        else:
            # "all" for non-admins = any participation (assignee OR watcher)
            query = query.join(TicketUser, TicketUser.ticket_id == Ticket.id).where(
                TicketUser.user_id == current_user.id,
            )

    # ── Filters ───────────────────────────────────────────────────────────────
    if status:
        query = query.where(Ticket.status == status)
    if priority:
        query = query.where(Ticket.priority == priority)
    if search and search.strip():
        query = query.where(Ticket.title.ilike(f"%{search.strip()}%"))

    query = query.order_by(Ticket.updated_at.desc()).offset(skip).limit(limit)

    tickets_result = await session.exec(query)
    tickets = tickets_result.unique().all()

    # ── Add ticket numbers ────────────────────────────────────────────────────
    number_map = await _get_ticket_number_map(session)

    result = []
    for t in tickets:
        d = t.model_dump()
        d["ticket_number"] = _fmt_ticket_number(number_map.get(t.id, 0))
        result.append(d)

    return result


async def update_ticket(
    session: AsyncSession, ticket_id: str, data: dict, current_user: User
):
    """Partial-update a ticket (status, priority). Admin / Reviewer only."""
    has_access = await check_ticket_access(session, ticket_id, current_user)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied.")

    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    if "status" in data:
        ticket.status = data["status"]
    if "priority" in data:
        ticket.priority = data["priority"]

    from datetime import datetime
    ticket.updated_at = datetime.utcnow()
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def assign_ticket(
    session: AsyncSession, ticket_id: str, user_id: str, current_user: User
):
    """Assign a user to a ticket. ONLY Admin / Reviewer can do this."""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only Admins or Reviewers can assign tickets.")

    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    assigned_user = await session.get(User, user_id)
    if not assigned_user or not assigned_user.is_active:
        raise HTTPException(status_code=400, detail="Invalid user.")

    existing_tu = await session.exec(
        select(TicketUser).where(
            TicketUser.ticket_id == ticket_id, TicketUser.user_id == user_id
        )
    )
    tu = existing_tu.first()

    is_new_participant = False

    if tu:
        tu.role = "assignee"
    else:
        tu = TicketUser(ticket_id=ticket_id, user_id=user_id, role="assignee")
        is_new_participant = True

    session.add(tu)
    await session.commit()

    if is_new_participant:
        await send_ticket_assignment(
            session, ticket_id, [assigned_user.email], current_user.full_name
        )

    return tu


async def post_message(
    session: AsyncSession, ticket_id: str, content: str, current_user: User
):
    """Post an internal message to a ticket from the app UI."""
    has_access = await check_ticket_access(session, ticket_id, current_user)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied.")

    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    msg = Message(
        ticket_id=ticket.id,
        sender_id=current_user.id,
        content=content,
        source="app",
    )
    session.add(msg)

    from datetime import datetime
    ticket.updated_at = datetime.utcnow()
    session.add(ticket)

    await session.commit()
    await session.refresh(msg)
    return msg


async def handle_mention(
    session: AsyncSession,
    ticket_id: str,
    mentioned_user_id: str,
    current_user: User,
):
    """
    When @username is typed in chat.
    Validates if mentioned user is a participant.
    If not, grants watcher access so the ticket becomes visible to them.
    """
    has_access = await check_ticket_access(session, ticket_id, current_user)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied.")

    mentioned_user = await session.get(User, mentioned_user_id)
    if not mentioned_user:
        raise HTTPException(status_code=400, detail="User not found.")

    # Upsert as watcher so the ticket is now visible to the mentioned user
    tu_result = await session.exec(
        select(TicketUser).where(
            TicketUser.ticket_id == ticket_id, TicketUser.user_id == mentioned_user_id
        )
    )
    existing_tu = tu_result.first()
    if not existing_tu:
        tu = TicketUser(ticket_id=ticket_id, user_id=mentioned_user_id, role="watcher")
        session.add(tu)
        await session.commit()

    # Send notification email
    await send_message_notification(
        session, ticket_id, [mentioned_user.email], current_user.full_name, is_mention=True
    )
    return {"status": "ok", "message": "Mention notification sent."}
