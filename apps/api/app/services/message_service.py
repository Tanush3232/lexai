"""
messageService.py — Legal Ticketing System

Stub service for ticket message operations.
Handles both in-app messages and email-sourced messages.

Business logic will be implemented in a follow-up.
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession


async def post_message(
    session: AsyncSession,
    ticket_id: str,
    sender_id: str,
    content: str,
    source: str = "app",
    email_thread_id: Optional[str] = None,
):
    """
    Append a new message to a ticket.
    source: 'app' for UI messages, 'email' for ingested emails.
    Returns: Message
    """
    raise NotImplementedError


async def list_messages(
    session: AsyncSession,
    ticket_id: str,
    skip: int = 0,
    limit: int = 100,
):
    """
    Retrieve all messages for a ticket in chronological order.
    Returns: list[Message]
    """
    raise NotImplementedError


async def get_message(session: AsyncSession, message_id: str):
    """
    Fetch a single message by ID.
    Returns: Message | None
    """
    raise NotImplementedError
