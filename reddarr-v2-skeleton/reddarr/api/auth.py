"""Authentication dependencies for FastAPI.

Replaces the inline auth logic from web/app.py.
"""

from fastapi import Header, HTTPException

from reddarr.config import get_settings


async def require_api_key(x_api_key: str = Header(None)):
    """FastAPI dependency that validates the X-Api-Key header.

    Used on all /api/admin/* routes.
    """
    settings = get_settings()
    if not settings.api_key:
        return  # No API key configured = no auth required

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    if x_api_key.strip() != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
