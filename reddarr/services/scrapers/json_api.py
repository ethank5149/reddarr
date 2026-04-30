"""Reddit public JSON API scraper.

Reddit exposes unauthenticated JSON for most listing endpoints by appending
'.json' to the URL.  No API key or OAuth token is required.

Limits:
  - 100 posts per page
  - ~60 requests/minute without auth (we stay well under that)
  - Only returns posts that are currently visible on Reddit (not deleted ones)

Pagination uses the 'after' cursor returned in each response.
"""

import logging
import time
from typing import Optional

import requests

from reddarr.services.scrapers import serialize_post_dict

logger = logging.getLogger(__name__)

_BASE = "https://www.reddit.com"
_PAGE_SIZE = 100
_REQUEST_DELAY = 2.0   # seconds between paginated requests
_MAX_PAGES = 10        # hard cap to avoid infinite loops


def fetch_posts_json_api(
    target_type: str,
    target_name: str,
    sort: str = "new",
    time_filter: str = "all",
    limit: int = 100,
    user_agent: Optional[str] = None,
) -> list[dict]:
    """Fetch posts from Reddit's public JSON API.

    Args:
        target_type: 'subreddit' or 'user'
        target_name: subreddit name or username
        sort: 'new', 'hot', 'top', 'rising'  ('rising' only for subreddits)
        time_filter: 'hour','day','week','month','year','all'  (used with sort='top')
        limit: maximum number of posts to return
        user_agent: override User-Agent header

    Returns:
        List of post dicts in the standard ingestion format.

    Raises:
        ValueError: unknown target_type
        requests.HTTPError: non-2xx response that isn't retried
    """
    if user_agent is None:
        try:
            from reddarr.config import get_settings
            user_agent = get_settings().reddit_user_agent
        except Exception:
            user_agent = "Reddarr/2.0 (self-hosted archiver)"

    if target_type == "subreddit":
        _sort = sort if sort in ("new", "hot", "top", "rising") else "new"
        url = f"{_BASE}/r/{target_name}/{_sort}.json"
    elif target_type == "user":
        # User listing only supports new/top
        _sort = sort if sort in ("new", "top") else "new"
        url = f"{_BASE}/user/{target_name}/submitted.json"
    else:
        raise ValueError(f"Unknown target_type: {target_type!r}")

    params: dict = {"limit": min(_PAGE_SIZE, limit), "raw_json": "1"}
    if sort == "top":
        params["t"] = time_filter

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    results: list[dict] = []
    after: Optional[str] = None
    pages = 0

    while len(results) < limit and pages < _MAX_PAGES:
        if after:
            params["after"] = after

        try:
            resp = session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            body = resp.json()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (429, 503):
                # Rate-limited — back off and retry once
                logger.warning(f"JSON API rate-limited ({e.response.status_code}), backing off 30s")
                time.sleep(30)
                try:
                    resp = session.get(url, params=params, timeout=20)
                    resp.raise_for_status()
                    body = resp.json()
                except Exception as retry_exc:
                    logger.error(f"JSON API retry failed: {retry_exc}")
                    raise
            else:
                raise
        except Exception as e:
            logger.error(f"JSON API request error: {e}")
            raise

        listing = body.get("data", {})
        children = listing.get("children", [])
        after = listing.get("after")

        for child in children:
            if child.get("kind") != "t3":
                continue
            try:
                post = serialize_post_dict(child["data"])
                results.append(post)
            except Exception as e:
                logger.warning(f"Failed to serialize post {child.get('data', {}).get('id')}: {e}")

        pages += 1

        if not after or not children or len(results) >= limit:
            break

        time.sleep(_REQUEST_DELAY)

    logger.info(
        f"JSON API: fetched {len(results)} posts for {target_type}:{target_name} "
        f"in {pages} page(s)"
    )
    return results[:limit]
