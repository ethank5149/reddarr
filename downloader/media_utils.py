"""Media utilities for downloader.

Provides utilities for:
- SHA256 hashing
- Image corruption detection
- Thumbnail generation
- URL best image resolution
- Filename sanitization
"""

import os
import re
import hashlib
import subprocess
import requests
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image
import logging

logger = logging.getLogger(__name__)


def sha256(path: str) -> str:
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(131072), b""):
            h.update(c)
    return h.hexdigest()


def detect_image_corruption(path: str) -> bool:
    """Check if an image file is corrupted due to non-thread-safe writes.

    Detects corruption patterns from concurrent writes:
    - Partial writes (truncated files)
    - Interleaved writes (mixed data from multiple threads)
    - Invalid image headers (incomplete/malformed headers)

    Uses multiple detection strategies for robustness.
    """
    try:
        file_size = os.path.getsize(path)
        if file_size < 100:
            logger.warning(
                f"Image corruption detected (truncated): {path} ({file_size} bytes)"
            )
            return True

        with open(path, "rb") as f:
            header = f.read(32)

        if len(header) < 12:
            logger.warning(f"Image corruption detected (incomplete header): {path}")
            return True

        png_sig = b"\x89PNG\r\n\x1a\n"
        jpeg_sig = b"\xff\xd8\xff"
        gif_sig = b"GIF87a" or b"GIF89a"
        webp_sig = b"RIFF"
        webp_riff = b"WEBP"
        webm_sig = b"\x1a\x45\xdf\xa3"

        is_png = header.startswith(png_sig)
        is_jpeg = header.startswith(jpeg_sig)
        is_gif = header.startswith((b"GIF87a", b"GIF89a"))
        is_webp = (
            header.startswith(webp_sig)
            and len(header) >= 12
            and header[8:12] == webp_riff
        )
        is_video = header.startswith(webm_sig) or path.lower().endswith(
            (".mp4", ".webm", ".gifv")
        )

        if is_video:
            return False

        if not (is_png or is_jpeg or is_gif or is_webp):
            logger.warning(
                f"Image corruption detected (invalid header): {path}, header={header[:8]!r}"
            )
            return True

        try:
            with Image.open(path) as img:
                img.verify()
        except Exception as e:
            logger.warning(
                f"Image corruption detected (PIL verify failed): {path}: {e}"
            )
            return True

        try:
            with Image.open(path) as img:
                img.load()
        except Exception as e:
            logger.warning(f"Image corruption detected (PIL load failed): {path}: {e}")
            return True

        return False
    except Exception as e:
        logger.warning(f"Image corruption detection failed for {path}: {e}")
        return True


def make_thumb(path: str, media_dir: str, thumb_dir: str) -> str | None:
    """Create a thumbnail for a media file."""
    try:
        rel = os.path.relpath(path, media_dir)
    except ValueError:
        rel = Path(path).name

    thumb_subdir = Path(thumb_dir) / Path(rel).parent
    thumb_subdir.mkdir(parents=True, exist_ok=True)
    thumb = str(thumb_subdir / (Path(path).stem + ".thumb.jpg"))

    logger.info(f"Creating thumbnail: {thumb}")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vf", "scale=320:-1", "-frames:v", "1", thumb],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        logger.info(f"Thumbnail created: {thumb}")
        return thumb
    else:
        logger.warning(
            f"Thumbnail creation failed for {path}: {result.stderr.decode()[:200]}"
        )
        return None


def get_best_image_url(url: str, session: requests.Session) -> str:
    """Follow redirects to get the final image URL."""
    try:
        r = session.head(url, allow_redirects=True, timeout=10)
        return r.url
    except Exception as e:
        logger.warning(f"Redirect follow error: {e}")
        return url


def sanitize_name(s: str, max_len: int = 60) -> str:
    """Sanitize a string for use as a filename."""
    s = re.sub(r"[^\w\s-]", "", str(s)).strip()
    s = re.sub(r"[\s_]+", "_", s)
    return s[:max_len].strip("_")


def make_filename(
    subreddit: str, author: str, title: str, post_id: str, url: str
) -> str:
    """Generate a filename for a media file."""
    if subreddit and subreddit not in ("", "None"):
        prefix = f"r_{sanitize_name(subreddit, 30)}"
    elif author and author not in ("", "None"):
        prefix = f"u_{sanitize_name(author, 30)}"
    else:
        prefix = post_id

    title_part = sanitize_name(title, 80) if title else ""
    ext = Path(url.split("?")[0]).suffix
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]

    if title_part:
        name = f"{prefix}_{title_part}_{post_id}_{url_hash}{ext}"
    else:
        name = f"{prefix}_{post_id}_{url_hash}{ext}"

    stem = Path(name).stem[:195]
    return stem + ext


def get_post_dir(
    post_id: str, subreddit: str | None, author: str | None, media_dir: str, get_db
) -> str:
    """Get the directory for a post's media files."""

    def _resolve(subreddit, author):
        if subreddit and subreddit not in ("", "None"):
            return Path(media_dir) / "r" / subreddit
        if author and author not in ("", "None"):
            return Path(media_dir) / "u" / author
        return Path(media_dir)

    if subreddit or author:
        d = _resolve(subreddit, author)
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT subreddit, author FROM posts WHERE id = %s", (post_id,))
            row = cur.fetchone()
        if row:
            d = _resolve(row[0], row[1])
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
    except Exception as e:
        logger.warning(f"Could not resolve post dir for {post_id}: {e}")
    return media_dir


def check_existing_media(url: str, get_db) -> tuple | None:
    """Check if media URL has already been downloaded."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path, thumb_path, sha256 FROM media WHERE url = %s AND status = 'done'",
                (url,),
            )
            return cur.fetchone()
    except Exception as e:
        logger.error(f"Error checking existing media for {url}: {e}")
        return None
