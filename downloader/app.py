import os, re, json, hashlib, requests, subprocess, sys, time
import psycopg2, redis
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path
import logging
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

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
downloader_errors_total = Counter(
    "reddit_downloader_errors_total", "Total downloader errors", ["error_type"]
)
queue_wait_seconds = Histogram(
    "reddit_queue_wait_seconds",
    "Time item spent in queue before processing",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600),
)

# Start Prometheus metrics HTTP server on port 8002
start_http_server(8002)
logger.info("Prometheus metrics server started on port 8002")

logger.info("Starting downloader...")

_DB_URL = os.getenv("DB_URL")
_db_conn = None


def get_db():
    """Return a live DB connection, reconnecting if the connection has been lost."""
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.cursor().execute("SELECT 1")
            return _db_conn
        except Exception:
            logger.warning("DB connection lost, reconnecting...")
            try:
                _db_conn.close()
            except Exception:
                pass
            _db_conn = None
    _db_conn = psycopg2.connect(_DB_URL)
    logger.info("DB connected")
    return _db_conn


# Initial connection with retry
for _attempt in range(10):
    try:
        _db_conn = psycopg2.connect(_DB_URL)
        logger.info("DB initial connection established")
        break
    except Exception as _e:
        logger.warning(f"DB connection attempt {_attempt + 1}/10 failed: {_e}")
        time.sleep(3)
else:
    logger.error("Could not connect to DB after 10 attempts, exiting")
    sys.exit(1)

rd = redis.Redis(host=os.getenv("REDIS_HOST"))
MEDIA_DIR = os.getenv("ARCHIVE_PATH", "/data")
THUMB_DIR = os.getenv("THUMB_PATH", os.path.join(MEDIA_DIR, ".thumbs"))
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
Path(THUMB_DIR).mkdir(parents=True, exist_ok=True)

logger.info(f"MEDIA_DIR set to: {MEDIA_DIR}")
logger.info(f"THUMB_DIR set to: {THUMB_DIR}")

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
    """Generate a thumbnail for *path* and save it under THUMB_DIR,
    mirroring the same r/{sub} / u/{author} subdirectory structure."""
    try:
        rel = os.path.relpath(path, MEDIA_DIR)
    except ValueError:
        rel = Path(path).name

    thumb_subdir = Path(THUMB_DIR) / Path(rel).parent
    thumb_subdir.mkdir(parents=True, exist_ok=True)
    thumb = str(thumb_subdir / (Path(path).stem + ".thumb.jpg"))

    logger.info(f"Creating thumbnail: {thumb}")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vf", "scale=320:-1", "-frames:v", "1", thumb],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        logger.info(f"Thumbnail created: {thumb}")
    else:
        logger.warning(
            f"Thumbnail creation failed for {path}: {result.stderr.decode()[:200]}"
        )
    return thumb


def get_best_image_url(url):
    """Follow redirects and get highest resolution image URL"""
    try:
        r = session.head(url, allow_redirects=True, timeout=10)
        return r.url
    except Exception as e:
        logger.warning(f"Redirect follow error: {e}")
        return url


def sanitize_name(s, max_len=60):
    """Strip filesystem-unsafe characters and collapse whitespace to underscores."""
    s = re.sub(r"[^\w\s-]", "", str(s)).strip()
    s = re.sub(r"[\s_]+", "_", s)
    return s[:max_len].strip("_")


def make_filename(subreddit, author, title, post_id, url):
    """Return a descriptive filename: {r_sub|u_author}_{title}_{post_id}{ext}

    The extension is taken from the source URL when present; the title is
    sanitized and truncated so the full path stays well under 255 chars.
    """
    if subreddit and subreddit not in ("", "None"):
        prefix = f"r_{sanitize_name(subreddit, 30)}"
    elif author and author not in ("", "None"):
        prefix = f"u_{sanitize_name(author, 30)}"
    else:
        prefix = post_id

    title_part = sanitize_name(title, 80) if title else ""
    ext = Path(url.split("?")[0]).suffix  # e.g. ".jpg", "" for extensionless

    if title_part:
        name = f"{prefix}_{title_part}_{post_id}{ext}"
    else:
        name = f"{prefix}_{post_id}{ext}"

    # Hard cap so the filename itself never exceeds 200 chars
    stem = Path(name).stem[:195]
    return stem + ext


def get_post_dir(post_id, subreddit=None, author=None):
    """Return the organised directory for a post: {MEDIA_DIR}/r/{subreddit} or u/{author}.

    Prefers subreddit/author supplied directly from the queue message to avoid a
    race condition where the downloader queries the DB before the ingester has
    committed the inserting transaction.  Falls back to a DB lookup only when
    those fields are absent.
    """

    def _resolve(subreddit, author):
        if subreddit and subreddit not in ("", "None"):
            return Path(MEDIA_DIR) / "r" / subreddit
        if author and author not in ("", "None"):
            return Path(MEDIA_DIR) / "u" / author
        return Path(MEDIA_DIR)

    # Fast path: metadata already in queue message
    if subreddit or author:
        d = _resolve(subreddit, author)
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    # Fallback: query the DB (post must already be committed at this point)
    try:
        conn = get_db()
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT subreddit, author FROM posts WHERE id = %s", (post_id,))
            row = cur.fetchone()
        if row:
            d = _resolve(row[0], row[1])
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
    except Exception as e:
        logger.warning(f"Could not resolve post dir for {post_id}: {e}")
    return MEDIA_DIR


while True:
    logger.info("Waiting for media in queue...")
    _dequeue_start = time.monotonic()
    _, data = rd.brpop("media_queue")
    queue_wait_seconds.observe(time.monotonic() - _dequeue_start)
    item = json.loads(data)
    post_id = item.get("post_id")
    url = item.get("url")
    q_subreddit = item.get("subreddit")
    q_author = item.get("author")
    q_title = item.get("title", "")

    if not url:
        conn = get_db()
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT url FROM posts WHERE id = %s", (post_id,))
            row = cur.fetchone()
        if row and row[0]:
            url = row[0]
            logger.info(f"Retrieved URL from DB for {post_id}: {url[:60]}...")
        else:
            logger.warning(f"Skipping {post_id} - no URL in queue or DB")
            continue

    logger.info(f"Dequeued: post_id={post_id}, url={url[:60]}...")

    _t_start = time.monotonic()
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
                get_db().rollback()
                continue

            post_dir = get_post_dir(post_id, q_subreddit, q_author)
            name = make_filename(q_subreddit, q_author, q_title, post_id, url)
            path = f"{post_dir}/{name}"

            bytes_written = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
                    bytes_written += len(chunk)
            media_download_bytes.inc(bytes_written)
            logger.info(f"Downloaded {bytes_written} bytes to {path}")

            h = sha256(path)
            logger.debug(f"SHA256: {h}")

            conn = get_db()
            conn.rollback()
            with conn.cursor() as cur:
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
                   ON CONFLICT (sha256) DO UPDATE SET 
                     post_id = EXCLUDED.post_id,
                     downloaded_at = EXCLUDED.downloaded_at,
                     status = EXCLUDED.status
                   """,
                    (
                        post_id,
                        url,
                        path,
                        thumb,
                        h,
                        datetime.now(timezone.utc),
                        status,
                    ),
                )
                conn.commit()
                logger.info(f"Saved to DB: post_id={post_id}, path={path}")

        elif "v.redd.it" in url or "youtube.com" in url or "youtu.be" in url:
            logger.info(f"Downloading video: {url}")
            post_dir = get_post_dir(post_id, q_subreddit, q_author)
            video_name = make_filename(q_subreddit, q_author, q_title, post_id, url)
            video_stem = Path(video_name).stem  # yt-dlp appends the real extension
            result = subprocess.run(
                ["yt-dlp", "-o", f"{post_dir}/{video_stem}.%(ext)s", url, "--quiet"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode == 0:
                logger.info(f"Video downloaded: {url}")
            else:
                logger.error(f"Video download failed: {result.stderr.decode()}")

            # Find the actual file yt-dlp wrote (it appends the real extension)
            matches = list(Path(post_dir).glob(f"{video_stem}.*"))
            # Exclude thumbnail files that yt-dlp may have written
            matches = [
                m for m in matches if m.suffix not in (".jpg", ".jpeg", ".png", ".webp")
            ]
            if matches:
                path = str(matches[0])
                h = sha256(path)
                thumb = make_thumb(path)

                conn = get_db()
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("SELECT file_path FROM media WHERE sha256=%s", (h,))
                    existing = cur.fetchone()
                    if existing:
                        logger.info(f"Video already exists in DB: {existing[0]}")
                        if path != existing[0]:
                            os.remove(path)
                        path = existing[0]

                    cur.execute(
                        """
                        INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (sha256) DO UPDATE SET 
                          post_id = EXCLUDED.post_id, 
                          thumb_path = EXCLUDED.thumb_path,
                          downloaded_at = EXCLUDED.downloaded_at,
                          status = EXCLUDED.status
                        """,
                        (
                            post_id,
                            url,
                            path,
                            thumb,
                            h,
                            datetime.now(timezone.utc),
                            status,
                        ),
                    )
                    conn.commit()
                    logger.info(f"Saved video to DB: post_id={post_id}, path={path}")
            else:
                logger.warning(
                    f"Could not find downloaded video file for stem: {post_dir}/{video_stem}"
                )

        elif url.startswith("https://preview.redd.it/") or url.startswith(
            "https://external-preview"
        ):
            url = get_best_image_url(url)
            logger.info(f"Following preview to: {url[:60]}...")
            r = session.get(url, stream=True, timeout=60)
            if r.status_code == 200:
                post_dir = get_post_dir(post_id, q_subreddit, q_author)
                name = make_filename(q_subreddit, q_author, q_title, post_id, url)
                path = f"{post_dir}/{name}"
                bytes_written = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                        bytes_written += len(chunk)
                media_download_bytes.inc(bytes_written)
                h = sha256(path)
                thumb = make_thumb(path)

                conn = get_db()
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                       INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (sha256) DO UPDATE SET 
                         post_id = EXCLUDED.post_id,
                         downloaded_at = EXCLUDED.downloaded_at,
                         status = EXCLUDED.status
                       """,
                        (
                            post_id,
                            url,
                            path,
                            thumb,
                            h,
                            datetime.now(timezone.utc),
                            status,
                        ),
                    )
                    conn.commit()
                    logger.info(f"Saved preview: {path}")
            else:
                logger.warning(f"Preview HTTP {r.status_code}")
                get_db().rollback()
                continue

        else:
            logger.info(f"External link, attempting extraction: {url}")
            try:
                r = session.get(url, timeout=30)
                content_type = r.headers.get("content-type", "")
                if "image" in content_type:
                    ext = "." + content_type.split("/")[-1].split(";")[0].strip()
                    post_dir = get_post_dir(post_id, q_subreddit, q_author)
                    name = (
                        make_filename(q_subreddit, q_author, q_title, post_id, url)
                        or f"{post_id}{ext}"
                    )
                    # Ensure the content-type extension is used when URL has none
                    if not Path(name).suffix:
                        name = name + ext
                    path = f"{post_dir}/{name}"
                    with open(path, "wb") as f:
                        f.write(r.content)
                    media_download_bytes.inc(len(r.content))
                    h = sha256(path)
                    thumb = make_thumb(path)

                    conn = get_db()
                    conn.rollback()
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                           INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                           VALUES(%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (sha256) DO UPDATE SET 
                             post_id = EXCLUDED.post_id,
                             downloaded_at = EXCLUDED.downloaded_at,
                             status = EXCLUDED.status
                           """,
                            (
                                post_id,
                                url,
                                path,
                                thumb,
                                h,
                                datetime.now(timezone.utc),
                                status,
                            ),
                        )
                        conn.commit()
                        logger.info(f"Saved extracted image: {path}")
                else:
                    logger.info(f"Not an image, skipping: {content_type}")
                    continue
            except Exception as e:
                logger.warning(f"Extraction failed: {e}")
                get_db().rollback()
                continue

        # Record download duration and increment counter for successful downloads
        _elapsed = time.monotonic() - _t_start
        download_duration.observe(_elapsed)
        if path:
            media_downloaded.labels(status="done").inc()

    except Exception as e:
        logger.error(f"ERROR processing {post_id}: {e}", exc_info=True)
        media_downloaded.labels(status="failed").inc()
        downloader_errors_total.labels(error_type="processing").inc()
        try:
            conn = get_db()
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO media(post_id,url,status,retries) VALUES(%s,%s,'failed',1) "
                    "ON CONFLICT DO NOTHING",
                    (post_id, url),
                )
                conn.commit()
                logger.info(f"Marked as failed in DB: {post_id}")
        except Exception as db_err:
            logger.error(f"Failed to record error in DB: {db_err}")
