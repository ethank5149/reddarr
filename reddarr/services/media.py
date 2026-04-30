"""Media URL extraction and classification.

Consolidates the old shared/media_utils.py, ingester/media.py, and
downloader/media_utils.py into a single authoritative service.

This module handles the "what URLs does this post have?" question.
The actual downloading is handled by services/providers/*.
"""

import logging
import re
import threading
import time
from typing import Optional, List
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Direct media file extensions
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mkv",
}

# Known media hosting domains
MEDIA_DOMAINS = {
    "i.redd.it",
    "v.redd.it",
    "i.imgur.com",
    "preview.redd.it",
    "external-preview.redd.it",
}

# Domains requiring special provider handling
PROVIDER_DOMAINS = {
    "redgifs.com": "redgifs",
    "www.redgifs.com": "redgifs",
    "gfycat.com": "gfycat",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "youtu.be": "youtube",
    "imgur.com": "imgur",
    "www.imgur.com": "imgur",
}

def _get_redgifs_token() -> Optional[str]:
    """Obtain (and cache) a temporary RedGifs API bearer token using Redis."""
    from reddarr.config import get_settings
    import redis

    settings = get_settings()
    r = redis.Redis.from_url(settings.redis_url)

    # Try to get cached token
    cached = r.get("redgifs_token")
    if cached:
        return cached.decode("utf-8")

    # Fetch new token
    try:
        resp = requests.get(
            "https://api.redgifs.com/v2/auth/temporary",
            timeout=10,
            headers={"User-Agent": "reddit-archive/1.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("token")
            if token:
                # Cache for 20 hours (tokens are valid for 24 hours)
                r.setex("redgifs_token", 20 * 3600, token)
                return token
    except Exception as e:
        logger.warning(f"RedGifs auth failed: {e}")
    return None


def classify_url(url: str) -> str:
    """Classify a URL into a provider type.

    Returns: 'reddit_image', 'reddit_video', 'imgur', 'redgifs',
             'youtube', 'generic', or 'unknown'
    """
    if not url:
        return "unknown"

    domain = urlparse(url).netloc.lower()

    if "i.redd.it" in domain or "preview.redd.it" in domain:
        return "reddit_image"
    if "v.redd.it" in domain:
        return "reddit_video"
    if domain in PROVIDER_DOMAINS:
        return PROVIDER_DOMAINS[domain]
    if is_direct_media_url(url):
        return "generic"

    return "unknown"
