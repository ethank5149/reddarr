import asyncio
import logging
import os
import json
import redis
import subprocess
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
from psycopg2 import pool as pg_pool
from fastapi import FastAPI, Query, HTTPException, Depends
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

app = FastAPI(title="Reddit Archive API", version="3.0.0")
logger.info("API STARTED - version 3.0.0")

ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "/data")
THUMB_PATH = os.getenv("THUMB_PATH", os.path.join(ARCHIVE_PATH, ".thumbs"))

connection_pool = None
redis_client = None


@app.on_event("startup")
def startup():
    global connection_pool, redis_client
    try:
        db_url = os.getenv("DB_URL")
        connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=10, dsn=db_url
        )
        logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise

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


@app.on_event("shutdown")
def shutdown():
    global connection_pool
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


if os.path.exists("dist"):
    app.mount("/static", StaticFiles(directory="dist/static"), name="static")


@app.get("/media/{path:path}")
def media(path: str):
    full_path = os.path.join(ARCHIVE_PATH, path)
    if os.path.exists(full_path):
        return FileResponse(full_path)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/thumb/{path:path}")
def thumb(path: str):
    full_path = os.path.join(THUMB_PATH, path)
    if os.path.exists(full_path):
        return FileResponse(full_path)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/")
def root():
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    return {"detail": "Not Found"}


@app.get("/icon.png")
def icon():
    if os.path.exists("dist/icon.png"):
        return FileResponse("dist/icon.png")
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/api/debug/{post_id}")
def debug_post(post_id: str):
    with get_db_cursor() as cur:
        cur.execute("SELECT id, raw FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return {"id": row[0], "raw": row[1]}


_VIDEO_URL_PATTERNS = ("v.redd.it", "youtube.com", "youtu.be", "streamable.com")


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
):
    # Whitelist sort fields to prevent SQL injection
    allowed_sort_by = {"created_utc", "title", "ingested_at"}
    allowed_sort_order = {"asc", "desc"}
    if sort_by not in allowed_sort_by:
        sort_by = "created_utc"
    if sort_order not in allowed_sort_order:
        sort_order = "desc"

    with get_db_cursor() as cur:
        query = """
            SELECT p.id, p.title, p.url, p.media_url, p.raw, p.subreddit, p.author, p.created_utc
            FROM posts p
            WHERE 1=1
        """
        params = []

        if subreddit:
            query += " AND subreddit = %s"
            params.append(subreddit)
        if author:
            query += " AND author = %s"
            params.append(author)

        # media_type supersedes legacy has_media
        if media_type and len(media_type) > 0:
            # Build OR conditions for multiple media types
            media_conditions = []
            if "video" in media_type:
                # Video if: downloaded video OR video URL
                media_conditions.append(
                    "(EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done' AND "
                    "(LOWER(m.file_path) LIKE '%.mp4' OR LOWER(m.file_path) LIKE '%.webm' OR LOWER(m.file_path) LIKE '%.mkv' OR LOWER(m.file_path) LIKE '%.mov')) OR "
                    "url LIKE '%v.redd.it%' OR url LIKE '%youtube.com%' OR url LIKE '%youtu.be%' OR url LIKE '%streamable.com%')"
                )
            if "image" in media_type:
                # Image if: downloaded image OR image URL in posts table
                media_conditions.append(
                    "(EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done' AND "
                    "(LOWER(m.file_path) LIKE '%.jpg' OR LOWER(m.file_path) LIKE '%.jpeg' OR LOWER(m.file_path) LIKE '%.png' OR "
                    "LOWER(m.file_path) LIKE '%.gif' OR LOWER(m.file_path) LIKE '%.webp')) OR "
                    "(url LIKE '%i.redd.it%' OR url LIKE '%i.imgur.com%' OR url LIKE '%.jpg' OR url LIKE '%.jpeg' OR "
                    "url LIKE '%.png' OR url LIKE '%.gif' OR url LIKE '%.webp'))"
                )
            if "text" in media_type:
                # Text if: no downloaded media AND no image/video URL
                media_conditions.append(
                    "NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done') AND "
                    "url NOT LIKE '%i.redd.it%' AND url NOT LIKE '%i.imgur.com%' AND url NOT LIKE '%.jpg' AND "
                    "url NOT LIKE '%.jpeg' AND url NOT LIKE '%.png' AND url NOT LIKE '%.gif' AND url NOT LIKE '%.webp' AND "
                    "url NOT LIKE '%v.redd.it%' AND url NOT LIKE '%youtube.com%' AND url NOT LIKE '%youtu.be%'"
                )
            if media_conditions:
                query += " AND (" + " OR ".join(media_conditions) + ")"
        elif has_media is True:
            query += " AND (EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done' AND m.file_path IS NOT NULL) OR url IS NOT NULL)"
        elif has_media is False:
            query += " AND NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.status = 'done') AND (url IS NULL OR url = '')"

        # NSFW filter - check raw JSON for over_18 field
        if nsfw == "exclude":
            query += " AND (raw IS NULL OR raw::text NOT LIKE '%\"over_18\":true%')"
        elif nsfw == "include":
            pass  # show all (default behavior)

        query += f" ORDER BY {sort_by} {sort_order.upper()} LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        results = []

        for row in cur.fetchall():
            (
                post_id,
                title,
                url,
                media_url,
                raw,
                subreddit,
                author,
                created_utc,
            ) = row
            selftext = None
            created_ts = None
            is_video = _is_video_url(url)

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
                    rel = os.path.relpath(m_file_path, ARCHIVE_PATH)
                    local_url = f"/media/{rel}"
                    # Check extension to determine type
                    if m_file_path.lower().endswith(
                        (".mp4", ".webm", ".mkv", ".mov", ".avi")
                    ):
                        video_urls.append(local_url)
                    else:
                        image_urls.append(local_url)
                if m_thumb_path and os.path.exists(m_thumb_path) and not thumb_url:
                    rel = os.path.relpath(m_thumb_path, THUMB_PATH)
                    thumb_url = f"/thumb/{rel}"

            preview_url = None
            # Only use remote URLs as fallback when no local files
            if raw:
                try:
                    data = raw if isinstance(raw, dict) else json.loads(raw)
                    selftext = data.get("selftext")
                    created_ts = data.get("created_utc")

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
            video_urls = list(dict.fromkeys([v for v in video_urls if v]))
            image_urls = list(dict.fromkeys([i for i in image_urls if i]))

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
                }
            )
        return results


@app.get("/api/post/{post_id}")
def get_post(post_id: str):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, url, media_url, raw, subreddit, author, created_utc 
            FROM posts WHERE id = %s
        """,
            (post_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")

        post_id, title, url, media_url, raw, subreddit, author, created_utc = row

        cur.execute(
            """
            SELECT name FROM tags 
            JOIN post_tags ON tags.id = post_tags.tag_id 
            WHERE post_id = %s
        """,
            (post_id,),
        )
        tags = [r[0] for r in cur.fetchall()]

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
                rel = os.path.relpath(m_file_path, ARCHIVE_PATH)
                local_url = f"/media/{rel}"
                if m_file_path.lower().endswith(
                    (".mp4", ".webm", ".mkv", ".mov", ".avi")
                ):
                    video_urls.append(local_url)
                else:
                    image_urls.append(local_url)
            if m_thumb_path and os.path.exists(m_thumb_path) and not thumb_url:
                rel = os.path.relpath(m_thumb_path, THUMB_PATH)
                thumb_url = f"/thumb/{rel}"

        # Build fallback URLs from raw JSON
        selftext = None
        is_video = _is_video_url(url)
        if raw:
            try:
                data = raw if isinstance(raw, dict) else json.loads(raw)
                selftext = data.get("selftext")

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
        video_urls = list(dict.fromkeys([v for v in video_urls if v]))
        image_urls = list(dict.fromkeys([i for i in image_urls if i]))

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
            "tags": tags,
            "comments": comments,
        }


@app.get("/api/search")
def search(q: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, title, subreddit, author, created_utc 
            FROM posts
            WHERE tsv @@ plainto_tsquery(%s)
            ORDER BY created_utc DESC
            LIMIT %s OFFSET %s
        """,
            (q, limit, offset),
        )

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
            SELECT
                t.type,
                t.name,
                t.enabled,
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
            GROUP BY t.type, t.name, t.enabled, t.last_created
            ORDER BY t.type, t.name
        """)
        targets = []

        for row in cur.fetchall():
            (
                ttype,
                name,
                enabled,
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

        cur.execute("SELECT COUNT(*) FROM posts")
        total_posts = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM comments")
        total_comments = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM media WHERE status = 'done'")
        downloaded_media = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM media")
        total_media = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM media WHERE status = 'pending'")
        pending_media = cur.fetchone()[0]

        cur.execute("""
            SELECT DATE(created_utc) as day, COUNT(*)
            FROM posts
            WHERE created_utc > now() - INTERVAL '7 days'
            GROUP BY DATE(created_utc)
            ORDER BY day
        """)
        posts_per_day = [{"date": str(r[0]), "count": r[1]} for r in cur.fetchall()]

        return {
            "targets": targets,
            "total_posts": total_posts,
            "total_comments": total_comments,
            "downloaded_media": downloaded_media,
            "total_media": total_media,
            "pending_media": pending_media,
            "posts_per_day": posts_per_day,
        }


@app.post("/api/admin/target/{target_type}/{name}/toggle")
def toggle_target(target_type: str, name: str):
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE targets SET enabled = NOT enabled WHERE type = %s AND name = %s RETURNING enabled",
            (target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Target not found")
        return {"enabled": result[0], "status": "ok"}


@app.post("/api/admin/target/{target_type}/{name}/rescan")
def rescan_target(target_type: str, name: str):
    with get_db_cursor() as cur:
        if target_type == "user":
            cur.execute("SELECT id FROM posts WHERE LOWER(author) = LOWER(%s)", (name,))
        else:
            cur.execute(
                "SELECT id FROM posts WHERE LOWER(subreddit) = LOWER(%s)", (name,)
            )

        post_ids = [r[0] for r in cur.fetchall()]

    if not redis_client:
        return {
            "status": "partial",
            "message": "Redis not available, posts listed but not queued",
            "post_ids": post_ids,
        }

    rd = get_redis()
    requeued = 0
    for post_id in post_ids:
        rd.lpush("media_queue", json.dumps({"post_id": post_id, "url": None}))
        requeued += 1

    return {"status": "ok", "requeued": requeued}


@app.post("/api/admin/scrape")
def trigger_scrape():
    """Trigger immediate scrape of all enabled targets."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()
    rd.lpush(
        "scrape_trigger", json.dumps({"triggered_at": datetime.utcnow().isoformat()})
    )

    return {"status": "ok", "message": "Scrape triggered"}


@app.post("/api/admin/target/{target_type}")
def add_target(target_type: str, name: str):
    if target_type not in ("subreddit", "user"):
        raise HTTPException(status_code=400, detail="Invalid target type")

    with get_db_cursor() as cur:
        cur.execute(
            "INSERT INTO targets(type, name, enabled) VALUES(%s, %s, true) ON CONFLICT (name) DO NOTHING RETURNING id",
            (target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=409, detail="Target already exists")
        return {"status": "ok", "name": name, "type": target_type}


@app.delete("/api/admin/target/{target_type}/{name}")
def delete_target(target_type: str, name: str):
    with get_db_cursor() as cur:
        cur.execute(
            "DELETE FROM targets WHERE type = %s AND name = %s RETURNING id",
            (target_type, name),
        )
        result = cur.fetchone()
        if result is None:
            raise HTTPException(status_code=404, detail="Target not found")
        return {"status": "ok", "deleted": name}


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
        params = []

        if subreddit:
            query += " AND p.subreddit = %s"
            params.append(subreddit)
        if author:
            query += " AND p.author = %s"
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
        except:
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
        _thumb_jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()

    logger.info(f"Thumb job {job_id} finished: {done} processed, {len(errors)} errors")


@app.get("/api/admin/thumbnails/stats")
def thumb_stats():
    """Return thumbnail coverage statistics."""
    with get_db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM media WHERE file_path IS NOT NULL")
        total_with_file = cur.fetchone()[0]

        # Pull every row that has a local file so we can check reality on disk
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE file_path IS NOT NULL"
        )
        rows = cur.fetchall()

    missing_no_path = 0  # thumb_path NULL/empty in DB
    missing_file_gone = 0  # thumb_path set in DB but file absent on disk
    good_count = 0  # thumb_path set AND file present on disk

    for media_id, file_path, thumb_path in rows:
        if not thumb_path:
            missing_no_path += 1
        elif not os.path.exists(thumb_path):
            missing_file_gone += 1
        else:
            good_count += 1

    total_missing = missing_no_path + missing_file_gone

    # Count .thumb.jpg files on disk
    thumb_files_on_disk = 0
    thumb_bytes = 0
    try:
        for dirpath, _, filenames in os.walk(THUMB_PATH):
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
    """Generate thumbnails for all media rows that are missing one (or whose
    thumb file no longer exists on disk).  Runs in background; returns a job_id
    for progress polling."""
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT id, file_path, thumb_path FROM media WHERE file_path IS NOT NULL ORDER BY id"
        )
        all_rows = cur.fetchall()

    # Include only rows where the thumb is absent or stale
    rows = []
    files_missing = 0
    thums_missing = 0
    for row in all_rows:
        media_id, file_path, thumb_path = row
        # Check if source file exists
        if not file_path or not os.path.exists(file_path):
            files_missing += 1
            continue
        # Check if thumb is missing or stale
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
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        }

    t = threading.Thread(target=_run_thumb_job, args=(job_id, rows, False), daemon=True)
    t.start()

    return {"job_id": job_id, "total": len(rows)}


@app.post("/api/admin/thumbnails/rebuild-all")
def thumb_rebuild_all():
    """Force-regenerate thumbnails for every media row that has a local file,
    overwriting existing thumbnails.  Runs in background; returns a job_id."""
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
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        }

    t = threading.Thread(target=_run_thumb_job, args=(job_id, rows, True), daemon=True)
    t.start()

    return {"job_id": job_id, "total": len(rows)}


@app.post("/api/admin/thumbnails/purge-orphans")
def thumb_purge_orphans():
    """Delete .thumb.jpg files on disk that have no corresponding DB row.
    Returns counts and any errors."""
    # Collect all thumb paths stored in DB for O(1) lookup
    with get_db_cursor() as cur:
        cur.execute("SELECT thumb_path FROM media WHERE thumb_path IS NOT NULL")
        db_paths = {row[0] for row in cur.fetchall()}

    deleted = 0
    freed_bytes = 0
    errors = []

    try:
        for dirpath, _, filenames in os.walk(THUMB_PATH):
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
                # variants (nsfw, gif, etc)
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
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return unique_urls


@app.post("/api/admin/media/rescan")
def media_rescan():
    """Re-scan existing posts for additional media that wasn't queued.
    Compares extracted URLs against what's already in media table,
    queues new ones to Redis. Returns count of newly queued items."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    rd = get_redis()

    with get_db_cursor() as cur:
        # Get all posts with raw JSON
        cur.execute(
            "SELECT id, url, raw, subreddit, author, title FROM posts WHERE raw IS NOT NULL"
        )
        post_rows = cur.fetchall()

        # Get all URLs already queued or downloaded for any post
        cur.execute("SELECT url FROM media")
        existing_urls = {row[0] for row in cur.fetchall() if row[0]}

        # Also get URLs currently in the queue
        queued_urls = set()
        try:
            queue_items = rd.lrange("media_queue", 0, -1)
            for item in queue_items:
                try:
                    data = json.loads(item)
                    u = data.get("url")
                    if u:
                        queued_urls.add(u)
                except:
                    pass
        except:
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

            # Extract all media URLs from this post's raw JSON
            try:
                extracted = _extract_media_urls_from_raw(data, url)
            except Exception as e:
                errors.append(f"post {post_id}: extract error - {e}")
                continue

            for media_url in extracted:
                if media_url not in all_existing:
                    # Queue this new URL
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
                        all_existing.add(
                            media_url
                        )  # prevent duplicates within same batch
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


@app.get("/api/events")
async def event_stream():
    """Server-Sent Events endpoint for real-time UI updates."""

    async def db_stats():
        def _query():
            conn = None
            cur = None
            try:
                if not connection_pool:
                    return {
                        "total_posts": 0,
                        "total_comments": 0,
                        "downloaded_media": 0,
                        "pending_media": 0,
                        "total_media": 0,
                    }
                conn = connection_pool.getconn()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM posts")
                total_posts = cur.fetchone()[0]
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
                    GROUP BY t.type, t.name, t.enabled, t.last_created
                    ORDER BY t.type, t.name
                """)
                target_rows = cur.fetchall()
                targets = []
                for row in target_rows:
                    (
                        ttype,
                        name,
                        enabled,
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
                        "SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts ORDER BY ingested_at DESC LIMIT 1"
                    )
                else:
                    cur.execute(
                        "SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts WHERE ingested_at > %s ORDER BY ingested_at DESC LIMIT 20",
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
        # Seed watermarks from the current most-recent rows so we only emit
        # events for things that arrive *after* the SSE connection is opened.
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

                # Per-target stats (post counts, rates, ETAs, media progress)
                try:
                    stats["targets"] = await db_target_stats()
                except Exception as e:
                    logger.error(f"SSE target stats error: {e}")

                # Health status
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

                # Check for newly downloaded media
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

                # Serialize — if targets/health contain an unexpected non-serializable
                # value, strip those fields and still emit the core stats.
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


@app.post("/api/tag")
def add_tag(post_id: str, tag: str):
    with get_db_cursor() as cur:
        cur.execute(
            "INSERT INTO tags(name) VALUES(%s) ON CONFLICT(name) DO NOTHING", (tag,)
        )
        cur.execute("SELECT id FROM tags WHERE name = %s", (tag,))
        tag_id = cur.fetchone()

        if tag_id is None:
            tag_id = cur.execute(
                "INSERT INTO tags(name) VALUES(%s) RETURNING id", (tag,)
            )
            tag_id = cur.fetchone()

        cur.execute(
            "INSERT INTO post_tags(post_id, tag_id) VALUES(%s, %s) ON CONFLICT DO NOTHING",
            (post_id, tag_id[0]),
        )

    return {"status": "ok"}


@app.get("/api/post/{post_id}/tags")
def get_post_tags(post_id: str):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT t.name FROM tags t
            JOIN post_tags pt ON t.id = pt.tag_id
            WHERE pt.post_id = %s
        """,
            (post_id,),
        )
        return [r[0] for r in cur.fetchall()]


@app.delete("/api/post/{post_id}/tag/{tag}")
def remove_tag(post_id: str, tag: str):
    with get_db_cursor() as cur:
        cur.execute("SELECT id FROM tags WHERE name = %s", (tag,))
        tag_row = cur.fetchone()

        if tag_row:
            cur.execute(
                "DELETE FROM post_tags WHERE post_id = %s AND tag_id = %s",
                (post_id, tag_row[0]),
            )

    return {"status": "ok"}


@app.get("/api/tags")
def list_tags(limit: int = Query(50, ge=1, le=200)):
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT t.name, COUNT(pt.post_id) as cnt
            FROM tags t
            LEFT JOIN post_tags pt ON t.id = pt.tag_id
            GROUP BY t.id
            ORDER BY cnt DESC
            LIMIT %s
        """,
            (limit,),
        )
        return [{"name": r[0], "count": r[1]} for r in cur.fetchall()]


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
    """Wipe all archived data: files on disk, every DB table, and the Redis queue."""
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

    # 2. Delete tracked files from disk
    for file_path, thumb_path in tracked:
        for p in [file_path, thumb_path]:
            if p and os.path.exists(p):
                try:
                    deleted_bytes += os.path.getsize(p)
                    os.remove(p)
                    deleted_files += 1
                except Exception as e:
                    errors.append(f"Delete failed {p}: {e}")

    # 3. Remove organised subdirectories (r/ and u/) left empty after file deletion
    for base_dir in [ARCHIVE_PATH, THUMB_PATH]:
        for subdir in ["r", "u"]:
            top = os.path.join(base_dir, subdir)
            if os.path.isdir(top):
                for entry in os.scandir(top):
                    if entry.is_dir():
                        try:
                            os.rmdir(entry.path)  # only removes if empty
                        except OSError:
                            pass
                try:
                    os.rmdir(top)
                except OSError:
                    pass

    # 4. Truncate all tables (CASCADE handles FK constraints)
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "TRUNCATE media, post_tags, comments, posts, tags RESTART IDENTITY CASCADE"
            )
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
):
    with get_db_cursor() as cur:
        query = "SELECT id, title, subreddit, author, created_utc FROM posts WHERE 1=1"
        params = []

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
