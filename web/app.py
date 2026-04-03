from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import psycopg2, os, json
from datetime import datetime, timedelta
from collections import defaultdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
logger.info("API STARTED - checking version 3")

db = psycopg2.connect(os.getenv("DB_URL"))

if os.path.exists("dist"):
    app.mount("/static", StaticFiles(directory="dist/static"), name="static")

ARCHIVE_PATH = os.getenv("ARCHIVE_PATH", "/data")


@app.get("/media/{path:path}")
def media(path: str):
    full_path = os.path.join(ARCHIVE_PATH, path)
    if os.path.exists(full_path):
        return FileResponse(full_path)
    return {"detail": "Not Found"}


@app.get("/")
def root():
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    return {"detail": "Not Found"}


@app.get("/api/debug/{post_id}")
def debug_post(post_id: str):
    cur = db.cursor()
    cur.execute("SELECT id, raw FROM posts WHERE id = %s", (post_id,))
    row = cur.fetchone()
    if not row:
        return {"detail": "not found"}
    return {"id": row[0], "raw": row[1]}


@app.get("/api/posts")
def posts(limit: int = 50, offset: int = 0):
    cur = db.cursor()
    cur.execute(
        "SELECT id, title, url, media_url, raw FROM posts ORDER BY created_utc DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
    results = []
    for row in cur.fetchall():
        post_id, title, url, media_url, raw = row[0], row[1], row[2], row[3], row[4]
        image_url = None
        selftext = None
        subreddit = None
        author = None
        created_utc = None

        if raw:
            try:
                data = raw if isinstance(raw, dict) else json.loads(raw)
                image_url = None
                selftext = data.get("selftext")
                subreddit = data.get("subreddit")
                author = data.get("author")
                created_utc = data.get("created_utc")

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
                        image_url = imgs[0].get("source", {}).get("url") or imgs[0].get(
                            "resolutions", [{}]
                        )[-1].get("url")
            except Exception as e:
                logger.error(f"ERROR parsing raw for {post_id}: {e}")

        results.append([post_id, title, image_url, selftext, subreddit, author])
    return results


@app.get("/api/search")
def search(q: str, limit: int = 50):
    cur = db.cursor()
    cur.execute(
        """
 SELECT id,title FROM posts
 WHERE tsv @@ plainto_tsquery(%s)
 LIMIT %s
 """,
        (q, limit),
    )
    return cur.fetchall()


@app.get("/api/admin/stats")
def admin_stats():
    cur = db.cursor()

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
            cur.execute(
                """
                SELECT COUNT(*) FROM posts WHERE subreddit = %s
            """,
                (name,),
            )
        else:
            cur.execute(
                """
                SELECT COUNT(*) FROM posts WHERE author = %s
            """,
                (name,),
            )
        post_count = cur.fetchone()[0]

        cur.execute(
            """
            SELECT COUNT(*), COUNT(CASE WHEN status = 'done' THEN 1 END)
            FROM media m
            JOIN posts p ON m.post_id = p.id
            WHERE p.subreddit = %s OR p.author = %s
        """,
            (name, name),
        )
        media_row = cur.fetchone()
        total_media = media_row[0] or 0
        downloaded_media = media_row[1] or 0

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
                "rate_per_second": round(rate, 4),
                "eta_seconds": round(eta_seconds, 0) if eta_seconds else None,
                "progress_percent": min(100, round(post_count / 10, 1)),
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
        "posts_per_day": posts_per_day,
    }


@app.post("/api/admin/target/{target_type}/{name}/toggle")
def toggle_target(target_type: str, name: str):
    cur = db.cursor()
    cur.execute(
        "UPDATE targets SET enabled = NOT enabled WHERE type = %s AND name = %s RETURNING enabled",
        (target_type, name),
    )
    result = cur.fetchone()
    db.commit()
    if result is None:
        return {"error": "Target not found"}
    return {"enabled": result[0]}


@app.post("/api/admin/target/{target_type}/{name}/reset")
def reset_target(target_type: str, name: str):
    cur = db.cursor()
    cur.execute(
        "UPDATE targets SET last_created = NULL WHERE type = %s AND name = %s",
        (target_type, name),
    )
    db.commit()
    return {"status": "ok"}


@app.get("/api/admin/logs")
def admin_logs(limit: int = 50):
    cur = db.cursor()
    cur.execute(
        """
        SELECT p.id, p.subreddit, p.author, p.created_utc, p.title
        FROM posts p
        ORDER BY p.created_utc DESC
        LIMIT %s
    """,
        (limit,),
    )
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


@app.post("/api/tag")
def tag(post_id: str, tag: str):
    cur = db.cursor()

    cur.execute(
        "INSERT INTO tags(name) VALUES(%s) ON CONFLICT(name) DO NOTHING", (tag,)
    )
    cur.execute("SELECT id FROM tags WHERE name=%s", (tag,))
    tag_id = cur.fetchone()[0]

    cur.execute(
        """
 INSERT INTO post_tags(post_id,tag_id)
 VALUES(%s,%s) ON CONFLICT DO NOTHING
 """,
        (post_id, tag_id),
    )

    db.commit()
    return {"status": "ok"}
