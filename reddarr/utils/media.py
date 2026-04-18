"""Media utility functions — hashing, thumbnails, filenames, corruption detection.

Consolidates the duplicated logic from shared/media_utils.py,
downloader/media_utils.py, and downloader/app.py into one module.
"""

import hashlib
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def sha256_file(path: str) -> Optional[str]:
    """Compute SHA-256 hash of a file."""
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_filename(post_id: str, url: str, max_len: int = 80) -> str:
    """Generate a safe filename from a post ID and URL.

    The filename is deterministic for the same (post_id, url) pair.
    """
    parsed = urlparse(url)
    path = parsed.path
    ext = Path(path).suffix.lower()

    # Sanitize
    base = re.sub(r"[^\w\-.]", "_", Path(path).stem)[:max_len]
    if not base:
        base = hashlib.md5(url.encode()).hexdigest()[:12]

    return f"{post_id}_{base}{ext}" if ext else f"{post_id}_{base}"


def sanitize_name(s: str, max_len: int = 60) -> str:
    """Sanitize a string for use in filenames."""
    s = re.sub(r"[^\w\s\-.]", "", s)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:max_len]


def get_post_dir(
    post_id: str,
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
    archive_path: str = "/data/archive",
) -> str:
    """Get the directory path for a post's media files.

    Structure: {archive_path}/{subreddit}/{post_id}/
    Falls back to: {archive_path}/_unsorted/{post_id}/
    """
    if subreddit:
        return os.path.join(archive_path, sanitize_name(subreddit), post_id)
    elif author:
        return os.path.join(archive_path, f"u_{sanitize_name(author)}", post_id)
    else:
        return os.path.join(archive_path, "_unsorted", post_id)


def make_thumb(
    source_path: str,
    thumb_base_dir: str = "/data/archive/.thumbs",
    scale: str = "320:-1",
) -> Optional[str]:
    """Generate a thumbnail for an image or video file.

    Uses ffmpeg for both image and video thumbnails.
    Returns the thumbnail path or None on failure.
    """
    if not source_path or not os.path.exists(source_path):
        return None

    # Determine output path
    rel = os.path.basename(source_path)
    thumb_name = Path(rel).stem + ".jpg"
    thumb_path = os.path.join(thumb_base_dir, thumb_name)

    os.makedirs(os.path.dirname(thumb_path), exist_ok=True)

    if os.path.exists(thumb_path):
        return thumb_path

    try:
        source_lower = source_path.lower()
        is_video = any(
            source_lower.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".avi", ".mkv")
        )

        if is_video:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                source_path,
                "-ss",
                "00:00:01",
                "-vframes",
                "1",
                "-vf",
                f"scale={scale}",
                "-q:v",
                "5",
                thumb_path,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                source_path,
                "-vf",
                f"scale={scale}",
                "-q:v",
                "5",
                thumb_path,
            ]

        proc = subprocess.run(cmd, capture_output=True, timeout=30)

        if proc.returncode == 0 and os.path.exists(thumb_path):
            return thumb_path
        else:
            logger.debug(f"Thumbnail generation failed for {source_path}")
            return None

    except subprocess.TimeoutExpired:
        logger.warning(f"Thumbnail generation timed out for {source_path}")
        return None
    except Exception as e:
        logger.warning(f"Thumbnail generation error: {e}")
        return None


def detect_image_corruption(path: str) -> bool:
    """Check if an image file is corrupted due to non-thread-safe writes.

    Detects corruption patterns from concurrent writes:
    - Partial writes (truncated files)
    - Interleaved writes (mixed data from multiple threads)
    - Invalid image headers (incomplete/malformed headers)

    Uses multiple detection strategies for robustness.
    """
    if not path or not os.path.exists(path):
        return True

    try:
        file_size = os.path.getsize(path)
        if file_size < 100:
            logger.warning(f"Image corruption detected (truncated): {path} ({file_size} bytes)")
            return True

        with open(path, "rb") as f:
            header = f.read(32)

        if len(header) < 12:
            logger.warning(f"Image corruption detected (incomplete header): {path}")
            return True

        png_sig = b"\x89PNG\r\n\x1a\n"
        jpeg_sig = b"\xff\xd8\xff"
        webp_sig = b"RIFF"
        webp_riff = b"WEBP"
        webm_sig = b"\x1a\x45\xdf\xa3"

        is_png = header.startswith(png_sig)
        is_jpeg = header.startswith(jpeg_sig)
        is_gif = header.startswith((b"GIF87a", b"GIF89a"))
        is_webp = header.startswith(webp_sig) and len(header) >= 12 and header[8:12] == webp_riff
        is_video = header.startswith(webm_sig) or path.lower().endswith((".mp4", ".webm", ".gifv"))

        if is_video:
            return False

        if not (is_png or is_jpeg or is_gif or is_webp):
            logger.warning(
                f"Image corruption detected (invalid header): {path}, header={header[:8]!r}"
            )
            return True

        from PIL import Image

        try:
            with Image.open(path) as img:
                img.verify()
        except Exception as e:
            logger.warning(f"Image corruption detected (PIL verify failed): {path}: {e}")
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
