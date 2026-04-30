"""Admin API routes — stats, activity, queue management, trigger actions.

Extracts admin-specific endpoints from the old web/app.py.
All routes require API key authentication.
"""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from reddarr.database import get_db
from reddarr.models import Post, Comment, Media, Target, PostHistory, CommentHistory
from reddarr.api.auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"], dependencies=[Depends(require_api_key)])


@router.get("/stats")
def admin_stats(db: Session = Depends(get_db)):
    """Overall archive statistics."""
    total_posts = db.query(func.count(Post.id)).filter(Post.hidden.is_(False)).scalar() or 0
    excluded_posts = db.query(func.count(Post.id)).filter(Post.hidden.is_(True)).scalar() or 0
    total_comments = db.query(func.count(Comment.id)).scalar() or 0
    total_media = db.query(func.count(Media.id)).scalar() or 0
    downloaded = db.query(func.count(Media.id)).filter(Media.status == "done").scalar() or 0
    pending = db.query(func.count(Media.id)).filter(Media.status == "pending").scalar() or 0
    failed = db.query(func.count(Media.id)).filter(Media.status == "failed").scalar() or 0
    targets_count = db.query(func.count(Target.id)).filter(Target.enabled.is_(True)).scalar() or 0

    return {
        "total_posts": total_posts,
        "excluded_posts": excluded_posts,
        "total_comments": total_comments,
        "total_media": total_media,
        "downloaded_media": downloaded,
        "pending_media": pending,
        "media_failed": failed,
        "targets_enabled": targets_count,
    }


@router.get("/activity")
def admin_activity(
    limit: int = Query(50, ge=1, le=200),
    hours: int = Query(24, ge=1, le=168),
    include_failures: bool = False,
    db: Session = Depends(get_db),
):
    """Recent ingestion activity as an event list.

    Returns an array of recent posts and optionally failed downloads,
    suitable for the activity stream UI.
    """
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    recent_posts = (
        db.query(Post)
        .filter(Post.ingested_at >= since)
        .order_by(desc(Post.ingested_at))
        .limit(limit)
        .all()
    )

    events = [
        {
            "id": p.id,
            "type": "ingest",
            "subreddit": p.subreddit,
            "author": p.author,
            "title": p.title,
            "created_utc": p.created_utc.isoformat() if p.created_utc else None,
            "created_at": p.ingested_at.isoformat() if p.ingested_at else None,
        }
        for p in recent_posts
    ]

    if include_failures:
        rows = (
            db.query(Media, Post)
            .join(Post, Media.post_id == Post.id, isouter=True)
            .filter(Media.status == "failed")
            .order_by(desc(Media.downloaded_at))
            .limit(limit)
            .all()
        )
        for m, post in rows:
            events.append({
                "id": m.id,
                "type": "failure",
                "post_id": m.post_id,
                "subreddit": post.subreddit if post else None,
                "author": post.author if post else None,
                "title": post.title if post else None,
                "url": m.url,
                "error_message": m.error_message,
                "created_at": m.downloaded_at.isoformat() if m.downloaded_at else None,
            })

    return events


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
        req.target_type,
        req.target_name,
        sort=req.sort,
        time_filter=req.time_filter,
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


@router.get("/health")
def admin_health():
    """Service health status for admin panel."""
    from reddarr.database import _engine
    from reddarr.config import get_settings

    settings = get_settings()
    health = {"api": "ok", "db": "unknown", "redis": "unknown"}

    try:
        from sqlalchemy import text
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        health["db"] = "ok"
    except Exception:
        health["db"] = "error"

    try:
        import redis

        r = redis.Redis.from_url(settings.redis_url)
        r.ping()
        health["redis"] = "ok"
    except Exception:
        health["redis"] = "error"

    return health


@router.get("/backfill-status")
def backfill_status():
    """Check if a backfill is currently running."""
    try:
        from reddarr.tasks import app as celery_app

        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.active() or {}

        backfill_running = any(
            "backfill" in str(t).lower() for tasks in active.values() for t in tasks
        )
        return {"running": backfill_running, "active_tasks": active}
    except Exception as e:
        return {"running": False, "error": str(e)}


@router.get("/thumbnails/stats")
def thumbnails_stats(db: Session = Depends(get_db)):
    """Get thumbnail generation stats."""
    total_with_thumb = (
        db.query(func.count(Media.id))
        .filter(Media.thumb_path.isnot(None), Media.status == "done")
        .scalar()
        or 0
    )

    total_done = db.query(func.count(Media.id)).filter(Media.status == "done").scalar() or 0

    missing = total_done - total_with_thumb

    return {
        "total_done": total_done,
        "with_thumbnails": total_with_thumb,
        "missing_thumbnails": missing,
    }


@router.delete("/queue")
def clear_queue():
    """Clear all pending media downloads."""
    from reddarr.tasks.download import download_media_item
    from reddarr.database import SessionLocal, init_engine
    from reddarr.models import Media
    from celery import group

    init_engine()
    with SessionLocal() as db:
        pending = db.query(Media).filter(Media.status == "pending").all()
        # Revoke pending tasks
        for m in pending:
            download_media_item.revoke(m.post_id, m.url, terminate=True)
        # Clear pending status
        db.query(Media).filter(Media.status == "pending").update(
            {"status": "failed", "error_message": "Cleared by admin"}
        )
        db.commit()

    return {"cleared": len(pending)}


@router.delete("/reset")
def full_reset(confirm: str = Query(...)):
    """Full reset - clears all data. Requires confirm=RESET."""
    if confirm != "RESET":
        return {"error": "Must provide confirm=RESET"}

    from reddarr.database import SessionLocal, init_engine

    init_engine()
    with SessionLocal() as db:
        db.query(Media).delete()
        db.query(PostHistory).delete()
        db.query(CommentHistory).delete()
        db.query(Comment).delete()
        db.query(Post).delete()
        db.commit()

    return {"status": "reset"}
