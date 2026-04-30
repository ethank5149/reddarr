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

_VIDEO_URL_PATTERNS = (
    "v.redd.it",
    "youtube.com",
    "youtu.be",
    "streamable.com",
    "redgifs.com",
)


def is_video_url(url: Optional[str]) -> bool:
    """Check if a URL is a video URL."""
    if not url:
        return False
    return any(pat in url for pat in _VIDEO_URL_PATTERNS)


def is_direct_media_url(url: str) -> bool:
    """Check if a URL points to a direct media file."""
    if not url:
        return False
    domain = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if domain in MEDIA_DOMAINS:
        return True
    return any(path.endswith(ext) for ext in MEDIA_EXTENSIONS)


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


def extract_media_urls(post) -> list:
    """Extract all downloadable media URLs from a PRAW submission.

    Handles direct images, Reddit galleries, Reddit-hosted videos,
    and third-party hosts (imgur, redgifs, etc.).

    Args:
        post: A PRAW Submission object.

    Returns:
        List of URL strings. May be empty for text/link posts with no media.
    """
    urls = []

    # Reddit galleries (multiple images in one post)
    if getattr(post, "is_gallery", False) and hasattr(post, "gallery_data"):
        try:
            media_metadata = getattr(post, "media_metadata", {}) or {}
            for item in post.gallery_data.get("items", []):
                media_id = item.get("media_id")
                if not media_id:
                    continue
                meta = media_metadata.get(media_id, {})
                # Prefer the source image; fall back to largest preview
                source = meta.get("s", {})
                url = source.get("u") or source.get("gif")
                if url:
                    urls.append(url.replace("&amp;", "&"))
        except Exception as e:
            logger.warning(f"Gallery extraction failed for {post.id}: {e}")
        return urls

    # Reddit-hosted video (v.redd.it)
    if getattr(post, "is_video", False):
        try:
            media = getattr(post, "media", None) or {}
            reddit_video = media.get("reddit_video", {})
            video_url = reddit_video.get("fallback_url") or reddit_video.get("hls_url")
            if video_url:
                urls.append(video_url)
                return urls
        except Exception as e:
            logger.warning(f"Reddit video extraction failed for {post.id}: {e}")

    # Direct URL or supported third-party host
    url = getattr(post, "url", None)
    if url and is_direct_media_url(url):
        urls.append(url)
    elif url:
        domain = urlparse(url).netloc.lower()
        if domain in PROVIDER_DOMAINS:
            urls.append(url)

    return urls


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
