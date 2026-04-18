"""Download providers for different media sources.

Implements a provider pattern for handling different sources:
- Reddit images/videos (i.redd.it, v.redd.it)
- Reddit preview images (preview.redd.it)
- Videos (YouTube, RedGifs, v.redd.it)
- External links (generic extraction)

This makes it easy to add support for new sites (Twitter/X, TikTok, etc.)
"""

import re
import subprocess
import os
import requests
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class DownloadProvider(ABC):
    """Base class for download providers."""

    @abstractmethod
    def match(self, url: str) -> bool:
        """Check if this provider handles the given URL."""
        pass

    @abstractmethod
    def download(
        self,
        url: str,
        post_id: str,
        post_dir: str,
        session: requests.Session,
        media_utils,
    ) -> dict:
        """Download the media and return result dict with path, thumb, hash, status."""
        pass


class RedditImageProvider(DownloadProvider):
    """Provider for Reddit-hosted images."""

    def match(self, url: str) -> bool:
        lower = url.lower()
        return "i.redd.it" in lower or lower.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif")
        )

    def download(self, url, post_id, post_dir, session, media_utils) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        if "i.redd.it" in url and not url.lower().split("?")[0].endswith(".gif"):
            url = media_utils.get_best_image_url(url, session)
            logger.info(f"High-res URL: {url[:60]}...")

        if "preview.redd.it" in url or "external-preview.redd.it" in url:
            if not url.lower().split("?")[0].endswith(".gif"):
                url = media_utils.get_best_image_url(url, session)
                logger.info(f"Following preview to: {url[:60]}...")
            else:
                url = url.split("?")[0]

        r = session.get(url, stream=True, timeout=60)
        if r.status_code != 200:
            return result

        name = media_utils.make_filename("", "", "", post_id, url)
        path = f"{post_dir}/{name}"

        bytes_written = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
                    bytes_written += len(chunk)

        if media_utils.detect_image_corruption(path):
            logger.warning(f"Corrupt image detected for {post_id}")
            result["status"] = "corrupted"

        result["path"] = path
        result["hash"] = media_utils.sha256(path)
        result["thumb"] = media_utils.make_thumb(path)
        result["status"] = "done"
        return result


class RedditVideoProvider(DownloadProvider):
    """Provider for Reddit-hosted videos."""

    def match(self, url: str) -> bool:
        return "v.redd.it" in url

    def download(self, url, post_id, post_dir, session, media_utils) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        video_name = media_utils.make_filename("", "", "", post_id, url)
        video_stem = Path(video_name).stem

        proc = subprocess.run(
            ["yt-dlp", "-o", f"{post_dir}/{video_stem}.%(ext)s", url, "--quiet"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if proc.returncode != 0:
            logger.error(f"Video download failed for {url}: {proc.stderr.decode()}")
            return result

        matches = list(Path(post_dir).glob(f"{video_stem}.*"))
        matches = [
            m for m in matches if m.suffix not in (".jpg", ".jpeg", ".png", ".webp")
        ]

        if matches:
            result["path"] = str(matches[0])
            result["hash"] = media_utils.sha256(result["path"])
            result["thumb"] = media_utils.make_thumb(result["path"])
            result["status"] = "done"

        return result


class YouTubeProvider(DownloadProvider):
    """Provider for YouTube videos."""

    def match(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url

    def download(self, url, post_id, post_dir, session, media_utils) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        video_name = media_utils.make_filename("", "", "", post_id, url)
        video_stem = Path(video_name).stem

        # YouTube requires yt-dlp
        proc = subprocess.run(
            ["yt-dlp", "-o", f"{post_dir}/{video_stem}.%(ext)s", url, "--quiet"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if proc.returncode == 0:
            matches = list(Path(post_dir).glob(f"{video_stem}.*"))
            if matches:
                result["path"] = str(matches[0])
                result["hash"] = media_utils.sha256(result["path"])
                result["thumb"] = media_utils.make_thumb(result["path"])
                result["status"] = "done"

        return result


class ExternalImageProvider(DownloadProvider):
    """Provider for external image links."""

    def match(self, url: str) -> bool:
        return True  # Catch-all for external links

    def download(self, url, post_id, post_dir, session, media_utils) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        try:
            r = session.get(url, timeout=30)
            content_type = r.headers.get("content-type", "")

            if "image" in content_type or "video" in content_type:
                ext = "." + content_type.split("/")[-1].split(";")[0].strip()
                name = (
                    media_utils.make_filename("", "", "", post_id, url)
                    or f"{post_id}{ext}"
                )
                if not Path(name).suffix:
                    name = name + ext
                path = f"{post_dir}/{name}"

                with open(path, "wb") as f:
                    f.write(r.content)

                if media_utils.detect_image_corruption(path):
                    result["status"] = "corrupted"

                result["path"] = path
                result["hash"] = media_utils.sha256(path)
                result["thumb"] = media_utils.make_thumb(path)
                result["status"] = "done"
            else:
                result["status"] = f"not_image:{content_type}"
        except Exception as e:
            logger.warning(f"Extraction failed: {e}")
            result["status"] = f"error:{str(e)[:50]}"

        return result


# Provider registry
PROVIDERS = [
    RedditVideoProvider(),
    YouTubeProvider(),
    RedditImageProvider(),
    ExternalImageProvider(),
]


def get_provider(url: str) -> Optional[DownloadProvider]:
    """Get the appropriate provider for a URL."""
    for provider in PROVIDERS:
        if provider.match(url):
            return provider
    return PROVIDERS[-1]  # Return ExternalImageProvider as fallback
