import os, json, hashlib, requests, subprocess, sys
import psycopg2, redis
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path
import logging
from prometheus_client import Counter, Gauge, Histogram, generate_latest

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

media_downloaded = Counter(
    "reddit_media_downloaded_total", "Total media downloaded", ["status"]
)
media_download_bytes = Counter(
    "reddit_media_download_bytes_total", "Total bytes downloaded"
)
download_duration = Histogram("reddit_download_duration_seconds", "Download duration")

logger.info("Starting downloader...")

db = psycopg2.connect(os.getenv("DB_URL"))
rd = redis.Redis(host=os.getenv("REDIS_HOST"))
MEDIA_DIR = os.getenv("ARCHIVE_PATH", "/data")
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)

logger.info(f"MEDIA_DIR set to: {MEDIA_DIR}")

session = requests.Session()
session.headers.update(
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(8192), b""):
            h.update(c)
    return h.hexdigest()


def make_thumb(path):
    logger.info(f"Creating thumbnail for: {path}")
    thumb = path + ".thumb.jpg"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vf", "scale=320:-1", "-frames:v", "1", thumb],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        logger.info(f"Thumbnail created: {thumb}")
    else:
        logger.warning(f"Thumbnail creation failed for {path}")
    return thumb


def get_best_image_url(url):
    """Follow redirects and get highest resolution image URL"""
    try:
        r = session.head(url, allow_redirects=True, timeout=10)
        final_url = r.url

        if "i.redd.it" in final_url:
            base = final_url.split(".")[0]
            high_res = f"{base}AUTO.format.jpg"
            logger.debug(f"Upgraded to high-res: {high_res}")
            return high_res
        return final_url
    except Exception as e:
        logger.warning(f"Redirect follow error: {e}")
        return url


while True:
    logger.info("Waiting for media in queue...")
    _, data = rd.brpop("media_queue")
    item = json.loads(data)
    post_id = item.get("post_id")
    url = item.get("url")

    logger.info(f"Dequeued: post_id={post_id}, url={url[:60]}...")

    if not url:
        logger.warning(f"Skipping {post_id} - no URL")
        continue

    try:
        path = None
        h = None
        thumb = None
        status = "done"

        if "i.redd.it" in url:
            url = get_best_image_url(url)
            logger.info(f"High-res URL: {url[:60]}...")

        if (
            any(url.endswith(x) for x in [".jpg", ".jpeg", ".png", ".webp", ".gif"])
            or "i.redd.it" in url
        ):
            logger.info(f"Downloading image: {url[:80]}...")
            r = session.get(url, stream=True, timeout=60)
            if r.status_code != 200:
                logger.warning(f"HTTP {r.status_code} for {url}")
                continue

            name = f"{post_id}_{url.split('/')[-1].split('?')[0][:100]}"
            path = f"{MEDIA_DIR}/{name}"

            bytes_written = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
                    bytes_written += len(chunk)
            logger.info(f"Downloaded {bytes_written} bytes to {path}")

            h = sha256(path)
            logger.debug(f"SHA256: {h}")

            with db.cursor() as cur:
                cur.execute("SELECT file_path FROM media WHERE sha256=%s", (h,))
                existing = cur.fetchone()

                if existing:
                    logger.info(f"File already exists in DB: {existing[0]}")
                    os.remove(path)
                    path = existing[0]
                else:
                    thumb = make_thumb(path)

                cur.execute(
                    """
                   INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (sha256) DO UPDATE SET post_id = EXCLUDED.post_id
                   """,
                    (
                        post_id,
                        url,
                        path,
                        thumb,
                        h,
                        datetime.utcnow(),
                        status,
                    ),
                )
                db.commit()
                logger.info(f"Saved to DB: post_id={post_id}, path={path}")

        elif "v.redd.it" in url or "youtube.com" in url or "youtu.be" in url:
            logger.info(f"Downloading video: {url}")
            result = subprocess.run(
                ["yt-dlp", "-o", f"{MEDIA_DIR}/%(id)s.%(ext)s", url, "--quiet"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode == 0:
                logger.info(f"Video downloaded: {url}")
            else:
                logger.error(f"Video download failed: {result.stderr.decode()}")
            path = f"{MEDIA_DIR}/{post_id}_video"

        elif url.startswith("https://preview.redd.it/") or url.startswith(
            "https://external-preview"
        ):
            url = get_best_image_url(url)
            logger.info(f"Following preview to: {url[:60]}...")
            r = session.get(url, stream=True, timeout=60)
            if r.status_code == 200:
                name = f"{post_id}_{url.split('/')[-1].split('?')[0][:100]}"
                path = f"{MEDIA_DIR}/{name}"
                with open(path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                h = sha256(path)
                thumb = make_thumb(path)

                with db.cursor() as cur:
                    cur.execute(
                        """
                       INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (sha256) DO UPDATE SET post_id = EXCLUDED.post_id
                       """,
                        (
                            post_id,
                            url,
                            path,
                            thumb,
                            h,
                            datetime.utcnow(),
                            status,
                        ),
                    )
                    db.commit()
                    logger.info(f"Saved preview: {path}")
            else:
                logger.warning(f"Preview HTTP {r.status_code}")
                continue

        else:
            logger.info(f"External link, attempting extraction: {url}")
            try:
                r = session.get(url, timeout=30)
                content_type = r.headers.get("content-type", "")
                if "image" in content_type:
                    ext = content_type.split("/")[-1].split(";")[0].strip()
                    path = f"{MEDIA_DIR}/{post_id}.{ext}"
                    with open(path, "wb") as f:
                        f.write(r.content)
                    h = sha256(path)
                    thumb = make_thumb(path)

                    with db.cursor() as cur:
                        cur.execute(
                            """
                           INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                           VALUES(%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (sha256) DO UPDATE SET post_id = EXCLUDED.post_id
                           """,
                            (
                                post_id,
                                url,
                                path,
                                thumb,
                                h,
                                datetime.utcnow(),
                                status,
                            ),
                        )
                        db.commit()
                        logger.info(f"Saved extracted image: {path}")
                else:
                    logger.info(f"Not an image, skipping: {content_type}")
                    continue
            except Exception as e:
                logger.warning(f"Extraction failed: {e}")
                continue

    except Exception as e:
        logger.error(f"ERROR processing {post_id}: {e}", exc_info=True)
        try:
            with db.cursor() as cur:
                cur.execute(
                    "INSERT INTO media(post_id,url,status,retries) VALUES(%s,%s,'failed',1) ON CONFLICT (post_id) DO NOTHING",
                    (post_id, url),
                )
                db.commit()
                logger.info(f"Marked as failed in DB: {post_id}")
        except Exception as db_err:
            logger.error(f"Failed to record error in DB: {db_err}")
