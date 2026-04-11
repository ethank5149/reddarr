import os, re, json, hashlib, requests, subprocess, sys, time
import psycopg2, redis
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from PIL import features
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
rate_limit_backoff = Counter(
    "reddit_downloader_rate_limit_backoff_total",
    "Total rate limit backoffs",
    ["domain"],
)

start_http_server(8002)
logger.info("Prometheus metrics server started on port 8002")

logger.info("Starting downloader...")

_DB_URL = os.getenv("DB_URL")

CONCURRENCY = int(os.getenv("DOWNLOAD_CONCURRENCY", "5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RATE_LIMIT_BASE = int(os.getenv("RATE_LIMIT_BASE", "2"))

rd = redis.Redis(host=os.getenv("REDIS_HOST"))
MEDIA_DIR = os.getenv("ARCHIVE_PATH", "/data")
THUMB_DIR = os.getenv("THUMB_PATH", os.path.join(MEDIA_DIR, ".thumbs"))
Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)
Path(THUMB_DIR).mkdir(parents=True, exist_ok=True)

logger.info(f"MEDIA_DIR set to: {MEDIA_DIR}")
logger.info(f"THUMB_DIR set to: {THUMB_DIR}")
logger.info(f"CONCURRENCY set to: {CONCURRENCY}")


class RateLimiter:
    def __init__(self, base_delay=2, max_delay=60):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.domain_locks = {}
        self.delays = {}
        self.counts = {}
        self.blocked_until = {}
        self._global_lock = threading.Lock()

    def _get_domain(self, url):
        try:
            return urlparse(url).netloc
        except Exception:
            return "unknown"

    def acquire(self, url):
        domain = self._get_domain(url)

        # Safely get or create the domain-specific lock
        with self._global_lock:
            if domain not in self.domain_locks:
                self.domain_locks[domain] = threading.Lock()
                self.delays[domain] = self.base_delay
                self.counts[domain] = 0
                self.blocked_until[domain] = 0

        # Acquire the domain-specific lock for rate limiting logic
        with self.domain_locks[domain]:
            if time.time() < self.blocked_until[domain]:
                wait_time = self.blocked_until[domain] - time.time()
                if wait_time > 0:
                    logger.warning(
                        f"Rate limited for {domain}, waiting {wait_time:.1f}s"
                    )
                    time.sleep(wait_time)
                    # After sleeping, check again if still blocked or if another thread acquired the lock
                    if time.time() < self.blocked_until[domain]:
                        return False, domain  # Still blocked
                else:  # Blocked time already passed while waiting for lock, proceed
                    pass

            self.counts[domain] += 1
            return True, domain

    def backoff(self, domain, is_retry=False):
        with self._global_lock:
            if domain not in self.domain_locks:
                self.domain_locks[domain] = (
                    threading.Lock()
                )  # Ensure lock exists for consistency
                self.delays[domain] = self.base_delay
                self.counts[domain] = 0
                self.blocked_until[domain] = 0

        with self.domain_locks[domain]:
            if is_retry:
                self.delays[domain] = min(self.delays[domain] * 2, self.max_delay)
            self.blocked_until[domain] = time.time() + self.delays[domain]
            rate_limit_backoff.labels(domain=domain).inc()
            logger.warning(
                f"Rate limit triggered for {domain}, backing off for {self.delays[domain]}s"
            )

    def release(self, domain, success=False):
        with self._global_lock:
            if domain not in self.domain_locks:
                self.domain_locks[domain] = (
                    threading.Lock()
                )  # Ensure lock exists for consistency
                self.delays[domain] = self.base_delay
                self.counts[domain] = 0
                self.blocked_until[domain] = 0

        with self.domain_locks[domain]:
            if success:
                self.delays[domain] = max(self.base_delay, self.delays[domain] // 2)


rate_limiter = RateLimiter(base_delay=RATE_LIMIT_BASE, max_delay=60)

_tls = threading.local()


def get_db():
    if not hasattr(_tls, "conn") or _tls.conn is None:
        _tls.conn = psycopg2.connect(_DB_URL)
        logger.debug("Thread-local DB connection created")
        return _tls.conn

    try:
        with _tls.conn.cursor() as cur:
            cur.execute("SELECT 1")
        return _tls.conn
    except Exception:
        logger.warning("DB connection lost in thread, reconnecting...")
        try:
            _tls.conn.close()
        except Exception:
            pass
        _tls.conn = psycopg2.connect(_DB_URL)
        logger.info("DB reconnected in thread")
        return _tls.conn


for _attempt in range(10):
    try:
        _tls.conn = psycopg2.connect(_DB_URL)
        logger.info("DB initial connection established")
        break
    except Exception as _e:
        logger.warning(f"DB connection attempt {_attempt + 1}/10 failed: {_e}")
        time.sleep(3)
else:
    logger.error("Could not connect to DB after 10 attempts, exiting")
    sys.exit(1)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(131072), b""):
            h.update(c)
    return h.hexdigest()


def detect_image_corruption(path):
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            img.load()
        return False
    except Exception as e:
        logger.warning(f"Image corruption detected for {path}: {e}")
        return True


def make_thumb(path):
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
        return thumb
    else:
        logger.warning(
            f"Thumbnail creation failed for {path}: {result.stderr.decode()[:200]}"
        )
        return None


def get_best_image_url(url, session):
    try:
        r = session.head(url, allow_redirects=True, timeout=10)
        return r.url
    except Exception as e:
        logger.warning(f"Redirect follow error: {e}")
        return url


def sanitize_name(s, max_len=60):
    s = re.sub(r"[^\w\s-]", "", str(s)).strip()
    s = re.sub(r"[\s_]+", "_", s)
    return s[:max_len].strip("_")


def make_filename(subreddit, author, title, post_id, url):
    if subreddit and subreddit not in ("", "None"):
        prefix = f"r_{sanitize_name(subreddit, 30)}"
    elif author and author not in ("", "None"):
        prefix = f"u_{sanitize_name(author, 30)}"
    else:
        prefix = post_id

    title_part = sanitize_name(title, 80) if title else ""
    ext = Path(url.split("?")[0]).suffix

    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]

    if title_part:
        name = f"{prefix}_{title_part}_{post_id}_{url_hash}{ext}"
    else:
        name = f"{prefix}_{post_id}_{url_hash}{ext}"

    stem = Path(name).stem[:195]
    return stem + ext


def get_post_dir(post_id, subreddit=None, author=None):
    def _resolve(subreddit, author):
        if subreddit and subreddit not in ("", "None"):
            return Path(MEDIA_DIR) / "r" / subreddit
        if author and author not in ("", "None"):
            return Path(MEDIA_DIR) / "u" / author
        return Path(MEDIA_DIR)

    if subreddit or author:
        d = _resolve(subreddit, author)
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    try:
        conn = get_db()
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


def process_item(item, session=None):
    post_id = item.get("post_id")
    url = item.get("url")
    q_subreddit = item.get("subreddit")
    q_author = item.get("author")
    q_title = item.get("title", "")

    if not url:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT url FROM posts WHERE id = %s", (post_id,))
            row = cur.fetchone()
        if row and row[0]:
            url = row[0]
            logger.info(f"Retrieved URL from DB for {post_id}: {url[:60]}...")
        else:
            logger.warning(f"Skipping {post_id} - no URL in queue or DB")
            return

    logger.info(f"Processing: post_id={post_id}, url={url[:60]}...")

    acquired, domain = rate_limiter.acquire(url)
    if not acquired:
        rd.lpush("media_queue", json.dumps(item))
        time.sleep(1)
        return

    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

    _t_start = time.monotonic()
    try:
        path = None
        h = None
        thumb = None
        status = "done"
        retries = 0
        is_corrupted = False

        while retries < MAX_RETRIES:
            if "imgur.com" in url and url.lower().split("?")[0].endswith(".gifv"):
                url = url.replace(".gifv", ".mp4").replace(".GIFV", ".mp4")

            if "i.redd.it" in url and not url.lower().split("?")[0].endswith(".gif"):
                url = get_best_image_url(url, session)
                logger.info(f"High-res URL: {url[:60]}...")

            # Strip query parameters for consistency if it's a reddit preview
            if "preview.redd.it" in url or "external-preview.redd.it" in url:
                if not url.lower().split("?")[0].endswith(".gif"):
                    url = get_best_image_url(url, session)
                    logger.info(f"Following preview to: {url[:60]}...")
                else:
                    url = url.split("?")[0]

            if (
                any(
                    url.lower().split("?")[0].endswith(x)
                    for x in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm"]
                )
                or "i.redd.it" in url
            ):
                logger.info(f"Downloading image: {url[:80]}...")
                r = session.get(url, stream=True, timeout=60)

                if r.status_code == 429:
                    rate_limiter.backoff(domain, is_retry=(retries > 0))
                    retries += 1
                    if retries < MAX_RETRIES:
                        time.sleep(rate_limiter.delays.get(domain, 2))
                        continue
                    else:
                        logger.warning(
                            f"Rate limited after {retries} retries for {url}"
                        )
                        break

                if r.status_code != 200:
                    logger.warning(f"HTTP {r.status_code} for {url}, recording failure")
                    try:
                        rd.lpush(
                            "failed_media_downloads",
                            json.dumps(
                                {
                                    "url": url,
                                    "post_id": post_id,
                                    "error": f"HTTP {r.status_code}",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to log failed download to Redis: {e}")
                    conn = get_db()
                    conn.rollback()
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO media(post_id,url,status,retries,error_message) VALUES(%s,%s,'failed',0,%s) "
                            "ON CONFLICT (post_id, url) DO UPDATE SET status='failed', retries=media.retries + 1, error_message=%s",
                            (
                                post_id,
                                url,
                                f"HTTP {r.status_code}",
                                f"HTTP {r.status_code}",
                            ),
                        )
                        conn.commit()
                    break

                post_dir = get_post_dir(post_id, q_subreddit, q_author)
                name = make_filename(q_subreddit, q_author, q_title, post_id, url)
                path = f"{post_dir}/{name}"

                bytes_written = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                            bytes_written += len(chunk)
                media_download_bytes.inc(bytes_written)
                logger.info(f"Downloaded {bytes_written} bytes to {path}")

                is_corrupted = detect_image_corruption(path)
                if is_corrupted:
                    logger.warning(f"Corrupt image detected for {post_id}, retrying...")
                    retries += 1
                    if retries < MAX_RETRIES:
                        time.sleep(1)
                        continue
                    logger.warning(
                        f"Giving up on corrupt image after {MAX_RETRIES} retries, keeping anyway"
                    )

                h = sha256(path)
                logger.debug(f"SHA256: {h}")

                if is_corrupted:
                    status = "corrupted"

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
                       ON CONFLICT (post_id, url) DO UPDATE SET 
                         file_path = EXCLUDED.file_path,
                         thumb_path = EXCLUDED.thumb_path,
                         sha256 = EXCLUDED.sha256,
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
                break

            elif (
                "v.redd.it" in url
                or "youtube.com" in url
                or "youtu.be" in url
                or "redgifs.com" in url
                or (".gif" in url.lower() and "redd.it" not in url)
            ):
                logger.info(f"Downloading video: {url}")
                post_dir = get_post_dir(post_id, q_subreddit, q_author)
                video_name = make_filename(q_subreddit, q_author, q_title, post_id, url)
                video_stem = Path(video_name).stem
                result = subprocess.run(
                    [
                        "yt-dlp",
                        "-o",
                        f"{post_dir}/{video_stem}.%(ext)s",
                        url,
                        "--quiet",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.returncode == 0:
                    logger.info(f"Video downloaded: {url}")
                else:
                    err_msg = result.stderr.decode()
                    logger.error(f"Video download failed for {url}: {err_msg}")
                    try:
                        rd.lpush(
                            "failed_video_downloads",
                            json.dumps(
                                {
                                    "url": url,
                                    "post_id": post_id,
                                    "error": err_msg[:500],
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to log failed video to Redis: {e}")

                matches = list(Path(post_dir).glob(f"{video_stem}.*"))
                matches = [
                    m
                    for m in matches
                    if m.suffix not in (".jpg", ".jpeg", ".png", ".webp")
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
                            ON CONFLICT (post_id, url) DO UPDATE SET 
                              file_path = EXCLUDED.file_path,
                              thumb_path = EXCLUDED.thumb_path,
                              sha256 = EXCLUDED.sha256,
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
                        logger.info(
                            f"Saved video to DB: post_id={post_id}, path={path}"
                        )
                else:
                    logger.warning(
                        f"Could not find downloaded video file for stem: {post_dir}/{video_stem}"
                    )
                    try:
                        rd.lpush(
                            "failed_video_downloads",
                            json.dumps(
                                {
                                    "url": url,
                                    "post_id": post_id,
                                    "error": "Video file not found after download",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to log failed video to Redis: {e}")
                    conn = get_db()
                    conn.rollback()
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO media(post_id,url,status,retries,error_message) VALUES(%s,%s,'failed',0,%s) "
                            "ON CONFLICT (post_id, url) DO UPDATE SET status='failed', retries=media.retries + 1, error_message=%s",
                            (
                                post_id,
                                url,
                                "Video file not found after download",
                                "Video file not found after download",
                            ),
                        )
                        conn.commit()
                break

            else:
                logger.info(f"External link, attempting extraction: {url}")
                try:
                    r = session.get(url, timeout=30)

                    if r.status_code == 429:
                        rate_limiter.backoff(domain, is_retry=(retries > 0))
                        retries += 1
                        if retries < MAX_RETRIES:
                            time.sleep(rate_limiter.delays.get(domain, 2))
                            continue

                    content_type = r.headers.get("content-type", "")
                    if "image" in content_type or "video" in content_type:
                        ext = "." + content_type.split("/")[-1].split(";")[0].strip()
                        post_dir = get_post_dir(post_id, q_subreddit, q_author)
                        name = (
                            make_filename(q_subreddit, q_author, q_title, post_id, url)
                            or f"{post_id}{ext}"
                        )
                        if not Path(name).suffix:
                            name = name + ext
                        path = f"{post_dir}/{name}"
                        with open(path, "wb") as f:
                            f.write(r.content)
                        media_download_bytes.inc(len(r.content))

                        is_corrupted = detect_image_corruption(path)
                        if is_corrupted:
                            logger.warning(
                                f"Corrupt extracted image for {post_id}, keeping anyway"
                            )
                            status = "corrupted"

                        h = sha256(path)
                        thumb = make_thumb(path)

                        conn = get_db()
                        conn.rollback()
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                               INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
                               VALUES(%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (post_id, url) DO UPDATE SET 
                                 file_path = EXCLUDED.file_path,
                                 thumb_path = EXCLUDED.thumb_path,
                                 sha256 = EXCLUDED.sha256,
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
                        logger.info(
                            f"Not an image, skipping: {content_type}, recording failure"
                        )
                        conn = get_db()
                        conn.rollback()
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO media(post_id,url,status,retries,error_message) VALUES(%s,%s,'failed',0,%s) "
                                "ON CONFLICT (post_id, url) DO UPDATE SET status='failed', retries=media.retries + 1, error_message=%s",
                                (
                                    post_id,
                                    url,
                                    f"Not an image: {content_type}",
                                    f"Not an image: {content_type}",
                                ),
                            )
                            conn.commit()
                    break
                except Exception as e:
                    logger.warning(f"Extraction failed: {e}, recording failure")
                    conn = get_db()
                    conn.rollback()
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO media(post_id,url,status,retries,error_message) VALUES(%s,%s,'failed',0,%s) "
                            "ON CONFLICT (post_id, url) DO UPDATE SET status='failed', retries=media.retries + 1, error_message=%s",
                            (post_id, url, str(e)[:500], str(e)[:500]),
                        )
                        conn.commit()
                    break

        rate_limiter.release(domain, success=(path is not None))

        _elapsed = time.monotonic() - _t_start
        download_duration.observe(_elapsed)
        if path:
            media_downloaded.labels(status="done").inc()
            try:
                conn = get_db()
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE posts SET ingested_at = %s WHERE id = %s",
                        (datetime.now(timezone.utc), post_id),
                    )
                    conn.commit()
            except Exception as db_err:
                logger.error(f"Failed to update ingested_at for {post_id}: {db_err}")

    except Exception as e:
        logger.error(f"ERROR processing {post_id}: {e}", exc_info=True)
        media_downloaded.labels(status="failed").inc()
        downloader_errors_total.labels(error_type="processing").inc()
        rate_limiter.release(domain, success=False)

        retries = item.get("_retries", 0)
        if retries < MAX_RETRIES:
            item["_retries"] = retries + 1
            rd.lpush("media_queue_retry", json.dumps(item))
            logger.info(f"Re-queued {post_id} for retry (attempt {retries + 1})")
        else:
            try:
                conn = get_db()
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO media(post_id,url,status,retries,error_message) VALUES(%s,%s,'failed',1,%s) "
                        "ON CONFLICT (post_id, url) DO UPDATE SET status='failed', retries=media.retries + 1, error_message=%s",
                        (post_id, url, str(e)[:500], str(e)[:500]),
                    )
                    conn.commit()
                    logger.info(f"Marked as failed in DB: {post_id}")
            except Exception as db_err:
                logger.error(f"Failed to record error in DB: {db_err}")


def worker(worker_id):
    processing_queue = f"media_processing_{worker_id}"
    # Reuse a single session per worker to benefit from connection pooling
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    while True:
        # Clean up any stuck item from previous crash
        stuck = rd.lrange(processing_queue, 0, -1)
        for item_data in stuck:
            logger.info(f"Recovering stuck item for worker {worker_id}")
            process_item(json.loads(item_data), session=session)
            rd.lrem(processing_queue, 0, item_data)

        logger.info(f"Worker {worker_id} waiting for media...")
        _dequeue_start = time.monotonic()

        # Use BLMOVE to atomically move item to processing queue
        # This prevents message loss if worker crashes
        data = rd.blmove(
            "media_queue",
            processing_queue,
            timeout=5,
            src="RIGHT",
            dest="LEFT",
        )
        if data is None:
            data = rd.blmove(
                "media_queue_retry",
                processing_queue,
                timeout=5,
                src="RIGHT",
                dest="LEFT",
            )

        if data is None:
            continue

        queue_wait_seconds.observe(time.monotonic() - _dequeue_start)
        try:
            item = json.loads(data)
            process_item(item, session=session)
            # Remove from processing queue once done
            rd.lrem(processing_queue, 0, data)
        except Exception as e:
            logger.error(f"Worker {worker_id} error: {e}")
            # If it failed, it might have been re-queued by process_item's retry logic
            # but we should still clean up the processing queue
            rd.lrem(processing_queue, 0, data)


def main():
    logger.info(f"Starting {CONCURRENCY} concurrent workers...")
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [executor.submit(worker, i) for i in range(CONCURRENCY)]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Worker failed: {e}")


if __name__ == "__main__":
    main()
