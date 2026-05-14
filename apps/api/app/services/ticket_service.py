"""
ticketService.py — Legal Ticketing System

Stub service for ticket operations.
Business logic will be implemented in a follow-up.

All public functions accept an AsyncSession injected by FastAPI's dependency
system and follow the same pattern as the rest of the LexAI services.
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession


async def get_ticket(session: AsyncSession, ticket_id: str):
    """
    Fetch a single ticket by ID.
    Returns: Ticket | None
    """
    raise NotImplementedError


async def list_tickets(
    session: AsyncSession,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    user_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """
    List tickets with optional filters (status, priority, assigned user).
    Returns: list[Ticket]
    """
    raise NotImplementedError


async def create_ticket(session: AsyncSession, data: dict, created_by: str):
    """
    Create a new ticket and set the creator in ticket_users.
    Returns: Ticket
    """
    raise NotImplementedError


async def update_ticket(session: AsyncSession, ticket_id: str, data: dict):
    """
    Partial-update a ticket (status, priority, title).
    Returns: Ticket
    """
    raise NotImplementedError


async def close_ticket(session: AsyncSession, ticket_id: str, closed_by: str):
    """
    Set ticket status to 'closed' and record the event.
    Returns: Ticket
    """
    raise NotImplementedError


async def assign_ticket(session: AsyncSession, ticket_id: str, user_id: str):
    """
    Assign a user to a ticket (upsert TicketUser with role='assignee').
    Returns: TicketUser
    """
    raise NotImplementedError


async def add_watcher(session: AsyncSession, ticket_id: str, user_id: str):
    """
    Add a watcher to a ticket (upsert TicketUser with role='watcher').
    Returns: TicketUser
    """
    raise NotImplementedError
