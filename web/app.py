import asyncio
import logging
import os
import json
import time
import redis
import shutil
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
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
    if path.startswith("/api/") and path != "/api/login":
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        token = auth_header.split(" ")[1]

        _get_tokens()
        is_admin = _ADMIN_TOKEN and token == _ADMIN_TOKEN
        is_guest = _GUEST_TOKEN and token == _GUEST_TOKEN

        if is_admin:
            pass
        elif is_guest:
            if request.method != "GET":
                return JSONResponse(
                    status_code=403, content={"detail": "Forbidden: Admin required"}
                )
            allowed_guest_get = [
                "/api/posts",
                "/api/post/",
                "/api/search",
                "/api/comments",
                "/api/media",
                "/api/admin/stats",
                "/api/events",
            ]
            if not any(path.startswith(p) for p in allowed_guest_get):
                return JSONResponse(
                    status_code=403, content={"detail": "Forbidden: Admin required"}
                )
        else:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    # --- End Auth check ---

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


def get_admin_password() -> str:
    from shared.config import get_secret

    return get_secret("admin_password", "admin")


def get_guest_password() -> str:
    from shared.config import get_secret

    return get_secret("guest_password", "guest")


def generate_token(role: str) -> str:
    import hashlib
    import secrets

    raw = f"{role}:{secrets.token_urlsafe(32)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


_ADMIN_TOKEN: Optional[str] = None
_GUEST_TOKEN: Optional[str] = None


def _get_tokens():
    global _ADMIN_TOKEN, _GUEST_TOKEN
    from shared.config import get_secret

    admin_pw = get_secret("admin_password")
    guest_pw = get_secret("guest_password")
    if admin_pw:
        import hashlib

        _ADMIN_TOKEN = hashlib.sha256(f"admin:{admin_pw}".encode()).hexdigest()[:32]
    if guest_pw:
        import hashlib

        _GUEST_TOKEN = hashlib.sha256(f"guest:{guest_pw}".encode()).hexdigest()[:32]


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(req: LoginRequest):
    if req.username == "admin" and req.password == get_admin_password():
        return {"token": _ADMIN_TOKEN or generate_token("admin"), "role": "admin"}
    elif req.username == "guest" and req.password == get_guest_password():
        return {"token": _GUEST_TOKEN or generate_token("guest"), "role": "guest"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


logger.info("API STARTED - version 4.0.0")

# Use absolute paths anchored to this file's location for dist assets
_HERE = Path(__file__).parent
DIST_DIR = _HERE / "dist"

ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "/data")
THUMB_PATH = os.getenv("THUMB_PATH", os.path.join(ARCHIVE_PATH, ".thumbs"))
# Path where hidden posts' media files are moved to
HIDDEN_MEDIA_PATH = os.getenv(
    "HIDDEN_MEDIA_PATH", os.path.join(ARCHIVE_PATH, ".hidden")
)
# Thumbnails for hidden posts mirror under THUMB_PATH/.hidden
HIDDEN_THUMB_PATH = os.path.join(THUMB_PATH, ".hidden")

connection_pool = None
redis_client = None


_MIGRATIONS = [
    # Ensure columns added in v4 exist on databases initialised before this version
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMP DEFAULT now()",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS hidden BOOLEAN DEFAULT FALSE NOT NULL",
    "ALTER TABLE posts ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMP",
    "CREATE INDEX IF NOT EXISTS idx_posts_hidden ON posts(hidden)",
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
    "CREATE INDEX IF NOT EXISTS idx_posts_hidden_created ON posts(hidden, created_utc DESC)",
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

        connection_pool = init_pool(minconn=1, maxconn=10)
        logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

    try:
        from shared.config import get_secret

        run_migrations = get_secret("RUN_MIGRATIONS", "true").lower() == "true"
        if run_migrations:
            try:
                _run_migrations(connection_pool)
                logger.info("Schema migrations complete")
            except Exception as e:
                logger.error(f"Migration error (non-fatal): {e}")
    finally:
        try:
            from shared.config import get_secret

            _get_tokens()
        except Exception:
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

    # Ensure archive directories exist
    for d in [HIDDEN_MEDIA_PATH, HIDDEN_THUMB_PATH]:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

    # Fetch target icons in background so startup isn't blocked
    threading.Thread(target=_refresh_target_icons, daemon=True).start()


ICONS_DIR = os.path.join(THUMB_PATH, ".icons")


def _refresh_target_icons():
    """Download subreddit/user icons from Reddit and save locally."""
    import urllib.request
    import urllib.error

    try:
        os.makedirs(ICONS_DIR, exist_ok=True)
    except Exception:
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


@app.get("/hidden-media/{path:path}")
def hidden_media(path: str):
    return _safe_file_response(HIDDEN_MEDIA_PATH, path)


@app.get("/thumb/{path:path}")
def thumb(path: str):
    return _safe_file_response(THUMB_PATH, path)


@app.get("/hidden-thumb/{path:path}")
def hidden_thumb(path: str):
    return _safe_file_response(HIDDEN_THUMB_PATH, path)


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
    """Return the API URL for a media file, handling both regular and hidden paths."""
    if not file_path:
        return None
    if file_path.startswith(HIDDEN_MEDIA_PATH):
        rel = os.path.relpath(file_path, HIDDEN_MEDIA_PATH)
        return f"/hidden-media/{rel}"
    else:
        try:
            rel = os.path.relpath(file_path, ARCHIVE_PATH)
            return f"/media/{rel}"
        except ValueError:
            return None


def _build_thumb_url(thumb_path: str) -> Optional[str]:
    """Return the API URL for a thumbnail, handling both regular and hidden paths."""
    if not thumb_path:
        return None
    if thumb_path.startswith(HIDDEN_THUMB_PATH):
        rel = os.path.relpath(thumb_path, HIDDEN_THUMB_PATH)
        return f"/hidden-thumb/{rel}"
    else:
        try:
            rel = os.path.relpath(thumb_path, THUMB_PATH)
            return f"/thumb/{rel}"
        except ValueError:
            return None


@app.get("/api/posts")
def posts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
    sort_by: Optional[str] = Query("created_utc"),
    sort_order: Optional[str] = Query("desc"),
    has_media: Optional[bool] = None,
    media_type: Optional[List[str]] = Query(None),
    nsfw: Optional[str] = None,  # "include" | "exclude" | None (show all)
    hidden: Optional[bool] = Query(None),  # default: all posts (None shows all)
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

        # Archive/Hidden filter - None shows all, True shows hidden, False shows visible
        if hidden is not None:
            if hidden:
                where_clauses.append("p.hidden = TRUE")
            else:
                where_clauses.append("p.hidden = FALSE")

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

        # Count query (uses same WHERE, no ORDER/LIMIT)
        cur.execute(f"SELECT COUNT(*) FROM posts p WHERE 1=1{where_sql}", params)
        total = cur.fetchone()[0] or 0

        # Main query
        query = f"""
            SELECT p.id, p.title, p.url, p.media_url, p.raw, p.subreddit, p.author, p.created_utc, p.hidden,
                   p.raw->>'selftext' as selftext,
                   p.raw->>'created_utc' as raw_created_utc
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
                is_hidden,
                selftext,
                raw_created_ts,
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
                    logger.error(f"ERROR parsing raw for {post_id}: {e}")

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
                    "thumb_url": thumb_url,
                    "preview_url": preview_url,
                    "hidden": is_hidden,
                }
            )

        return {"posts": results, "total": total, "limit": limit, "offset": offset}


@app.get("/api/post/{post_id}")
def get_post(post_id: str):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, url, media_url, raw, subreddit, author, created_utc, hidden
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
            is_hidden,
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
                logger.error(f"ERROR parsing raw for {post_id}: {e}")

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
            "hidden": is_hidden,
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
        dst_media_root = HIDDEN_MEDIA_PATH
        src_thumb_root = THUMB_PATH
        dst_thumb_root = HIDDEN_THUMB_PATH
    else:
        src_media_root = HIDDEN_MEDIA_PATH
        dst_media_root = ARCHIVE_PATH
        src_thumb_root = HIDDEN_THUMB_PATH
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
    """Mark a post as hidden and move its media files to the hidden storage directory."""
    with get_db_cursor() as cur:
        cur.execute("SELECT id, hidden FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        if row[1]:
            return {"status": "already_hidden", "post_id": post_id}

    # Move media files
    files_moved, errors = _move_post_media(post_id, archive=True)

    # Update post hidden flag
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE posts SET hidden = TRUE, hidden_at = now() WHERE id = %s",
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
        cur.execute("SELECT id, hidden FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        if not row[1]:
            return {"status": "not_hidden", "post_id": post_id}

    # Move media files back
    files_moved, errors = _move_post_media(post_id, archive=False)

    # Update post hidden flag
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE posts SET hidden = FALSE, hidden_at = NULL WHERE id = %s",
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
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    files_deleted += 1
                except Exception as e:
                    errors.append(f"delete {file_path}: {e}")
                    logger.error(f"Delete media error: {e}")

            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception as e:
                    errors.append(f"delete thumb {thumb_path}: {e}")
                    logger.error(f"Delete thumb error: {e}")

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

    return files_deleted, errors


@app.delete("/api/post/{post_id}")
def delete_post(post_id: str):
    """Delete a post and all its media from the database and disk.
    Does NOT blacklist the post - it can be re-hidden on next scrape."""
    conn = None
    cur = None
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()

        cur.execute("SELECT id FROM posts WHERE id = %s", (post_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Post not found")

        files_deleted = 0
        errors = []

        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE post_id = %s",
            (post_id,),
        )
        media_rows = cur.fetchall()

        for media_id, file_path, thumb_path in media_rows:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    files_deleted += 1
                except Exception as e:
                    errors.append(f"delete {file_path}: {e}")
                    logger.error(f"Delete media error: {e}")

            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except Exception as e:
                    errors.append(f"delete thumb {thumb_path}: {e}")
                    logger.error(f"Delete thumb error: {e}")

        cur.execute("DELETE FROM comments WHERE post_id = %s", (post_id,))
        cur.execute("DELETE FROM media WHERE post_id = %s", (post_id,))
        cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))

        conn.commit()
        return {
            "status": "ok",
            "post_id": post_id,
            "files_deleted": files_deleted,
            "errors": errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cur:
            cur.close()
        if conn and connection_pool:
            connection_pool.putconn(conn)


@app.get("/api/search")
def search(
    q: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    hidden: Optional[bool] = Query(False),
):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, subreddit, author, created_utc, url, media_url, raw
            FROM posts
            WHERE tsv @@ plainto_tsquery(%s)
              AND hidden = %s
            ORDER BY created_utc DESC
            LIMIT %s OFFSET %s
        """,
            (q, hidden, limit, offset),
        )

        results = []
        rows = cur.fetchall()
        if not rows:
            return results

        # Batch-fetch media for all result posts
        post_ids = [row[0] for row in rows]
        media_by_post: dict[str, list[tuple]] = {pid: [] for pid in post_ids}
        cur.execute(
            "SELECT post_id, file_path, thumb_path FROM media WHERE post_id = ANY(%s)",
            (post_ids,),
        )
        for m_pid, m_file_path, m_thumb_path in cur.fetchall():
            if m_pid in media_by_post:
                media_by_post[m_pid].append((m_file_path, m_thumb_path))

        for row in rows:
            post_id, title, subreddit, author, created_utc, url, media_url, raw = row

            is_video = _is_video_url(url)
            if raw:
                try:
                    data = raw if isinstance(raw, dict) else json.loads(raw)
                    if data.get("media") and data["media"].get("reddit_video"):
                        is_video = True
                except Exception:
                    pass

            media_rows = media_by_post.get(post_id, [])

            image_url = None
            video_url = None
            thumb_url = None

            for m_file_path, m_thumb_path in media_rows:
                if m_file_path:
                    local_url = _build_media_url(m_file_path)
                    if local_url:
                        if m_file_path.lower().endswith(
                            (".mp4", ".webm", ".mkv", ".mov", ".avi")
                        ):
                            video_url = local_url
                        else:
                            image_url = local_url
                if m_thumb_path and not thumb_url:
                    thumb_url = _build_thumb_url(m_thumb_path)

            if raw and not image_url and not video_url:
                try:
                    data = raw if isinstance(raw, dict) else json.loads(raw)
                    if is_video:
                        if not video_url:
                            extracted = _extract_video_url(url, data)
                            video_url = extracted if extracted else url
                    else:
                        if not image_url:
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
                            if remote_imgs:
                                image_url = remote_imgs[0]
                except Exception:
                    pass

            results.append(
                {
                    "id": post_id,
                    "title": title,
                    "subreddit": subreddit,
                    "author": author,
                    "created_utc": created_utc.isoformat() if created_utc else None,
                    "image_url": image_url,
                    "video_url": video_url,
                    "thumb_url": thumb_url,
                    "is_video": is_video,
                }
            )

        return results


@app.get("/api/subreddits")
def list_subreddits(limit: int = Query(50, ge=1, le=200)):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT subreddit, COUNT(*) as cnt 
            FROM posts 
            GROUP BY subreddit 
            ORDER BY cnt DESC 
            LIMIT %s
        """,
            (limit,),
        )
        return [{"subreddit": r[0], "count": r[1]} for r in cur.fetchall()]


@app.get("/api/authors")
def list_authors(limit: int = Query(50, ge=1, le=200)):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT author, COUNT(*) as cnt 
            FROM posts 
            GROUP BY author 
            ORDER BY cnt DESC 
            LIMIT %s
        """,
            (limit,),
        )
        return [{"author": r[0], "count": r[1]} for r in cur.fetchall()]


@app.get("/api/admin/stats")
def admin_stats():
    with get_db_cursor() as cur:
        cur.execute("""
            WITH sub_stats AS (
                SELECT 
                    LOWER(p.subreddit) as name,
                    COUNT(p.id) as post_count,
                    COUNT(p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d
                FROM targets t
                JOIN posts p ON LOWER(p.subreddit) = LOWER(t.name)
                WHERE t.type = 'subreddit'
                GROUP BY LOWER(p.subreddit)
            ),
            user_stats AS (
                SELECT 
                    LOWER(p.author) as name,
                    COUNT(p.id) as post_count,
                    COUNT(p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d
                FROM targets t
                JOIN posts p ON LOWER(p.author) = LOWER(t.name)
                WHERE t.type = 'user'
                GROUP BY LOWER(p.author)
            ),
            sub_media_stats AS (
                SELECT 
                    LOWER(p.subreddit) as name,
                    COUNT(m.id) as total_media,
                    COUNT(m.id) FILTER (WHERE m.status = 'done') AS downloaded_media,
                    COUNT(m.id) FILTER (WHERE m.status = 'pending') AS pending_media
                FROM targets t
                JOIN posts p ON LOWER(p.subreddit) = LOWER(t.name)
                JOIN media m ON m.post_id = p.id
                WHERE t.type = 'subreddit'
                GROUP BY LOWER(p.subreddit)
            ),
            user_media_stats AS (
                SELECT 
                    LOWER(p.author) as name,
                    COUNT(m.id) as total_media,
                    COUNT(m.id) FILTER (WHERE m.status = 'done') AS downloaded_media,
                    COUNT(m.id) FILTER (WHERE m.status = 'pending') AS pending_media
                FROM targets t
                JOIN posts p ON LOWER(p.author) = LOWER(t.name)
                JOIN media m ON m.post_id = p.id
                WHERE t.type = 'user'
                GROUP BY LOWER(p.author)
            )
            SELECT
                t.type,
                t.name,
                t.enabled,
                t.status,
                t.icon_url,
                t.last_created,
                COALESCE(CASE WHEN t.type = 'subreddit' THEN ss.post_count ELSE us.post_count END, 0) AS post_count,
                COALESCE(CASE WHEN t.type = 'subreddit' THEN ss.posts_7d ELSE us.posts_7d END, 0) AS posts_7d,
                COALESCE(CASE WHEN t.type = 'subreddit' THEN sms.total_media ELSE ums.total_media END, 0) AS total_media,
                COALESCE(CASE WHEN t.type = 'subreddit' THEN sms.downloaded_media ELSE ums.downloaded_media END, 0) AS downloaded_media,
                COALESCE(CASE WHEN t.type = 'subreddit' THEN sms.pending_media ELSE ums.pending_media END, 0) AS pending_media
            FROM targets t
            LEFT JOIN sub_stats ss ON t.type = 'subreddit' AND LOWER(t.name) = ss.name
            LEFT JOIN user_stats us ON t.type = 'user' AND LOWER(t.name) = us.name
            LEFT JOIN sub_media_stats sms ON t.type = 'subreddit' AND LOWER(t.name) = sms.name
            LEFT JOIN user_media_stats ums ON t.type = 'user' AND LOWER(t.name) = ums.name
            ORDER BY t.type, t.name
        """)
        targets = []

        for row in cur.fetchall():
            (
                ttype,
                name,
                enabled,
                status,
                icon_url,
                last_created,
                post_count,
                posts_7d,
                total_media,
                downloaded_media,
                pending_media,
            ) = row
            post_count = post_count or 0
            posts_7d = posts_7d or 0
            total_media = total_media or 0
            downloaded_media = downloaded_media or 0
            pending_media = pending_media or 0

            # Rate = actual subreddit activity over last 7 days (posts/sec)
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
                    "total_media": total_media,
                    "downloaded_media": downloaded_media,
                    "pending_media": pending_media,
                    "rate_per_second": round(rate, 4),
                    "eta_seconds": round(eta_seconds, 0) if eta_seconds else None,
                    "progress_percent": min(100, round(post_count / 10, 1))
                    if post_count > 0
                    else 0,
                }
            )

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE hidden = FALSE),
                COUNT(*) FILTER (WHERE hidden = TRUE)
            FROM posts
        """)
        total_posts, hidden_posts = cur.fetchone()

        cur.execute("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE status = 'done'),
                COUNT(*) FILTER (WHERE status = 'pending')
            FROM media
        """)
        total_media, downloaded_media, pending_media = cur.fetchone()

        cur.execute("SELECT COUNT(*) FROM comments")
        total_comments = cur.fetchone()[0]

        cur.execute("""
            SELECT DATE(created_utc) as day, COUNT(*)
            FROM posts
            WHERE created_utc > now() - INTERVAL '7 days'
              AND hidden = FALSE
            GROUP BY DATE(created_utc)
            ORDER BY day
        """)
        posts_per_day = [{"date": str(r[0]), "count": r[1]} for r in cur.fetchall()]

        return {
            "targets": targets,
            "total_posts": total_posts,
            "hidden_posts": hidden_posts,
            "total_comments": total_comments,
            "downloaded_media": downloaded_media,
            "total_media": total_media,
            "pending_media": pending_media,
            "posts_per_day": posts_per_day,
        }


@app.post("/api/admin/target/{target_type}/{name}/toggle")
def toggle_target(target_type: str, name: str):
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE targets SET enabled = NOT enabled WHERE type = %s AND name = %s RETURNING enabled",
            (target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Target not found")
        return {"enabled": result[0], "status": "ok"}


@app.post("/api/admin/target/{target_type}/{name}/status")
def set_target_status(target_type: str, name: str, new_status: str = "active"):
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")
    if new_status not in ("active", "taken_down", "deleted"):
        raise HTTPException(status_code=400, detail="Invalid status")
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE targets SET status = %s, enabled = false WHERE type = %s AND name = %s RETURNING status",
            (new_status, target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Target not found")
        return {"status": result[0], "new_status": new_status}


@app.post("/api/admin/target/{target_type}/{name}/rescan")
def rescan_target(target_type: str, name: str):
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    with get_db_cursor() as cur:
        if target_type == "user":
            cur.execute(
                "SELECT id, url, raw, subreddit, author, title FROM posts WHERE LOWER(author) = LOWER(%s)",
                (name,),
            )
        else:
            cur.execute(
                "SELECT id, url, raw, subreddit, author, title FROM posts WHERE LOWER(subreddit) = LOWER(%s)",
                (name,),
            )
        post_rows = cur.fetchall()

    if not redis_client:
        return {
            "status": "partial",
            "message": "Redis not available, posts listed but not queued",
            "post_count": len(post_rows),
        }

    rd = get_redis()
    requeued = 0
    for post_id, url, raw, subreddit, author, title in post_rows:
        try:
            data = raw if isinstance(raw, dict) else json.loads(raw) if raw else {}
            media_urls = (
                _extract_media_urls_from_raw(data, url)
                if data
                else ([url] if url else [])
            )
        except Exception:
            media_urls = [url] if url else []

        for media_url in media_urls:
            if media_url:
                rd.lpush(
                    "media_queue",
                    json.dumps(
                        {
                            "post_id": post_id,
                            "url": media_url,
                            "subreddit": subreddit,
                            "author": author,
                            "title": title or "",
                        }
                    ),
                )
                requeued += 1

    return {"status": "ok", "requeued": requeued, "posts": len(post_rows)}


@app.post("/api/admin/target/{target_type}/{name}/rescrape")
def rescrape_target(target_type: str, name: str):
    """Retry failed or missing media downloads for a specific target."""
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()

    with get_db_cursor() as cur:
        if target_type == "subreddit":
            cur.execute(
                """
                SELECT m.id, m.post_id, m.url, p.subreddit, p.author, p.title
                FROM media m
                JOIN posts p ON m.post_id = p.id
                WHERE LOWER(p.subreddit) = LOWER(%s) AND (
                   (m.status IS NULL OR m.status = '')
                   OR m.status = 'error'
                   OR m.status = 'pending'
                   OR m.status = 'failed'
                   OR (m.status IN ('done', 'partial') AND (m.file_path IS NULL OR m.file_path = ''))
                )
            """,
                (name,),
            )
        else:
            cur.execute(
                """
                SELECT m.id, m.post_id, m.url, p.subreddit, p.author, p.title
                FROM media m
                JOIN posts p ON m.post_id = p.id
                WHERE LOWER(p.author) = LOWER(%s) AND (
                   (m.status IS NULL OR m.status = '')
                   OR m.status = 'error'
                   OR m.status = 'pending'
                   OR m.status = 'failed'
                   OR (m.status IN ('done', 'partial') AND (m.file_path IS NULL OR m.file_path = ''))
                )
            """,
                (name,),
            )
        failed_media = cur.fetchall()

    if not failed_media:
        return {
            "status": "ok",
            "message": "No failed or missing media found for this target",
            "requeued": 0,
        }

    requeued = 0
    errors = []

    for media_id, post_id, url, subreddit, author, title in failed_media:
        if not url:
            errors.append(f"media {media_id}: no URL")
            continue

        try:
            rd.lpush(
                "media_queue",
                json.dumps(
                    {
                        "post_id": post_id,
                        "url": url,
                        "subreddit": subreddit,
                        "author": author,
                        "title": title or "",
                    }
                ),
            )
            requeued += 1
        except Exception as e:
            errors.append(f"media {media_id}: {e}")

    with get_db_cursor() as cur:
        if target_type == "subreddit":
            cur.execute(
                """
                UPDATE media SET status = 'pending'
                FROM posts p
                WHERE media.post_id = p.id AND LOWER(p.subreddit) = LOWER(%s) AND (
                   (media.status IS NULL OR media.status = '')
                   OR media.status = 'error'
                   OR media.status = 'pending'
                   OR media.status = 'failed'
                   OR (media.status IN ('done', 'partial') AND (media.file_path IS NULL OR media.file_path = ''))
                )
            """,
                (name,),
            )
        else:
            cur.execute(
                """
                UPDATE media SET status = 'pending'
                FROM posts p
                WHERE media.post_id = p.id AND LOWER(p.author) = LOWER(%s) AND (
                   (media.status IS NULL OR media.status = '')
                   OR media.status = 'error'
                   OR media.status = 'pending'
                   OR media.status = 'failed'
                   OR (media.status IN ('done', 'partial') AND (media.file_path IS NULL OR media.file_path = ''))
                )
            """,
                (name,),
            )

    return {
        "status": "ok",
        "requeued": requeued,
        "total_found": len(failed_media),
        "errors": errors[:10] if errors else [],
    }


@app.get("/api/admin/target/{target_type}/{name}/audit")
def audit_target(target_type: str, name: str):
    """Return per-target media integrity stats."""
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    col = "author" if target_type == "user" else "subreddit"
    with get_db_cursor() as cur:
        cur.execute(
            f"""
            SELECT
                COUNT(DISTINCT p.id)                                            AS total_posts,
                COUNT(m.id)                                                     AS total_media,
                COUNT(m.id) FILTER (WHERE m.status='done' AND m.file_path IS NOT NULL AND m.file_path != '') AS media_ok,
                COUNT(m.id) FILTER (WHERE m.status='error')                    AS media_error,
                COUNT(m.id) FILTER (WHERE m.status='pending' OR m.status IS NULL) AS media_pending,
                COUNT(DISTINCT p.id) FILTER (
                    WHERE NOT EXISTS (SELECT 1 FROM media mm WHERE mm.post_id=p.id)
                )                                                               AS posts_no_media,
                COUNT(DISTINCT p.id) FILTER (
                    WHERE EXISTS (SELECT 1 FROM media mm WHERE mm.post_id=p.id)
                    AND NOT EXISTS (
                        SELECT 1 FROM media mm WHERE mm.post_id=p.id
                        AND (mm.status!='done' OR mm.file_path IS NULL OR mm.file_path='')
                    )
                )                                                               AS posts_ok,
                COUNT(DISTINCT p.id) FILTER (
                    WHERE EXISTS (SELECT 1 FROM media mm WHERE mm.post_id=p.id
                        AND (mm.status!='done' OR mm.file_path IS NULL OR mm.file_path=''))
                    AND EXISTS (SELECT 1 FROM media mm WHERE mm.post_id=p.id
                        AND mm.status='done' AND mm.file_path IS NOT NULL AND mm.file_path!='')
                )                                                               AS posts_partial,
                COUNT(DISTINCT p.id) FILTER (
                    WHERE EXISTS (SELECT 1 FROM media mm WHERE mm.post_id=p.id)
                    AND NOT EXISTS (
                        SELECT 1 FROM media mm WHERE mm.post_id=p.id
                        AND mm.status='done' AND mm.file_path IS NOT NULL AND mm.file_path!=''
                    )
                )                                                               AS posts_all_missing
            FROM posts p
            LEFT JOIN media m ON m.post_id = p.id
            WHERE LOWER(p.{col}) = LOWER(%s)
            """,
            (name,),
        )
        row = cur.fetchone()

    (
        total_posts,
        total_media,
        media_ok,
        media_error,
        media_pending,
        posts_no_media,
        posts_ok,
        posts_partial,
        posts_all_missing,
    ) = row
    media_missing = (total_media or 0) - (media_ok or 0)
    return {
        "total_posts": total_posts or 0,
        "total_media": total_media or 0,
        "media_ok": media_ok or 0,
        "media_error": media_error or 0,
        "media_pending": media_pending or 0,
        "media_missing": media_missing,
        "posts_no_media": posts_no_media or 0,
        "posts_ok": posts_ok or 0,
        "posts_partial": posts_partial or 0,
        "posts_all_missing": posts_all_missing or 0,
    }


@app.post("/api/admin/target/{target_type}/{name}/scrape")
def trigger_target_scrape(target_type: str, name: str):
    """Trigger an immediate scrape for a single target."""
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()
    rd.lpush(
        "scrape_trigger",
        json.dumps(
            {
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "target_type": target_type,
                "target_name": name,
            }
        ),
    )
    return {"status": "ok", "message": f"Scrape triggered for {target_type}:{name}"}


@app.post("/api/admin/target/{target_type}/{name}/backfill")
def backfill_target(
    target_type: str,
    name: str,
    passes: int = Query(2, ge=1, le=10),
    workers: int = Query(3, ge=1, le=20),
):
    """Trigger a backfill for a single target."""
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()
    rd.lpush(
        "backfill_trigger",
        json.dumps(
            {
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "target_type": target_type,
                "target_name": name,
                "passes": passes,
                "workers": workers,
            }
        ),
    )
    return {"status": "ok", "message": f"Backfill triggered for {target_type}:{name}"}


@app.post("/api/admin/scrape")
def trigger_scrape():
    """Trigger immediate scrape of all enabled targets."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()
    rd.lpush(
        "scrape_trigger",
        json.dumps({"triggered_at": datetime.now(timezone.utc).isoformat()}),
    )

    return {"status": "ok", "message": "Scrape triggered"}


@app.post("/api/admin/backfill")
def trigger_backfill(
    passes: int = Query(2, ge=1, le=10, description="Number of backfill passes"),
    workers: int = Query(3, ge=1, le=20, description="Parallel workers"),
):
    """Trigger a backfill scrape to get historical posts."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()
    rd.lpush(
        "backfill_trigger",
        json.dumps(
            {
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "passes": passes,
                "workers": workers,
            }
        ),
    )

    return {
        "status": "ok",
        "message": f"Backfill triggered with {passes} passes, {workers} workers",
    }


@app.get("/api/admin/backfill/status")
def backfill_status():
    """Poll the status of the last backfill run."""
    rd = get_redis()
    if not rd:
        raise HTTPException(status_code=503, detail="Redis not available")

    status = rd.get("backfill_status")
    if not status:
        return {"status": "none", "message": "No backfill run yet"}

    return json.loads(status)


# ---------------------------------------------------------------------------
# Audit endpoints
# ---------------------------------------------------------------------------


@app.get("/api/admin/audit/summary")
def audit_summary():
    """Get summary statistics for the audit dashboard.

    Shows all posts since all scraped content is considered fully hidden.
    """
    with get_db_cursor() as cur:
        # Total posts (all are considered hidden since they're fully scraped)
        cur.execute("SELECT COUNT(*) FROM posts")
        total_posts = cur.fetchone()[0] or 0

        # Posts with media
        cur.execute("""
            SELECT COUNT(DISTINCT p.id) FROM posts p
            JOIN media m ON m.post_id = p.id
        """)
        posts_with_media = cur.fetchone()[0] or 0

        # Posts where all media is downloaded (file exists)
        cur.execute("""
            SELECT COUNT(DISTINCT p.id) FROM posts p
            JOIN media m ON m.post_id = p.id
            WHERE m.status = 'done' AND m.file_path IS NOT NULL AND m.file_path != ''
        """)
        posts_all_downloaded = cur.fetchone()[0] or 0

        # Posts with some missing media
        cur.execute("""
            SELECT COUNT(DISTINCT p.id) FROM posts p
            JOIN media m ON m.post_id = p.id
            WHERE m.status != 'done' OR m.file_path IS NULL OR m.file_path = ''
        """)
        posts_with_missing = cur.fetchone()[0] or 0

        # Total media items
        cur.execute("SELECT COUNT(*) FROM media m JOIN posts p ON m.post_id = p.id")
        total_media = cur.fetchone()[0] or 0

        # Media downloaded
        cur.execute("""
            SELECT COUNT(*) FROM media m
            JOIN posts p ON m.post_id = p.id
            WHERE m.status = 'done' AND m.file_path IS NOT NULL AND m.file_path != ''
        """)
        media_downloaded = cur.fetchone()[0] or 0

        # Media missing/failed
        cur.execute("""
            SELECT COUNT(*) FROM media m
            JOIN posts p ON m.post_id = p.id
            WHERE m.status != 'done' OR m.file_path IS NULL OR m.file_path = ''
        """)
        media_missing = cur.fetchone()[0] or 0

        return {
            "total_hidden_posts": total_posts,
            "posts_with_media": posts_with_media,
            "posts_all_ok": posts_all_downloaded,
            "posts_with_issues": posts_with_missing,
            "total_media_items": total_media,
            "media_ok": media_downloaded,
            "media_missing": media_missing,
        }


@app.get("/api/admin/audit/posts")
def audit_posts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = None,  # "ok" | "missing" | "partial"
    subreddit: Optional[str] = None,
):
    """Get detailed audit results for each post."""
    with get_db_cursor() as cur:
        # Build query based on filters - show all posts
        base_query = """
            SELECT p.id, p.title, p.subreddit, p.author, p.created_utc, p.url
            FROM posts p
        """
        params: list[Any] = []

        if subreddit:
            base_query += " AND LOWER(p.subreddit) = LOWER(%s)"
            params.append(subreddit)

        if status_filter == "ok":
            base_query += """
                AND NOT EXISTS (
                    SELECT 1 FROM media m WHERE m.post_id = p.id AND (
                        m.status != 'done' OR m.file_path IS NULL OR m.file_path = ''
                    )
                )
            """
        elif status_filter == "missing":
            base_query += """
                AND EXISTS (
                    SELECT 1 FROM media m WHERE m.post_id = p.id AND (
                        m.status != 'done' OR m.file_path IS NULL OR m.file_path = ''
                    )
                )
            """

        # Get total count
        count_query = base_query.replace(
            "SELECT p.id, p.title, p.subreddit, p.author, p.created_utc, p.url",
            "SELECT COUNT(*)",
        )
        cur.execute(count_query, params)
        total = cur.fetchone()[0] or 0

        # Get paginated results
        base_query += " ORDER BY p.created_utc DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cur.execute(base_query, params)

        results = []
        for row in cur.fetchall():
            post_id, title, subreddit, author, created_utc, url = row

            # Get media for this post
            cur.execute(
                """
                SELECT id, url, file_path, thumb_path, status
                FROM media WHERE post_id = %s
            """,
                (post_id,),
            )
            media_rows = cur.fetchall()

            media_items = []
            ok_count = 0
            missing_count = 0

            for m_id, m_url, m_file_path, m_thumb_path, m_status in media_rows:
                file_exists = (
                    m_file_path and os.path.exists(m_file_path)
                    if m_file_path
                    else False
                )
                thumb_exists = (
                    m_thumb_path and os.path.exists(m_thumb_path)
                    if m_thumb_path
                    else False
                )

                if m_status == "done" and file_exists:
                    status = "ok"
                    ok_count += 1
                elif m_status == "done" and not file_exists:
                    status = "missing_file"
                    missing_count += 1
                elif m_status == "pending":
                    status = "pending"
                    missing_count += 1
                elif m_status == "failed":
                    status = "failed"
                    missing_count += 1
                else:
                    status = "unknown"
                    missing_count += 1

                media_items.append(
                    {
                        "id": m_id,
                        "url": m_url,
                        "file_path": m_file_path,
                        "file_exists": file_exists,
                        "thumb_exists": thumb_exists,
                        "status": status,
                    }
                )

            total_media = len(media_items)
            if total_media == 0:
                post_status = "no_media"
            elif missing_count == 0:
                post_status = "ok"
            elif ok_count == 0:
                post_status = "all_missing"
            else:
                post_status = "partial"

            results.append(
                {
                    "id": post_id,
                    "title": title,
                    "subreddit": subreddit,
                    "author": author,
                    "created_utc": created_utc.isoformat() if created_utc else None,
                    "url": url,
                    "status": post_status,
                    "media_count": total_media,
                    "media_ok": ok_count,
                    "media_missing": missing_count,
                    "media": media_items,
                }
            )

        return {
            "posts": results,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@app.get("/api/admin/audit/post/{post_id}")
def audit_post_detail(post_id: str):
    """Get detailed audit information for a single post."""
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, subreddit, author, created_utc, url, raw
            FROM posts WHERE id = %s AND hidden = TRUE
        """,
            (post_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")

        post_id, title, subreddit, author, created_utc, url, raw = row

        cur.execute(
            """
            SELECT id, url, file_path, thumb_path, status, downloaded_at, retries
            FROM media WHERE post_id = %s
        """,
            (post_id,),
        )

        media_items = []
        for m in cur.fetchall():
            (
                m_id,
                m_url,
                m_file_path,
                m_thumb_path,
                m_status,
                m_downloaded_at,
                m_retries,
            ) = m

            file_exists = (
                m_file_path and os.path.exists(m_file_path) if m_file_path else False
            )
            thumb_exists = (
                m_thumb_path and os.path.exists(m_thumb_path) if m_thumb_path else False
            )

            if m_status == "done" and file_exists:
                status = "ok"
            elif m_status == "done" and not file_exists:
                status = "missing_file"
            elif m_status == "pending":
                status = "pending"
            elif m_status == "failed":
                status = "failed"
            else:
                status = "unknown"

            media_items.append(
                {
                    "id": m_id,
                    "url": m_url,
                    "file_path": m_file_path,
                    "file_exists": file_exists,
                    "thumb_path": m_thumb_path,
                    "thumb_exists": thumb_exists,
                    "status": m_status,
                    "resolved_status": status,
                    "downloaded_at": m_downloaded_at.isoformat()
                    if m_downloaded_at
                    else None,
                    "retries": m_retries,
                }
            )

        # Determine overall status
        if not media_items:
            overall = "no_media"
        elif all(m["resolved_status"] == "ok" for m in media_items):
            overall = "ok"
        elif all(m["resolved_status"] != "ok" for m in media_items):
            overall = "all_missing"
        else:
            overall = "partial"

        return {
            "id": post_id,
            "title": title,
            "subreddit": subreddit,
            "author": author,
            "created_utc": created_utc.isoformat() if created_utc else None,
            "url": url,
            "overall_status": overall,
            "media": media_items,
        }


@app.post("/api/admin/target/{target_type}")
def add_target(target_type: str, name: str):
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    with get_db_cursor() as cur:
        # Check case-insensitively first to prevent pseudo-duplicates
        cur.execute(
            "SELECT id FROM targets WHERE type = %s AND LOWER(name) = LOWER(%s)",
            (target_type, name),
        )
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Target already exists")

        cur.execute(
            "INSERT INTO targets(type, name, enabled) VALUES(%s, %s, true) ON CONFLICT (name) DO NOTHING RETURNING id",
            (target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=409, detail="Target already exists")
        return {"status": "ok", "name": name, "type": target_type}


@app.delete("/api/admin/target/{target_type}/{name}")
def delete_target(
    target_type: str,
    name: str,
    prune: bool = Query(False, description="Also delete associated posts and media"),
    delete_files: bool = Query(False, description="Also delete media files from disk"),
):
    conn = None
    cur = None
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()

        cur.execute(
            "DELETE FROM targets WHERE type = %s AND name = %s RETURNING id",
            (target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Target not found")

        deleted_posts = 0
        deleted_media = 0
        deleted_files = 0

        if prune:
            # First, fetch post IDs safely
            if target_type == "subreddit":
                cur.execute(
                    "SELECT id FROM posts WHERE LOWER(subreddit) = LOWER(%s)",
                    (name,),
                )
            else:
                cur.execute(
                    "SELECT id FROM posts WHERE LOWER(author) = LOWER(%s)",
                    (name,),
                )
            post_ids = [row[0] for row in cur.fetchall()]

            if post_ids:
                if delete_files:
                    cur.execute(
                        "SELECT file_path, thumb_path FROM media WHERE post_id = ANY(%s)",
                        (post_ids,),
                    )
                    for file_path, thumb_path in cur.fetchall():
                        for p in [file_path, thumb_path]:
                            if p and os.path.exists(p):
                                try:
                                    os.remove(p)
                                    if p == file_path:
                                        deleted_files += 1
                                except Exception:
                                    pass

                cur.execute("DELETE FROM media WHERE post_id = ANY(%s)", (post_ids,))
                deleted_media = cur.rowcount
                cur.execute("DELETE FROM comments WHERE post_id = ANY(%s)", (post_ids,))
                cur.execute("DELETE FROM posts WHERE id = ANY(%s)", (post_ids,))
                deleted_posts = cur.rowcount

        conn.commit()
        return {
            "status": "ok",
            "deleted": name,
            "pruned": prune,
            "deleted_posts": deleted_posts,
            "deleted_media": deleted_media,
            "deleted_files": deleted_files,
        }
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cur:
            cur.close()
        if conn and connection_pool:
            connection_pool.putconn(conn)


@app.get("/api/admin/logs")
def admin_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
):
    with get_db_cursor() as cur:
        query = """
            SELECT p.id, p.subreddit, p.author, p.created_utc, p.title
            FROM posts p
            WHERE 1=1
        """
        params: list[Any] = []

        if subreddit:
            query += " AND LOWER(p.subreddit) = LOWER(%s)"
            params.append(subreddit)
        if author:
            query += " AND LOWER(p.author) = LOWER(%s)"
            params.append(author)

        query += " ORDER BY p.created_utc DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        return [
            {
                "id": r[0],
                "subreddit": r[1],
                "author": r[2],
                "created_utc": r[3].isoformat() if r[3] else None,
                "title": r[4],
            }
            for r in cur.fetchall()
        ]


@app.get("/api/admin/queue")
def get_queue_status():
    if not redis_client:
        return {"status": "unavailable", "message": "Redis not connected"}

    rd = get_redis()
    queue_len = rd.llen("media_queue")

    pending_items = []
    items = rd.lrange("media_queue", 0, 9)
    for item in items:
        try:
            pending_items.append(json.loads(item))
        except Exception:
            pass

    return {"queue_length": queue_len, "recent_items": pending_items}


@app.delete("/api/admin/queue")
def clear_queue():
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()
    rd.delete("media_queue")
    return {"status": "ok", "message": "Queue cleared"}


# ---------------------------------------------------------------------------
# Thumbnail utilities
# ---------------------------------------------------------------------------

# In-memory job registry: job_id -> {status, total, done, errors, started_at}
_thumb_jobs: Dict[str, Any] = {}
_thumb_jobs_lock = threading.Lock()


def _make_thumb(src_path: str) -> str:
    """Generate a .thumb.jpg for *src_path* inside THUMB_PATH, mirroring the
    directory structure relative to ARCHIVE_PATH.  Returns the thumb path."""
    # Handle hidden files (in HIDDEN_MEDIA_PATH) -> put thumbs in HIDDEN_THUMB_PATH
    if src_path.startswith(HIDDEN_MEDIA_PATH):
        try:
            rel = os.path.relpath(src_path, HIDDEN_MEDIA_PATH)
        except ValueError:
            rel = Path(src_path).name
        thumb_subdir = Path(HIDDEN_THUMB_PATH) / Path(rel).parent
    else:
        try:
            rel = os.path.relpath(src_path, ARCHIVE_PATH)
        except ValueError:
            rel = Path(src_path).name
        thumb_subdir = Path(THUMB_PATH) / Path(rel).parent

    thumb_subdir.mkdir(parents=True, exist_ok=True)
    thumb = str(thumb_subdir / (Path(src_path).stem + ".thumb.jpg"))

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            src_path,
            "-vf",
            "scale=320:-1",
            "-frames:v",
            "1",
            thumb,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[:300])
    return thumb


def _run_thumb_job(job_id: str, rows: list, force: bool):
    """Background worker: generate thumbnails for *rows* and update DB."""
    with _thumb_jobs_lock:
        _thumb_jobs[job_id]["status"] = "running"

    done = 0
    errors = []

    for media_id, file_path, existing_thumb in rows:
        # Skip if thumb already exists on disk and we're not forcing
        if not force and existing_thumb and os.path.exists(existing_thumb):
            with _thumb_jobs_lock:
                _thumb_jobs[job_id]["done"] += 1
            done += 1
            continue

        if not file_path or not os.path.exists(file_path):
            err_msg = f"source file not found: {file_path}"
            errors.append(err_msg)
            logger.warning(f"Thumb job {job_id}: {err_msg}")
            with _thumb_jobs_lock:
                _thumb_jobs[job_id]["done"] += 1
                _thumb_jobs[job_id]["skipped"] = (
                    _thumb_jobs[job_id].get("skipped", 0) + 1
                )
            done += 1
            continue

        try:
            thumb = _make_thumb(file_path)
            conn = None
            cur = None
            try:
                conn = connection_pool.getconn()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE media SET thumb_path = %s WHERE id = %s",
                    (thumb, media_id),
                )
                conn.commit()
            finally:
                if cur:
                    cur.close()
                if conn and connection_pool:
                    connection_pool.putconn(conn)
        except Exception as e:
            errors.append(f"id={media_id}: {e}")
            logger.error(f"Thumb job {job_id}: failed for media id={media_id}: {e}")

        done += 1
        with _thumb_jobs_lock:
            _thumb_jobs[job_id]["done"] = done
            _thumb_jobs[job_id]["errors"] = errors[-20:]  # keep last 20

    with _thumb_jobs_lock:
        _thumb_jobs[job_id]["status"] = "done"
        _thumb_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        # Evict completed jobs older than 1 hour to prevent unbounded memory growth
        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        to_evict = [
            jid
            for jid, jdata in _thumb_jobs.items()
            if jdata.get("status") == "done"
            and jdata.get("finished_at")
            and datetime.fromisoformat(jdata["finished_at"]).timestamp() < cutoff
            and jid != job_id
        ]
        for jid in to_evict:
            del _thumb_jobs[jid]

    logger.info(f"Thumb job {job_id} finished: {done} processed, {len(errors)} errors")


@app.get("/api/admin/thumbnails/stats")
def thumb_stats():
    """Return thumbnail coverage statistics."""
    with get_db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM media WHERE file_path IS NOT NULL")
        total_with_file = cur.fetchone()[0]

        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE file_path IS NOT NULL"
        )
        rows = cur.fetchall()

    missing_no_path = 0
    missing_file_gone = 0
    good_count = 0

    for media_id, file_path, thumb_path in rows:
        if not thumb_path:
            missing_no_path += 1
        elif not os.path.exists(thumb_path):
            missing_file_gone += 1
        else:
            good_count += 1

    total_missing = missing_no_path + missing_file_gone

    # Count .thumb.jpg files across both thumb directories
    thumb_files_on_disk = 0
    thumb_bytes = 0
    for thumb_root in [THUMB_PATH, HIDDEN_THUMB_PATH]:
        try:
            for dirpath, _, filenames in os.walk(thumb_root):
                for fn in filenames:
                    if fn.endswith(".thumb.jpg"):
                        thumb_files_on_disk += 1
                        try:
                            thumb_bytes += os.path.getsize(os.path.join(dirpath, fn))
                        except OSError:
                            pass
        except Exception:
            pass

    return {
        "total_media_with_file": total_with_file,
        "with_thumb_in_db": good_count,
        "missing_thumb_in_db": total_missing,
        "missing_no_db_path": missing_no_path,
        "missing_file_gone": missing_file_gone,
        "thumb_files_on_disk": thumb_files_on_disk,
        "thumb_disk_mb": round(thumb_bytes / 1024 / 1024, 2),
    }


@app.post("/api/admin/thumbnails/backfill")
def thumb_backfill():
    """Generate thumbnails for all media rows that are missing one."""
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE file_path IS NOT NULL ORDER BY id"
        )
        all_rows = cur.fetchall()

    rows = []
    files_missing = 0
    thums_missing = 0
    for row in all_rows:
        media_id, file_path, thumb_path = row
        if not file_path or not os.path.exists(file_path):
            files_missing += 1
            continue
        if not thumb_path or not os.path.exists(thumb_path):
            thums_missing += 1
            rows.append(row)

    logger.info(
        f"Backfill: {len(all_rows)} total, {files_missing} files missing, {thums_missing} need thumbs"
    )

    if not rows:
        return {"job_id": None, "total": 0, "message": "No items need thumbnails"}

    job_id = str(uuid.uuid4())
    with _thumb_jobs_lock:
        _thumb_jobs[job_id] = {
            "type": "backfill",
            "status": "pending",
            "total": len(rows),
            "done": 0,
            "skipped": 0,
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

    t = threading.Thread(target=_run_thumb_job, args=(job_id, rows, False), daemon=True)
    t.start()

    return {"job_id": job_id, "total": len(rows)}


@app.post("/api/admin/thumbnails/rebuild-all")
def thumb_rebuild_all():
    """Force-regenerate thumbnails for every media row that has a local file."""
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE file_path IS NOT NULL ORDER BY id"
        )
        rows = cur.fetchall()

    job_id = str(uuid.uuid4())
    with _thumb_jobs_lock:
        _thumb_jobs[job_id] = {
            "type": "rebuild-all",
            "status": "pending",
            "total": len(rows),
            "done": 0,
            "skipped": 0,
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

    t = threading.Thread(target=_run_thumb_job, args=(job_id, rows, True), daemon=True)
    t.start()

    return {"job_id": job_id, "total": len(rows)}


@app.post("/api/admin/thumbnails/purge-orphans")
def thumb_purge_orphans():
    """Delete .thumb.jpg files on disk that have no corresponding DB row."""
    with get_db_cursor() as cur:
        cur.execute("SELECT thumb_path FROM media WHERE thumb_path IS NOT NULL")
        db_paths = {row[0] for row in cur.fetchall()}

    deleted = 0
    freed_bytes = 0
    errors = []

    for thumb_root in [THUMB_PATH, HIDDEN_THUMB_PATH]:
        try:
            for dirpath, _, filenames in os.walk(thumb_root):
                for fn in filenames:
                    if not fn.endswith(".thumb.jpg"):
                        continue
                    full = os.path.join(dirpath, fn)
                    if full not in db_paths:
                        try:
                            freed_bytes += os.path.getsize(full)
                            os.remove(full)
                            deleted += 1
                        except Exception as e:
                            errors.append(str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return {
        "deleted": deleted,
        "freed_mb": round(freed_bytes / 1024 / 1024, 2),
        "errors": errors[:20],
    }


@app.get("/api/admin/thumbnails/job/{job_id}")
def thumb_job_status(job_id: str):
    """Poll the status of a background thumbnail job."""
    with _thumb_jobs_lock:
        job = _thumb_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# Bulk archive utilities
# ---------------------------------------------------------------------------

# In-memory job registry for bulk archive jobs
_archive_jobs: Dict[str, Any] = {}
_archive_jobs_lock = threading.Lock()


def _run_bulk_archive_job(job_id: str, post_ids: list, archive: bool):
    """Background worker: archive (or unarchive) a list of post IDs."""
    with _archive_jobs_lock:
        _archive_jobs[job_id]["status"] = "running"

    done = 0
    skipped = 0
    files_moved = 0
    errors = []

    for post_id in post_ids:
        try:
            moved, errs = _move_post_media(post_id, archive=archive)
            files_moved += moved
            if errs:
                errors.extend(errs[:3])

            conn = None
            cur = None
            try:
                conn = connection_pool.getconn()
                cur = conn.cursor()
                if archive:
                    cur.execute(
                        "UPDATE posts SET hidden = TRUE, hidden_at = now() WHERE id = %s",
                        (post_id,),
                    )
                else:
                    cur.execute(
                        "UPDATE posts SET hidden = FALSE, hidden_at = NULL WHERE id = %s",
                        (post_id,),
                    )
                conn.commit()
            finally:
                if cur:
                    cur.close()
                if conn and connection_pool:
                    connection_pool.putconn(conn)

        except Exception as e:
            errors.append(f"post {post_id}: {e}")
            skipped += 1
            logger.error(f"Bulk archive job {job_id}: failed for post {post_id}: {e}")

        done += 1
        with _archive_jobs_lock:
            _archive_jobs[job_id]["done"] = done
            _archive_jobs[job_id]["skipped"] = skipped
            _archive_jobs[job_id]["files_moved"] = files_moved
            _archive_jobs[job_id]["errors"] = errors[-20:]

    with _archive_jobs_lock:
        _archive_jobs[job_id]["status"] = "done"
        _archive_jobs[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        # Evict old completed jobs
        cutoff = datetime.now(timezone.utc).timestamp() - 3600
        to_evict = [
            jid
            for jid, jdata in _archive_jobs.items()
            if jdata.get("status") == "done"
            and jdata.get("finished_at")
            and datetime.fromisoformat(jdata["finished_at"]).timestamp() < cutoff
            and jid != job_id
        ]
        for jid in to_evict:
            del _archive_jobs[jid]

    logger.info(
        f"Bulk archive job {job_id} finished: {done} processed, {skipped} skipped, "
        f"{files_moved} files moved, {len(errors)} errors"
    )


@app.get("/api/admin/archive/stats")
def archive_stats():
    """Return unhidden post counts broken down by target and date buckets."""
    with get_db_cursor() as cur:
        # Total unhidden / hidden
        cur.execute("SELECT COUNT(*) FROM posts WHERE hidden = FALSE")
        total_unhidden = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM posts WHERE hidden = TRUE")
        total_hidden = cur.fetchone()[0]

        # Per subreddit target breakdown (unhidden)
        cur.execute("""
            SELECT p.subreddit, COUNT(*) as cnt
            FROM posts p
            WHERE p.hidden = FALSE
            GROUP BY p.subreddit
            ORDER BY cnt DESC
            LIMIT 50
        """)
        by_subreddit = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

        # Per user target breakdown (unhidden)
        cur.execute("""
            SELECT p.author, COUNT(*) as cnt
            FROM posts p
            JOIN targets t ON t.type = 'user' AND LOWER(t.name) = LOWER(p.author)
            WHERE p.hidden = FALSE
            GROUP BY p.author
            ORDER BY cnt DESC
            LIMIT 50
        """)
        by_user = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]

        # By age bucket
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE created_utc < now() - INTERVAL '365 days') as older_than_1y,
                COUNT(*) FILTER (WHERE created_utc < now() - INTERVAL '180 days'
                                   AND created_utc >= now() - INTERVAL '365 days') as age_6m_1y,
                COUNT(*) FILTER (WHERE created_utc < now() - INTERVAL '90 days'
                                   AND created_utc >= now() - INTERVAL '180 days') as age_3m_6m,
                COUNT(*) FILTER (WHERE created_utc < now() - INTERVAL '30 days'
                                   AND created_utc >= now() - INTERVAL '90 days') as age_1m_3m,
                COUNT(*) FILTER (WHERE created_utc >= now() - INTERVAL '30 days') as newer_than_1m
            FROM posts
            WHERE hidden = FALSE
        """)
        row = cur.fetchone()
        by_age = {
            "older_1y": row[0] or 0,
            "age_6m_1y": row[1] or 0,
            "age_3m_6m": row[2] or 0,
            "age_1m_3m": row[3] or 0,
            "newer_1m": row[4] or 0,
        }

        # Active archive jobs
        with _archive_jobs_lock:
            active_jobs = [
                {**v, "id": k}
                for k, v in _archive_jobs.items()
                if v.get("status") in ("pending", "running")
            ]

    return {
        "total_unhidden": total_unhidden,
        "total_hidden": total_hidden,
        "total_posts": total_unhidden + total_hidden,
        "archive_pct": round(
            total_hidden / max(1, total_unhidden + total_hidden) * 100, 1
        ),
        "by_subreddit": by_subreddit,
        "by_user": by_user,
        "by_age": by_age,
        "active_jobs": active_jobs,
    }


@app.post("/api/admin/archive/bulk")
def bulk_archive(
    target_type: Optional[str] = None,
    target_name: Optional[str] = None,
    before_days: Optional[int] = None,
    media_status: Optional[str] = None,
    dry_run: bool = False,
):
    """
    Bulk archive posts matching specified criteria.

    Filters (all optional, combined with AND):
    - target_type + target_name: limit to a specific subreddit or user
    - before_days: only posts older than N days
    - media_status: 'done' | 'pending' | 'none' (posts with no media)
    """
    conditions = ["hidden = FALSE"]
    params = []

    if target_type and target_name:
        if target_type == "subreddit":
            conditions.append("LOWER(subreddit) = LOWER(%s)")
        else:
            conditions.append("LOWER(author) = LOWER(%s)")
        params.append(target_name)

    if before_days is not None and before_days > 0:
        conditions.append("created_utc < now() - INTERVAL '%s days'")
        params.append(before_days)

    where = " AND ".join(conditions)

    with get_db_cursor() as cur:
        if media_status == "done":
            query = f"""
                SELECT DISTINCT p.id FROM posts p
                JOIN media m ON m.post_id = p.id AND m.status = 'done'
                WHERE {where}
            """
        elif media_status == "none":
            query = f"""
                SELECT p.id FROM posts p
                WHERE {where}
                  AND NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
            """
        else:
            query = f"SELECT id FROM posts WHERE {where}"

        cur.execute(query, params)
        post_ids = [r[0] for r in cur.fetchall()]

    if dry_run:
        return {"dry_run": True, "post_count": len(post_ids)}

    if not post_ids:
        return {
            "job_id": None,
            "total": 0,
            "message": "No posts match the given filters",
        }

    job_id = str(uuid.uuid4())
    with _archive_jobs_lock:
        _archive_jobs[job_id] = {
            "type": "bulk_archive",
            "status": "pending",
            "total": len(post_ids),
            "done": 0,
            "skipped": 0,
            "files_moved": 0,
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "filter_summary": {
                "target_type": target_type,
                "target_name": target_name,
                "before_days": before_days,
                "media_status": media_status,
            },
        }

    t = threading.Thread(
        target=_run_bulk_archive_job, args=(job_id, post_ids, True), daemon=True
    )
    t.start()

    return {"job_id": job_id, "total": len(post_ids)}


@app.post("/api/admin/archive/all")
def archive_all_posts():
    """Archive every unhidden post. Starts a background job."""
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id FROM posts WHERE hidden = FALSE ORDER BY created_utc ASC"
        )
        post_ids = [r[0] for r in cur.fetchall()]

    if not post_ids:
        return {"job_id": None, "total": 0, "message": "All posts are already hidden"}

    job_id = str(uuid.uuid4())
    with _archive_jobs_lock:
        _archive_jobs[job_id] = {
            "type": "archive_all",
            "status": "pending",
            "total": len(post_ids),
            "done": 0,
            "skipped": 0,
            "files_moved": 0,
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

    t = threading.Thread(
        target=_run_bulk_archive_job, args=(job_id, post_ids, True), daemon=True
    )
    t.start()

    return {"job_id": job_id, "total": len(post_ids)}


@app.post("/api/admin/target/{target_type}/{name}/archive-all")
def archive_all_target(target_type: str, name: str):
    """Archive all unhidden posts belonging to a specific target."""
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    with get_db_cursor() as cur:
        if target_type == "subreddit":
            cur.execute(
                "SELECT id FROM posts WHERE hidden = FALSE AND LOWER(subreddit) = LOWER(%s) ORDER BY created_utc ASC",
                (name,),
            )
        else:
            cur.execute(
                "SELECT id FROM posts WHERE hidden = FALSE AND LOWER(author) = LOWER(%s) ORDER BY created_utc ASC",
                (name,),
            )
        post_ids = [r[0] for r in cur.fetchall()]

    if not post_ids:
        return {
            "job_id": None,
            "total": 0,
            "message": f"No unhidden posts for {target_type}:{name}",
        }

    job_id = str(uuid.uuid4())
    with _archive_jobs_lock:
        _archive_jobs[job_id] = {
            "type": "archive_target",
            "status": "pending",
            "total": len(post_ids),
            "done": 0,
            "skipped": 0,
            "files_moved": 0,
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "filter_summary": {"target_type": target_type, "target_name": name},
        }

    t = threading.Thread(
        target=_run_bulk_archive_job, args=(job_id, post_ids, True), daemon=True
    )
    t.start()

    return {"job_id": job_id, "total": len(post_ids)}


@app.get("/api/admin/archive/job/{job_id}")
def archive_job_status(job_id: str):
    """Poll the status of a background bulk archive job."""
    with _archive_jobs_lock:
        job = _archive_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# Media re-scan utilities
# ---------------------------------------------------------------------------


def _extract_media_urls_from_raw(raw: dict, post_url: str) -> list:
    """Extract ALL media URLs from a post's raw JSON data (mirrors ingester logic)."""
    urls = []

    has_media_metadata = bool(raw.get("media_metadata"))
    if has_media_metadata:
        for img_id, img_data in raw["media_metadata"].items():
            if "s" in img_data:
                u = img_data["s"].get("u")
                if u:
                    urls.append(u)
            elif img_data.get("p"):
                u = img_data["p"][-1].get("u")
                if u:
                    urls.append(u)
    else:
        if post_url and (
            "i.redd.it" in post_url
            or "i.imgur.com" in post_url
            or post_url.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
        ):
            urls.append(post_url)

        if not urls and "preview" in raw:
            imgs = raw["preview"].get("images", [])
            for img in imgs:
                u = img.get("source", {}).get("url")
                if u:
                    urls.append(u)
                for var_type, var_imgs in img.get("variants", {}).items():
                    if isinstance(var_imgs, dict):
                        vu = var_imgs.get("url")
                        if vu:
                            urls.append(vu)
                    elif isinstance(var_imgs, list):
                        for vi in var_imgs:
                            vu = vi.get("url")
                            if vu:
                                urls.append(vu)

    if "crosspost_parent_list" in raw:
        for cp in raw.get("crosspost_parent_list", []):
            for img_id, img_data in cp.get("media_metadata", {}).items():
                if "s" in img_data:
                    u = img_data["s"].get("u")
                    if u:
                        urls.append(u)

    # Deduplicate
    seen = set()
    unique_urls = []
    for u in urls:
        if u:
            # Strip query params from reddit previews for consistency
            if "preview.redd.it" in u or "external-preview.redd.it" in u:
                u = u.split("?")[0]
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

    return unique_urls


@app.post("/api/admin/media/rescan")
def media_rescan():
    """Re-scan existing posts for additional media that wasn't queued."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()

    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id, url, raw, subreddit, author, title FROM posts WHERE raw IS NOT NULL"
        )
        post_rows = cur.fetchall()

        cur.execute("SELECT url FROM media")
        existing_urls = {row[0] for row in cur.fetchall() if row[0]}

        queued_urls = set()
        try:
            queue_items = rd.lrange("media_queue", 0, -1)
            for item in queue_items:
                try:
                    data = json.loads(item)
                    u = data.get("url")
                    if u:
                        queued_urls.add(u)
                except Exception:
                    pass
        except Exception:
            pass

        all_existing = existing_urls | queued_urls

    new_queue_count = 0
    posts_scanned = 0
    urls_found = 0
    errors = []

    try:
        for post_id, url, raw, subreddit, author, title in post_rows:
            if not raw:
                continue

            try:
                data = raw if isinstance(raw, dict) else json.loads(raw)
            except Exception as e:
                errors.append(f"post {post_id}: parse error")
                continue

            try:
                extracted = _extract_media_urls_from_raw(data, url)
            except Exception as e:
                errors.append(f"post {post_id}: extract error - {e}")
                continue

            for media_url in extracted:
                if media_url not in all_existing:
                    try:
                        rd.lpush(
                            "media_queue",
                            json.dumps(
                                {
                                    "post_id": post_id,
                                    "url": media_url,
                                    "subreddit": subreddit,
                                    "author": author,
                                    "title": title or "",
                                }
                            ),
                        )
                        new_queue_count += 1
                        all_existing.add(media_url)
                    except Exception as e:
                        errors.append(f"queue error for {media_url}: {e}")

            if extracted:
                posts_scanned += 1
                urls_found += len(extracted)
    except Exception as e:
        logger.error(f"Media rescan error: {e}")
        return {
            "error": str(e),
            "posts_scanned": posts_scanned,
            "urls_found": urls_found,
            "newly_queued": new_queue_count,
        }

    if errors:
        logger.warning(f"Media rescan had {len(errors)} errors: {errors[:10]}")

    logger.info(
        f"Media rescan: found {urls_found} URLs across {posts_scanned} posts, queued {new_queue_count} new"
    )

    return {
        "posts_scanned": posts_scanned,
        "urls_found": urls_found,
        "newly_queued": new_queue_count,
    }


@app.post("/api/admin/media/rescrape")
def media_rescrape():
    """Retry all failed or missing media downloads.
    Finds all media with status='error' or status='pending' or missing file_path
    and requeues them for download.
    """
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()

    with get_db_cursor() as cur:
        cur.execute("""
            SELECT m.id, m.post_id, m.url, p.subreddit, p.author, p.title
            FROM media m
            JOIN posts p ON m.post_id = p.id
            WHERE (m.status IS NULL OR m.status = '')
               OR m.status = 'error'
               OR m.status = 'pending'
               OR (m.status IN ('done', 'partial') AND (m.file_path IS NULL OR m.file_path = ''))
            ORDER BY m.id
        """)
        failed_media = cur.fetchall()

    if not failed_media:
        return {
            "status": "ok",
            "message": "No failed or missing media found",
            "requeued": 0,
        }

    requeued = 0
    errors = []

    for media_id, post_id, url, subreddit, author, title in failed_media:
        if not url:
            errors.append(f"media {media_id}: no URL")
            continue

        try:
            rd.lpush(
                "media_queue",
                json.dumps(
                    {
                        "post_id": post_id,
                        "url": url,
                        "subreddit": subreddit,
                        "author": author,
                        "title": title or "",
                    }
                ),
            )
            requeued += 1
        except Exception as e:
            errors.append(f"media {media_id}: {e}")

    with get_db_cursor() as cur:
        cur.execute("""
            UPDATE media
            SET status = 'pending'
            WHERE (status IS NULL OR status = '')
               OR status = 'error'
               OR status = 'pending'
               OR (status IN ('done', 'partial') AND (file_path IS NULL OR file_path = ''))
        """)

    logger.info(f"Media rescrape: requeued {requeued} failed/missing items")

    return {
        "status": "ok",
        "requeued": requeued,
        "total_found": len(failed_media),
        "errors": errors[:10] if errors else [],
    }


@app.get("/api/admin/health")
def health_check():
    issues = []

    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:
        issues.append(f"Database: {str(e)}")

    try:
        if redis_client:
            redis_client.ping()
        else:
            issues.append("Redis: not connected")
    except Exception as e:
        issues.append(f"Redis: {str(e)}")

    for label, path in [
        ("Archive path", ARCHIVE_PATH),
        ("Thumb path", THUMB_PATH),
        ("Archive media path", HIDDEN_MEDIA_PATH),
    ]:
        try:
            if not os.path.exists(path):
                issues.append(f"{label} not accessible: {path}")
        except Exception as e:
            issues.append(f"{label}: {str(e)}")

    return {"status": "healthy" if not issues else "degraded", "issues": issues}


@app.get("/api/events")
async def event_stream():
    """Server-Sent Events endpoint for real-time UI updates."""

    async def db_stats():
        def _query() -> Dict[str, Any]:
            conn = None
            cur = None
            try:
                if not connection_pool:
                    return {
                        "total_posts": 0,
                        "hidden_posts": 0,
                        "total_comments": 0,
                        "downloaded_media": 0,
                        "pending_media": 0,
                        "total_media": 0,
                    }
                conn = connection_pool.getconn()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM posts WHERE hidden = FALSE")
                total_posts = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM posts WHERE hidden = TRUE")
                hidden_posts = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM comments")
                total_comments = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM media WHERE status='done'")
                dl_media = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM media WHERE status='pending'")
                pend_media = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM media")
                tot_media = cur.fetchone()[0]
                return {
                    "total_posts": total_posts,
                    "hidden_posts": hidden_posts,
                    "total_comments": total_comments,
                    "downloaded_media": dl_media,
                    "pending_media": pend_media,
                    "total_media": tot_media,
                }
            finally:
                if cur:
                    cur.close()
                if conn and connection_pool:
                    connection_pool.putconn(conn)

        return await asyncio.to_thread(_query)

    async def db_target_stats():
        def _query():
            conn = None
            cur = None
            try:
                if not connection_pool:
                    return []
                conn = connection_pool.getconn()
                cur = conn.cursor()
                cur.execute("""
                    SELECT
                        t.type,
                        t.name,
                        t.enabled,
                        t.status,
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
                    tot_media = tot_media or 0
                    dl_media = dl_media or 0
                    pend_media = pend_media or 0

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
                            "total_media": tot_media,
                            "downloaded_media": dl_media,
                            "pending_media": pend_media,
                            "rate_per_second": round(rate, 4),
                            "eta_seconds": round(eta_seconds, 0)
                            if eta_seconds
                            else None,
                            "progress_percent": min(100, round(post_count / 10, 1))
                            if post_count > 0
                            else 0,
                        }
                    )
                return targets
            finally:
                if cur:
                    cur.close()
                if conn and connection_pool:
                    connection_pool.putconn(conn)

        return await asyncio.to_thread(_query)

    async def db_new_posts(after_dt):
        def _query():
            conn = None
            cur = None
            try:
                if not connection_pool:
                    return []
                conn = connection_pool.getconn()
                cur = conn.cursor()
                if after_dt is None:
                    cur.execute(
                        "SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts WHERE hidden = FALSE ORDER BY ingested_at DESC NULLS LAST LIMIT 1"
                    )
                else:
                    cur.execute(
                        "SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts WHERE hidden = FALSE AND ingested_at > %s ORDER BY ingested_at DESC NULLS LAST LIMIT 20",
                        (after_dt,),
                    )
                return cur.fetchall()
            finally:
                if cur:
                    cur.close()
                if conn and connection_pool:
                    connection_pool.putconn(conn)

        return await asyncio.to_thread(_query)

    async def db_new_media(after_dt):
        def _query():
            conn = None
            cur = None
            try:
                if not connection_pool:
                    return []
                conn = connection_pool.getconn()
                cur = conn.cursor()
                if after_dt is None:
                    cur.execute(
                        "SELECT id, post_id, url, file_path, downloaded_at FROM media WHERE status = 'done' ORDER BY downloaded_at DESC LIMIT 1"
                    )
                else:
                    cur.execute(
                        "SELECT id, post_id, url, file_path, downloaded_at FROM media WHERE status = 'done' AND downloaded_at > %s ORDER BY downloaded_at DESC LIMIT 20",
                        (after_dt,),
                    )
                return cur.fetchall()
            finally:
                if cur:
                    cur.close()
                if conn and connection_pool:
                    connection_pool.putconn(conn)

        return await asyncio.to_thread(_query)

    async def check_health():
        def _check():
            issues = []
            conn = None
            cur = None
            try:
                if connection_pool:
                    conn = connection_pool.getconn()
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT 1")
                    finally:
                        if cur:
                            cur.close()
                        if conn and connection_pool:
                            connection_pool.putconn(conn)
            except Exception as e:
                issues.append(f"Database: {str(e)}")
            try:
                if not os.path.exists(ARCHIVE_PATH):
                    issues.append(f"Archive path not accessible: {ARCHIVE_PATH}")
            except Exception as e:
                issues.append(f"Archive path: {str(e)}")
            try:
                if not os.path.exists(THUMB_PATH):
                    issues.append(f"Thumb path not accessible: {THUMB_PATH}")
            except Exception as e:
                issues.append(f"Thumb path: {str(e)}")
            return {"status": "healthy" if not issues else "degraded", "issues": issues}

        return await asyncio.to_thread(_check)

    async def generate():
        seed_posts = await db_new_posts(None)
        last_post_ingested = seed_posts[0][5] if seed_posts else None

        seed_media = await db_new_media(None)
        last_media_downloaded = seed_media[0][4] if seed_media else None

        while True:
            try:
                stats = await db_stats()

                redis_ok = False
                if redis_client:
                    try:
                        stats["queue_length"] = await asyncio.to_thread(
                            redis_client.llen, "media_queue"
                        )
                        redis_ok = True
                    except Exception:
                        stats["queue_length"] = 0
                else:
                    stats["queue_length"] = 0

                try:
                    stats["targets"] = await db_target_stats()
                except Exception as e:
                    logger.error(f"SSE target stats error: {e}")

                try:
                    health = await check_health()
                    if not redis_ok:
                        health["issues"].append("Redis: not connected")
                        health["status"] = "degraded"
                    stats["health"] = health
                except Exception as e:
                    logger.error(f"SSE health check error: {e}")

                new_rows = await db_new_posts(last_post_ingested)
                if new_rows:
                    stats["new_posts"] = [
                        {
                            "id": r[0],
                            "title": r[1],
                            "subreddit": r[2],
                            "author": r[3],
                            "created_utc": r[4].isoformat() if r[4] else None,
                        }
                        for r in new_rows
                    ]
                    last_post_ingested = new_rows[0][5]

                media_rows = await db_new_media(last_media_downloaded)
                if media_rows:
                    stats["new_media"] = [
                        {
                            "id": r[0],
                            "post_id": r[1],
                            "url": r[2],
                            "file_path": r[3],
                        }
                        for r in media_rows
                    ]
                    last_media_downloaded = media_rows[0][4]

                try:
                    payload = json.dumps(stats)
                except Exception as e:
                    logger.error(f"SSE serialize error (stripping targets/health): {e}")
                    stats.pop("targets", None)
                    stats.pop("health", None)
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
def metrics():
    if redis_client:
        try:
            queue_len = redis_client.llen("media_queue")
            queue_length.set(queue_len)
        except Exception as e:
            logger.warning(f"Redis unavailable for metrics: {e}")

    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM posts")
            posts_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM comments")
            comments_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM media")
            media_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(*) FROM media WHERE status = 'done'")
            media_downloaded_in_db.set(cur.fetchone()[0] or 0)

            cur.execute("SELECT subreddit, COUNT(*) FROM posts GROUP BY subreddit")
            for row in cur.fetchall():
                posts_total.labels(subreddit=row[0]).set(row[1] or 0)

            cur.execute("""
                SELECT p.subreddit, COUNT(c.id)
                FROM comments c
                JOIN posts p ON c.post_id = p.id
                GROUP BY p.subreddit
            """)
            for row in cur.fetchall():
                comments_total.labels(subreddit=row[0]).set(row[1] or 0)

            cur.execute(
                "SELECT type, name, EXTRACT(EPOCH FROM last_created) FROM targets WHERE last_created IS NOT NULL"
            )
            for row in cur.fetchall():
                target_last_fetch.labels(target_type=row[0], target_name=row[1]).set(
                    row[2] or 0
                )
    except Exception as e:
        logger.error(f"Metrics error: {e}")

    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/comments")
def get_comments(
    post_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, author, body, created_utc 
            FROM comments 
            WHERE post_id = %s
            ORDER BY created_utc
            LIMIT %s OFFSET %s
        """,
            (post_id, limit, offset),
        )

        return [
            {
                "id": r[0],
                "author": r[1],
                "body": r[2],
                "created_utc": r[3].isoformat() if r[3] else None,
            }
            for r in cur.fetchall()
        ]


@app.get("/api/comments/search")
def search_comments(q: str, limit: int = Query(50, ge=1, le=200)):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.post_id, c.author, c.body, c.created_utc, p.title
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            WHERE c.tsv @@ plainto_tsquery(%s)
            LIMIT %s
        """,
            (q, limit),
        )

        return [
            {
                "id": r[0],
                "post_id": r[1],
                "author": r[2],
                "body": r[3],
                "created_utc": r[4].isoformat() if r[4] else None,
                "post_title": r[5],
            }
            for r in cur.fetchall()
        ]


@app.get("/api/media")
def get_media(
    post_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    with get_db_cursor() as cur:
        query = "SELECT id, post_id, url, file_path, status, downloaded_at FROM media WHERE 1=1"
        params = []

        if post_id:
            query += " AND post_id = %s"
            params.append(post_id)
        if status:
            query += " AND status = %s"
            params.append(status)

        query += " ORDER BY downloaded_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)

        return [
            {
                "id": r[0],
                "post_id": r[1],
                "url": r[2],
                "file_path": r[3],
                "status": r[4],
                "downloaded_at": r[5].isoformat() if r[5] else None,
            }
            for r in cur.fetchall()
        ]


@app.delete("/api/admin/reset")
def reset_all(confirm: str = Query(...)):
    """Wipe all hidden data: files on disk, every DB table, and the Redis queue."""
    if confirm != "RESET":
        raise HTTPException(status_code=400, detail="Pass confirm=RESET to proceed")

    deleted_files = 0
    deleted_bytes = 0
    errors = []

    # 1. Collect tracked file paths before truncating tables
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT file_path, thumb_path FROM media WHERE file_path IS NOT NULL"
            )
            tracked = cur.fetchall()
    except Exception as e:
        tracked = []
        errors.append(f"Could not read media table: {e}")

    # 2. Delete tracked files from disk (both active and hidden)
    for file_path, thumb_path in tracked:
        for p in [file_path, thumb_path]:
            if p and os.path.exists(p):
                try:
                    deleted_bytes += os.path.getsize(p)
                    os.remove(p)
                    deleted_files += 1
                except Exception as e:
                    errors.append(f"Delete failed {p}: {e}")

    # 3. Remove organised subdirectories left empty after file deletion
    for base_dir in [ARCHIVE_PATH, THUMB_PATH, HIDDEN_MEDIA_PATH, HIDDEN_THUMB_PATH]:
        for subdir in ["r", "u"]:
            top = os.path.join(base_dir, subdir)
            if os.path.isdir(top):
                for entry in os.scandir(top):
                    if entry.is_dir():
                        try:
                            os.rmdir(entry.path)
                        except OSError:
                            pass
                try:
                    os.rmdir(top)
                except OSError:
                    pass

    # 4. Truncate all tables (CASCADE handles FK constraints)
    try:
        with get_db_cursor() as cur:
            cur.execute("TRUNCATE media, comments, posts RESTART IDENTITY CASCADE")
    except Exception as e:
        errors.append(f"DB truncate failed: {e}")

    # 5. Clear Redis queue
    if redis_client:
        try:
            redis_client.delete("media_queue")
        except Exception as e:
            errors.append(f"Redis flush failed: {e}")

    return {
        "status": "ok",
        "deleted_files": deleted_files,
        "deleted_mb": round(deleted_bytes / 1024 / 1024, 2),
        "errors": errors,
    }


@app.get("/api/posts/by-date")
def posts_by_date(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    hidden: Optional[bool] = Query(False),
):
    with get_db_cursor() as cur:
        query = "SELECT id, title, subreddit, author, created_utc FROM posts WHERE hidden = %s"
        params: list[Any] = [hidden]

        if start_date:
            query += " AND created_utc >= %s"
            params.append(start_date)
        if end_date:
            query += " AND created_utc <= %s"
            params.append(end_date)

        query += " ORDER BY created_utc DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)

        return [
            {
                "id": r[0],
                "title": r[1],
                "subreddit": r[2],
                "author": r[3],
                "created_utc": r[4].isoformat() if r[4] else None,
            }
            for r in cur.fetchall()
        ]


# SPA catch-all: serve index.html for all client-side routes
# MUST be the last route so it doesn't shadow /api/* handlers
@app.get("/{full_path:path}")
def spa_catchall(full_path: str):
    if full_path.startswith(
        (
            "api/",
            "media/",
            "hidden-media/",
            "thumb/",
            "hidden-thumb/",
            "static/",
            "icon.png",
        )
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    idx = DIST_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"detail": "Not Found"}
