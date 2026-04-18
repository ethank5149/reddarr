"""YouTube video download provider."""

import logging
import subprocess
from pathlib import Path

from reddarr.services.providers.base import DownloadProvider
from reddarr.utils.media import make_filename, sha256_file

logger = logging.getLogger(__name__)


class YouTubeProvider(DownloadProvider):
    def match(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url

    def download(self, url, post_id, post_dir, session) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        stem = Path(make_filename(post_id, url)).stem

        proc = subprocess.run(
            ["yt-dlp", "-o", f"{post_dir}/{stem}.%(ext)s", url, "--quiet"],
            capture_output=True,
        )

        if proc.returncode != 0:
            logger.error(f"yt-dlp failed for {url}: {proc.stderr.decode()[:200]}")
            result["error"] = proc.stderr.decode()[:200]
            return result

        matches = [
            m for m in Path(post_dir).glob(f"{stem}.*")
            if m.suffix not in {".jpg", ".jpeg", ".png", ".webp", ".part"}
        ]

        if matches:
            result["path"] = str(matches[0])
            result["hash"] = sha256_file(result["path"])
            result["status"] = "done"

        return result
