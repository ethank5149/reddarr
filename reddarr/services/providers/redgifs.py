"""RedGifs video download provider.

Handles RedGifs API authentication and video URL resolution.
Preserves the auth token caching from the old shared/media_utils.py.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

import requests

from reddarr.services.providers.base import DownloadProvider
from reddarr.services.media import extract_redgifs_video_id
from reddarr.utils.media import make_filename, sha256_file

logger = logging.getLogger(__name__)

_redgifs_token: Optional[str] = None


class RedGifsProvider(DownloadProvider):
    def match(self, url: str) -> bool:
        return "redgifs.com" in url.lower()

    def download(self, url, post_id, post_dir, session) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        video_id = extract_redgifs_video_id(url)
        if not video_id:
            result["error"] = "Could not extract RedGifs video ID"
            return result

        video_url = _resolve_redgifs_url(video_id, session)
        if not video_url:
            # Fallback to yt-dlp
            return _ytdlp_fallback(url, post_id, post_dir, result)

        try:
            headers = {"User-Agent": session.headers.get("User-Agent", "Reddarr/2.0")}
            r = session.get(video_url, stream=True, timeout=60, headers=headers)
            if r.status_code != 200:
                return _ytdlp_fallback(url, post_id, post_dir, result)

            name = f"{make_filename(post_id, url)}.mp4"
            path = f"{post_dir}/{name}"

            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)

            result["path"] = path
            result["hash"] = sha256_file(path)
            result["status"] = "done"
        except Exception as e:
            logger.warning(f"RedGifs direct download failed: {e}, trying yt-dlp")
            return _ytdlp_fallback(url, post_id, post_dir, result)

        return result


def _resolve_redgifs_url(video_id: str, session: requests.Session) -> Optional[str]:
    """Resolve a RedGifs video ID to a direct MP4 URL via their API."""
    global _redgifs_token

    if not _redgifs_token:
        _redgifs_token = _get_redgifs_token(session)
        if not _redgifs_token:
            return None

    headers = {
        "Authorization": f"Bearer {_redgifs_token}",
        "User-Agent": session.headers.get("User-Agent", "Reddarr/2.0"),
    }

    try:
        r = session.get(
            f"https://api.redgifs.com/v2/gifs/{video_id}",
            headers=headers,
            timeout=15,
        )
        if r.status_code == 401:
            # Token expired, refresh
            _redgifs_token = _get_redgifs_token(session)
            if _redgifs_token:
                headers["Authorization"] = f"Bearer {_redgifs_token}"
                r = session.get(
                    f"https://api.redgifs.com/v2/gifs/{video_id}",
                    headers=headers,
                    timeout=15,
                )

        if r.status_code == 200:
            data = r.json()
            urls = data.get("gif", {}).get("urls", {})
            return urls.get("hd") or urls.get("sd")
    except Exception as e:
        logger.warning(f"RedGifs API error: {e}")

    return None


def _get_redgifs_token(session: requests.Session) -> Optional[str]:
    """Get a temporary auth token from the RedGifs API."""
    try:
        r = session.get("https://api.redgifs.com/v2/auth/temporary", timeout=10)
        if r.status_code == 200:
            return r.json().get("token")
    except Exception as e:
        logger.warning(f"RedGifs auth failed: {e}")
    return None


def _ytdlp_fallback(url: str, post_id: str, post_dir: str, result: dict) -> dict:
    """Fallback to yt-dlp for RedGifs downloads."""
    stem = Path(make_filename(post_id, url)).stem

    proc = subprocess.run(
        ["yt-dlp", "-o", f"{post_dir}/{stem}.%(ext)s", url, "--quiet"],
        capture_output=True,
    )

    if proc.returncode == 0:
        matches = [
            m for m in Path(post_dir).glob(f"{stem}.*")
            if m.suffix not in {".jpg", ".jpeg", ".png", ".webp", ".part"}
        ]
        if matches:
            result["path"] = str(matches[0])
            result["hash"] = sha256_file(result["path"])
            result["status"] = "done"
    else:
        result["error"] = proc.stderr.decode()[:200]

    return result
