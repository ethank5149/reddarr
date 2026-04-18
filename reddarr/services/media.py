"""Media URL extraction and classification.

Consolidates the old shared/media_utils.py, ingester/media.py, and
downloader/media_utils.py into a single authoritative service.

This module handles the "what URLs does this post have?" question.
The actual downloading is handled by services/providers/*.
"""

import logging
import re
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Direct media file extensions
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".mp4", ".webm", ".mov", ".avi", ".mkv",
}

# Known media hosting domains
MEDIA_DOMAINS = {
    "i.redd.it", "v.redd.it", "i.imgur.com", "preview.redd.it",
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


def extract_media_urls(post) -> list[str]:
    """Extract all downloadable media URLs from a PRAW submission.

    Checks multiple sources in priority order:
    1. Direct URL (i.redd.it, i.imgur.com, etc.)
    2. Reddit-hosted video (v.redd.it)
    3. Gallery images
    4. Preview images (fallback)
    5. External links (YouTube, RedGifs, etc.)

    Args:
        post: PRAW Submission object

    Returns:
        List of media URLs to download
    """
    urls = []

    url = getattr(post, "url", "")
    if not url:
        return urls

    # 1. Direct media URL
    if is_direct_media_url(url):
        urls.append(url)
        return urls

    # 2. Reddit video
    if hasattr(post, "media") and post.media:
        reddit_video = post.media.get("reddit_video", {})
        if reddit_video:
            fallback = reddit_video.get("fallback_url", "")
            if fallback:
                urls.append(fallback.split("?")[0])
                return urls

    # 3. Reddit gallery
    if hasattr(post, "is_gallery") and post.is_gallery:
        gallery_urls = _extract_gallery_urls(post)
        if gallery_urls:
            return gallery_urls

    # 4. Crosspost video
    if hasattr(post, "crosspost_parent_list"):
        for xpost in (post.crosspost_parent_list or []):
            media = xpost.get("media") or {}
            rv = media.get("reddit_video", {})
            if rv.get("fallback_url"):
                urls.append(rv["fallback_url"].split("?")[0])
                return urls

    # 5. Preview image (last resort for Reddit-hosted content)
    if not urls and hasattr(post, "preview"):
        preview = post.preview or {}
        images = preview.get("images", [])
        if images:
            source = images[0].get("source", {})
            preview_url = source.get("url", "").replace("&amp;", "&")
            if preview_url:
                urls.append(preview_url)
                return urls

    # 6. External provider URL (YouTube, RedGifs, etc.)
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain in PROVIDER_DOMAINS or any(d in domain for d in ["redgifs", "youtube", "youtu.be"]):
        urls.append(url)

    # 7. v.redd.it bare URL
    if "v.redd.it" in url and url not in urls:
        urls.append(url)

    return urls


def is_direct_media_url(url: str) -> bool:
    """Check if a URL points directly to a media file."""
    if not url:
        return False

    parsed = urlparse(url.split("?")[0].lower())
    domain = parsed.netloc

    # Known media domains
    if domain in MEDIA_DOMAINS:
        return True

    # File extension check
    path = parsed.path
    return any(path.endswith(ext) for ext in MEDIA_EXTENSIONS)


def _extract_gallery_urls(post) -> list[str]:
    """Extract image URLs from a Reddit gallery post."""
    urls = []
    try:
        media_metadata = getattr(post, "media_metadata", None) or {}
        gallery_data = getattr(post, "gallery_data", None)

        if gallery_data and media_metadata:
            items = gallery_data.get("items", [])
            for item in items:
                media_id = item.get("media_id")
                if media_id and media_id in media_metadata:
                    meta = media_metadata[media_id]
                    # Prefer source resolution
                    source = meta.get("s", {})
                    img_url = source.get("u") or source.get("gif")
                    if img_url:
                        urls.append(img_url.replace("&amp;", "&"))
        elif media_metadata:
            for media_id, meta in media_metadata.items():
                source = meta.get("s", {})
                img_url = source.get("u") or source.get("gif")
                if img_url:
                    urls.append(img_url.replace("&amp;", "&"))
    except Exception as e:
        logger.warning(f"Gallery extraction failed: {e}")

    return urls


def extract_redgifs_video_id(url_or_html: str) -> Optional[str]:
    """Extract a RedGifs video ID from a URL or HTML snippet."""
    patterns = [
        r"redgifs\.com/watch/([a-zA-Z]+)",
        r"redgifs\.com/ifr/([a-zA-Z]+)",
        r'"gfyId"\s*:\s*"([a-zA-Z]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_html, re.IGNORECASE)
        if match:
            return match.group(1).lower()
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
