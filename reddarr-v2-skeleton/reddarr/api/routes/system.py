"""System routes - health check, Prometheus metrics, SSE event stream.

Replaces the /health, /metrics, and /api/events endpoints from web/app.py.
The SSE polling loop is replaced by a simpler async generator that queries
the DB on demand rather than maintaining a background thread.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import func

from reddarr.database import get_db, SessionLocal, init_engine
from reddarr.models import Post, Comment, Media, Target

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint.

    Refreshes database gauges before returning metrics.
    """
    try:
        from reddarr.utils.metrics import posts_total, comments_total, media_total, targets_enabled

        init_engine()
        with SessionLocal() as db:
            posts_total.set(db.query(func.count(Post.id)).scalar() or 0)
            comments_total.set(db.query(func.count(Comment.id)).scalar() or 0)

            for status in ("done", "failed", "pending", "corrupted"):
                count = (
                    db.query(func.count(Media.id))
                    .filter(Media.status == status)
                    .scalar()
                    or 0
                )
                media_total.labels(status=status).set(count)

            targets_enabled.set(
                db.query(func.count(Target.id))
                .filter(Target.enabled.is_(True))
                .scalar()
                or 0
            )
    except Exception as e:
        logger.warning(f"Could not refresh metrics: {e}")

    return PlainTextResponse(
        generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/api/events")
async def event_stream(request: Request):
    """Server-Sent Events stream for real-time dashboard updates.

    Replaces the old _run_sse_polling_loop() background thread with
    an async generator. Each connected client gets its own lightweight
    polling loop that queries the DB every 5 seconds.

    The frontend connects to this and updates dashboard stats in real-time.
    """

    async def generate():
        init_engine()

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                data = _build_sse_payload()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.warning(f"SSE error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_sse_payload() -> dict:
    """Build the SSE payload with current stats.

    This is the simplified version of the old _run_sse_polling_loop()
    that ran in a background thread with a global cache.
    """
    with SessionLocal() as db:
        total_posts = db.query(func.count(Post.id)).filter(Post.hidden.is_(False)).scalar() or 0
        hidden_posts = db.query(func.count(Post.id)).filter(Post.hidden.is_(True)).scalar() or 0
        total_comments = db.query(func.count(Comment.id)).scalar() or 0
        dl_media = db.query(func.count(Media.id)).filter(Media.status == "done").scalar() or 0
        pending_media = db.query(func.count(Media.id)).filter(Media.status == "pending").scalar() or 0
        total_media = db.query(func.count(Media.id)).scalar() or 0

        # Target summaries
        targets = db.query(Target).order_by(Target.type, Target.name).all()
        target_list = []
        for t in targets:
            if t.type == "subreddit":
                post_count = (
                    db.query(func.count(Post.id))
                    .filter(func.lower(Post.subreddit) == t.name.lower())
                    .scalar()
                    or 0
                )
            else:
                post_count = (
                    db.query(func.count(Post.id))
                    .filter(func.lower(Post.author) == t.name.lower())
                    .scalar()
                    or 0
                )

            target_list.append({
                "type": t.type,
                "name": t.name,
                "enabled": t.enabled,
                "status": t.status or "active",
                "icon_url": t.icon_url,
                "last_created": t.last_created.isoformat() if t.last_created else None,
                "post_count": post_count,
            })

    return {
        "stats": {
            "total_posts": total_posts,
            "hidden_posts": hidden_posts,
            "total_comments": total_comments,
            "downloaded_media": dl_media,
            "pending_media": pending_media,
            "total_media": total_media,
        },
        "targets": target_list,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
