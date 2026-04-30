"""Media file serving routes — /media/*, /thumb/*.

Replaces the static file serving from web/app.py with proper
path traversal protection.
"""

import os
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from reddarr.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["media"])


def _safe_file_response(base_dir: str, path: str) -> FileResponse:
    """Serve a file with path traversal protection."""
    full = os.path.realpath(os.path.join(base_dir, path))
    base_real = os.path.realpath(base_dir)

    if not full.startswith(base_real):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="File not found")

    # Determine media type based on file extension
    from mimetypes import guess_type
    media_type, _ = guess_type(full)
    if not media_type:
        media_type = "application/octet-stream"

    return FileResponse(full, media_type=media_type)


@router.get("/media/{path:path}")
def serve_media(path: str):
    """Serve archived media files."""
    settings = get_settings()
    return _safe_file_response(settings.archive_path, path)


@router.get("/thumb/{path:path}")
def serve_thumb(path: str):
    """Serve thumbnail files."""
    settings = get_settings()
    return _safe_file_response(settings.thumb_path, path)


@router.get("/excluded-media/{path:path}")
def serve_excluded_media(path: str):
    """Serve media from the excluded/archived directory."""
    settings = get_settings()
    return _safe_file_response(settings.archive_media_path, path)


@router.get("/excluded-thumb/{path:path}")
def serve_excluded_thumb(path: str):
    """Serve thumbnails from the excluded/archived directory."""
    settings = get_settings()
    excluded_thumb = os.path.join(settings.archive_media_path, ".thumbs")
    return _safe_file_response(excluded_thumb, path)
