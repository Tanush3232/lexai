"""
Webhook maintenance tasks.
"""
import asyncio
import logging
from celery import shared_task

from app.services.graph_webhook_service import maintain_subscription

log = logging.getLogger(__name__)


@shared_task(name="app.tasks.webhook_tasks.check_graph_subscriptions")
def check_graph_subscriptions():
    """
    Celery task to maintain Microsoft Graph webhook subscriptions.
    Because the maintain_subscription function is async, we use asyncio.run
    to execute it inside the synchronous Celery worker context.
    """
    try:
        log.info("[Celery] Starting Graph subscription maintenance check...")
        asyncio.run(maintain_subscription())
        log.info("[Celery] Graph subscription maintenance complete.")
    except Exception as e:
        log.error("[Celery] Error maintaining Graph subscriptions: %s", e)
