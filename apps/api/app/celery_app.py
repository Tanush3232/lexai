"""
Celery worker application and tasks for background document processing.
"""
import sys
from celery import Celery
from app.core.config import settings

# Windows (local dev): prefork subprocesses break after broker reconnects — use solo.
# Linux (production):  use prefork for true concurrency.
_WORKER_POOL = "solo" if sys.platform == "win32" else "prefork"

celery_app = Celery(
    "lexai_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.ingestion_tasks",
        "app.tasks.translation_tasks",
        "app.tasks.act_ingestion_tasks",
        "app.tasks.webhook_tasks",
    ],
)

from celery.schedules import crontab

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Production (Linux/Lightsail): use prefork pool for true concurrency.
    # NOTE: 'solo' was used for local Windows dev only — do NOT use in production.
    worker_pool=_WORKER_POOL,
    # Silence Celery 6.0 deprecation warning
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.tasks.ingestion_tasks.*": {"queue": "ingestion"},
        "app.tasks.translation_tasks.*": {"queue": "translation"},
        "app.tasks.act_ingestion_tasks.*": {"queue": "ingestion"},
        "app.tasks.webhook_tasks.*": {"queue": "ingestion"},  # We can run lightweight tasks on the ingestion queue
    },
    beat_schedule={
        "maintain-graph-subscription-daily": {
            "task": "app.tasks.webhook_tasks.check_graph_subscriptions",
            # Run at midnight UTC daily
            "schedule": crontab(hour=0, minute=0),
        },
    },
)
