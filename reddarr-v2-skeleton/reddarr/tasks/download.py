"""Download tasks — media fetching, thumbnail generation, deduplication.

Replaces the old downloader/app.py monolith. Instead of a BLPOP worker loop,
each media download is a discrete Celery task with automatic retries.

Migration notes:
  - The old `process_item()` becomes `download_media_item` task
  - The old `worker()` loop is replaced by Celery's worker pool
  - Rate limiting per domain is handled by Celery rate_limit
  - The provider pattern from downloader/providers.py is preserved
"""

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from reddarr.tasks import app
from reddarr.database import SessionLocal, init_engine
from reddarr.models import Media, Post

logger = logging.getLogger(__name__)

# Reusable session with connection pooling
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        from reddarr.config import get_settings
        _session.headers["User-Agent"] = get_settings().reddit_user_agent
    return _session


@app.task(
    name="reddarr.tasks.download.download_media_item",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    rate_limit="10/m",  # per-worker rate limit
    acks_late=True,
)
def download_media_item(self, post_id: str, url: str):
    """Download a single media item for a post.

    This replaces the old BLPOP-based download queue. Each URL is its own
    task, giving us per-item retry, rate limiting, and observability via
    Celery/Flower.

    Args:
        post_id: Reddit post ID
        url: Media URL to download
    """
    from reddarr.services.providers import get_provider
    from reddarr.utils.media import sha256_file, make_thumb, make_filename, get_post_dir

    init_engine()
    session = _get_session()

    with SessionLocal() as db:
        # Check if already downloaded (dedup by post_id + url)
        existing = db.query(Media).filter_by(post_id=post_id, url=url).first()
        if existing and existing.status == "done":
            logger.debug(f"Already downloaded: {post_id} {url[:60]}")
            return {"status": "skipped", "reason": "already_done"}

        # Get post metadata for directory structure
        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            logger.warning(f"Post {post_id} not found, skipping download")
            return {"status": "skipped", "reason": "post_not_found"}

        # Resolve output directory
        from reddarr.config import get_settings
        settings = get_settings()
        post_dir = get_post_dir(post_id, post.subreddit, post.author, settings.archive_path)
        os.makedirs(post_dir, exist_ok=True)

        # Find the right download provider
        provider = get_provider(url)
        logger.info(f"Downloading {url[:60]}... via {provider.__class__.__name__}")

        try:
            result = provider.download(
                url=url,
                post_id=post_id,
                post_dir=post_dir,
                session=session,
            )
        except Exception as e:
            logger.error(f"Download failed for {post_id} {url[:60]}: {e}")
            # Record failure in DB
            _record_media(db, post_id, url, status="failed", error=str(e))
            raise self.retry(exc=e)

        if result["status"] == "done" and result.get("path"):
            # Check dedup by SHA-256
            file_hash = result.get("hash") or sha256_file(result["path"])
            dup = db.query(Media).filter_by(sha256=file_hash).first() if file_hash else None

            if dup and dup.post_id != post_id:
                logger.info(f"Dedup hit: {file_hash[:12]} already exists for post {dup.post_id}")
                # Hard-link instead of duplicate storage
                try:
                    os.link(dup.file_path, result["path"])
                except OSError:
                    pass  # Different filesystem, keep the copy

            # Generate thumbnail if not already done
            thumb_path = result.get("thumb")
            if not thumb_path and result["path"]:
                thumb_path = make_thumb(result["path"], settings.thumb_path)

            _record_media(
                db, post_id, url,
                file_path=result["path"],
                thumb_path=thumb_path,
                sha256=file_hash,
                status="done",
            )
            logger.info(f"Downloaded: {post_id} -> {result['path']}")
            return {"status": "done", "path": result["path"]}
        else:
            _record_media(
                db, post_id, url,
                status=result.get("status", "failed"),
                error=result.get("error"),
            )
            return {"status": result.get("status", "failed")}


def _record_media(
    db,
    post_id: str,
    url: str,
    file_path: str = None,
    thumb_path: str = None,
    sha256: str = None,
    status: str = "pending",
    error: str = None,
):
    """Insert or update a media record."""
    existing = db.query(Media).filter_by(post_id=post_id, url=url).first()
    if existing:
        existing.file_path = file_path or existing.file_path
        existing.thumb_path = thumb_path or existing.thumb_path
        existing.sha256 = sha256 or existing.sha256
        existing.status = status
        existing.error_message = error
        existing.downloaded_at = datetime.now(timezone.utc)
        if status == "failed":
            existing.retries = (existing.retries or 0) + 1
    else:
        media = Media(
            post_id=post_id,
            url=url,
            file_path=file_path,
            thumb_path=thumb_path,
            sha256=sha256,
            status=status,
            error_message=error,
            downloaded_at=datetime.now(timezone.utc) if status == "done" else None,
        )
        db.add(media)
    db.commit()


@app.task(name="reddarr.tasks.download.requeue_failed")
def requeue_failed(max_retries: int = 5):
    """Re-queue all failed downloads that haven't exceeded max retries.

    Replaces the old requeue_gifs.py script.
    """
    init_engine()
    with SessionLocal() as db:
        failed = (
            db.query(Media)
            .filter(Media.status == "failed", Media.retries < max_retries)
            .all()
        )
        count = 0
        for m in failed:
            download_media_item.delay(m.post_id, m.url)
            count += 1
        logger.info(f"Re-queued {count} failed downloads")
        return {"requeued": count}


@app.task(name="reddarr.tasks.download.generate_thumbnails")
def generate_thumbnails(post_id: Optional[str] = None):
    """Regenerate thumbnails for media missing them.

    Replaces the old thumbnails_backfill admin endpoint logic.
    """
    from reddarr.utils.media import make_thumb
    from reddarr.config import get_settings

    init_engine()
    settings = get_settings()

    with SessionLocal() as db:
        query = db.query(Media).filter(
            Media.status == "done",
            Media.file_path.isnot(None),
            Media.thumb_path.is_(None),
        )
        if post_id:
            query = query.filter(Media.post_id == post_id)

        media_items = query.limit(500).all()
        generated = 0
        for m in media_items:
            if m.file_path and os.path.exists(m.file_path):
                thumb = make_thumb(m.file_path, settings.thumb_path)
                if thumb:
                    m.thumb_path = thumb
                    generated += 1

        db.commit()
        logger.info(f"Generated {generated} thumbnails")
        return {"generated": generated}
