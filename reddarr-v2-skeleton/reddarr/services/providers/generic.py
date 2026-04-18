"""Generic download provider — catch-all for external media links."""

import logging
from pathlib import Path

from reddarr.services.providers.base import DownloadProvider
from reddarr.utils.media import make_filename, sha256_file, detect_image_corruption

logger = logging.getLogger(__name__)


class GenericProvider(DownloadProvider):
    """Catch-all provider for external image/video links."""

    def match(self, url: str) -> bool:
        return True  # Always matches — must be last in the registry

    def download(self, url, post_id, post_dir, session) -> dict:
        result = {"path": None, "thumb": None, "hash": None, "status": "failed"}

        try:
            r = session.get(url, timeout=30)
            content_type = r.headers.get("content-type", "")

            if "image" not in content_type and "video" not in content_type:
                result["status"] = f"not_media:{content_type.split(';')[0]}"
                return result

            ext = "." + content_type.split("/")[-1].split(";")[0].strip()
            name = make_filename(post_id, url)
            if not Path(name).suffix:
                name = name + ext
            path = f"{post_dir}/{name}"

            with open(path, "wb") as f:
                f.write(r.content)

            if detect_image_corruption(path):
                result["status"] = "corrupted"

            result["path"] = path
            result["hash"] = sha256_file(path)
            result["status"] = "done"

        except Exception as e:
            logger.warning(f"Generic download failed for {url[:60]}: {e}")
            result["error"] = str(e)[:200]

        return result
