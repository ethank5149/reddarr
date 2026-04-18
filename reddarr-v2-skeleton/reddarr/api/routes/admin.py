"""Admin API routes — stats, activity, queue management, trigger actions.

Extracts admin-specific endpoints from the old web/app.py.
All routes require API key authentication.
"""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from reddarr.database import get_db
from reddarr.models import Post, Comment, Media, Target
from reddarr.api.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"], dependencies=[Depends(require_api_key)])


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db)):
    """Overall archive statistics."""
    total_posts = db.query(func.count(Post.id)).scalar() or 0
    total_comments = db.query(func.count(Comment.id)).scalar() or 0
    total_media = db.query(func.count(Media.id)).scalar() or 0
    downloaded = db.query(func.count(Media.id)).filter(Media.status == "done").scalar() or 0
    pending = db.query(func.count(Media.id)).filter(Media.status == "pending").scalar() or 0
    failed = db.query(func.count(Media.id)).filter(Media.status == "failed").scalar() or 0
    targets_count = db.query(func.count(Target.id)).filter(Target.enabled.is_(True)).scalar() or 0

    return {
        "posts": total_posts,
        "comments": total_comments,
        "media": {
            "total": total_media,
            "downloaded": downloaded,
            "pending": pending,
            "failed": failed,
        },
        "targets_enabled": targets_count,
    }


@router.get("/activity")
def admin_activity(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
):
    """Recent ingestion and download activity."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    recent_posts = (
        db.query(func.count(Post.id))
        .filter(Post.ingested_at >= since)
        .scalar()
        or 0
    )

    recent_downloads = (
        db.query(func.count(Media.id))
        .filter(Media.downloaded_at >= since, Media.status == "done")
        .scalar()
        or 0
    )

    recent_failures = (
        db.query(func.count(Media.id))
        .filter(Media.downloaded_at >= since, Media.status == "failed")
        .scalar()
        or 0
    )

    return {
        "period_hours": hours,
        "posts_ingested": recent_posts,
        "media_downloaded": recent_downloads,
        "media_failed": recent_failures,
    }


@router.get("/queue")
def admin_queue(db: Session = Depends(get_db)):
    """Current download queue status.

    With Celery, we can also query the Celery inspect API for active tasks.
    """
    pending = db.query(func.count(Media.id)).filter(Media.status == "pending").scalar() or 0
    failed = db.query(func.count(Media.id)).filter(Media.status == "failed").scalar() or 0

    # Try to get Celery queue info
    celery_info = {}
    try:
        from reddarr.tasks import app as celery_app
        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        celery_info = {
            "active_tasks": sum(len(v) for v in active.values()),
            "reserved_tasks": sum(len(v) for v in reserved.values()),
            "workers": list(active.keys()),
        }
    except Exception:
        celery_info = {"error": "Could not reach Celery workers"}

    return {
        "db_pending": pending,
        "db_failed": failed,
        "celery": celery_info,
    }


class ScrapeRequest(BaseModel):
    target_type: str = "subreddit"
    target_name: str
    sort: str = "new"


@router.post("/trigger-scrape")
def trigger_scrape(req: ScrapeRequest):
    """Manually trigger an ingest for a specific target."""
    from reddarr.tasks.ingest import ingest_target

    task = ingest_target.delay(req.target_type, req.target_name)
    return {"status": "queued", "task_id": task.id}


class BackfillRequest(BaseModel):
    target_type: str = "subreddit"
    target_name: str
    sort: str = "top"
    time_filter: str = "all"
    passes: int = 1


@router.post("/trigger-backfill")
def trigger_backfill(req: BackfillRequest):
    """Manually trigger a backfill for a specific target."""
    from reddarr.tasks.ingest import trigger_backfill as backfill_task

    task = backfill_task.delay(
        req.target_type, req.target_name,
        sort=req.sort, time_filter=req.time_filter,
        passes=req.passes,
    )
    return {"status": "queued", "task_id": task.id}


@router.post("/requeue-failed")
def requeue_failed(max_retries: int = Query(5)):
    """Re-queue all failed downloads."""
    from reddarr.tasks.download import requeue_failed as requeue_task

    task = requeue_task.delay(max_retries=max_retries)
    return {"status": "queued", "task_id": task.id}


@router.post("/thumbnails/backfill")
def thumbnails_backfill():
    """Generate missing thumbnails."""
    from reddarr.tasks.download import generate_thumbnails

    task = generate_thumbnails.delay()
    return {"status": "queued", "task_id": task.id}


@router.post("/integrity-check")
def run_integrity_check():
    """Run media integrity check."""
    from reddarr.tasks.maintenance import integrity_check

    task = integrity_check.delay()
    return {"status": "queued", "task_id": task.id}
