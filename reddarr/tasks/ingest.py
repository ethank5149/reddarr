"""Ingest tasks - Reddit API polling and post ingestion.

Replaces the old ingester/app.py monolith. Each function is a Celery task
that can be invoked by the beat scheduler or triggered manually via the API.

Migration notes:
  - The old `run_cycle()` loop becomes `run_ingest_cycle` beat task
  - The old `ingest_post()` remains the core upsert logic, now using ORM
  - `fetch_target_posts()` becomes a helper called within the cycle
  - The old `targets.txt` file is replaced by querying Target model
  - PubSub.publish_media() becomes download_media_item.delay()
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from reddarr.tasks import app
from reddarr.database import get_session_local, init_engine
from reddarr.models import Post, Comment, Media, Target, PostHistory, CommentHistory

logger = logging.getLogger(__name__)


@app.task(name="reddarr.tasks.ingest.run_ingest_cycle", bind=True, max_retries=1)
def run_ingest_cycle(self):
    """Main ingest cycle - queries all enabled targets and ingests new posts.

    This replaces the old `while True: run_cycle(); sleep(POLL_INTERVAL)` loop.
    Celery Beat now handles the scheduling.
    """
    init_engine()

    with get_session_local()() as db:
        targets = db.query(Target).filter(Target.enabled.is_(True)).all()
        logger.info(f"Ingest cycle starting: {len(targets)} enabled targets")

        for target in targets:
            try:
                ingest_target.delay(target.type, target.name)
            except Exception as e:
                logger.error(f"Failed to dispatch ingest for {target.type}:{target.name}: {e}")

    logger.info("Ingest cycle dispatched all targets")


@app.task(name="reddarr.tasks.ingest.ingest_target", bind=True, max_retries=3)
def ingest_target(self, target_type: str, target_name: str):
    """Ingest posts for a single target (subreddit or user).

    Fetches posts from Reddit API and upserts into the database.
    Tries PRAW first; falls back to no-auth scrapers if credentials are
    missing or the API call fails.
    For each post with media, dispatches a download task.

    Args:
        target_type: 'subreddit' or 'user'
        target_name: name of the subreddit or user
    """
    from reddarr.services.reddit import has_credentials, create_reddit_client, fetch_posts
    from reddarr.services.scrapers import fetch_posts_no_auth

    init_engine()

    posts: list[dict] | None = None

    # --- Primary: PRAW (requires API credentials) ---
    if has_credentials():
        try:
            reddit = create_reddit_client()
            posts = fetch_posts(reddit, target_type, target_name)
            logger.debug(f"PRAW fetch succeeded for {target_type}:{target_name}")
        except Exception as e:
            logger.warning(
                f"PRAW fetch failed for {target_type}:{target_name}: {e} — "
                "falling back to no-auth scrapers"
            )
    else:
        logger.info(
            f"No Reddit API credentials configured; using no-auth scrapers "
            f"for {target_type}:{target_name}"
        )

    # --- Fallback: public JSON API → Arctic Shift ---
    if posts is None:
        try:
            posts = fetch_posts_no_auth(target_type, target_name)
        except Exception as e:
            logger.error(f"All fetch methods failed for {target_type}:{target_name}: {e}")
            raise self.retry(exc=e, countdown=60)

    with get_session_local()() as db:
        new_count = 0
        for post_data in posts:
            was_new = _upsert_post(db, post_data)
            if was_new:
                new_count += 1

        # Commit all posts first before dispatching download tasks
        db.commit()

        # Now dispatch download tasks for the newly ingested posts
        for post_data in posts:
            media_urls = post_data.get("media_urls", [])
            for url in media_urls:
                from reddarr.tasks.download import download_media_item
                download_media_item.delay(post_data["id"], url)

        # Update target's last_created timestamp
        target = db.query(Target).filter_by(type=target_type, name=target_name).first()
        if target and posts:
            latest = max(p.get("created_utc", 0) for p in posts)
            if isinstance(latest, (int, float)):
                target.last_created = datetime.fromtimestamp(latest, tz=timezone.utc)
            db.commit()

        logger.info(f"Ingested {new_count} new posts for {target_type}:{target_name}")


def _upsert_post(db, post_data: dict) -> bool:
    """Insert or update a post, creating a history version if changed.

    Returns True if this was a new post.

    This is the core logic from the old ingester/app.py::ingest_post(),
    now using SQLAlchemy ORM instead of raw psycopg2.
    """
    post_id = post_data["id"]
    existing = db.query(Post).filter_by(id=post_id).first()

    content_hash = _compute_hash(
        post_data.get("title", ""),
        post_data.get("selftext", ""),
        post_data.get("url", ""),
    )

    if existing is None:
        # New post
        post = Post(
            id=post_id,
            subreddit=post_data.get("subreddit", ""),
            author=post_data.get("author", ""),
            created_utc=_to_datetime(post_data.get("created_utc")),
            title=post_data.get("title", ""),
            selftext=post_data.get("selftext", ""),
            url=post_data.get("url", ""),
            media_url=post_data.get("media_url"),
            raw=post_data.get("raw"),
        )
        db.add(post)

        # Create first history version
        history = PostHistory(
            post_id=post_id,
            version=1,
            subreddit=post.subreddit,
            author=post.author,
            created_utc=post.created_utc,
            title=post.title,
            selftext=post.selftext,
            url=post.url,
            media_url=post.media_url,
            raw=post.raw,
            version_hash=content_hash,
        )
        db.add(history)
        db.flush()
        return True
    else:
        # Check if content changed
        last_history = (
            db.query(PostHistory)
            .filter_by(post_id=post_id)
            .order_by(PostHistory.version.desc())
            .first()
        )

        if last_history and last_history.version_hash == content_hash:
            return False  # No changes

        # Update existing post
        existing.title = post_data.get("title", existing.title)
        existing.selftext = post_data.get("selftext", existing.selftext)
        existing.url = post_data.get("url", existing.url)
        existing.raw = post_data.get("raw", existing.raw)

        # Create new history version
        next_version = (last_history.version + 1) if last_history else 1
        history = PostHistory(
            post_id=post_id,
            version=next_version,
            subreddit=existing.subreddit,
            author=existing.author,
            created_utc=existing.created_utc,
            title=existing.title,
            selftext=existing.selftext,
            url=existing.url,
            media_url=existing.media_url,
            raw=existing.raw,
            version_hash=content_hash,
        )
        db.add(history)
        db.flush()
        return False


def _compute_hash(*fields: str) -> str:
    """SHA-256 hash for change detection, matching compute_content_hash()."""
    combined = "|".join(f or "" for f in fields)
    return hashlib.sha256(combined.encode()).hexdigest()


def _to_datetime(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    return None


@app.task(name="reddarr.tasks.ingest.trigger_backfill", bind=True)
def trigger_backfill(self, target_type: str, target_name: str, sort: str = "top", time_filter: str = "all", passes: int = 1):
    """Backfill historical posts for a target.

    Uses PRAW when credentials are available; falls back to no-auth scrapers
    (Reddit JSON API → Arctic Shift) otherwise.

    Replaces the old backfill PubSub trigger pattern.
    """
    from reddarr.services.reddit import has_credentials, create_reddit_client, fetch_posts
    from reddarr.services.scrapers import fetch_posts_no_auth

    init_engine()

    # Resolve the fetch callable once before the loop
    if has_credentials():
        try:
            reddit = create_reddit_client()
            def _fetch(s, tf):
                return fetch_posts(reddit, target_type, target_name, sort=s, time_filter=tf)
        except Exception as e:
            logger.warning(f"PRAW client init failed: {e} — will use no-auth scrapers")
            def _fetch(s, tf):
                return fetch_posts_no_auth(target_type, target_name, sort=s, time_filter=tf)
    else:
        logger.info(
            f"No Reddit API credentials; backfill will use no-auth scrapers "
            f"for {target_type}:{target_name}"
        )
        def _fetch(s, tf):
            return fetch_posts_no_auth(target_type, target_name, sort=s, time_filter=tf)

    logger.info(f"Backfill starting: {target_type}:{target_name} sort={sort} time={time_filter}")

    for pass_num in range(passes):
        try:
            posts = _fetch(sort, time_filter)
            with get_session_local()() as db:
                new_posts = []
                for i, post_data in enumerate(posts):
                    was_new = _upsert_post(db, post_data)
                    if was_new:
                        new_posts.append(post_data)

                    # Commit every 100 posts to avoid holding too many in memory
                    if i % 100 == 0:
                        db.commit()

                # Final commit for remaining posts
                db.commit()

                # Then dispatch download tasks
                for post_data in new_posts:
                    media_urls = post_data.get("media_urls", [])
                    for url in media_urls:
                        from reddarr.tasks.download import download_media_item
                        download_media_item.delay(post_data["id"], url)

            logger.info(f"Backfill pass {pass_num + 1}/{passes} complete for {target_type}:{target_name}")
        except Exception as e:
            logger.error(f"Backfill pass {pass_num + 1} failed: {e}")
