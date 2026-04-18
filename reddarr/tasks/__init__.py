"""Celery application for Reddarr background tasks.

Replaces the old raw Redis RPUSH/BLPOP queue + PubSub pattern with
proper Celery task dispatch and scheduling.

Run workers:
    celery -A reddarr.tasks worker -l info -c 4 -Q default,download,ingest

Run beat scheduler:
    celery -A reddarr.tasks beat -l info
"""

from celery import Celery
from celery.schedules import timedelta

from reddarr.config import get_settings

settings = get_settings()

app = Celery("reddarr")

app.config_from_object(
    {
        "broker_url": settings.celery_broker_url,
        "result_backend": settings.celery_result_backend,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "UTC",
        "enable_utc": True,
        # Route tasks to specific queues
        "task_routes": {
            "reddarr.tasks.ingest.*": {"queue": "ingest"},
            "reddarr.tasks.download.*": {"queue": "download"},
            "reddarr.tasks.maintenance.*": {"queue": "default"},
        },
        # Default queue
        "task_default_queue": "default",
        # Retry policy
        "task_acks_late": True,
        "worker_prefetch_multiplier": 1,
        # Result expiry (24h)
        "result_expires": 86400,
        # Beat schedule — replaces the old poll loop in ingester
        "beat_schedule": {
            "ingest-cycle": {
                "task": "reddarr.tasks.ingest.run_ingest_cycle",
                "schedule": timedelta(seconds=settings.poll_interval),
            },
            "refresh-target-icons": {
                "task": "reddarr.tasks.maintenance.refresh_target_icons",
                "schedule": timedelta(hours=6),
            },
            "cleanup-failed-downloads": {
                "task": "reddarr.tasks.maintenance.cleanup_failed_downloads",
                "schedule": timedelta(hours=1),
            },
        },
    }
)

# Auto-discover tasks in all reddarr.tasks.* modules
app.autodiscover_tasks(["reddarr.tasks"])

# Force import task modules to ensure they're registered
import reddarr.tasks.ingest
import reddarr.tasks.download
import reddarr.tasks.maintenance
