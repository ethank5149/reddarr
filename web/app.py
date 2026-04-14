import asyncio
import logging
import os, json, time, redis, shutil, subprocess, threading, uuid, hashlib, secrets
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from typing import Optional, List, Dict, Any
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Redis-based rate limiter for API endpoints.

    Uses Redis to track request counts, allowing rate limiting to work
    correctly across multiple API workers (Gunicorn/Uvicorn).
    """

    def __init__(self, requests_per_minute: int = 60, redis_client=None):
        self.requests_per_minute = requests_per_minute
        self._redis = redis_client
        self._window = 60  # seconds

    def _get_redis(self):
        if self._redis is None:
            return redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                decode_responses=True,
                socket_connect_timeout=2,
            )
        return self._redis

    def check(self, key: str) -> bool:
        """Check if request is allowed. Returns True if allowed, False if rate limited."""
        try:
            r = self._get_redis()
            rate_key = f"ratelimit:{key}"
            current = r.get(rate_key)
            if current is None:
                r.setex(rate_key, self._window, "1")
                return True
            count = int(current)
            if count >= self.requests_per_minute:
                return False
            r.incr(rate_key)
            return True
        except Exception as e:
            logger.warning(f"Redis rate limit error: {e}, allowing request")
            return True  # Fail open on Redis errors

    def get_remaining(self, key: str) -> int:
        """Get remaining requests for this key."""
        try:
            r = self._get_redis()
            rate_key = f"ratelimit:{key}"
            current = r.get(rate_key)
            if current is None:
                return self.requests_per_minute
            return max(0, self.requests_per_minute - int(current))
        except Exception:
            return self.requests_per_minute


_rate_limiter = None


def get_rate_limiter():
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RedisRateLimiter(
            requests_per_minute=int(os.getenv("RATE_LIMIT_RPM", "60"))
        )
    return _rate_limiter


posts_total = Gauge("reddit_posts_total", "Total posts ingested", ["subreddit"])
comments_total = Gauge(
    "reddit_comments_total", "Total comments ingested", ["subreddit"]
)
media_queued = Counter("reddit_media_queued_total", "Total media items queued")
media_downloaded = Counter("reddit_media_downloaded_total", "Total media downloaded")
media_failed = Counter("reddit_media_failed_total", "Total media download failures")
queue_length = Gauge("reddit_queue_length", "Current queue length")
posts_in_db = Gauge("reddit_posts_in_db", "Total posts in database")
comments_in_db = Gauge("reddit_comments_in_db", "Total comments in database")
media_in_db = Gauge("reddit_media_in_db", "Total media records in database")
media_downloaded_in_db = Gauge(
    "reddit_media_downloaded_in_db", "Total downloaded media"
)
target_last_fetch = Gauge(
    "reddit_target_last_fetch_ts",
    "Last fetch timestamp",
    ["target_type", "target_name"],
)
ingest_duration = Histogram("reddit_ingest_duration_seconds", "Ingest cycle duration")


def get_api_key():
    """Load the API key from secrets or environment."""
    from shared.config import get_secret

    key = get_secret("API_KEY", os.getenv("API_KEY", ""))
    return key


async def require_api_key(x_api_key: str = Header(None)):
    """FastAPI dependency to require API key for protected endpoints."""
    api_key = get_api_key()
    if not api_key:
        logger.warning("API_KEY not configured - admin endpoints are unprotected!")
        return  # Fail open if no key configured
    if x_api_key is None or x_api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# HTTP request metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

app = FastAPI(title="Reddit Archive API", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    path = request.url.path

    # Rate limiting for API endpoints
    if path.startswith("/api/"):
        client_ip = request.client.host if request.client else "unknown"
        rate_key = f"api:{client_ip}"
        limiter = get_rate_limiter()
        if not limiter.check(rate_key):
            remaining = limiter.get_remaining(rate_key)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "retry_after": 60,
                },
                headers={"Retry-After": "60", "X-RateLimit-Remaining": str(remaining)},
            )

    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start
    # Use route template to avoid high-cardinality path labels
    route = request.scope.get("route")
    endpoint = getattr(route, "path", request.url.path)
    http_requests_total.labels(
        method=request.method,
        endpoint=endpoint,
        status_code=str(response.status_code),
    ).inc()
    http_request_duration_seconds.labels(
        method=request.method,
        endpoint=endpoint,
    ).observe(duration)
    return response


logger.info("API STARTED - version 4.0.0")

# Use absolute paths anchored to this file's location for dist assets
_HERE = Path(__file__).parent
DIST_DIR = _HERE / "dist"

ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "/data")
THUMB_PATH = os.getenv("THUMB_PATH", os.path.join(ARCHIVE_PATH, ".thumbs"))
FAILED_TARGETS_FILE = os.getenv(
    "FAILED_TARGETS_FILE", os.path.join(ARCHIVE_PATH, "failed_targets.txt")
)
# Path where excluded posts' media files are moved to
EXCLUDED_MEDIA_PATH = os.getenv(
    "EXCLUDED_MEDIA_PATH", os.path.join(ARCHIVE_PATH, ".excluded")
)
# Thumbnails for excluded posts mirror under THUMB_PATH/.excluded
EXCLUDED_THUMB_PATH = os.path.join(THUMB_PATH, ".excluded")

connection_pool = None
redis_client = None


_MIGRATIONS = [
    # Ensure columns added in v4 exist on databases initialised before this version
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMP DEFAULT now()",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS excluded BOOLEAN DEFAULT FALSE NOT NULL",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS excluded_at TIMESTAMP",
    "CREATE INDEX IF NOT EXISTS idx_posts_excluded ON posts(excluded)",
    "CREATE INDEX IF NOT EXISTS idx_posts_ingested_at ON posts(ingested_at)",
    # v5: History tables for preserving all post/comment versions
    """CREATE TABLE IF NOT EXISTS posts_history (
        id SERIAL PRIMARY KEY,
        post_id TEXT NOT NULL,
        version INT NOT NULL DEFAULT 1,
        subreddit TEXT,
        author TEXT,
        created_utc TIMESTAMP,
        title TEXT,
        selftext TEXT,
        url TEXT,
        media_url TEXT,
        raw JSONB,
        is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
        version_hash TEXT,
        captured_at TIMESTAMP DEFAULT now(),
        UNIQUE(post_id, version)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_posts_history_post_id ON posts_history(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_posts_history_version ON posts_history(post_id, version DESC)",
    """CREATE TABLE IF NOT EXISTS comments_history (
        id SERIAL PRIMARY KEY,
        comment_id TEXT NOT NULL,
        version INT NOT NULL DEFAULT 1,
        post_id TEXT,
        author TEXT,
        body TEXT,
        created_utc TIMESTAMP,
        raw JSONB,
        is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
        version_hash TEXT,
        captured_at TIMESTAMP DEFAULT now(),
        UNIQUE(comment_id, version)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_comments_history_comment_id ON comments_history(comment_id)",
    "CREATE INDEX IF NOT EXISTS idx_comments_history_version ON comments_history(comment_id, version DESC)",
    # v6: targets.status column for tracking banned/deleted subreddits
    "ALTER TABLE targets ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'",
    # v7: targets.icon_url for subreddit/user avatars
    "ALTER TABLE targets ADD COLUMN IF NOT EXISTS icon_url TEXT",
    # v8: media unique constraint on (post_id, url)
    "CREATE INDEX IF NOT EXISTS idx_media_post_id_url ON media(post_id, url)",
    "DELETE FROM media a USING media b WHERE a.id > b.id AND a.post_id = b.post_id AND a.url = b.url",
    "ALTER TABLE media ADD CONSTRAINT media_post_id_url_key UNIQUE (post_id, url)",
    "DROP INDEX IF EXISTS idx_media_sha256",
    "CREATE INDEX IF NOT EXISTS idx_media_sha256_non_unique ON media(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_posts_subreddit_lower ON posts(LOWER(subreddit))",
    "CREATE INDEX IF NOT EXISTS idx_posts_author_lower ON posts(LOWER(author))",
    # v9: Performance indexes for common query patterns
    "CREATE INDEX IF NOT EXISTS idx_media_status ON media(status)",
    "CREATE INDEX IF NOT EXISTS idx_posts_subreddit_created ON posts(subreddit, created_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_posts_author_created ON posts(author, created_utc DESC)",
    "CREATE INDEX IF NOT EXISTS idx_posts_excluded_created ON posts(excluded, created_utc DESC)",
    # v10: scrape_failures table for tracking failed scrapes
    """CREATE TABLE IF NOT EXISTS scrape_failures (
        id SERIAL PRIMARY KEY,
        target_type TEXT NOT NULL,
        target_name TEXT NOT NULL,
        sort_method TEXT,
        post_id TEXT,
        error_message TEXT,
        created_at TIMESTAMP DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_scrape_failures_target ON scrape_failures(target_type, target_name)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_failures_post_id ON scrape_failures(post_id)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_failures_created_at ON scrape_failures(created_at)",
    # v10b: schema_version table
    "CREATE TABLE IF NOT EXISTS schema_version (version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT now())",
    "INSERT INTO schema_version (version) VALUES ('v10') ON CONFLICT (version) DO NOTHING",
]


def _run_migrations(pool):
    """Run all idempotent schema migrations at startup."""
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        for sql in _MIGRATIONS:
            try:
                cur.execute(sql)
                logger.info(f"Migration OK: {sql[:80]}")
            except Exception as e:
                logger.warning(f"Migration skipped ({e}): {sql[:80]}")
                conn.rollback()
        conn.commit()
        cur.close()
    finally:
        pool.putconn(conn)


@app.on_event("startup")
def startup():
    global connection_pool, redis_client
    try:
        from shared.database import init_pool

        connection_pool = init_pool(minconn=5, maxconn=100)
        logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

        # Validate critical tables exist
        critical_tables = ["posts", "media", "targets", "comments", "scrape_failures"]
        conn = connection_pool.getconn()
        cur = conn.cursor()
        for tbl in critical_tables:
            try:
                cur.execute(f"SELECT 1 FROM {tbl} LIMIT 1")
            except Exception:
                logger.error(f"CRITICAL TABLE MISSING: {tbl} - run migrations!")
        cur.close()
        connection_pool.putconn(conn)

        # Load targets from targets.txt if no targets exist
        targets_file = os.getenv("TARGETS_FILE") or "/app/targets.txt"
        if os.path.exists(targets_file):
            conn = connection_pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM targets WHERE enabled = true")
            target_count = cur.fetchone()[0]
            if target_count == 0:
                logger.info("No targets found - loading from targets.txt")
                try:
                    with open(targets_file, "r") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if ":" not in line:
                                continue
                            ttype, name = line.split(":", 1)
                            ttype = ttype.lower().strip()
                            name = name.strip()
                            if ttype in ("subreddit", "user") and name:
                                cur.execute(
                                    "INSERT INTO targets(type,name) VALUES(%s,%s) ON CONFLICT (name) DO NOTHING",
                                    (ttype, name),
                                )
                    conn.commit()
                    logger.info("Targets loaded from targets.txt")
                except Exception as e:
                    logger.warning(f"Failed to load targets from targets.txt: {e}")
            cur.close()
            connection_pool.putconn(conn)
    finally:
        pass

    try:
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_client = redis.Redis(
            host=redis_host, decode_responses=True, socket_connect_timeout=2
        )
        redis_client.ping()
        logger.info(f"Redis connected at {redis_host}")
    except Exception as e:
        logger.warning(f"Redis not available (queue features disabled): {e}")
        redis_client = None

    for d in [EXCLUDED_MEDIA_PATH, EXCLUDED_THUMB_PATH]:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

    threading.Thread(target=_refresh_target_icons, daemon=True).start()


ICONS_DIR = os.path.join(THUMB_PATH, ".icons")


def _refresh_target_icons():
    """Download subreddit/user icons from Reddit and save locally."""
    import urllib.request
    import urllib.error

    try:
        os.makedirs(ICONS_DIR, exist_ok=True)
    except Exception as e:
        logger.warning(f"Could not create icon directory {ICONS_DIR}: {e}")
        return

    if not connection_pool:
        return

    conn = None
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()
        cur.execute(
            "SELECT type, name FROM targets WHERE enabled = true AND (icon_url IS NULL OR icon_url = '')"
        )
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.warning(f"Icon refresh query failed: {e}")
        return
    finally:
        if conn:
            connection_pool.putconn(conn)

    if not rows:
        return

    logger.info(f"Fetching icons for {len(rows)} targets...")
    ua = "Mozilla/5.0 (compatible; Reddarr/1.0)"

    for ttype, name in rows:
        try:
            if ttype == "subreddit":
                url = f"https://www.reddit.com/r/{name}/about.json"
            elif ttype == "user":
                url = f"https://www.reddit.com/user/{name}/about.json"
            else:
                continue

            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            about = data.get("data", {})
            icon_remote = None
            if ttype == "subreddit":
                icon_remote = (
                    about.get("community_icon") or about.get("icon_img") or None
                )
            else:
                icon_remote = (
                    about.get("icon_img") or about.get("snoovatar_img") or None
                )

            if icon_remote:
                # Strip query params (Reddit appends cache-busters)
                if "?" in icon_remote:
                    icon_remote = icon_remote.split("?")[0]

                ext = os.path.splitext(icon_remote)[1] or ".png"
                local_name = f"{ttype}_{name}{ext}"
                local_path = os.path.join(ICONS_DIR, local_name)

                req2 = urllib.request.Request(icon_remote, headers={"User-Agent": ua})
                with urllib.request.urlopen(req2, timeout=15) as img_resp:
                    with open(local_path, "wb") as f:
                        f.write(img_resp.read())

                # Store as relative path under THUMB_PATH so /thumb/ endpoint serves it
                rel_path = os.path.join(".icons", local_name)

                conn2 = connection_pool.getconn()
                try:
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "UPDATE targets SET icon_url = %s WHERE type = %s AND name = %s",
                        (f"/thumb/{rel_path}", ttype, name),
                    )
                    conn2.commit()
                    cur2.close()
                    logger.info(f"Icon saved for {ttype}:{name}")
                finally:
                    connection_pool.putconn(conn2)

            time.sleep(1)  # Rate limit Reddit requests
        except Exception as e:
            logger.warning(f"Failed to fetch icon for {ttype}:{name}: {e}")
            time.sleep(2)


@app.on_event("shutdown")
def shutdown():
    global connection_pool
    try:
        from shared.database import close_pool

        close_pool()
        logger.info("Database connections closed")
    except Exception:
        if connection_pool:
            connection_pool.closeall()
            logger.info("Database connections closed")


@contextmanager
def get_db_cursor():
    global connection_pool
    if not connection_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    conn = None
    cur = None
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"DB error: {e}")
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            connection_pool.putconn(conn)


def get_redis():
    global redis_client
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")
    return redis_client


dist_dir = str(DIST_DIR)
if (DIST_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(DIST_DIR / "static")), name="static")


def _safe_file_response(base_dir: str, path: str) -> FileResponse:
    """Resolve *path* relative to *base_dir* and return it, rejecting any
    path that escapes the base directory (path traversal prevention)."""
    base = os.path.realpath(base_dir)
    full_path = os.path.realpath(os.path.join(base_dir, path))
    if not full_path.startswith(base + os.sep) and full_path != base:
        raise HTTPException(status_code=400, detail="Invalid path")
    if os.path.exists(full_path):
        return FileResponse(full_path)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/media/{path:path}")
def media(path: str):
    return _safe_file_response(ARCHIVE_PATH, path)


@app.get("/excluded-media/{path:path}")
def excluded_media(path: str):
    return _safe_file_response(EXCLUDED_MEDIA_PATH, path)


@app.get("/thumb/{path:path}")
def thumb(path: str):
    return _safe_file_response(THUMB_PATH, path)


@app.get("/excluded-thumb/{path:path}")
def excluded_thumb(path: str):
    return _safe_file_response(EXCLUDED_THUMB_PATH, path)


@app.get("/")
def root():
    idx = DIST_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"detail": "Not Found"}


@app.get("/icon.png")
def icon():
    ico = DIST_DIR / "icon.png"
    if ico.exists():
        return FileResponse(str(ico))
    # Fallback to public/icon.png if dist hasn't been built yet
    pub = _HERE / "public" / "icon.png"
    if pub.exists():
        return FileResponse(str(pub))
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/api/debug/{post_id}")
def debug_post(post_id: str):
    with get_db_cursor() as cur:
        cur.execute("SELECT id, raw FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return {"id": row[0], "raw": row[1]}


_VIDEO_URL_PATTERNS = (
    "v.redd.it",
    "youtube.com",
    "youtu.be",
    "streamable.com",
    "redgifs.com",
)


def _is_video_url(url: Optional[str]) -> bool:
    if not url:
        return False
    return any(pat in url for pat in _VIDEO_URL_PATTERNS)


def _extract_video_url(url: Optional[str], raw: Optional[dict]) -> Optional[str]:
    """Extract a playable video URL from post data."""
    if not url:
        return None
    # Reddit hosted video: get the fallback MP4 from raw media
    if "v.redd.it" in url:
        if raw:
            media = raw.get("media") or {}
            rv = media.get("reddit_video") or {}
            fallback = rv.get("fallback_url")
            if fallback:
                # Strip query params that cause issues
                return fallback.split("?")[0]
            # Also check crosspost
            for cp in raw.get("crosspost_parent_list", []):
                media2 = cp.get("media") or {}
                rv2 = media2.get("reddit_video") or {}
                fb2 = rv2.get("fallback_url")
                if fb2:
                    return fb2.split("?")[0]
        return url
    # YouTube: return the original URL (frontend will handle embed)
    if "youtube.com" in url or "youtu.be" in url:
        return url
    # Other video hosts
    if _is_video_url(url):
        return url
    return None


def _build_media_url(file_path: str) -> Optional[str]:
    """Return the API URL for a media file, handling both regular and excluded paths."""
    if not file_path:
        return None
    if file_path.startswith(EXCLUDED_MEDIA_PATH):
        rel = os.path.relpath(file_path, EXCLUDED_MEDIA_PATH)
        return f"/excluded-media/{rel}"
    else:
        try:
            rel = os.path.relpath(file_path, ARCHIVE_PATH)
            return f"/media/{rel}"
        except ValueError:
            return None


def _build_thumb_url(thumb_path: str) -> Optional[str]:
    """Return the API URL for a thumbnail, handling both regular and excluded paths."""
    if not thumb_path:
        return None
    if thumb_path.startswith(EXCLUDED_THUMB_PATH):
        rel = os.path.relpath(thumb_path, EXCLUDED_THUMB_PATH)
        return f"/excluded-thumb/{rel}"
    else:
        try:
            rel = os.path.relpath(thumb_path, THUMB_PATH)
            return f"/thumb/{rel}"
        except ValueError:
            return None


@app.get("/api/posts")
def posts(
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = None,  # ISO timestamp for cursor-based pagination
    offset: int = Query(0, ge=0),  # Deprecated but still accepted for backward compat
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
    sort_by: Optional[str] = Query("created_utc"),
    sort_order: Optional[str] = Query("desc"),
    has_media: Optional[bool] = None,
    media_type: Optional[List[str]] = Query(None),
    nsfw: Optional[str] = None,  # "include" | "exclude" | None (show all)
    excluded: Optional[bool] = Query(None),  # default: all posts (None shows all)
):
    # Whitelist sort fields to prevent SQL injection
    allowed_sort_by = {"created_utc", "title", "ingested_at"}
    allowed_sort_order = {"asc", "desc"}
    if sort_by not in allowed_sort_by:
        sort_by = "created_utc"
    if sort_order not in allowed_sort_order:
        sort_order = "desc"

    with get_db_cursor() as cur:
        where_clauses = []
        params: list[Any] = []

        # Archive/Excluded filter - None shows all, True shows excluded, False shows visible
        if excluded is not None:
            if excluded:
                where_clauses.append("p.excluded = TRUE")
            else:
                where_clauses.append("p.excluded = FALSE")

        if subreddit:
            where_clauses.append("LOWER(subreddit) = LOWER(%s)")
            params.append(subreddit)
        if author:
            where_clauses.append("LOWER(author) = LOWER(%s)")
            params.append(author)

        # media_type supersedes legacy has_media
        if media_type and len(media_type) > 0:
            # Build OR conditions for multiple media types
            media_conditions = []
            if "video" in media_type:
                media_conditions.append(
                    "(EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done' AND "
                    "(LOWER(m.file_path) LIKE '%%.mp4' OR LOWER(m.file_path) LIKE '%%.webm' OR LOWER(m.file_path) LIKE '%%.mkv' OR LOWER(m.file_path) LIKE '%%.mov')) OR "
                    "url LIKE '%%v.redd.it%%' OR url LIKE '%%youtube.com%%' OR url LIKE '%%youtu.be%%' OR url LIKE '%%streamable.com%%' OR "
                    "(raw IS NOT NULL AND (raw->'media'->'reddit_video') IS NOT NULL))"
                )
            if "image" in media_type:
                media_conditions.append(
                    "(EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done' AND "
                    "(LOWER(m.file_path) LIKE '%%.jpg' OR LOWER(m.file_path) LIKE '%%.jpeg' OR LOWER(m.file_path) LIKE '%%.png' OR "
                    "LOWER(m.file_path) LIKE '%%.gif' OR LOWER(m.file_path) LIKE '%%.webp')) OR "
                    "(url LIKE '%%i.redd.it%%' OR url LIKE '%%i.imgur.com%%' OR url LIKE '%%.jpg' OR url LIKE '%%.jpeg' OR "
                    "url LIKE '%%.png' OR url LIKE '%%.gif' OR url LIKE '%%.webp') OR "
                    "(raw IS NOT NULL AND ((raw->>'media_metadata') IS NOT NULL OR (raw->'preview'->'images') IS NOT NULL)))"
                )
            if "text" in media_type:
                media_conditions.append(
                    "NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done') AND "
                    "(url IS NULL OR (url NOT LIKE '%%i.redd.it%%' AND url NOT LIKE '%%i.imgur.com%%' AND url NOT LIKE '%%.jpg' AND "
                    "url NOT LIKE '%%.jpeg' AND url NOT LIKE '%%.png' AND url NOT LIKE '%%.gif' AND url NOT LIKE '%%.webp' AND "
                    "url NOT LIKE '%%v.redd.it%%' AND url NOT LIKE '%%youtube.com%%' AND url NOT LIKE '%%youtu.be%%')) AND "
                    "(raw IS NULL OR "
                    "(raw->>'media_metadata') IS NULL AND "
                    "(raw->'preview'->'images') IS NULL AND "
                    "(raw->'media'->'reddit_video') IS NULL))"
                )
            if media_conditions:
                where_clauses.append("(" + " OR ".join(media_conditions) + ")")
        elif has_media is True:
            where_clauses.append(
                "(EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done' AND m.file_path IS NOT NULL) OR url IS NOT NULL)"
            )
        elif has_media is False:
            where_clauses.append(
                "NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done') AND (url IS NULL OR url = '')"
            )

        # NSFW filter - use native JSONB operator for correctness and performance
        if nsfw == "exclude":
            where_clauses.append(
                "(raw IS NULL OR NOT COALESCE((raw->>'over_18')::boolean, false))"
            )
        elif nsfw == "include":
            pass  # show all (default behavior)

        where_sql = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

        # Cursor-based pagination: use created_utc as cursor
        # cursor is ISO timestamp, offset is deprecated fallback
        if cursor:
            try:
                cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
                if sort_order.lower() == "desc":
                    where_clauses.append("p.created_utc < %s")
                else:
                    where_clauses.append("p.created_utc > %s")
                params.append(cursor_dt)
                where_sql = (
                    (" AND " + " AND ".join(where_clauses)) if where_clauses else ""
                )
            except ValueError:
                pass  # Invalid cursor, ignore

        # Count query (uses same WHERE, no ORDER/LIMIT)
        cur.execute(f"SELECT COUNT(*) FROM posts p WHERE 1=1{where_sql}", params)
        total = cur.fetchone()[0] or 0

        # Main query - use cursor-based when available, otherwise offset
        if cursor:
            # Cursor-based pagination - no offset needed
            query = f"""
                SELECT p.id, p.title, p.url, p.media_url, p.raw, p.subreddit, p.author, p.created_utc, p.excluded,
                       p.raw->>'selftext' as selftext,
                       p.raw->>'created_utc' as raw_created_utc,
                       p.ingested_at
                FROM posts p
                WHERE 1=1{where_sql}
                ORDER BY {sort_by} {sort_order.upper()} LIMIT %s
            """
            cur.execute(query, params + [limit])
        else:
            # Legacy offset-based pagination
            query = f"""
                SELECT p.id, p.title, p.url, p.media_url, p.raw, p.subreddit, p.author, p.created_utc, p.excluded,
                       p.raw->>'selftext' as selftext,
                       p.raw->>'created_utc' as raw_created_utc,
                       p.ingested_at
                FROM posts p
                WHERE 1=1{where_sql}
                ORDER BY {sort_by} {sort_order.upper()} LIMIT %s OFFSET %s
            """
            cur.execute(query, params + [limit, offset])
        post_rows = cur.fetchall()

        if not post_rows:
            return {"posts": [], "total": 0, "limit": limit, "offset": offset}

        post_ids = [row[0] for row in post_rows]

        results = []

        media_by_post: dict[str, list[tuple]] = {pid: [] for pid in post_ids}
        if post_ids:
            cur.execute(
                "SELECT post_id, id, file_path, thumb_path FROM media WHERE post_id = ANY(%s)",
                (post_ids,),
            )
            for m_pid, m_id, m_file_path, m_thumb_path in cur.fetchall():
                if m_pid in media_by_post:
                    media_by_post[m_pid].append((m_id, m_file_path, m_thumb_path))

        for row in post_rows:
            (
                post_id,
                title,
                url,
                media_url,
                raw,
                subreddit,
                author,
                created_utc,
                is_excluded,
                selftext,
                raw_created_ts,
                ingested_at,
            ) = row
            created_ts = raw_created_ts
            is_video = _is_video_url(url)

            # Check raw JSONB for embedded media that wasn't captured in url field
            if raw:
                try:
                    # If raw has reddit_video, this is a video post even if URL doesn't match patterns
                    if raw.get("media") and raw["media"].get("reddit_video"):
                        is_video = True
                except Exception:
                    pass

            # Use pre-fetched media from the batch query
            media_rows = media_by_post.get(post_id, [])

            # Build local URLs from downloaded files
            image_urls = []
            video_urls = []
            thumb_url = None

            for m_id, m_file_path, m_thumb_path in media_rows:
                # Optimized: trust status and avoid disk I/O
                if m_file_path:
                    local_url = _build_media_url(m_file_path)
                    if local_url:
                        if m_file_path.lower().endswith(
                            (".mp4", ".webm", ".mkv", ".mov", ".avi")
                        ):
                            video_urls.append(local_url)
                        else:
                            image_urls.append(local_url)
                if m_thumb_path and not thumb_url:
                    thumb_url = _build_thumb_url(m_thumb_path)

            preview_url = None
            # Only use remote URLs as fallback when no local files
            if raw:
                try:
                    data = raw
                    # Extract preview thumbnail (works for both videos and images)
                    if not thumb_url and "preview" in data:
                        for img in data.get("preview", {}).get("images", []):
                            u = img.get("source", {}).get("url")
                            if u:
                                preview_url = u
                                break

                    # For videos: use remote as fallback only if no local
                    if is_video:
                        if not video_urls:
                            extracted = _extract_video_url(url, data)
                            video_urls = (
                                [extracted] if extracted else [url] if url else []
                            )
                    # For images: collect all from media_metadata, use remote as fallback
                    else:
                        if not image_urls:
                            remote_imgs = []
                            if "media_metadata" in data:
                                for img_id, img_data in data.get(
                                    "media_metadata", {}
                                ).items():
                                    if "s" in img_data:
                                        u = img_data["s"].get("u")
                                        if u:
                                            remote_imgs.append(u)
                                    elif img_data.get("p"):
                                        u = img_data["p"][-1].get("u")
                                        if u:
                                            remote_imgs.append(u)
                            if not remote_imgs and "preview" in data:
                                for img in data.get("preview", {}).get("images", []):
                                    u = img.get("source", {}).get("url") or img.get(
                                        "resolutions", [{}]
                                    )[-1].get("url")
                                    if u:
                                        remote_imgs.append(u)
                                    if img.get("variants", {}).get("n"):
                                        for v in img["variants"]["n"].values():
                                            vu = v.get("url")
                                            if vu:
                                                remote_imgs.append(vu)
                            if remote_imgs:
                                image_urls = remote_imgs
                except Exception as e:
                    logger.error(
                        f"ERROR parsing raw for {post_id}: {e}. Raw type: {type(raw).__name__}. Raw content (first 200 chars): {str(raw)[:200]}"
                    )

            # Remove None values and deduplicate
            video_urls = list(
                dict.fromkeys([v.replace("&amp;", "&") for v in video_urls if v])
            )
            image_urls = list(
                dict.fromkeys([i.replace("&amp;", "&") for i in image_urls if i])
            )

            results.append(
                {
                    "id": post_id,
                    "title": title,
                    "image_url": image_urls[0] if image_urls else None,
                    "image_urls": image_urls,
                    "video_url": video_urls[0] if video_urls else None,
                    "video_urls": video_urls,
                    "is_video": is_video,
                    "selftext": selftext,
                    "subreddit": subreddit,
                    "author": author,
                    "created_utc": created_ts
                    or (created_utc.isoformat() if created_utc else None),
                    "ingested_at": ingested_at.isoformat() if ingested_at else None,
                    "thumb_url": thumb_url,
                    "preview_url": preview_url,
                    "excluded": is_excluded,
                }
            )

        # Build next_cursor for cursor-based pagination
        next_cursor = None
        if cursor is None and post_rows and len(results) == limit:
            # Only provide cursor for offset-based requests that returned full results
            last_post = post_rows[-1]
            last_created = last_post[7]  # created_utc is at index 7
            if last_created:
                next_cursor = last_created.isoformat()

        response = {"posts": results, "total": total, "limit": limit}

        # Include offset only for backward compatibility when cursor not used
        if cursor is None:
            response["offset"] = offset

        if next_cursor:
            response["next_cursor"] = next_cursor

        return response


@app.get("/api/post/{post_id}")
def get_post(post_id: str):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, url, media_url, raw, subreddit, author, created_utc, excluded
            FROM posts WHERE id = %s
        """,
            (post_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")

        (
            post_id,
            title,
            url,
            media_url,
            raw,
            subreddit,
            author,
            created_utc,
            is_excluded,
        ) = row

        cur.execute(
            """
            SELECT id, author, body, created_utc FROM comments 
            WHERE post_id = %s ORDER BY created_utc
        """,
            (post_id,),
        )
        comments = []
        for c in cur.fetchall():
            comments.append(
                {
                    "id": c[0],
                    "author": c[1],
                    "body": c[2],
                    "created_utc": c[3].isoformat() if c[3] else None,
                }
            )

        # Get all media rows for this post
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE post_id = %s",
            (post_id,),
        )
        media_rows = cur.fetchall()

        # Build local URLs from downloaded files
        image_urls = []
        video_urls = []
        thumb_url = None

        for m_id, m_file_path, m_thumb_path in media_rows:
            if m_file_path and os.path.exists(m_file_path):
                local_url = _build_media_url(m_file_path)
                if local_url:
                    if m_file_path.lower().endswith(
                        (".mp4", ".webm", ".mkv", ".mov", ".avi")
                    ):
                        video_urls.append(local_url)
                    else:
                        image_urls.append(local_url)
            if m_thumb_path and os.path.exists(m_thumb_path) and not thumb_url:
                thumb_url = _build_thumb_url(m_thumb_path)

        # Build fallback URLs from raw JSON
        selftext = None
        is_video = _is_video_url(url)
        if raw:
            try:
                data = raw if isinstance(raw, dict) else json.loads(raw)
                selftext = data.get("selftext")

                # If raw has reddit_video, this is a video post even if URL doesn't match patterns
                if data.get("media") and data["media"].get("reddit_video"):
                    is_video = True

                if is_video:
                    if not video_urls:
                        extracted = _extract_video_url(url, data)
                        video_urls = [extracted] if extracted else [url] if url else []
                else:
                    if not image_urls:
                        remote_imgs = []
                        if "media_metadata" in data:
                            for img_id, img_data in data.get(
                                "media_metadata", {}
                            ).items():
                                if "s" in img_data:
                                    u = img_data["s"].get("u")
                                    if u:
                                        remote_imgs.append(u)
                                elif img_data.get("p"):
                                    u = img_data["p"][-1].get("u")
                                    if u:
                                        remote_imgs.append(u)
                        if not remote_imgs and "preview" in data:
                            for img in data.get("preview", {}).get("images", []):
                                u = img.get("source", {}).get("url") or img.get(
                                    "resolutions", [{}]
                                )[-1].get("url")
                                if u:
                                    remote_imgs.append(u)
                                if img.get("variants", {}).get("n"):
                                    for v in img["variants"]["n"].values():
                                        vu = v.get("url")
                                        if vu:
                                            remote_imgs.append(vu)
                        if remote_imgs:
                            image_urls = remote_imgs
            except Exception as e:
                logger.error(
                    f"ERROR parsing raw for {post_id}: {e}. Raw type: {type(raw).__name__}. Raw content (first 200 chars): {str(raw)[:200]}"
                )

            # Remove None values and deduplicate
            video_urls = list(
                dict.fromkeys([v.replace("&amp;", "&") for v in video_urls if v])
            )
            image_urls = list(
                dict.fromkeys([i.replace("&amp;", "&") for i in image_urls if i])
            )

        return {
            "id": post_id,
            "title": title,
            "url": url,
            "image_url": image_urls[0] if image_urls else None,
            "image_urls": image_urls,
            "video_url": video_urls[0] if video_urls else None,
            "video_urls": video_urls,
            "is_video": is_video,
            "selftext": selftext,
            "subreddit": subreddit,
            "author": author,
            "created_utc": created_utc.isoformat() if created_utc else None,
            "excluded": is_excluded,
            "comments": comments,
        }


@app.get("/api/post/{post_id}/history")
def get_post_history(post_id: str):
    """Get all versions of a post (for audit trail)."""
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT version, title, selftext, url, author, subreddit, is_deleted, version_hash, captured_at
            FROM posts_history
            WHERE post_id = %s
            ORDER BY version DESC
            """,
            (post_id,),
        )
        rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Post history not found")

        versions = []
        for row in rows:
            versions.append(
                {
                    "version": row[0],
                    "title": row[1],
                    "selftext": row[2],
                    "url": row[3],
                    "author": row[4],
                    "subreddit": row[5],
                    "is_deleted": row[6],
                    "version_hash": row[7],
                    "captured_at": row[8].isoformat() if row[8] else None,
                }
            )

        return {
            "post_id": post_id,
            "versions": versions,
            "total_versions": len(versions),
        }


@app.get("/api/comment/{comment_id}/history")
def get_comment_history(comment_id: str):
    """Get all versions of a comment (for audit trail)."""
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT version, body, author, post_id, is_deleted, version_hash, captured_at
            FROM comments_history
            WHERE comment_id = %s
            ORDER BY version DESC
            """,
            (comment_id,),
        )
        rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Comment history not found")

        versions = []
        for row in rows:
            versions.append(
                {
                    "version": row[0],
                    "body": row[1],
                    "author": row[2],
                    "post_id": row[3],
                    "is_deleted": row[4],
                    "version_hash": row[5],
                    "captured_at": row[6].isoformat() if row[6] else None,
                }
            )

        return {
            "comment_id": comment_id,
            "versions": versions,
            "total_versions": len(versions),
        }


# ---------------------------------------------------------------------------
# Archive / Unarchive endpoints
# ---------------------------------------------------------------------------


def _move_post_media(post_id: str, archive: bool):
    """Move all media files for a post between active and archive directories.
    Updates media.file_path and media.thumb_path in the DB.
    Returns (files_moved, errors)."""
    if archive:
        src_media_root = ARCHIVE_PATH
        dst_media_root = EXCLUDED_MEDIA_PATH
        src_thumb_root = THUMB_PATH
        dst_thumb_root = EXCLUDED_THUMB_PATH
    else:
        src_media_root = EXCLUDED_MEDIA_PATH
        dst_media_root = ARCHIVE_PATH
        src_thumb_root = EXCLUDED_THUMB_PATH
        dst_thumb_root = THUMB_PATH

    files_moved = 0
    errors = []

    conn = None
    cur = None
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE post_id = %s",
            (post_id,),
        )
        media_rows = cur.fetchall()

        for media_id, file_path, thumb_path in media_rows:
            new_file_path = file_path
            new_thumb_path = thumb_path

            # Move the main media file
            if file_path and os.path.exists(file_path):
                try:
                    rel = os.path.relpath(file_path, src_media_root)
                    new_path = os.path.join(dst_media_root, rel)
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    shutil.move(file_path, new_path)
                    new_file_path = new_path
                    files_moved += 1
                except Exception as e:
                    errors.append(f"move {file_path}: {e}")
                    logger.error(f"Archive move error: {e}")

            # Move the thumbnail
            if thumb_path and os.path.exists(thumb_path):
                try:
                    rel = os.path.relpath(thumb_path, src_thumb_root)
                    new_tp = os.path.join(dst_thumb_root, rel)
                    os.makedirs(os.path.dirname(new_tp), exist_ok=True)
                    shutil.move(thumb_path, new_tp)
                    new_thumb_path = new_tp
                except Exception as e:
                    errors.append(f"move thumb {thumb_path}: {e}")
                    logger.error(f"Archive thumb move error: {e}")

            # Update DB paths if anything changed
            if new_file_path != file_path or new_thumb_path != thumb_path:
                cur.execute(
                    "UPDATE media SET file_path = %s, thumb_path = %s WHERE id = %s",
                    (new_file_path, new_thumb_path, media_id),
                )

        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        errors.append(str(e))
    finally:
        if cur:
            cur.close()
        if conn and connection_pool:
            connection_pool.putconn(conn)

    return files_moved, errors


@app.post("/api/post/{post_id}/hide")
def hide_post(post_id: str):
    """Mark a post as excluded and move its media files to the excluded storage directory."""
    with get_db_cursor() as cur:
        cur.execute("SELECT id, excluded FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        if row[1]:
            return {"status": "already_excluded", "post_id": post_id}

    # Move media files
    files_moved, errors = _move_post_media(post_id, archive=True)

    # Update post excluded flag
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE posts SET excluded = TRUE, excluded_at = now() WHERE id = %s",
            (post_id,),
        )

    return {
        "status": "ok",
        "post_id": post_id,
        "files_moved": files_moved,
        "errors": errors,
    }


@app.post("/api/post/{post_id}/unhide")
def unhide_post(post_id: str):
    """Unhide a post and move its media files back to the active directory."""
    with get_db_cursor() as cur:
        cur.execute("SELECT id, excluded FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        if not row[1]:
            return {"status": "not_excluded", "post_id": post_id}

    # Move media files back
    files_moved, errors = _move_post_media(post_id, archive=False)

    # Update post excluded flag
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE posts SET excluded = FALSE, excluded_at = NULL WHERE id = %s",
            (post_id,),
        )

    return {
        "status": "ok",
        "post_id": post_id,
        "files_moved": files_moved,
        "errors": errors,
    }


def _delete_post_media(post_id: str):
    """Delete all media files for a post from disk.
    Returns (files_deleted, errors)."""
    files_deleted = 0
    errors = []

    conn = None
    cur = None
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE post_id = %s",
            (post_id,),
        )
        media_rows = cur.fetchall()

        for media_id, file_path, thumb_path in media_rows:
            # Delete the main media file
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    files_deleted += 1
                except Exception as e:
                    errors.append(f"delete {file_path}: {e}")
                    logger.error(f"Delete error: {e}")

            # Delete the thumbnail
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception as e:
                    errors.append(f"delete thumb {thumb_path}: {e}")
                    logger.error(f"Delete thumb error: {e}")
    except Exception as e:
        errors.append(str(e))
    finally:
        if cur:
            cur.close()
        if conn and connection_pool:
            connection_pool.putconn(conn)

    return files_deleted, errors


@app.post("/api/post/{post_id}/delete", dependencies=[Depends(require_api_key)])
def delete_post(post_id: str, delete_media: bool = Query(True)):
    """Delete a post and its associated data (comments, media records).
    If delete_media is true, also deletes the files from disk."""

    if delete_media:
        files_deleted, errors = _delete_post_media(post_id)
        if errors:
            logger.error(f"Errors deleting media for {post_id}: {errors}")
            # Do not proceed with DB delete if file deletion failed
            raise HTTPException(
                status_code=500,
                detail={"message": "Failed to delete media files", "errors": errors},
            )
    else:
        files_deleted, errors = 0, []

    with get_db_cursor() as cur:
        cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        p_deleted = cur.rowcount
        cur.execute("DELETE FROM comments WHERE post_id = %s", (post_id,))
        c_deleted = cur.rowcount
        cur.execute("DELETE FROM media WHERE post_id = %s", (post_id,))
        m_deleted = cur.rowcount
        cur.execute("DELETE FROM posts_history WHERE post_id = %s", (post_id,))
        ph_deleted = cur.rowcount
        cur.execute("DELETE FROM comments_history WHERE post_id = %s", (post_id,))
        ch_deleted = cur.rowcount

    return {
        "status": "ok",
        "post_id": post_id,
        "deleted_rows": {
            "posts": p_deleted,
            "comments": c_deleted,
            "media": m_deleted,
            "posts_history": ph_deleted,
            "comments_history": ch_deleted,
        },
        "media_files_deleted": files_deleted,
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _ts_headline(cur, query: str, field: str, text: str) -> str:
    """Use Postgres ts_headline to highlight search matches."""
    if not text:
        return ""
    cur.execute(
        f"SELECT ts_headline('english', %s, to_tsquery('english', %s), 'StartSel=**, StopSel=**')",
        (text, query),
    )
    return cur.fetchone()[0]


@app.get("/api/search")
def search(
    q: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query("rank"),
    sort_order: Optional[str] = Query("desc"),
    excluded: Optional[bool] = Query(
        None
    ),  # None shows all, True shows excluded, False shows visible
):
    """Full-text search for posts.

    If the query starts with 'r/' or 'u/', it redirects to the subreddit/user page.
    """
    q = q.strip()
    if not q:
        return {"posts": [], "total": 0, "query": ""}

    # Redirect for r/ and u/ searches
    if q.lower().startswith("r/"):
        return {"redirect": f"/r/{q[2:]}"}
    elif q.lower().startswith("u/"):
        return {"redirect": f"/u/{q[2:]}"}

    allowed_sort_by = {"rank", "created_utc"}
    allowed_sort_order = {"asc", "desc"}
    if sort_by not in allowed_sort_by:
        sort_by = "rank"
    if sort_order not in allowed_sort_order:
        sort_order = "desc"

    with get_db_cursor() as cur:
        # Prepare search query for tsquery (replace spaces with '&')
        # This gives AND semantics, which is more intuitive for search
        search_query = "&".join(q.split())

        # Count total matches using pre-computed tsv column
        excluded_filter = ""
        if excluded is True:
            excluded_filter = "AND p.excluded = TRUE"
        elif excluded is False:
            excluded_filter = "AND p.excluded = FALSE"
        # excluded=None shows all posts (no filter)

        cur.execute(
            f"""
            SELECT COUNT(*) FROM posts p
            WHERE p.tsv @@ to_tsquery('english', %s)
            {excluded_filter}
            """,
            (search_query,),
        )
        total = cur.fetchone()[0] or 0

        # Fetch results with ranking using pre-computed tsv column
        cur.execute(
            f"""
            SELECT p.id, p.title, p.url, p.media_url, p.raw, p.subreddit, p.author, p.created_utc, p.excluded,
                   p.raw->>'selftext' as selftext,
                   p.raw->>'created_utc' as raw_created_utc,
                   ts_rank_cd(p.tsv, to_tsquery('english', %s)) as rank
            FROM posts p
            WHERE p.tsv @@ to_tsquery('english', %s)
            {excluded_filter}
            ORDER BY {sort_by} {sort_order.upper()} LIMIT %s OFFSET %s
            """,
            (search_query, search_query, limit, offset),
        )
        rows = cur.fetchall()
        if not rows:
            return {
                "posts": [],
                "total": 0,
                "query": q,
                "limit": limit,
                "offset": offset,
            }

        post_ids = [row[0] for row in rows]
        media_by_post: dict[str, list[tuple]] = {pid: [] for pid in post_ids}
        if post_ids:
            cur.execute(
                "SELECT post_id, id, file_path, thumb_path FROM media WHERE post_id = ANY(%s) AND status = 'done'",
                (post_ids,),
            )
            for m_pid, m_id, m_file_path, m_thumb_path in cur.fetchall():
                if m_pid in media_by_post:
                    media_by_post[m_pid].append((m_id, m_file_path, m_thumb_path))

        results = []
        for row in rows:
            (
                post_id,
                title,
                url,
                media_url,
                raw,
                subreddit,
                author,
                created_utc,
                is_excluded,
                selftext,
                raw_created_ts,
                rank,
            ) = row
            created_ts = raw_created_ts

            media_rows = media_by_post.get(post_id, [])
            thumb_url = None
            if media_rows:
                for _, _, m_thumb_path in media_rows:
                    if m_thumb_path and os.path.exists(m_thumb_path):
                        thumb_url = _build_thumb_url(m_thumb_path)
                        break

            # Generate headline fragments with matched terms highlighted
            title_headline = _ts_headline(cur, search_query, "title", title)
            selftext_headline = (
                _ts_headline(cur, search_query, "selftext", selftext)
                if selftext
                else ""
            )

            results.append(
                {
                    "id": post_id,
                    "title": title,
                    "title_headline": title_headline,
                    "selftext_headline": selftext_headline,
                    "subreddit": subreddit,
                    "author": author,
                    "created_utc": created_ts
                    or (created_utc.isoformat() if created_utc else None),
                    "thumb_url": thumb_url,
                    "rank": rank,
                    "excluded": is_excluded,
                }
            )

        return {
            "posts": results,
            "total": total,
            "query": q,
            "limit": limit,
            "offset": offset,
        }


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@app.get("/api/admin/stats", dependencies=[Depends(require_api_key)])
def admin_stats():
    """Get high-level stats for the archive."""
    with get_db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM posts WHERE excluded = FALSE")
        total_posts = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM posts WHERE excluded = TRUE")
        excluded_posts = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM comments")
        total_comments = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM media WHERE status = 'done'")
        dl_media = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM media WHERE status = 'pending'")
        pend_media = cur.fetchone()[0] or 0
        cur.execute("SELECT COUNT(*) FROM media")
        tot_media = cur.fetchone()[0] or 0
        cur.execute("SELECT SUM(file_size) FROM media WHERE status = 'done'")
        total_size = cur.fetchone()[0] or 0

    return {
        "total_posts": total_posts,
        "excluded_posts": excluded_posts,
        "total_comments": total_comments,
        "downloaded_media": dl_media,
        "pending_media": pend_media,
        "total_media": tot_media,
        "total_media_size_bytes": total_size,
    }


@app.get("/api/admin/targets", dependencies=[Depends(require_api_key)])
def admin_targets():
    """Get detailed stats for all targets."""
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT 
                t.type, 
                t.name, 
                t.enabled,
                t.status,
                t.icon_url,
                t.last_created,
                COUNT(DISTINCT p.id) AS post_count,
                COUNT(DISTINCT p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d,
                COUNT(DISTINCT m.id) AS total_media,
                COUNT(DISTINCT CASE WHEN m.status = 'done' THEN m.id END) AS downloaded_media,
                COUNT(DISTINCT CASE WHEN m.status = 'pending' THEN m.id END) AS pending_media
            FROM targets t
            LEFT JOIN posts p ON (t.type = 'subreddit' AND LOWER(p.subreddit) = LOWER(t.name)) 
                              OR (t.type = 'user'      AND LOWER(p.author)    = LOWER(t.name))
            LEFT JOIN media m ON m.post_id = p.id
            GROUP BY t.type, t.name, t.enabled, t.status, t.icon_url, t.last_created
            ORDER BY t.type, t.name
        """
        )
        rows = cur.fetchall()

        targets = []
        for row in rows:
            (
                ttype,
                name,
                enabled,
                status,
                icon_url,
                last_created,
                post_count,
                posts_7d,
                tot_media,
                dl_media,
                pend_media,
            ) = row
            post_count = post_count or 0
            posts_7d = posts_7d or 0
            tot_media = tot_media or 0
            dl_media = dl_media or 0
            pend_media = pend_media or 0

            # Estimate post rate (posts/sec) and ETA to 1000 posts
            rate = posts_7d / (7 * 86400) if posts_7d > 0 else 0
            eta_seconds = None
            if rate > 0:
                remaining = max(0, 1000 - post_count)
                if remaining > 0:
                    eta_seconds = remaining / rate

            targets.append(
                {
                    "type": ttype,
                    "name": name,
                    "enabled": enabled,
                    "status": status or "active",
                    "icon_url": icon_url,
                    "last_created": last_created.isoformat() if last_created else None,
                    "post_count": post_count,
                    "total_media": tot_media,
                    "downloaded_media": dl_media,
                    "pending_media": pend_media,
                    "rate_per_second": round(rate, 4),
                    "eta_seconds": round(eta_seconds, 0) if eta_seconds else None,
                    "progress_percent": min(100, round(post_count / 10, 1))
                    if post_count > 0
                    else 0,
                }
            )

    return targets


class TargetRequest(BaseModel):
    type: str
    name: str


@app.post("/api/admin/targets", dependencies=[Depends(require_api_key)])
def add_target(req: TargetRequest):
    """Add a new subreddit or user target via JSON body."""
    if req.type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    with get_db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO targets(type,name) VALUES(%s, %s) ON CONFLICT (name) DO UPDATE SET enabled = true, status = 'active'",
                (req.type, req.name.strip()),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "type": req.type, "name": req.name}


@app.post("/api/admin/target/{target_type}", dependencies=[Depends(require_api_key)])
def add_target_by_name(target_type: str, name: str):
    """Add a new subreddit or user target via query parameter."""
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    with get_db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO targets(type,name) VALUES(%s, %s) ON CONFLICT (name) DO UPDATE SET enabled = true, status = 'active'",
                (target_type, name.strip()),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok", "type": target_type, "name": name}


@app.post("/internal/add-target")
async def add_target_internal(request: Request):
    """Internal endpoint to add target - works from same origin."""
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
    except:
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    api_key = data.get("api_key", "")
    if not api_key:
        return JSONResponse({"error": "Missing API key"}, status_code=401)
    expected_key = os.getenv("API_KEY", "!!19077h053j37p4ck81u35!!")
    if api_key != expected_key:
        return JSONResponse({"error": "Invalid API key"}, status_code=401)

    target_type = data.get("type", "subreddit")
    name = data.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "Missing name"}, status_code=400)
    if target_type not in ("subreddit", "user"):
        return JSONResponse({"error": "Invalid type"}, status_code=400)

    with get_db_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO targets(type,name) VALUES(%s, %s) ON CONFLICT (name) DO UPDATE SET enabled = true, status = 'active'",
                (target_type, name),
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    return {"status": "ok", "type": target_type, "name": name}


@app.delete(
    "/api/admin/targets/{target_type}/{name}", dependencies=[Depends(require_api_key)]
)
def delete_target(target_type: str, name: str):
    """Delete a target (disables it, does not remove data)."""
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE targets SET enabled = false WHERE type = %s AND name = %s",
            (target_type, name),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Target not found")
    return {"status": "ok"}


@app.get("/api/admin/queue", dependencies=[Depends(require_api_key)])
def admin_queue():
    """Get the current media download queue from Redis."""
    r = get_redis()
    items = r.lrange("media_queue", 0, 100)
    retry_items = r.lrange("media_queue_retry", 0, 100)
    failed_items = r.lrange("media_dead_letter", 0, 100)
    return {
        "queue": [json.loads(i) for i in items],
        "queue_length": r.llen("media_queue"),
        "retry_queue": [json.loads(i) for i in retry_items],
        "retry_queue_length": r.llen("media_queue_retry"),
        "dead_letter_queue": [json.loads(i) for i in failed_items],
        "dead_letter_queue_length": r.llen("media_dead_letter"),
    }


class ScrapeRequest(BaseModel):
    target_type: Optional[str] = None
    target_name: Optional[str] = None


@app.post("/api/admin/trigger-scrape", dependencies=[Depends(require_api_key)])
def trigger_scrape(req: ScrapeRequest):
    """Manually trigger a scrape cycle for all targets, or a single target."""
    r = get_redis()
    payload = {}
    if req.target_type and req.target_name:
        payload = {"target_type": req.target_type, "target_name": req.target_name}
    r.lpush("scrape_trigger", json.dumps(payload))
    return {"status": "ok", "message": "Scrape triggered"}


class BackfillRequest(BaseModel):
    passes: Optional[int] = None
    workers: Optional[int] = None
    target_type: Optional[str] = None
    target_name: Optional[str] = None


@app.post("/api/admin/trigger-backfill", dependencies=[Depends(require_api_key)])
def trigger_backfill(req: BackfillRequest):
    """Manually trigger a backfill for all targets, or a single target."""
    r = get_redis()
    payload = {}
    if req.passes:
        payload["passes"] = req.passes
    if req.workers:
        payload["workers"] = req.workers
    if req.target_type and req.target_name:
        payload["target_type"] = req.target_type
        payload["target_name"] = req.target_name
    r.lpush("backfill_trigger", json.dumps(payload))
    r.setex("backfill_status", 300, json.dumps({"status": "starting"}))
    return {"status": "ok", "message": "Backfill triggered"}


@app.get("/api/admin/backfill-status", dependencies=[Depends(require_api_key)])
def backfill_status():
    """Get the status of the last backfill job."""
    r = get_redis()
    status = r.get("backfill_status")
    return json.loads(status) if status else {"status": "unknown"}


@app.get("/api/admin/activity", dependencies=[Depends(require_api_key)])
def admin_activity(
    limit: int = Query(50, ge=1, le=500), include_failures: bool = Query(True)
):
    """Get recent activity (new posts, media, failures)."""
    results = []
    with get_db_cursor() as cur:
        # Recent posts
        cur.execute(
            """
        SELECT id, title, subreddit, author, created_utc, ingested_at 
        FROM posts WHERE excluded = FALSE
        ORDER BY ingested_at DESC NULLS LAST LIMIT %s
      """,
            (limit,),
        )
        for r in cur.fetchall():
            results.append(
                {
                    "type": "new_post",
                    "id": r[0],
                    "title": r[1],
                    "subreddit": r[2],
                    "author": r[3],
                    "created_utc": r[4].isoformat() if r[4] else None,
                    "timestamp": r[5].isoformat() if r[5] else None,
                }
            )

        # Recent media
        cur.execute(
            """
        SELECT m.id, m.url, m.status, m.downloaded_at, m.file_path, m.thumb_path,
               p.title, p.subreddit, p.author
        FROM media m
        JOIN posts p ON m.post_id = p.id
        WHERE m.status = 'done'
        ORDER BY m.downloaded_at DESC LIMIT %s
      """,
            (limit,),
        )
        for r in cur.fetchall():
            results.append(
                {
                    "type": "new_media",
                    "id": r[0],
                    "url": r[1],
                    "status": r[2],
                    "timestamp": r[3].isoformat() if r[3] else None,
                    "file_path": r[4],
                    "thumb_path": r[5],
                    "post_title": r[6],
                    "subreddit": r[7],
                    "author": r[8],
                }
            )

        if include_failures:
            # Failed media downloads
            cur.execute(
                f"""
              SELECT m.id, m.url, m.status, m.error_message, m.created_at, m.file_path, m.thumb_path,
                     p.title, p.subreddit, p.author
              FROM media m
              JOIN posts p ON m.post_id = p.id
              WHERE m.status IN ('failed', 'corrupted')
              ORDER BY m.created_at DESC NULLS LAST
              LIMIT %s
            """,
                (limit,),
            )
            for r in cur.fetchall():
                results.append(
                    {
                        "type": "failed_media",
                        "id": r[0],
                        "url": r[1],
                        "status": r[2],
                        "error": r[3],
                        "timestamp": r[4].isoformat() if r[4] else None,
                        "file_path": r[5],
                        "thumb_path": r[6],
                        "post_title": r[7],
                        "subreddit": r[8],
                        "author": r[9],
                    }
                )

    results.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return results[:limit]


@app.get("/api/admin/health", dependencies=[Depends(require_api_key)])
def admin_health():
    """Check health of DB, Redis, and essential file paths."""
    issues = []
    # DB check
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:
        issues.append(f"Database: {str(e)}")

    # Redis check
    try:
        r = get_redis()
        r.ping()
    except Exception as e:
        issues.append(f"Redis: {str(e)}")

    # Path checks
    for label, path in [
        ("Archive path", ARCHIVE_PATH),
        ("Thumbnail path", THUMB_PATH),
        ("Excluded media path", EXCLUDED_MEDIA_PATH),
    ]:
        try:
            if not os.path.exists(path):
                issues.append(f"{label} not accessible: {path}")
        except Exception as e:
            issues.append(f"{label}: {str(e)}")

    return {"status": "healthy" if not issues else "degraded", "issues": issues}


_sse_cache: Dict[str, Any] = {}
_sse_cache_lock = threading.Lock()
_sse_last_post_ts: Optional[datetime] = None
_sse_last_media_ts: Optional[datetime] = None
_sse_background_running = False


def _run_sse_polling_loop():
    """Background task that polls DB once every 5 seconds and caches results."""
    global _sse_cache, _sse_last_post_ts, _sse_last_media_ts, _sse_background_running

    if not connection_pool:
        logger.warning("SSE polling: no connection pool available")
        return

    while _sse_background_running:
        try:
            conn = connection_pool.getconn()
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM posts WHERE excluded = FALSE")
            total_posts = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM posts WHERE excluded = TRUE")
            excluded_posts = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM comments")
            total_comments = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM media WHERE status='done'")
            dl_media = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM media WHERE status='pending'")
            pend_media = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM media")
            tot_media = cur.fetchone()[0] or 0

            cur.execute("""
                SELECT 
                    t.type, t.name, t.enabled, t.status, t.icon_url, t.last_created,
                    COUNT(DISTINCT p.id) AS post_count,
                    COUNT(DISTINCT p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d,
                    COUNT(DISTINCT m.id) AS total_media,
                    COUNT(DISTINCT CASE WHEN m.status = 'done' THEN m.id END) AS downloaded_media,
                    COUNT(DISTINCT CASE WHEN m.status = 'pending' THEN m.id END) AS pending_media
                FROM targets t
                LEFT JOIN posts p ON (t.type = 'subreddit' AND LOWER(p.subreddit) = LOWER(t.name)) 
                                  OR (t.type = 'user' AND LOWER(p.author) = LOWER(t.name))
                LEFT JOIN media m ON m.post_id = p.id
                GROUP BY t.type, t.name, t.enabled, t.status, t.icon_url, t.last_created
                ORDER BY t.type, t.name
            """)
            target_rows = cur.fetchall()
            targets = []
            for row in target_rows:
                (
                    ttype,
                    name,
                    enabled,
                    status,
                    icon_url,
                    last_created,
                    post_count,
                    posts_7d,
                    tot_media,
                    dl_media,
                    pend_media,
                ) = row
                post_count = post_count or 0
                posts_7d = posts_7d or 0
                rate = posts_7d / (7 * 86400) if posts_7d > 0 else 0
                eta_seconds = None
                if rate > 0:
                    remaining = max(0, 1000 - post_count)
                    if remaining > 0:
                        eta_seconds = remaining / rate
                targets.append(
                    {
                        "type": ttype,
                        "name": name,
                        "enabled": enabled,
                        "status": status or "active",
                        "icon_url": icon_url,
                        "last_created": last_created.isoformat()
                        if last_created
                        else None,
                        "post_count": post_count,
                        "total_media": tot_media or 0,
                        "downloaded_media": dl_media or 0,
                        "pending_media": pend_media or 0,
                        "rate_per_second": round(rate, 4),
                        "eta_seconds": round(eta_seconds, 0) if eta_seconds else None,
                        "progress_percent": min(100, round(post_count / 10, 1))
                        if post_count > 0
                        else 0,
                    }
                )

            new_posts = []
            if _sse_last_post_ts is None:
                cur.execute(
                    "SELECT id, title, subreddit, author, created_utc FROM posts WHERE excluded = FALSE ORDER BY ingested_at DESC NULLS LAST LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    _sse_last_post_ts = row[4]
            else:
                cur.execute(
                    "SELECT id, title, subreddit, author, created_utc FROM posts WHERE excluded = FALSE AND ingested_at > %s ORDER BY ingested_at DESC NULLS LAST LIMIT 20",
                    (_sse_last_post_ts,),
                )
                for r in cur.fetchall():
                    new_posts.append(
                        {
                            "id": r[0],
                            "title": r[1],
                            "subreddit": r[2],
                            "author": r[3],
                            "created_utc": r[4].isoformat() if r[4] else None,
                        }
                    )
                if new_posts:
                    _sse_last_post_ts = new_posts[0].get("created_utc")

            new_media = []
            if _sse_last_media_ts is None:
                cur.execute(
                    "SELECT id, post_id, url, file_path FROM media WHERE status = 'done' ORDER BY downloaded_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    _sse_last_media_ts = row[3]
            else:
                cur.execute(
                    "SELECT id, post_id, url, file_path FROM media WHERE status = 'done' AND downloaded_at > %s ORDER BY downloaded_at DESC LIMIT 20",
                    (_sse_last_media_ts,),
                )
                for r in cur.fetchall():
                    new_media.append(
                        {"id": r[0], "post_id": r[1], "url": r[2], "file_path": r[3]}
                    )
                if new_media:
                    cur.execute(
                        "SELECT downloaded_at FROM media WHERE id = %s",
                        (new_media[0]["id"],),
                    )
                    row = cur.fetchone()
                    if row:
                        _sse_last_media_ts = row[0]

            cur.close()
            connection_pool.putconn(conn)

            with _sse_cache_lock:
                _sse_cache = {
                    "total_posts": total_posts,
                    "excluded_posts": excluded_posts,
                    "total_comments": total_comments,
                    "downloaded_media": dl_media,
                    "pending_media": pend_media,
                    "total_media": tot_media,
                    "targets": targets,
                    "new_posts": new_posts,
                    "new_media": new_media,
                }

        except Exception as e:
            logger.error(f"SSE polling error: {e}")

        time.sleep(5)


@app.on_event("startup")
def _start_sse_background():
    global _sse_background_running
    _sse_background_running = True
    threading.Thread(target=_run_sse_polling_loop, daemon=True).start()
    logger.info("SSE background polling started")


@app.on_event("shutdown")
def _stop_sse_background():
    global _sse_background_running
    _sse_background_running = False


@app.get("/api/events")
async def event_stream():
    """Server-Sent Events endpoint for real-time UI updates (uses cached DB state)."""

    async def generate():
        while True:
            try:
                with _sse_cache_lock:
                    stats = dict(_sse_cache)

                if redis_client:
                    try:
                        stats["queue_length"] = await asyncio.to_thread(
                            redis_client.llen, "media_queue"
                        )
                    except Exception:
                        stats["queue_length"] = 0
                else:
                    stats["queue_length"] = 0

                try:
                    issues = []
                    conn = connection_pool.getconn()
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT 1")
                        cur.close()
                    finally:
                        connection_pool.putconn(conn)
                    if not os.path.exists(ARCHIVE_PATH):
                        issues.append(f"Archive path not accessible")
                    if not os.path.exists(THUMB_PATH):
                        issues.append(f"Thumb path not accessible")
                    health = {
                        "status": "healthy" if not issues else "degraded",
                        "issues": issues,
                    }
                except Exception as e:
                    health = {"status": "degraded", "issues": [str(e)]}

                if not redis_client:
                    health["issues"].append("Redis: not connected")
                    health["status"] = "degraded"
                stats["health"] = health

                payload = json.dumps(stats)
                yield f"data: {payload}\n\n"
            except Exception as e:
                logger.error(f"SSE generation error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/metrics")
async def metrics():
    if redis_client:
        try:
            queue_len = await asyncio.to_thread(redis_client.llen, "media_queue")
            queue_length.set(queue_len)
        except Exception as e:
            logger.warning(f"Redis unavailable for metrics: {e}")

    def _fetch_metrics():
        with get_db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM posts")
            posts_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM comments")
            comments_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM media")
            media_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM media WHERE status = 'done'")
            media_downloaded_in_db.set(cur.fetchone()[0] or 0)

            cur.execute(
                "SELECT type, name, EXTRACT(EPOCH FROM last_created) FROM targets"
            )
            for ttype, name, ts in cur.fetchall():
                if ts:
                    target_last_fetch.labels(target_type=ttype, target_name=name).set(
                        ts
                    )

    try:
        await asyncio.to_thread(_fetch_metrics)
    except Exception as e:
        logger.error(f"Metrics DB error: {e}")

    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Catch-all for single-page app routing
@app.get("/{full_path:path}")
def spa(full_path: str):
    idx = DIST_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"detail": "Not Found"}
