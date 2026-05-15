"""
ticket_service.py — Legal Ticketing System

Business logic for managing tickets.
Enforces RBAC and participant visibility.
"""
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func
from fastapi import HTTPException

from app.models.ticket import Ticket, TicketUser, EmailThread
from app.models.message import Message
from app.models.user import User
from app.services.legal_email_service import send_ticket_assignment, send_message_notification

_ADMIN_ROLES = {"ops_admin", "super_admin", "reviewer"}

def _is_admin(user: User) -> bool:
    return user.role in _ADMIN_ROLES

async def check_ticket_access(session: AsyncSession, ticket_id: str, user: User) -> bool:
    """Check if user is allowed to access this ticket."""
    if _is_admin(user):
        return True
    
    # Check if user is a participant
    result = await session.exec(
        select(TicketUser).where(
            TicketUser.ticket_id == ticket_id,
            TicketUser.user_id == user.id
        )
    )
    if result.first():
        return True
    return False

async def get_ticket_with_details(session: AsyncSession, ticket_id: str, current_user: User) -> Dict[str, Any]:
    """
    Fetch ticket details, participants, and messages.
    Enforces visibility rules.
    """
    has_access = await check_ticket_access(session, ticket_id, current_user)
    if not has_access:
        raise HTTPException(status_code=403, detail="You do not have access to this ticket.")

    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    # Get participants
    users_result = await session.exec(
        select(TicketUser, User)
        .join(User, TicketUser.user_id == User.id)
        .where(TicketUser.ticket_id == ticket_id)
    )
    participants = []
    for tu, u in users_result.all():
        participants.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": tu.role,
            "assigned_at": tu.assigned_at
        })

    # Get messages
    msgs_result = await session.exec(
        select(Message, User)
        .join(User, Message.sender_id == User.id)
        .where(Message.ticket_id == ticket_id)
        .order_by(Message.timestamp.asc())
    )
    messages = []
    for m, u in msgs_result.all():
        messages.append({
            "id": m.id,
            "content": m.content,
            "source": m.source,
            "timestamp": m.timestamp,
            "sender_id": u.id,
            "sender_name": u.full_name,
        })

    return {
        "ticket": ticket.model_dump(),
        "participants": participants,
        "messages": messages,
    }

async def list_tickets(
    session: AsyncSession,
    current_user: User,
    view: str = "all", # 'all', 'assigned', 'involved'
    status: Optional[str] = None,
    priority: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """
    List tickets enforcing visibility rules.
    Admin/Reviewer can see all.
    Counsel can only see involved/assigned.
    """
    query = select(Ticket)

    # Base access filter
    if not _is_admin(current_user):
        # Must be in ticket_users
        query = query.join(TicketUser, TicketUser.ticket_id == Ticket.id).where(TicketUser.user_id == current_user.id)
    else:
        # Admin applying filters
        if view == "assigned":
            query = query.join(TicketUser, TicketUser.ticket_id == Ticket.id).where(
                TicketUser.user_id == current_user.id,
                TicketUser.role == "assignee"
            )
        elif view == "involved":
            query = query.join(TicketUser, TicketUser.ticket_id == Ticket.id).where(TicketUser.user_id == current_user.id)

    if status:
        query = query.where(Ticket.status == status)
    if priority:
        query = query.where(Ticket.priority == priority)

    query = query.order_by(Ticket.updated_at.desc()).offset(skip).limit(limit)
    
    # We must deduplicate because of potential multiple TicketUser joins
    tickets_result = await session.exec(query)
    tickets = tickets_result.unique().all()
    
    return tickets

async def update_ticket(session: AsyncSession, ticket_id: str, data: dict, current_user: User):
    """
    Partial-update a ticket (status, priority).
    """
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
        
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket

async def assign_ticket(session: AsyncSession, ticket_id: str, user_id: str, current_user: User):
    """
    Assign a user to a ticket. ONLY Admin/Reviewer can do this.
    """
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Only Admins or Reviewers can assign tickets.")
        
    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
        
    assigned_user = await session.get(User, user_id)
    if not assigned_user or not assigned_user.is_active:
        raise HTTPException(status_code=400, detail="Invalid user.")

    # Check if user is already a participant
    existing_tu = await session.exec(
        select(TicketUser).where(TicketUser.ticket_id == ticket_id, TicketUser.user_id == user_id)
    )
    tu = existing_tu.first()
    
    is_new_participant = False
    
    if tu:
        # Update role to assignee
        tu.role = "assignee"
    else:
        # New participant
        tu = TicketUser(ticket_id=ticket_id, user_id=user_id, role="assignee")
        is_new_participant = True
        
    session.add(tu)
    await session.commit()
    
    if is_new_participant:
        # Send email notification
        await send_ticket_assignment(session, ticket_id, [assigned_user.email], current_user.full_name)
        
    return tu

async def post_message(session: AsyncSession, ticket_id: str, content: str, current_user: User):
    """
    Post a message to a ticket from the app.
    """
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
        source="app"
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg

async def handle_mention(session: AsyncSession, ticket_id: str, mentioned_user_id: str, current_user: User):
    """
    When @username is typed in chat.
    Validates if mentioned user is a participant. 
    If yes, sends notification. If no, returns error.
    """
    has_access = await check_ticket_access(session, ticket_id, current_user)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied.")

    mentioned_user = await session.get(User, mentioned_user_id)
    if not mentioned_user:
        raise HTTPException(status_code=400, detail="User not found.")

    # Check if participant
    tu_result = await session.exec(
        select(TicketUser).where(TicketUser.ticket_id == ticket_id, TicketUser.user_id == mentioned_user_id)
    )
    if not tu_result.first() and not _is_admin(mentioned_user):
        # Not a participant, block action
        raise HTTPException(
            status_code=400, 
            detail="This user is not part of this ticket. Only Admin/Reviewer can add users via assignment."
        )
        
    # Send email
    await send_message_notification(session, ticket_id, [mentioned_user.email], current_user.full_name, is_mention=True)
    return {"status": "ok", "message": "Mention notification sent."}
