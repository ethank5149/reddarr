import logging
import os
import json
import redis
from contextlib import contextmanager
from datetime import datetime, timedelta

import psycopg2
from psycopg2 import pool as pg_pool
from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
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

posts_total = Counter("reddit_posts_total", "Total posts ingested", ["subreddit"])
comments_total = Counter(
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
    conn = connection_pool.getconn()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error: {e}")
        raise
    finally:
        cur.close()
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


@app.get("/")
def root():
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    return {"detail": "Not Found"}


@app.get("/api/debug/{post_id}")
def debug_post(post_id: str):
    with get_db_cursor() as cur:
        cur.execute("SELECT id, raw FROM posts WHERE id = %s", (post_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return {"id": row[0], "raw": row[1]}


@app.get("/api/posts")
def posts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    subreddit: Optional[str] = None,
    author: Optional[str] = None,
):
    with get_db_cursor() as cur:
        query = """
            SELECT id, title, url, media_url, raw, subreddit, author, created_utc 
            FROM posts 
            WHERE 1=1
        """
        params = []

        if subreddit:
            query += " AND subreddit = %s"
            params.append(subreddit)
        if author:
            query += " AND author = %s"
            params.append(author)

        query += " ORDER BY created_utc DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        results = []

        for row in cur.fetchall():
            post_id, title, url, media_url, raw, subreddit, author, created_utc = row
            image_url = None
            selftext = None
            created_ts = None

            if raw:
                try:
                    data = raw if isinstance(raw, dict) else json.loads(raw)
                    selftext = data.get("selftext")
                    created_ts = data.get("created_utc")

                    if "media_metadata" in data:
                        for img_id, img_data in data.get("media_metadata", {}).items():
                            if "s" in img_data:
                                image_url = img_data["s"].get("u")
                                break

                    if not image_url and "crosspost_parent_list" in data:
                        for cp in data.get("crosspost_parent_list", []):
                            if "media_metadata" in cp:
                                for img_id, img_data in cp.get(
                                    "media_metadata", {}
                                ).items():
                                    if "s" in img_data:
                                        image_url = img_data["s"].get("u")
                                        break
                                if image_url:
                                    break

                    if not image_url and "preview" in data:
                        imgs = data.get("preview", {}).get("images", [])
                        if imgs:
                            image_url = imgs[0].get("source", {}).get("url") or imgs[
                                0
                            ].get("resolutions", [{}])[-1].get("url")
                except Exception as e:
                    logger.error(f"ERROR parsing raw for {post_id}: {e}")

            results.append(
                {
                    "id": post_id,
                    "title": title,
                    "image_url": image_url,
                    "selftext": selftext,
                    "subreddit": subreddit,
                    "author": author,
                    "created_utc": created_ts
                    or (created_utc.isoformat() if created_utc else None),
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

        image_url = None
        selftext = None

        if raw:
            try:
                data = raw if isinstance(raw, dict) else json.loads(raw)
                selftext = data.get("selftext")

                if "media_metadata" in data:
                    for img_id, img_data in data.get("media_metadata", {}).items():
                        if "s" in img_data:
                            image_url = img_data["s"].get("u")
                            break

                if not image_url and "preview" in data:
                    imgs = data.get("preview", {}).get("images", [])
                    if imgs:
                        image_url = imgs[0].get("source", {}).get("url")
            except Exception as e:
                logger.error(f"ERROR parsing raw for {post_id}: {e}")

        return {
            "id": post_id,
            "title": title,
            "url": url,
            "image_url": image_url,
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
            SELECT type, name, enabled, last_created,
                   EXTRACT(EPOCH FROM last_created) as last_ts,
                   EXTRACT(EPOCH FROM now() - last_created) as seconds_ago
            FROM targets
            ORDER BY type, name
        """)
        targets = []

        for row in cur.fetchall():
            ttype, name, enabled, last_created, last_ts, seconds_ago = row

            if ttype == "subreddit":
                cur.execute("SELECT COUNT(*) FROM posts WHERE subreddit = %s", (name,))
            else:
                cur.execute("SELECT COUNT(*) FROM posts WHERE author = %s", (name,))
            post_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*), 
                       COUNT(CASE WHEN m.status = 'done' THEN 1 END), 
                       COUNT(CASE WHEN m.status = 'pending' THEN 1 END)
                FROM media m
                JOIN posts p ON m.post_id = p.id
                WHERE p.subreddit = %s OR p.author = %s
            """,
                (name, name),
            )
            media_row = cur.fetchone()
            total_media = media_row[0] or 0
            downloaded_media = media_row[1] or 0
            pending_media = media_row[2] or 0

            rate = 0
            eta_seconds = None
            if last_created and seconds_ago and seconds_ago > 0:
                rate = post_count / seconds_ago if seconds_ago > 0 else 0
                remaining = max(0, 1000 - post_count)
                if rate > 0:
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
            cur.execute("SELECT id FROM posts WHERE author = %s", (name,))
        else:
            cur.execute("SELECT id FROM posts WHERE subreddit = %s", (name,))

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

    return {"status": "healthy" if not issues else "degraded", "issues": issues}


@app.get("/metrics")
def metrics():
    if not redis_client:
        return PlainTextResponse("Redis not available", status_code=503)

    rd = get_redis()
    queue_len = rd.llen("media_queue")
    queue_length.set(queue_len)

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

            cur.execute("SELECT subreddit, COUNT(*) FROM comments GROUP BY subreddit")
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
