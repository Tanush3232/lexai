"""
tickets.py — Legal Ticketing System APIs
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.ticket import Ticket
from app.services.ticket_service import (
    list_tickets,
    get_ticket_with_details,
    update_ticket,
    assign_ticket,
    post_message,
    handle_mention
)
from pydantic import BaseModel

router = APIRouter()

class TicketUpdateBody(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None

class AssignBody(BaseModel):
    user_id: str

class MessageBody(BaseModel):
    content: str

class MentionBody(BaseModel):
    user_id: str

@router.get("")
async def get_tickets_route(
    view: str = Query("all", description="all, assigned, involved"),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    tickets = await list_tickets(session, current_user, view, status, priority, skip, limit)
    return tickets

@router.get("/{ticket_id}")
async def get_ticket_route(
    ticket_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await get_ticket_with_details(session, ticket_id, current_user)

@router.patch("/{ticket_id}")
async def update_ticket_route(
    ticket_id: str,
    body: TicketUpdateBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await update_ticket(session, ticket_id, body.model_dump(exclude_unset=True), current_user)

@router.post("/{ticket_id}/assign")
async def assign_ticket_route(
    ticket_id: str,
    body: AssignBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await assign_ticket(session, ticket_id, body.user_id, current_user)

@router.post("/{ticket_id}/messages")
async def post_message_route(
    ticket_id: str,
    body: MessageBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await post_message(session, ticket_id, body.content, current_user)

@router.post("/{ticket_id}/mentions")
async def mention_user_route(
    ticket_id: str,
    body: MentionBody,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await handle_mention(session, ticket_id, body.user_id, current_user)

@router.get("/{ticket_id}/mentionable_users")
async def get_mentionable_users(
    ticket_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    # Valid LexAI users. We just return all active users for the mention dropdown.
    # The actual blocking happens if they pick a non-participant.
    result = await session.exec(select(User).where(User.is_active == True)) # noqa: E712
    users = result.all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role} for u in users]
