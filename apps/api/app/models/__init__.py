"""
Models package — imports all models so SQLModel registers them.
"""
from app.models import user, folder, document, chat, draft, translation, audit, clause, llm_usage, legal_act  # noqa
from app.models import ticket, message  # noqa  ← Legal Ticketing System
from app.models import web_search  # noqa  ← Web Search Feature

__all__ = [
    "user", "folder", "document", "chat", "draft", "translation",
    "audit", "clause", "llm_usage", "legal_act",
    "ticket", "message",
    "web_search",
]
