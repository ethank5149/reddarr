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

# RedGifs token caching
_redgifs_token: Optional[str] = None
_redgifs_token_expiry: float = 0.0
_redgifs_token_lock = threading.Lock()


def _get_redgifs_token() -> Optional[str]:
    """Obtain (and cache) a temporary RedGifs API bearer token."""
    global _redgifs_token, _redgifs_token_expiry
    with _redgifs_token_lock:
        if _redgifs_token and time.time() < _redgifs_token_expiry:
            return _redgifs_token
        try:
            resp = requests.get(
                "https://api.redgifs.com/v2/auth/temporary",
                timeout=10,
                headers={"User-Agent": "reddit-archive/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                _redgifs_token = data.get("token")
                _redgifs_token_expiry = time.time() + 20 * 3600
                return _redgifs_token
            else:
                logger.warning(f"RedGifs auth returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to obtain RedGifs token: {e}")
        return _redgifs_token


def fetch_redgifs_video_urls(video_id: str) -> List[str]:
    """Fetch HD/SD video URLs from RedGifs API."""
    urls = []
    token = _get_redgifs_token()
    headers = {"User-Agent": "reddit-archive/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"https://api.redgifs.com/v2/gifs/{video_id}",
            timeout=10,
            headers=headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "gif" in data:
                gif = data["gif"]
                hd = gif.get("urls", {}).get("hd")
                sd = gif.get("urls", {}).get("sd")
                if hd:
                    urls.append(hd)
                if sd:
                    urls.append(sd)
        elif resp.status_code == 401:
            global _redgifs_token_expiry
            _redgifs_token_expiry = 0.0
            token = _get_redgifs_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.get(
                    f"https://api.redgifs.com/v2/gifs/{video_id}",
                    timeout=10,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "gif" in data:
                        gif = data["gif"]
                        hd = gif.get("urls", {}).get("hd")
                        sd = gif.get("urls", {}).get("sd")
                        if hd:
                            urls.append(hd)
                        if sd:
                            urls.append(sd)
    except Exception as e:
        logger.warning(f"Failed to fetch RedGifs video URLs for {video_id}: {e}")
    return urls


def extract_media_urls(post) -> list[str]:
    """Extract all downloadable media URLs from a Reddit post.

    Checks multiple sources in priority order:
    1. Direct URL (i.redd.it, i.imgur.com, etc.)
    2. Reddit-hosted video (v.redd.it) with DASH playlist support
    3. Gallery images (media_metadata, gallery_data)
    4. Preview images with variants (GIF, mp4)
    5. Rich video JSON (embedded video in selftext)
    6. Poll images
    7. Crosspost media
    8. Secure media (Reddit's secure_media field)
    9. External links (YouTube, RedGifs, etc.)

    Args:
        post: PRAW Submission object or dict with raw post data

    Returns:
        List of media URLs to download
    """
    urls = []

    # Handle both PRAW objects and dicts
    if hasattr(post, "__dict__"):
        data = post.__dict__
        url = getattr(post, "url", "")
    else:
        data = post if isinstance(post, dict) else {}
        url = data.get("url", "")

    if not url:
        return urls

    # 1. Direct media URL
    if is_direct_media_url(url):
        urls.append(url)
        return urls

    # 2. Reddit video - handle v.redd.it with DASH playlist
    media = data.get("media") or data.get("secure_media")
    if media:
        if isinstance(media, dict):
            reddit_video = media.get("reddit_video") or {}
            if reddit_video:
                fallback = reddit_video.get("fallback_url", "")
                if fallback:
                    # Get DASH playlist if available (for higher quality)
                    dash_url = reddit_video.get("dash_url")
                    if dash_url:
                        urls.append(dash_url)
                    # Always include fallback
                    urls.append(fallback.split("?")[0])
                    return urls

    # 3. Handle crosspost video
    crosspost_parent_list = data.get("crosspost_parent_list")
    if crosspost_parent_list and isinstance(crosspost_parent_list, list):
        for cp in crosspost_parent_list:
            cp_media = cp.get("media") or cp.get("secure_media") or {}
            if isinstance(cp_media, dict):
                cp_rv = cp_media.get("reddit_video") or {}
                if cp_rv.get("fallback_url"):
                    urls.append(cp_rv["fallback_url"].split("?")[0])

    # 4. Gallery posts - media_metadata
    media_metadata = data.get("media_metadata")
    if media_metadata and isinstance(media_metadata, dict):
        for img_id, img_data in media_metadata.items():
            if not img_data or not isinstance(img_data, dict):
                continue
            if "s" in img_data:
                s = img_data["s"]
                if s and isinstance(s, dict):
                    u = s.get("gif") or s.get("mp4") or s.get("u")
                else:
                    u = None
            elif img_data.get("p") and isinstance(img_data["p"], list) and img_data["p"]:
                u = img_data["p"][-1].get("u") if isinstance(img_data["p"][-1], dict) else None
            else:
                u = None
            if u:
                urls.append(u.replace("&amp;", "&"))

    # 5. Gallery posts - gallery_data
    gallery_data = data.get("gallery_data")
    if gallery_data and isinstance(gallery_data, dict) and media_metadata:
        for item in gallery_data.get("items", []):
            if not item or not isinstance(item, dict):
                continue
            media_id = item.get("media_id")
            if media_id and media_id in media_metadata:
                img_data = media_metadata[media_id]
                if "s" in img_data:
                    s = img_data["s"]
                    u = s.get("gif") or s.get("mp4") or s.get("u") if s else None
                elif img_data.get("p"):
                    u = img_data["p"][-1].get("u") if isinstance(img_data["p"][-1], dict) else None
                else:
                    u = None
                if u:
                    urls.append(u.replace("&amp;", "&"))

    # 6. Preview images with variants
    preview = data.get("preview")
    if preview and isinstance(preview, dict):
        for img in preview.get("images", []):
            source = img.get("source", {})
            u = source.get("url")
            if u:
                urls.append(u.replace("&amp;", "&"))
            # Handle variants (GIF, mp4)
            for var_type, var_imgs in img.get("variants", {}).items():
                if isinstance(var_imgs, dict):
                    vu = var_imgs.get("source", {}).get("url")
                    if vu:
                        urls.append(vu.replace("&amp;", "&"))
                elif isinstance(var_imgs, list):
                    for vi in var_imgs:
                        vu = vi.get("source", {}).get("url")
                        if vu:
                            urls.append(vu.replace("&amp;", "&"))

        # 7. Rich video JSON (embedded video in selftext)
        rich_video = preview.get("rich_video_json")
        if rich_video:
            fallback = rich_video.get("fallback_url")
            if fallback:
                urls.append(fallback.replace("&amp;", "&"))
            dash_url = rich_video.get("dash_url")
            if dash_url:
                urls.append(dash_url.replace("&amp;", "&"))

    # 8. Poll images
    poll_data = data.get("poll_data")
    if poll_data and isinstance(poll_data, dict):
        for option in poll_data.get("options", []):
            img = option.get("image")
            if img and isinstance(img, dict):
                u = img.get("url")
                if u:
                    urls.append(u.replace("&amp;", "&"))

    # 9. Crosspost media_metadata
    if crosspost_parent_list and isinstance(crosspost_parent_list, list):
        for cp in crosspost_parent_list:
            cp_media_metadata = cp.get("media_metadata")
            if cp_media_metadata and isinstance(cp_media_metadata, dict):
                for img_id, img_data in cp_media_metadata.items():
                    if "s" in img_data:
                        s = img_data["s"]
                        u = s.get("gif") or s.get("mp4") or s.get("u") if s else None
                        if u:
                            urls.append(u.replace("&amp;", "&"))
                    elif img_data.get("p"):
                        u = (
                            img_data["p"][-1].get("u")
                            if isinstance(img_data["p"][-1], dict)
                            else None
                        )
                        if u:
                            urls.append(u.replace("&amp;", "&"))
            cp_preview = cp.get("preview")
            if cp_preview and isinstance(cp_preview, dict):
                for img in cp_preview.get("images", []):
                    u = img.get("source", {}).get("url")
                    if u:
                        urls.append(u.replace("&amp;", "&"))
                    for var_type, var_imgs in img.get("variants", {}).items():
                        if isinstance(var_imgs, dict):
                            vu = var_imgs.get("source", {}).get("url")
                            if vu:
                                urls.append(vu.replace("&amp;", "&"))
                        elif isinstance(var_imgs, list):
                            for vi in var_imgs:
                                vu = vi.get("source", {}).get("url")
                                if vu:
                                    urls.append(vu.replace("&amp;", "&"))

    # 10. Secure media (Reddit's secure_media field)
    secure = data.get("secure_media")
    if secure and isinstance(secure, dict):
        if "reddit_video" in secure:
            rv = secure["reddit_video"]
            fallback = rv.get("fallback_url")
            if fallback:
                urls.append(fallback.replace("&amp;", "&"))
        elif "oembed" in secure:
            oembed = secure["oembed"]
            secure_type = secure.get("type", "")
            if "redgifs" in secure_type.lower() or "redgifs" in str(oembed).lower():
                html = oembed.get("html", "")
                video_id = extract_redgifs_video_id(html)
                if video_id:
                    video_urls = fetch_redgifs_video_urls(video_id)
                    urls.extend(video_urls)

    # 11. External provider URL (YouTube, RedGifs, etc.)
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain in PROVIDER_DOMAINS or any(d in domain for d in ["redgifs", "youtube", "youtu.be"]):
        urls.append(url)

    # 12. v.redd.it bare URL
    if "v.redd.it" in url and url not in urls:
        urls.append(url)

    # Deduplicate URLs
    seen = set()
    unique_urls = []
    for u in urls:
        if u:
            u = u.replace("&amp;", "&")
            # Clean preview URLs
            if "preview.redd.it" in u or "external-preview.redd.it" in u:
                u = u.split("?")[0]
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

    return unique_urls


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


def is_video_url(url: Optional[str]) -> bool:
    """Check if a URL is a video URL."""
    if not url:
        return False
    video_patterns = ("v.redd.it", "youtube.com", "youtu.be", "streamable.com", "redgifs.com")
    return any(pat in url.lower() for pat in video_patterns)


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
