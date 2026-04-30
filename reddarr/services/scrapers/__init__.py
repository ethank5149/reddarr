"""Fallback scrapers for Reddit data extraction without PRAW / API keys.

Scraper priority (highest to lowest):
  1. Reddit public JSON API  — live data, no auth, ~100/page
  2. Arctic Shift API        — historical archive, no auth, covers deleted content
"""

import logging
from types import SimpleNamespace
from typing import Optional

logger = logging.getLogger(__name__)


def dict_to_post_like(data: dict) -> SimpleNamespace:
    """Wrap a raw Reddit API post dict in a SimpleNamespace so that
    extract_media_urls() can consume it with getattr() the same way it
    would consume a PRAW Submission object.
    """
    ns = SimpleNamespace(**data)
    # Ensure nested dicts are present so .get() calls don't blow up
    ns.media = data.get("media") or {}
    ns.gallery_data = data.get("gallery_data") or {}
    ns.media_metadata = data.get("media_metadata") or {}
    return ns


def serialize_post_dict(data: dict) -> dict:
    """Convert a raw Reddit API post dict to the ingestion-ready format that
    _upsert_post() expects — same shape as _serialize_post() in services/reddit.py.
    """
    from reddarr.services.media import extract_media_urls

    post_like = dict_to_post_like(data)
    media_urls = extract_media_urls(post_like)

    raw = {k: v for k, v in data.items()}

    return {
        "id": data["id"],
        "subreddit": data.get("subreddit", ""),
        "author": data.get("author") or "[deleted]",
        "created_utc": data.get("created_utc", 0),
        "title": data.get("title", ""),
        "selftext": data.get("selftext", ""),
        "url": data.get("url", ""),
        "media_url": media_urls[0] if media_urls else None,
        "raw": raw,
        "media_urls": media_urls,
    }


def fetch_posts_no_auth(
    target_type: str,
    target_name: str,
    sort: str = "new",
    time_filter: str = "all",
    limit: int = 100,
) -> list[dict]:
    """Fetch posts using no-auth fallback scrapers.

    Tries Reddit's public JSON API first (most current data), then
    Arctic Shift (historical archive, survives deletions).

    Raises RuntimeError if all scrapers fail.
    """
    from reddarr.services.scrapers.json_api import fetch_posts_json_api
    from reddarr.services.scrapers.arctic_shift import fetch_posts_arctic_shift

    errors: list[str] = []

    try:
        posts = fetch_posts_json_api(
            target_type, target_name,
            sort=sort, time_filter=time_filter, limit=limit,
        )
        if posts:
            logger.info(
                f"No-auth fallback (JSON API) returned {len(posts)} posts "
                f"for {target_type}:{target_name}"
            )
            return posts
    except Exception as e:
        errors.append(f"JSON API: {e}")
        logger.warning(f"JSON API scraper failed for {target_type}:{target_name}: {e}")

    try:
        posts = fetch_posts_arctic_shift(
            target_type, target_name,
            sort=sort, time_filter=time_filter, limit=limit,
        )
        if posts:
            logger.info(
                f"No-auth fallback (Arctic Shift) returned {len(posts)} posts "
                f"for {target_type}:{target_name}"
            )
            return posts
    except Exception as e:
        errors.append(f"Arctic Shift: {e}")
        logger.warning(f"Arctic Shift scraper failed for {target_type}:{target_name}: {e}")

    raise RuntimeError(
        f"All no-auth scrapers failed for {target_type}:{target_name}: "
        + "; ".join(errors)
    )
