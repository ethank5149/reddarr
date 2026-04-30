"""Arctic Shift API scraper.

Arctic Shift (https://arctic-shift.photon-reddit.com) is a public archive of
Reddit data that includes posts and comments that have since been deleted from
Reddit itself.  It provides a free, unauthenticated REST API.

Key advantages over the live Reddit JSON API:
  - Contains deleted posts/comments
  - No rate-limit as strict
  - Good for historical backfill

API reference: https://arctic-shift.photon-reddit.com/api-docs
"""

import logging
import time
from typing import Optional

import requests

from reddarr.services.scrapers import serialize_post_dict

logger = logging.getLogger(__name__)

_BASE = "https://arctic-shift.photon-reddit.com/api"
_PAGE_SIZE = 100
_REQUEST_DELAY = 1.0   # seconds between pages
_MAX_PAGES = 10


def fetch_posts_arctic_shift(
    target_type: str,
    target_name: str,
    sort: str = "new",
    time_filter: str = "all",
    limit: int = 100,
) -> list[dict]:
    """Fetch posts from the Arctic Shift public archive API.

    Args:
        target_type: 'subreddit' or 'user'
        target_name: subreddit name or username
        sort: 'new' or 'top' ('hot'/'rising' are mapped to 'new'/'score')
        time_filter: used to compute an 'after' timestamp for 'top' queries
        limit: maximum number of posts to return

    Returns:
        List of post dicts in the standard ingestion format.

    Raises:
        ValueError: unknown target_type
        requests.HTTPError: non-2xx response
    """
    url = f"{_BASE}/posts/search"

    params: dict = {"limit": min(_PAGE_SIZE, limit)}

    if target_type == "subreddit":
        params["subreddit"] = target_name
    elif target_type == "user":
        params["author"] = target_name
    else:
        raise ValueError(f"Unknown target_type: {target_type!r}")

    # Arctic Shift sort options: 'score' (≈top) or default chronological (≈new)
    if sort == "top":
        params["sort"] = "score"
    # 'new', 'hot', 'rising' → use default (chronological desc)

    session = requests.Session()
    session.headers.update({"User-Agent": "Reddarr/2.0 (self-hosted archiver)"})

    results: list[dict] = []
    pages = 0
    # Cursor: use created_utc of the last seen post for pagination
    before_utc: Optional[int] = None

    while len(results) < limit and pages < _MAX_PAGES:
        page_params = dict(params)
        if before_utc is not None:
            page_params["before"] = str(before_utc)

        try:
            resp = session.get(url, params=page_params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("Arctic Shift rate-limited, backing off 30s")
                time.sleep(30)
                try:
                    resp = session.get(url, params=page_params, timeout=30)
                    resp.raise_for_status()
                    body = resp.json()
                except Exception as retry_exc:
                    logger.error(f"Arctic Shift retry failed: {retry_exc}")
                    raise
            else:
                raise
        except Exception as e:
            logger.error(f"Arctic Shift request error: {e}")
            raise

        # Arctic Shift returns {"data": [...]} where each item is a post object
        raw_posts = body.get("data", [])
        if not raw_posts:
            break

        for raw in raw_posts:
            try:
                post = _normalize_arctic_post(raw)
                results.append(post)
            except Exception as e:
                logger.warning(f"Failed to serialize Arctic Shift post {raw.get('id')}: {e}")

        # Advance cursor to the oldest post in this page
        try:
            before_utc = int(raw_posts[-1].get("created_utc", 0))
        except (TypeError, ValueError):
            break

        pages += 1

        if len(raw_posts) < page_params["limit"] or len(results) >= limit:
            break

        time.sleep(_REQUEST_DELAY)

    logger.info(
        f"Arctic Shift: fetched {len(results)} posts for {target_type}:{target_name} "
        f"in {pages} page(s)"
    )
    return results[:limit]


def _normalize_arctic_post(raw: dict) -> dict:
    """Normalize an Arctic Shift post record to match the Reddit API shape
    expected by serialize_post_dict().

    Arctic Shift posts closely mirror the Reddit API structure but may omit
    some fields or use slightly different types.
    """
    # Arctic Shift uses integer timestamps; ensure float for consistency
    if "created_utc" in raw:
        try:
            raw["created_utc"] = float(raw["created_utc"])
        except (TypeError, ValueError):
            pass

    # Ensure 'media' is a dict so getattr lookups don't fail
    if not isinstance(raw.get("media"), dict):
        raw["media"] = {}
    if not isinstance(raw.get("gallery_data"), dict):
        raw["gallery_data"] = {}
    if not isinstance(raw.get("media_metadata"), dict):
        raw["media_metadata"] = {}

    # 'selftext' may be None in Arctic Shift; normalize to empty string
    if raw.get("selftext") is None:
        raw["selftext"] = ""

    # 'author' may be absent for deleted posts
    if not raw.get("author"):
        raw["author"] = "[deleted]"

    return serialize_post_dict(raw)
