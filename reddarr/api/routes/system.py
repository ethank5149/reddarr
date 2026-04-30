"""System routes — health check, Prometheus metrics, SSE event stream.

Replaces the /health, /metrics, and /api/events endpoints from web/app.py.
The SSE polling loop is replaced by a simpler async generator that queries
the DB on demand rather than maintaining a background thread.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import func

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


# Lazy imports to avoid circular dependency and ensure init
def _get_session_local():
    from reddarr.database import SessionLocal, init_engine

    init_engine()
    return SessionLocal


def _get_models():
    from reddarr.models import Post, Comment, Media, Target

    return Post, Comment, Media, Target


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
        from reddarr.database import init_engine
        from reddarr.models import Post, Comment, Media, Target

        init_engine()
        SessionLocal = _get_session_local()
        with SessionLocal() as db:
            posts_total.set(db.query(func.count(Post.id)).scalar() or 0)
            comments_total.set(db.query(func.count(Comment.id)).scalar() or 0)

            for status in ("done", "failed", "pending", "corrupted"):
                count = db.query(func.count(Media.id)).filter(Media.status == status).scalar() or 0
                media_total.labels(status=status).set(count)

            targets_enabled.set(
                db.query(func.count(Target.id)).filter(Target.enabled.is_(True)).scalar() or 0
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

    Key design: `since` is tracked per-connection so new_posts / new_media
    only contain items that arrived *after* the previous SSE cycle.  This
    prevents the frontend from calling refreshPosts() on every tick.
    """

    async def generate():
        from reddarr.database import init_engine

        init_engine()

        # Track the watermark so we only surface genuinely new data.
        # Initialise to "now" so the very first message has empty
        # new_posts / new_media and doesn't trigger a spurious refresh.
        last_check = datetime.now(timezone.utc)

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                now = datetime.now(timezone.utc)
                data = _build_sse_payload(since=last_check)
                last_check = now
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


def _build_sse_payload(since: Optional[datetime] = None) -> dict:
    """Build the SSE payload with current stats.

    Args:
        since: If provided, new_posts and new_media are filtered to only
               items ingested/downloaded after this timestamp.  Pass the
               timestamp of the *previous* SSE cycle so the frontend only
               sees genuinely new activity and doesn't call refreshPosts()
               on every tick.

    Includes:
    - Overall stats (posts, comments, media)
    - queue_length (pending download count) for the sidebar indicator
    - health (quick db status)
    - Target summaries with post counts and media counts
    - new_posts / new_media — only items newer than `since`
    - Per-target detailed stats (rate, ETA, progress_percent)
    """
    from reddarr.database import init_engine

    init_engine()
    SessionLocal = _get_session_local()
    Post, Comment, Media, Target = _get_models()

    with SessionLocal() as db:
        total_posts = db.query(func.count(Post.id)).filter(Post.hidden.is_(False)).scalar() or 0
        hidden_posts = db.query(func.count(Post.id)).filter(Post.hidden.is_(True)).scalar() or 0
        total_comments = db.query(func.count(Comment.id)).scalar() or 0
        dl_media = db.query(func.count(Media.id)).filter(Media.status == "done").scalar() or 0
        pending_media = (
            db.query(func.count(Media.id)).filter(Media.status == "pending").scalar() or 0
        )
        total_media = db.query(func.count(Media.id)).scalar() or 0

        # Target summaries with detailed stats
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
                # Posts in last 7 days for rate calculation
                from datetime import timedelta

                seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
                posts_7d = (
                    db.query(func.count(Post.id))
                    .filter(func.lower(Post.subreddit) == t.name.lower())
                    .filter(Post.created_utc >= seven_days_ago)
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
                seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
                posts_7d = (
                    db.query(func.count(Post.id))
                    .filter(func.lower(Post.author) == t.name.lower())
                    .filter(Post.created_utc >= seven_days_ago)
                    .scalar()
                    or 0
                )

            # Media counts per target
            if t.type == "subreddit":
                total_media_q = (
                    db.query(func.count(Media.id))
                    .join(Post)
                    .filter(func.lower(Post.subreddit) == t.name.lower())
                )
                downloaded_media_q = total_media_q.filter(Media.status == "done")
                pending_media_q = total_media_q.filter(Media.status == "pending")
            else:
                total_media_q = (
                    db.query(func.count(Media.id))
                    .join(Post)
                    .filter(func.lower(Post.author) == t.name.lower())
                )
                downloaded_media_q = total_media_q.filter(Media.status == "done")
                pending_media_q = total_media_q.filter(Media.status == "pending")

            tot_media = total_media_q.scalar() or 0
            dl_media_cnt = downloaded_media_q.scalar() or 0
            pend_media_cnt = pending_media_q.scalar() or 0

            # Calculate rate and ETA
            rate = posts_7d / (7 * 86400) if posts_7d > 0 else 0
            eta_seconds = None
            if rate > 0:
                remaining = max(0, 1000 - post_count)
                if remaining > 0:
                    eta_seconds = remaining / rate

            target_list.append(
                {
                    "type": t.type,
                    "name": t.name,
                    "enabled": t.enabled,
                    "status": t.status or "active",
                    "icon_url": t.icon_url,
                    "last_created": t.last_created.isoformat() if t.last_created else None,
                    "post_count": post_count,
                    "total_media": tot_media,
                    "downloaded_media": dl_media_cnt,
                    "pending_media": pend_media_cnt,
                    "rate_per_second": round(rate, 4),
                    "eta_seconds": round(eta_seconds, 0) if eta_seconds else None,
                    "progress_percent": min(100, round(post_count / 10, 1))
                    if post_count > 0
                    else 0,
                }
            )

        # New posts since the last SSE cycle (or last 20 if no watermark)
        new_posts_q = (
            db.query(Post)
            .filter(Post.hidden.is_(False))
            .order_by(Post.ingested_at.desc())
            .limit(20)
        )
        if since is not None:
            new_posts_q = new_posts_q.filter(Post.ingested_at > since)
        new_posts = [
            {
                "id": p.id,
                "title": p.title,
                "subreddit": p.subreddit,
                "author": p.author,
                "created_utc": p.created_utc.isoformat() if p.created_utc else None,
            }
            for p in new_posts_q.all()
        ]

        # New media downloads since the last SSE cycle
        new_media_q = (
            db.query(Media)
            .filter(Media.status == "done")
            .order_by(Media.downloaded_at.desc())
            .limit(20)
        )
        if since is not None:
            new_media_q = new_media_q.filter(Media.downloaded_at > since)
        new_media = [
            {
                "id": m.id,
                "post_id": m.post_id,
                "url": m.url,
                "file_path": m.file_path,
                "downloaded_at": m.downloaded_at.isoformat() if m.downloaded_at else None,
            }
            for m in new_media_q.all()
        ]

    # Quick health check (no extra DB connection needed — engine already init'd)
    health = {"db": "ok"}
    try:
        from reddarr.database import _engine
        from sqlalchemy import text as _text
        with _engine.connect() as _conn:
            _conn.execute(_text("SELECT 1"))
    except Exception:
        health["db"] = "error"

    return {
        # Flat fields for frontend SSE handler
        "total_posts": total_posts,
        "hidden_posts": hidden_posts,
        "total_comments": total_comments,
        "downloaded_media": dl_media,
        "pending_media": pending_media,
        "total_media": total_media,
        # queue_length mirrors pending_media so the sidebar indicator works
        "queue_length": pending_media,
        "health": health,
        # Nested stats kept for API consumers
        "stats": {
            "total_posts": total_posts,
            "hidden_posts": hidden_posts,
            "total_comments": total_comments,
            "downloaded_media": dl_media,
            "pending_media": pending_media,
            "total_media": total_media,
        },
        "targets": target_list,
        "new_posts": new_posts,
        "new_media": new_media,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
