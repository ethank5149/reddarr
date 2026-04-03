from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
import psycopg2, os, json

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
