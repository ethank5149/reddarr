"""Centralized configuration for Reddarr.

All settings are loaded from environment variables and Docker secrets.
This replaces the old shared/config.py with a proper settings object.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional


def _read_secret(name: str, default: str = "") -> str:
    """Read a Docker secret from /run/secrets/{name}."""
    path = Path(f"/run/secrets/{name}")
    if path.exists():
        return path.read_text().strip()
    return os.environ.get(name.upper(), default)


@dataclass(frozen=True)
class Settings:
    """Application settings, immutable after creation."""

    # --- Database ---
    db_url: str = ""
    db_pool_min: int = 2
    db_pool_max: int = 20

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None

    # --- Reddit API ---
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "Reddarr/2.0"

    # --- Paths ---
    archive_path: str = "/data/archive"
    thumb_path: str = "/data/archive/.thumbs"
    archive_media_path: str = "/data/archive/.archive"

    # --- Scheduling ---
    poll_interval: int = 300  # seconds between ingest cycles
    scrape_limit: int = 500

    # --- Auth ---
    api_key: str = ""
    admin_password: str = ""
    guest_password: str = ""

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        # Use a separate Redis DB for results
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db + 1}"


def _build_db_url() -> str:
    """Construct DB URL from environment/secrets, matching legacy priority."""
    explicit = os.environ.get("DB_URL")
    if explicit:
        return explicit

    pg_password = _read_secret("postgres_password")
    if pg_password:
        user = os.environ.get("POSTGRES_USER", "reddit")
        host = os.environ.get("POSTGRES_HOST", "db")
        port = os.environ.get("POSTGRES_PORT", "5432")
        db = os.environ.get("POSTGRES_DB", "reddit")
        return f"postgresql://{user}:{pg_password}@{host}:{port}/{db}"

    return "postgresql://reddit:changeme@db:5432/reddit"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create the global Settings singleton.

    Values are resolved once from env/secrets and then cached.
    """
    return Settings(
        db_url=_build_db_url(),
        db_pool_min=int(os.getenv("DB_POOL_MIN", "2")),
        db_pool_max=int(os.getenv("DB_POOL_MAX", "20")),
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        redis_password=os.getenv("REDIS_PASSWORD") or None,
        reddit_client_id=_read_secret("reddit_client_id"),
        reddit_client_secret=_read_secret("reddit_client_secret"),
        reddit_user_agent=os.getenv(
            "REDDIT_USER_AGENT", "Reddarr/2.0 (self-hosted archiver)"
        ),
        archive_path=os.getenv("ARCHIVE_PATH", "/data/archive"),
        thumb_path=os.getenv("THUMB_PATH", "/data/archive/.thumbs"),
        archive_media_path=os.getenv("ARCHIVE_MEDIA_PATH", "/data/archive/.archive"),
        poll_interval=int(os.getenv("POLL_INTERVAL", "300")),
        scrape_limit=int(os.getenv("SCRAPE_LIMIT", "500")),
        api_key=_read_secret("API_KEY") or _read_secret("api_key"),
        admin_password=_read_secret("admin_password"),
        guest_password=_read_secret("guest_password"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
