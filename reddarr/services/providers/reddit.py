"""Reddit-hosted media providers (i.redd.it, v.redd.it, preview.redd.it)."""

import logging
import subprocess
from pathlib import Path

import requests

from reddarr.services.providers.base import DownloadProvider
from reddarr.utils.media import make_filename, sha256_file, make_thumb, detect_image_corruption

logger = logging.getLogger(__name__)


class RedditImageProvider(DownloadProvider):
    """Provider for Reddit-hosted images (i.redd.it, preview.redd.it)."""

    def match(self, url: str) -> bool:
        lower = url.lower()
        return (
            "i.redd.it" in lower
            or "preview.redd.it" in lower
            or "external-preview.redd.it" in lower
            or lower.split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
        )

    def download(self, url, post_id, post_dir, session) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        # Upgrade to high-res if possible
        download_url = url
        if "i.redd.it" in url and not url.lower().split("?")[0].endswith(".gif"):
            download_url = _try_highres(url, session)
        elif "preview.redd.it" in url or "external-preview.redd.it" in url:
            if not url.lower().split("?")[0].endswith(".gif"):
                download_url = _try_highres(url, session)
            else:
                download_url = url.split("?")[0]

        try:
            r = session.get(download_url, stream=True, timeout=60)
            if r.status_code != 200:
                result["error"] = f"HTTP {r.status_code}"
                return result

            name = make_filename(post_id, url)
            path = f"{post_dir}/{name}"

            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)

            if detect_image_corruption(path):
                logger.warning(f"Corrupt image for {post_id}")
                result["status"] = "corrupted"

            result["path"] = path
            result["hash"] = sha256_file(path)
            result["status"] = "done"
        except Exception as e:
            result["error"] = str(e)

        return result


class RedditVideoProvider(DownloadProvider):
    """Provider for Reddit-hosted videos (v.redd.it)."""

    def match(self, url: str) -> bool:
        return "v.redd.it" in url

    def download(self, url, post_id, post_dir, session) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        stem = make_filename(post_id, url)
        stem = Path(stem).stem

        proc = subprocess.run(
            ["yt-dlp", "-o", f"{post_dir}/{stem}.%(ext)s", url, "--quiet"],
            capture_output=True,
        )

        if proc.returncode != 0:
            logger.error(f"yt-dlp failed for {url}: {proc.stderr.decode()[:200]}")
            result["error"] = proc.stderr.decode()[:200]
            return result

        # Find the output file
        matches = [
            m for m in Path(post_dir).glob(f"{stem}.*")
            if m.suffix not in {".jpg", ".jpeg", ".png", ".webp", ".part"}
        ]

        if matches:
            result["path"] = str(matches[0])
            result["hash"] = sha256_file(result["path"])
            result["status"] = "done"

        return result


def _try_highres(url: str, session: requests.Session) -> str:
    """Attempt to get the highest-resolution version of a Reddit image."""
    try:
        # Try i.redd.it direct (no query params = full res)
        clean = url.split("?")[0]
        r = session.head(clean, timeout=10, allow_redirects=True)
        if r.status_code == 200:
            return clean
    except Exception:
        pass
    return url
