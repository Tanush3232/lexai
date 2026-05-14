"""
notificationService.py (Legal Ticketing) — Legal Ticketing System

Stub service for in-app notifications scoped to the ticketing module.

Complements the existing LexAI audit service — these notifications surface
in the UI rather than being background audit log entries.

Business logic will be implemented in a follow-up.
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession


async def notify_ticket_assigned(
    session: AsyncSession,
    ticket_id: str,
    assignee_id: str,
    assigned_by_id: str,
):
    """
    Notify a user that they have been assigned to a ticket.
    """
    raise NotImplementedError


async def notify_ticket_status_changed(
    session: AsyncSession,
    ticket_id: str,
    new_status: str,
    changed_by_id: str,
    notify_user_ids: list[str],
):
    """
    Notify all stakeholders (creator, assignees, watchers) of a status change.
    """
    raise NotImplementedError


async def notify_new_message(
    session: AsyncSession,
    ticket_id: str,
    message_id: str,
    sender_id: str,
    notify_user_ids: list[str],
):
    """
    Notify ticket participants that a new message has been posted.
    """
    raise NotImplementedError


async def notify_watcher_added(
    session: AsyncSession,
    ticket_id: str,
    watcher_id: str,
    added_by_id: str,
):
    """
    Notify a user that they have been added as a watcher on a ticket.
    """
    raise NotImplementedError
