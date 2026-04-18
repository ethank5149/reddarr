"""Media URL extraction from Reddit posts.

Extracts all media URLs from a Reddit post including:
- media_metadata (gallery posts)
- gallery_data
- direct URLs (i.redd.it, v.redd.it)
- preview images
- crosspost media
- rich_video_json
- poll images
- secure media (Reddit video, RedGifs, YouTube)
"""

import re
import requests
from typing import List, Optional
from datetime import datetime, timezone
import hashlib
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

_DIRECT_IMAGE_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".gifv",
    ".webm",
)
_DIRECT_MEDIA_HOSTS = (
    "i.redd.it",
    "v.redd.it",
    "youtube.com",
    "youtu.be",
    "i.imgur.com",
)

_redgifs_token: str | None = None
_redgifs_token_expiry: float = 0.0
_redgifs_token_lock = threading.Lock()


def _extract_redgifs_video_id(url_or_html: str) -> str | None:
    """Extract RedGifs video ID from iframe HTML or URL."""
    patterns = [
        r"redgifs\.com/ifr/([a-zA-Z0-9]+)",
        r"redgifs\.com/watch/([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_html)
        if match:
            return match.group(1)
    return None


def _get_redgifs_token() -> str | None:
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
        except Exception as e:
            logger.warning(f"Failed to obtain RedGifs token: {e}")
    return _redgifs_token


def _parse_redgifs_urls(data: dict) -> list[str]:
    """Extract HD/SD URLs from RedGifs API response."""
    urls = []
    if "gif" in data:
        gif = data["gif"]
        hd = gif.get("urls", {}).get("hd")
        sd = gif.get("urls", {}).get("sd")
        if hd:
            urls.append(hd)
        if sd:
            urls.append(sd)
    return urls


def _fetch_redgifs_video_urls(video_id: str) -> list[str]:
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
            urls = _parse_redgifs_urls(resp.json())
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
                    urls = _parse_redgifs_urls(resp.json())
    except Exception as e:
        logger.warning(f"Failed to fetch RedGifs video URLs for {video_id}: {e}")
    return urls


def _fetch_youtube_video_url(post_url: str) -> str | None:
    """Fetch best quality YouTube video URL using oembed API."""
    try:
        match = re.search(
            r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})", post_url
        )
        if match:
            video_id = match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"
    except Exception as e:
        logger.warning(f"Failed to get YouTube info for {post_url}: {e}")
    return None


def _is_direct_media_url(url: str) -> bool:
    """Check if URL is a direct media URL."""
    lower = url.lower().split("?")[0]
    if any(lower.endswith(ext) for ext in _DIRECT_IMAGE_EXTS):
        return True
    return any(host in url for host in _DIRECT_MEDIA_HOSTS)


def extract_media_urls(post) -> List[str]:
    """Extract all media URLs from a Reddit post."""
    urls = []
    data = post.__dict__

    # 1. media_metadata
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
            elif (
                img_data.get("p") and isinstance(img_data["p"], list) and img_data["p"]
            ):
                u = (
                    img_data["p"][-1].get("u")
                    if isinstance(img_data["p"][-1], dict)
                    else None
                )
            else:
                u = None
            if u:
                urls.append(u)

    # 2. gallery_data
    gallery_data = data.get("gallery_data")
    if gallery_data and isinstance(gallery_data, dict):
        for item in gallery_data.get("items", []):
            if not item or not isinstance(item, dict):
                continue
            media_id = item.get("media_id")
            if media_id and media_metadata:
                img_data = media_metadata.get(media_id)
                if img_data and isinstance(img_data, dict):
                    if "s" in img_data:
                        s = img_data["s"]
                        u = s.get("gif") or s.get("mp4") or s.get("u") if s else None
                    elif img_data.get("p"):
                        u = (
                            img_data["p"][-1].get("u")
                            if isinstance(img_data["p"][-1], dict)
                            else None
                        )
                    else:
                        u = None
                    if u:
                        urls.append(u)

    # 3. Direct post URL
    post_url = getattr(post, "url", None)
    if post_url:
        if _is_direct_media_url(post_url):
            urls.append(post_url)
        elif "youtube.com" in post_url.lower() or "youtu.be" in post_url.lower():
            yt_url = _fetch_youtube_video_url(post_url)
            if yt_url:
                urls.append(yt_url)

    # 4. Preview images
    preview = data.get("preview")
    if preview and isinstance(preview, dict):
        for img in preview.get("images", []):
            u = img.get("source", {}).get("url")
            if u:
                urls.append(u)
            for var_type, var_imgs in img.get("variants", {}).items():
                if isinstance(var_imgs, dict):
                    vu = var_imgs.get("source", {}).get("url")
                    if vu:
                        urls.append(vu)

    # 5. rich_video_json
    rich_video = preview.get("rich_video_json") if preview else None
    if rich_video:
        fallback = rich_video.get("fallback_url")
        if fallback:
            urls.append(fallback)
        dash_url = rich_video.get("dash_url")
        if dash_url:
            urls.append(dash_url)

    # 6. Poll images
    poll_data = data.get("poll_data")
    if poll_data and isinstance(poll_data, dict):
        for option in poll_data.get("options", []):
            img = option.get("image")
            if img and isinstance(img, dict):
                u = img.get("url")
                if u:
                    urls.append(u)

    # 7. Crosspost media
    if data.get("crosspost_parent_list"):
        for cp in data.get("crosspost_parent_list", []):
            for img_id, img_data in cp.get("media_metadata", {}).items():
                if "s" in img_data:
                    s = img_data["s"]
                    u = s.get("gif") or s.get("mp4") or s.get("u") if s else None
                    if u:
                        urls.append(u)
            if cp.get("preview"):
                for img in cp["preview"].get("images", []):
                    u = img.get("source", {}).get("url")
                    if u:
                        urls.append(u)

    # 8. Secure media
    secure = data.get("secure_media")
    if secure and isinstance(secure, dict):
        if "reddit_video" in secure:
            rv = secure["reddit_video"]
            fallback = rv.get("fallback_url")
            if fallback:
                urls.append(fallback)
        elif "oembed" in secure:
            oembed = secure["oembed"]
            secure_type = secure.get("type", "")
            if "redgifs" in secure_type.lower() or "redgifs" in str(oembed).lower():
                html = oembed.get("html", "")
                video_id = _extract_redgifs_video_id(html)
                if video_id:
                    video_urls = _fetch_redgifs_video_urls(video_id)
                    urls.extend(video_urls)
                else:
                    thumbnail = oembed.get("thumbnail_url")
                    if thumbnail:
                        urls.append(thumbnail)
            elif "youtube" in secure_type.lower():
                yt_url = _fetch_youtube_video_url(post_url or "")
                if yt_url:
                    urls.append(yt_url)
                else:
                    thumbnail = oembed.get("thumbnail_url")
                    if thumbnail:
                        urls.append(thumbnail)

    # 9. Media object (legacy)
    media = data.get("media")
    if media and isinstance(media, dict):
        if "reddit_video" in media:
            rv = media["reddit_video"]
            fallback = rv.get("fallback_url")
            if fallback:
                urls.append(fallback)

    # Deduplicate
    seen = set()
    unique_urls = []
    for u in urls:
        if u:
            u = u.replace("&amp;", "&")
            if "preview.redd.it" in u or "external-preview.redd.it" in u:
                u = u.split("?")[0]
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

    return unique_urls
