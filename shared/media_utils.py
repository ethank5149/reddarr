import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

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
    """Check if a URL points to direct media."""
    lower = url.lower().split("?")[0]
    if any(lower.endswith(ext) for ext in _DIRECT_IMAGE_EXTS):
        return True
    return any(host in url for host in _DIRECT_MEDIA_HOSTS)


def extract_redgifs_video_id(url_or_html: str) -> Optional[str]:
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


def extract_video_url(url: Optional[str], raw: Optional[dict]) -> Optional[str]:
    """Extract a playable video URL from post data."""
    if not url:
        return None
    if "v.redd.it" in url:
        if raw:
            media = raw.get("media") or {}
            rv = media.get("reddit_video") or {}
            fallback = rv.get("fallback_url")
            if fallback:
                return fallback.split("?")[0]
            for cp in raw.get("crosspost_parent_list", []):
                media2 = cp.get("media") or {}
                rv2 = media2.get("reddit_video") or {}
                fb2 = rv2.get("fallback_url")
                if fb2:
                    return fb2.split("?")[0]
        return url
    if "youtube.com" in url or "youtu.be" in url:
        return url
    if is_video_url(url):
        return url
    return None


def extract_media_urls(post) -> list[str]:
    """Extract all media URLs from a Reddit post.

    Covers:
    - media_metadata (gallery posts, uploaded images)
    - gallery_data (gallery post metadata)
    - Direct URL (i.redd.it, v.redd.it, external images)
    - preview images (fallback)
    - crosspost media_metadata and preview
    - rich_video_json (embedded video in selftext)
    - poll images
    - secure_media (Reddit's secure_media field)
    - media (legacy field)
    """
    urls = []
    data = post.__dict__

    media_metadata = data.get("media_metadata")
    if media_metadata and isinstance(media_metadata, dict):
        for img_id, img_data in media_metadata.items():
            if "s" in img_data:
                s = img_data["s"]
                u = s.get("gif") or s.get("mp4") or s.get("u")
            elif img_data.get("p"):
                u = img_data["p"][-1].get("u")
            else:
                u = None
            if u:
                urls.append(u)

    gallery_data = data.get("gallery_data")
    if gallery_data and isinstance(gallery_data, dict):
        for item in gallery_data.get("items", []):
            media_id = item.get("media_id")
            if media_id and media_metadata:
                img_data = media_metadata.get(media_id)
                if img_data:
                    if "s" in img_data:
                        s = img_data["s"]
                        u = s.get("gif") or s.get("mp4") or s.get("u")
                    elif img_data.get("p"):
                        u = img_data["p"][-1].get("u")
                    else:
                        u = None
                    if u:
                        urls.append(u)

    post_url = getattr(post, "url", None)
    if post_url:
        if is_direct_media_url(post_url):
            urls.append(post_url)

    preview = data.get("preview")
    if preview and isinstance(preview, dict):
        imgs = preview.get("images", [])
        for img in imgs:
            u = img.get("source", {}).get("url")
            if u:
                urls.append(u)
            for var_type, var_imgs in img.get("variants", {}).items():
                if isinstance(var_imgs, dict):
                    vu = var_imgs.get("source", {}).get("url")
                    if vu:
                        urls.append(vu)
                elif isinstance(var_imgs, list):
                    for vi in var_imgs:
                        vu = vi.get("source", {}).get("url")
                        if vu:
                            urls.append(vu)

        rich_video = preview.get("rich_video_json")
        if rich_video:
            fallback = rich_video.get("fallback_url")
            if fallback:
                urls.append(fallback)
            dash_url = rich_video.get("dash_url")
            if dash_url:
                urls.append(dash_url)

    poll_data = data.get("poll_data")
    if poll_data and isinstance(poll_data, dict):
        for option in poll_data.get("options", []):
            img = option.get("image")
            if img and isinstance(img, dict):
                u = img.get("url")
                if u:
                    urls.append(u)

    crosspost_parent_list = data.get("crosspost_parent_list")
    if crosspost_parent_list and isinstance(crosspost_parent_list, list):
        for cp in crosspost_parent_list:
            cp_media_metadata = cp.get("media_metadata")
            if cp_media_metadata and isinstance(cp_media_metadata, dict):
                for img_id, img_data in cp_media_metadata.items():
                    if "s" in img_data:
                        s = img_data["s"]
                        u = s.get("gif") or s.get("mp4") or s.get("u")
                        if u:
                            urls.append(u)
                    elif img_data.get("p"):
                        u = img_data["p"][-1].get("u")
                        if u:
                            urls.append(u)
            cp_preview = cp.get("preview")
            if cp_preview and isinstance(cp_preview, dict):
                for img in cp_preview.get("images", []):
                    u = img.get("source", {}).get("url")
                    if u:
                        urls.append(u)
                    for var_type, var_imgs in img.get("variants", {}).items():
                        if isinstance(var_imgs, dict):
                            vu = var_imgs.get("source", {}).get("url")
                            if vu:
                                urls.append(vu)
                        elif isinstance(var_imgs, list):
                            for vi in var_imgs:
                                vu = vi.get("source", {}).get("url")
                                if vu:
                                    urls.append(vu)

    secure = data.get("secure_media")
    if secure and isinstance(secure, dict):
        if "reddit_video" in secure:
            rv = secure["reddit_video"]
            fallback = rv.get("fallback_url")
            if fallback:
                urls.append(fallback)

    media = data.get("media")
    if media and isinstance(media, dict):
        if "reddit_video" in media:
            rv = media["reddit_video"]
            fallback = rv.get("fallback_url")
            if fallback:
                urls.append(fallback)

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


def make_thumb(
    path: str,
    media_dir: str,
    thumb_dir: str,
    scale: str = "320:-1",
) -> Optional[str]:
    """Create a thumbnail for a media file using ffmpeg.

    Args:
        path: Path to the media file
        media_dir: Base directory for media files
        thumb_dir: Base directory for thumbnails
        scale: FFmpeg scale filter

    Returns:
        Path to the created thumbnail, or None if failed
    """
    try:
        rel = os.path.relpath(path, media_dir)
    except ValueError:
        rel = Path(path).name

    thumb_subdir = Path(thumb_dir) / Path(rel).parent
    thumb_subdir.mkdir(parents=True, exist_ok=True)
    thumb = str(thumb_subdir / (Path(path).stem + ".thumb.jpg"))

    logger.info(f"Creating thumbnail: {thumb}")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vf", scale, "-frames:v", "1", thumb],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        logger.info(f"Thumbnail created: {thumb}")
    else:
        logger.warning(
            f"Thumbnail creation failed for {path}: {result.stderr.decode()[:200]}"
        )
        return None
    return thumb


def detect_image_corruption(path: str) -> bool:
    """Check if an image file is corrupted.

    Uses PIL to verify the image can be opened.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            img.load()
        return False
    except Exception as e:
        logger.warning(f"Image corruption detected for {path}: {e}")
        return True


def sha256(path: str) -> str:
    """Calculate SHA256 hash of a file."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(131072), b""):
            h.update(c)
    return h.hexdigest()
