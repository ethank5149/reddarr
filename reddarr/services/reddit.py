"""Reddit API service — PRAW client and post fetching.

Consolidates the old ingester/reddit.py, ingester/app.py::_create_reddit_client(),
and ingester/app.py::fetch_target_posts() into a clean service layer.
"""

import logging
from typing import Optional

import praw

from reddarr.config import get_settings
from reddarr.services.media import extract_media_urls

logger = logging.getLogger(__name__)

import threading

_thread_local = threading.local()


def create_reddit_client() -> praw.Reddit:
    """Create or return a thread-local PRAW Reddit client.

    PRAW is not thread-safe, so we use thread-local storage to ensure
    each thread/worker gets its own client instance.
    """
    if hasattr(_thread_local, 'reddit_client') and _thread_local.reddit_client is not None:
        return _thread_local.reddit_client

    settings = get_settings()

    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise RuntimeError(
            "Reddit API credentials not configured. "
            "Set reddit_client_id and reddit_client_secret secrets."
        )

    _thread_local.reddit_client = praw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
    )
    logger.info("Reddit client initialized (read-only, thread-local)")
    return _thread_local.reddit_client


def fetch_posts(
    reddit: praw.Reddit,
    target_type: str,
    target_name: str,
    sort: str = "new",
    time_filter: str = "all",
    limit: Optional[int] = None,
) -> list[dict]:
    """Fetch posts from a subreddit or user.

    Args:
        reddit: PRAW client instance
        target_type: 'subreddit' or 'user'
        target_name: name of the target
        sort: 'new', 'hot', 'top', 'rising'
        time_filter: 'hour', 'day', 'week', 'month', 'year', 'all'
        limit: max posts to fetch (default from settings.scrape_limit)

    Returns:
        List of post dicts ready for ingestion, each with a 'media_urls' key.
    """
    settings = get_settings()
    if limit is None:
        limit = settings.scrape_limit

    try:
        if target_type == "subreddit":
            source = reddit.subreddit(target_name)
        elif target_type == "user":
            source = reddit.redditor(target_name).submissions
        else:
            raise ValueError(f"Unknown target type: {target_type}")

        # Select sort method
        if target_type == "subreddit":
            if sort == "new":
                listing = source.new(limit=limit)
            elif sort == "hot":
                listing = source.hot(limit=limit)
            elif sort == "top":
                listing = source.top(time_filter=time_filter, limit=limit)
            elif sort == "rising":
                listing = source.rising(limit=limit)
            else:
                listing = source.new(limit=limit)
        else:
            # User submissions
            listing = source.new(limit=limit) if sort == "new" else source.top(
                time_filter=time_filter, limit=limit
            )

        results = []
        for post in listing:
            post_data = _serialize_post(post)
            results.append(post_data)

        logger.info(f"Fetched {len(results)} posts from {target_type}:{target_name}")
        return results

    except Exception as e:
        logger.error(f"Failed to fetch {target_type}:{target_name}: {e}")
        raise


def _serialize_post(post) -> dict:
    """Convert a PRAW Submission to a dict for ingestion.

    Extracts all fields needed by the Post model plus media URLs.
    """
    raw = {}
    try:
        raw = vars(post)
        # Remove non-serializable PRAW internals
        raw = {
            k: v for k, v in raw.items()
            if not k.startswith("_") and not callable(v)
        }
    except Exception:
        pass

    media_urls = extract_media_urls(post)

    return {
        "id": post.id,
        "subreddit": str(post.subreddit),
        "author": str(post.author) if post.author else "[deleted]",
        "created_utc": post.created_utc,
        "title": post.title,
        "selftext": getattr(post, "selftext", ""),
        "url": post.url,
        "media_url": media_urls[0] if media_urls else None,
        "raw": raw,
        "media_urls": media_urls,
    }


def fetch_comments(reddit: praw.Reddit, post_id: str, limit: int = 100) -> list[dict]:
    """Fetch comments for a post.

    Args:
        reddit: PRAW client
        post_id: Reddit post ID
        limit: max comments to fetch

    Returns:
        List of comment dicts.
    """
    submission = reddit.submission(id=post_id)
    submission.comments.replace_more(limit=0)

    results = []
    for comment in submission.comments.list()[:limit]:
        results.append({
            "id": comment.id,
            "post_id": post_id,
            "author": str(comment.author) if comment.author else "[deleted]",
            "body": comment.body,
            "created_utc": comment.created_utc,
            "raw": {
                "score": comment.score,
                "is_submitter": comment.is_submitter,
                "parent_id": comment.parent_id,
            },
        })

    return results
