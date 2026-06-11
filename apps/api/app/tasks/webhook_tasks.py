import logging
from celery import shared_task

log = logging.getLogger(__name__)


@shared_task(name="app.tasks.webhook_tasks.check_graph_subscriptions")
def check_graph_subscriptions():
    """
    Celery task to maintain Microsoft Graph webhook subscriptions.
    Deprecated: MS Graph webhook service has been removed and replaced by Power Automate.
    """
    log.info("[Celery] Graph subscription maintenance check skipped (deprecated/decommissioned).")

