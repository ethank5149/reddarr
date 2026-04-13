import os, time, json, threading, hashlib, re
import praw, psycopg2, redis
import requests
from datetime import datetime, timezone
import logging
import signal
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

import sys

from targets import load_targets
from shared.media_utils import (
    extract_media_urls,
    fetch_youtube_video_url as _fetch_youtube_video_url,
)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Graceful shutdown handling
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

required_env_vars = ["DB_URL", "REDIS_HOST"]
missing = [v for v in required_env_vars if not os.getenv(v)]
if missing:
    logger.error(f"Missing required environment variables: {', '.join(missing)}")
    sys.exit(1)

posts_ingested = Counter(
    "reddit_posts_ingested_total", "Total posts ingested", ["subreddit"]
)
comments_ingested = Counter(
    "reddit_comments_ingested_total", "Total comments ingested", ["subreddit"]
)
media_queued = Counter("reddit_media_queued_total", "Total media items queued")
ingest_cycle_duration = Histogram(
    "reddit_ingest_cycle_duration_seconds", "Ingest cycle duration"
)
targets_enabled = Gauge("reddit_targets_enabled", "Number of enabled targets")
ingester_errors_total = Counter(
    "reddit_ingester_errors_total", "Total ingester errors", ["error_type"]
)
posts_skipped_total = Counter(
    "reddit_posts_skipped_total", "Posts already in DB (skipped)", ["subreddit"]
)

# Start Prometheus metrics HTTP server on port 8001
start_http_server(8001)
logger.info("Prometheus metrics server started on port 8001")

# Health check endpoint on port 8001
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
        elif self.path == "/metrics":
            self.send_response(404)  # Prometheus handles metrics
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress health check logs


health_server = HTTPServer(("0.0.0.0", 8003), HealthHandler)
import threading

threading.Thread(target=health_server.serve_forever, daemon=True).start()
logger.info("Health check server started on port 8003")

logger.info("Starting ingester...")

DB_URL = os.getenv("DB_URL")
rd = redis.Redis(host=os.getenv("REDIS_HOST"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 300))
SCRAPE_LIMIT = os.getenv("SCRAPE_LIMIT")
SCRAPE_LIMIT = int(SCRAPE_LIMIT) if SCRAPE_LIMIT else None
BACKFILL_MODE = os.getenv("BACKFILL_MODE", "false").lower() == "true"
BACKFILL_WORKERS = int(os.getenv("BACKFILL_WORKERS", "3"))
BACKFILL_PASSES = int(os.getenv("BACKFILL_PASSES", "2"))

logger.info(f"POLL_INTERVAL set to: {POLL_INTERVAL}")
logger.info(f"BACKFILL_MODE: {BACKFILL_MODE}")


def _read_secret(path):
    with open(path) as f:
        return f.read().strip()


def _create_reddit_client():
    """Create a new PRAW client instance.

    PRAW is not thread-safe, so each thread/worker needs its own instance.
    This function is used by both the main thread and backfill workers.
    """
    return praw.Reddit(
        client_id=_read_secret("/run/secrets/reddit_client_id"),
        client_secret=_read_secret("/run/secrets/reddit_client_secret"),
        user_agent=os.getenv("REDDIT_USER_AGENT"),
    )


# Global reddit instance for main thread only
# Backfill workers create their own instances via _create_reddit_client()
reddit = _create_reddit_client()

logger.info(f"Reddit client initialized (read only: {reddit.read_only})")


def get_db():
    """Return a live DB connection for the current thread.

    Uses a thread-local connection so that parallel backfill workers never
    share a single psycopg2 connection (which is not thread-safe).
    """
    if not hasattr(_tls, "conn") or _tls.conn is None:
        _tls.conn = psycopg2.connect(DB_URL)
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
        _tls.conn = psycopg2.connect(DB_URL)
        logger.info("DB reconnected in thread")
        return _tls.conn


# Thread-local storage for per-thread DB connections
_tls = threading.local()

# Initial connection for main thread (also seeds _tls.conn)
_tls.conn = psycopg2.connect(DB_URL)
# Keep _db as an alias for legacy code paths in the main thread
_db = _tls.conn

subreddits, users = load_targets()
logger.info(f"Target subreddits: {subreddits}")
logger.info(f"Target users: {users}")

db = get_db()
cur = db.cursor()
for s in subreddits:
    if s.strip():
        cur.execute(
            "INSERT INTO targets(type,name) VALUES('subreddit',%s) ON CONFLICT (name) DO UPDATE SET enabled = true",
            (s.strip(),),
        )
for u in users:
    if u.strip():
        cur.execute(
            "INSERT INTO targets(type,name) VALUES('user',%s) ON CONFLICT (name) DO UPDATE SET enabled = true",
            (u.strip(),),
        )
db.commit()
cur.close()

logger.info("Initial targets registered in database")


def fetch_comments(post):
    """Fetch top-level comments from a post (skip collapsed 'load more' trees)."""
    post.comments.replace_more(limit=0)
    comments = []

    def extract(comment):
        author = str(comment.author) if comment.author else None
        if author and author.lower() == "[deleted]":
            author = None
        comments.append(
            {
                "id": comment.id,
                "author": author,
                "body": comment.body,
                "created_utc": comment.created_utc,
                "parent_id": comment.parent_id,
            }
        )
        for reply in comment.replies:
            extract(reply)

    for comment in post.comments:
        extract(comment)
    return comments


_DIRECT_IMAGE_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".gifv",
    ".webm",
)
_DIRECT_MEDIA_HOSTS = (
    "i.redd.it",
    "v.redd.it",
    "youtube.com",
    "youtu.be",
    "i.imgur.com",
)


def _extract_redgifs_video_id(url_or_html: str) -> str | None:
    """Extract RedGifs video ID from iframe HTML or URL."""
    patterns = [
        r"redgifs\.com/ifr/([a-zA-Z0-9]+)",
        r"redgifs\.com/watch/([a-zA-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_html)
        if match:
            return match.group(1)
    return None


_redgifs_token: str | None = None
_redgifs_token_expiry: float = 0.0
_redgifs_token_lock = threading.Lock()


def _get_redgifs_token() -> str | None:
    """Obtain (and cache) a temporary RedGifs API bearer token."""
    global _redgifs_token, _redgifs_token_expiry
    with _redgifs_token_lock:
        if _redgifs_token and time.time() < _redgifs_token_expiry:
            return _redgifs_token
        try:
            resp = requests.get(
                "https://api.redgifs.com/v2/auth/temporary",
                timeout=10,
                headers={"User-Agent": "reddit-archive/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                _redgifs_token = data.get("token")
                # Tokens are valid for ~24 h; refresh after 20 h to be safe
                _redgifs_token_expiry = time.time() + 20 * 3600
                return _redgifs_token
            else:
                logger.warning(f"RedGifs auth returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to obtain RedGifs token: {e}")
        return _redgifs_token  # stale token is better than none


def _parse_redgifs_urls(data: dict) -> list[str]:
    """Extract HD/SD URLs from RedGifs API response."""
    urls = []
    if "gif" in data:
        gif = data["gif"]
        hd = gif.get("urls", {}).get("hd")
        sd = gif.get("urls", {}).get("sd")
        if hd:
            urls.append(hd)
        if sd:
            urls.append(sd)
    return urls


def _fetch_redgifs_video_urls(video_id: str) -> list[str]:
    """Fetch HD/SD video URLs from RedGifs API."""
    urls = []
    token = _get_redgifs_token()
    headers = {"User-Agent": "reddit-archive/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"https://api.redgifs.com/v2/gifs/{video_id}",
            timeout=10,
            headers=headers,
        )
        if resp.status_code == 200:
            urls = _parse_redgifs_urls(resp.json())
        elif resp.status_code == 401:
            # Token expired or invalid — force refresh and retry once
            global _redgifs_token_expiry
            _redgifs_token_expiry = 0.0
            token = _get_redgifs_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.get(
                    f"https://api.redgifs.com/v2/gifs/{video_id}",
                    timeout=10,
                    headers=headers,
                )
                if resp.status_code == 200:
                    urls = _parse_redgifs_urls(resp.json())
    except Exception as e:
        logger.warning(f"Failed to fetch RedGifs video URLs for {video_id}: {e}")
    return urls


from shared.media_utils import (
    extract_media_urls as _extract_media_urls,
    fetch_youtube_video_url as _fetch_youtube_video_url,
    extract_redgifs_video_id,
    fetch_redgifs_video_urls,
    is_direct_media_url as _is_direct_media_url,
    _DIRECT_IMAGE_EXTS,
    _DIRECT_MEDIA_HOSTS,
)


def extract_media_urls(post):
    """Extract all media URLs from a Reddit post.

    Uses the canonical implementation from shared.media_utils,
    then handles YouTube/RedGifs which require additional API calls.
    """
    urls = _extract_media_urls(post)

    data = post.__dict__
    post_url = getattr(post, "url", None)

    if post_url and (
        "youtube.com" in post_url.lower() or "youtu.be" in post_url.lower()
    ):
        yt_url = _fetch_youtube_video_url(post_url)
        if yt_url and yt_url not in urls:
            urls.append(yt_url)

    secure = data.get("secure_media")
    if secure and isinstance(secure, dict):
        secure_type = secure.get("type", "")
        if "oembed" in secure:
            oembed = secure["oembed"]
            if "redgifs" in secure_type.lower() or "redgifs" in str(oembed).lower():
                html = oembed.get("html", "")
                video_id = extract_redgifs_video_id(html)
                if video_id:
                    video_urls = _fetch_redgifs_video_urls(video_id)
                    for vu in video_urls:
                        if vu not in urls:
                            urls.append(vu)
            elif (
                "youtube" in secure_type.lower()
                or "youtube" in str(oembed.get("provider_name", "")).lower()
            ):
                yt_url = _fetch_youtube_video_url(post_url or "")
                if yt_url and yt_url not in urls:
                    urls.append(yt_url)

    return urls


def ingest_post(db, p):
    """Insert a single post + its comments, preserving all versions.

    This function now ALWAYS saves to history to track changes over time.
    If a user edits/deletes their post, we save it as a new version.
    """
    cur = db.cursor()
    created = datetime.fromtimestamp(p.created_utc, tz=timezone.utc).replace(
        tzinfo=None
    )

    subreddit = str(p.subreddit).lower()
    author = str(p.author).lower() if p.author else None

    if author and author == "[deleted]":
        author = None

    title = p.title
    selftext = p.selftext
    url = p.url

    is_deleted = (
        title in ("[deleted]", "[removed]")
        or selftext in ("[deleted]", "[removed]")
        or (title is None and selftext is None)
    )

    content_hash = hashlib.sha256(
        f"{title or ''}|{selftext or ''}|{url or ''}".encode()
    ).hexdigest()

    media_urls = extract_media_urls(p)

    # Set ingested_at to now() for all new posts.
    # The downloader may update this timestamp upon media download completion.
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        cur.execute(
            "SELECT version, version_hash FROM posts_history WHERE post_id=%s ORDER BY version DESC LIMIT 1",
            (p.id,),
        )
        row = cur.fetchone()
        current_version = row[0] if row else 0
        current_hash = row[1] if row else None

        if current_hash == content_hash:
            cur.close()
            posts_skipped_total.labels(subreddit=subreddit).inc()
            return False

        new_version = current_version + 1

        cur.execute(
            """INSERT INTO posts_history(post_id, version, subreddit, author, created_utc, title, selftext, url, media_url, raw, is_deleted, version_hash)
               VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                p.id,
                new_version,
                subreddit,
                author,
                created,
                title,
                selftext,
                url,
                url,
                json.dumps(p.__dict__, default=str),
                is_deleted,
                content_hash,
            ),
        )

        cur.execute(
            """INSERT INTO posts(id, subreddit, author, created_utc, title, selftext, url, media_url, raw, ingested_at)
               VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 title = EXCLUDED.title,
                 selftext = EXCLUDED.selftext,
                 url = EXCLUDED.url,
                 raw = EXCLUDED.raw,
                 ingested_at = EXCLUDED.ingested_at""",
            (
                p.id,
                subreddit,
                author,
                created,
                title,
                selftext,
                url,
                url,
                json.dumps(p.__dict__, default=str),
                ingested_at,
            ),
        )
        db.commit()

        if new_version > 1:
            logger.info(
                f"Post {p.id} updated to version {new_version} (was {current_version})"
            )
        else:
            logger.info(f"New post: {p.id} - {p.title[:50] if title else 'No title'}")

        posts_ingested.labels(subreddit=subreddit).inc()
        cur.close()

    except Exception as e:
        db.rollback()
        logger.error(f"Post ingest error for {p.id}: {e}", exc_info=True)
        cur.close()
        raise

    # media_urls already extracted at start of function for ingested_at logic
    urls_queued = 0
    if media_urls:
        cur = db.cursor()
        # Batch-check which URLs already exist for this post
        cur.execute(
            "SELECT url FROM media WHERE post_id = %s AND url = ANY(%s)",
            (p.id, media_urls),
        )
        existing_urls = {row[0] for row in cur.fetchall()}
        cur.close()

        for med_url in media_urls:
            if med_url and med_url not in existing_urls:
                rd.lpush(
                    "media_queue",
                    json.dumps(
                        {
                            "post_id": p.id,
                            "url": med_url,
                            "subreddit": subreddit,
                            "author": str(p.author),
                            "title": title,
                        }
                    ),
                )
                urls_queued += 1
                media_queued.inc()
    if urls_queued > 0:
        logger.info(f"Queued {urls_queued} media URLs for post {p.id}")

    try:
        comments = fetch_comments(p)
        if not comments:
            return True

        cur = db.cursor()
        comment_ids = [c["id"] for c in comments]

        # Batch-fetch latest version info for all comments at once
        cur.execute(
            """SELECT DISTINCT ON (comment_id) comment_id, version, version_hash
               FROM comments_history
               WHERE comment_id = ANY(%s)
               ORDER BY comment_id, version DESC""",
            (comment_ids,),
        )
        history_map = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

        for c in comments:
            c_author = c.get("author")
            if c_author and c_author.lower() == "[deleted]":
                c_author = None
            c_body = c.get("body")
            c_is_deleted = c_body in ("[deleted]", "[removed]") if c_body else False
            c_hash = (
                hashlib.sha256(f"{c_body or ''}".encode()).hexdigest()
                if c_body
                else None
            )

            prev = history_map.get(c["id"])
            c_version = (prev[0] + 1) if prev else 1
            c_current_hash = prev[1] if prev else None

            if c_current_hash != c_hash:
                cur.execute(
                    """INSERT INTO comments_history(comment_id, version, post_id, author, body, created_utc, raw, is_deleted, version_hash)
                       VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        c["id"],
                        c_version,
                        p.id,
                        c_author,
                        c_body,
                        datetime.fromtimestamp(
                            c["created_utc"], tz=timezone.utc
                        ).replace(tzinfo=None),
                        json.dumps(c, default=str),
                        c_is_deleted,
                        c_hash,
                    ),
                )

                cur.execute(
                    """INSERT INTO comments(id, post_id, author, body, created_utc, raw)
                       VALUES(%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         author = EXCLUDED.author,
                         body = EXCLUDED.body,
                         raw = EXCLUDED.raw""",
                    (
                        c["id"],
                        p.id,
                        c_author,
                        c_body,
                        datetime.fromtimestamp(
                            c["created_utc"], tz=timezone.utc
                        ).replace(tzinfo=None),
                        json.dumps(c, default=str),
                    ),
                )
                comments_ingested.labels(subreddit=subreddit).inc()

        db.commit()
        cur.close()
        logger.info(f"Archived {len(comments)} comments for {p.id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Comments error for {p.id}: {e}", exc_info=True)

    return True


def fetch_target_posts(ttype, name, sort_method="new", reddit_client=None):
    """Scrape a single target with the specified sort method.

    Args:
        ttype: Target type ('subreddit' or 'user')
        name: Target name
        sort_method: Sort method ('new', 'top_all', 'top_year', 'top_month')
        reddit_client: Optional PRAW client instance. If not provided, uses the
                      global instance (for main thread) or creates a new one.
    """
    # Use provided client, global (main thread), or create new (worker threads)
    rc = reddit_client
    if rc is None:
        rc = reddit

    db = get_db()
    new_posts_found = 0
    posts_processed = 0
    posts_failed = 0
    oldest_seen = None

    rate_limited = False
    failed_posts_list = []

    try:
        if ttype == "subreddit":
            sr = rc.subreddit(name)
            if sort_method == "top_all":
                src = sr.top(time_filter="all", limit=SCRAPE_LIMIT)
            elif sort_method == "top_year":
                src = sr.top(time_filter="year", limit=SCRAPE_LIMIT)
            elif sort_method == "top_month":
                src = sr.top(time_filter="month", limit=SCRAPE_LIMIT)
            else:
                src = sr.new(limit=SCRAPE_LIMIT)
        else:
            user = rc.redditor(name)
            if sort_method == "top_all":
                src = user.submissions.top(time_filter="all", limit=SCRAPE_LIMIT)
            elif sort_method == "top_year":
                src = user.submissions.top(time_filter="year", limit=SCRAPE_LIMIT)
            elif sort_method == "top_month":
                src = user.submissions.top(time_filter="month", limit=SCRAPE_LIMIT)
            else:
                src = user.submissions.new(limit=SCRAPE_LIMIT)

        for p in src:
            posts_processed += 1
            created = datetime.fromtimestamp(p.created_utc, tz=timezone.utc).replace(
                tzinfo=None
            )

            try:
                is_new = ingest_post(db, p)
                if is_new:
                    new_posts_found += 1
            except Exception as e:
                err_str = str(e).lower()
                if "rate" in err_str or "429" in err_str or "too many" in err_str:
                    rate_limited = True
                logger.error(f"Failed to ingest post {p.id}: {e}")
                ingester_errors_total.labels(error_type="ingest_post").inc()
                posts_failed += 1
                failed_posts_list.append({"post_id": p.id, "error": str(e)[:200]})

            if oldest_seen is None or created < oldest_seen:
                oldest_seen = created

        if posts_failed > 0:
            try:
                rd.lpush(
                    f"failed_posts:{ttype}:{name}",
                    json.dumps(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "sort_method": sort_method,
                            "failed": posts_failed,
                            "errors": failed_posts_list[-20:],
                        }
                    ),
                )
                rd.expire(f"failed_posts:{ttype}:{name}", 86400)
            except Exception:
                pass
            try:
                db = get_db()
                cur = db.cursor()
                for fp in failed_posts_list:
                    cur.execute(
                        "INSERT INTO scrape_failures(target_type, target_name, sort_method, post_id, error_message) VALUES(%s, %s, %s, %s, %s)",
                        (ttype, name, sort_method, fp["post_id"], fp["error"]),
                    )
                db.commit()
                cur.close()
            except Exception as e:
                logger.warning(f"Failed to log scrape failures to DB: {e}")

        logger.info(
            f"[{sort_method}] {ttype}:{name} - {posts_processed} fetched, {new_posts_found} new, {posts_failed} failed"
        )

    except Exception as e:
        err_str = str(e).lower()
        if "rate" in err_str or "429" in err_str or "too many" in err_str:
            rate_limited = True
            logger.warning(f"Rate limited on {ttype}:{name} ({sort_method}): {e}")
        else:
            logger.error(f"Error scraping {ttype}:{name} ({sort_method}): {e}")
        ingester_errors_total.labels(error_type="fetch_target_posts").inc()
        try:
            rd.lpush(
                f"failed_posts:{ttype}:{name}",
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "sort_method": sort_method,
                        "failed": "target_error",
                        "error": str(e)[:200],
                    }
                ),
            )
            rd.expire(f"failed_posts:{ttype}:{name}", 86400)
        except Exception:
            pass
        try:
            db = get_db()
            cur = db.cursor()
            cur.execute(
                "INSERT INTO scrape_failures(target_type, target_name, sort_method, error_message) VALUES(%s, %s, %s, %s)",
                (ttype, name, sort_method, str(e)[:200]),
            )
            db.commit()
            cur.close()
        except Exception as db_err:
            logger.warning(f"Failed to log target scrape failure to DB: {db_err}")

    return new_posts_found, posts_processed, rate_limited


def run_backfill_parallel(targets, passes=None, workers=None):
    """Run backfill on targets in parallel using multiple workers.

    Uses target sharding to ensure each target is processed by only one worker,
    preventing duplicate work and reducing rate limiting issues.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import hashlib

    passes = passes if passes else BACKFILL_PASSES
    workers = workers if workers else BACKFILL_WORKERS

    logger.info(f"Starting parallel backfill with {workers} workers, {passes} passes")

    backfill_errors = []
    backfill_stats = {"total": 0, "new": 0, "skipped": 0}
    rate_limited_count = 0

    def _shard_target(ttype, name, worker_id):
        key = f"{ttype}:{name}"
        shard = int(hashlib.md5(key.encode()).hexdigest(), 16) % workers
        return shard == worker_id

    targets_by_pass = {}
    for ttype, name, last in targets:
        for pass_num in range(passes):
            sort_method = "top_all" if pass_num == 0 else "new"
            targets_by_pass.setdefault(pass_num, []).append((ttype, name, sort_method))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        worker_queues = {i: [] for i in range(workers)}

        for pass_num, pass_targets in targets_by_pass.items():
            for ttype, name, sort_method in pass_targets:
                worker_id = (
                    int(hashlib.md5(f"{ttype}:{name}".encode()).hexdigest(), 16)
                    % workers
                )
                worker_queues[worker_id].append((ttype, name, sort_method))

        def _run_backfill_worker_batch(targets_batch):
            """Run backfill for a batch of targets assigned to a worker.

            Each worker creates its own PRAW client instance to avoid thread-safety issues.
            """
            # Create a separate PRAW client for this worker thread
            worker_reddit = _create_reddit_client()
            total_found = 0
            total_processed = 0
            rate_limited = False
            completed = set()
            for ttype, name, sort_method in targets_batch:
                try:
                    new_found, processed, was_limited = fetch_target_posts(
                        ttype, name, sort_method, reddit_client=worker_reddit
                    )
                    total_found += new_found
                    total_processed += processed
                    rate_limited = rate_limited or was_limited
                    completed.add((ttype, name))
                except Exception as e:
                    logger.error(f"Worker batch error for {ttype}:{name}: {e}")
            return total_found, total_processed, rate_limited, completed

        for worker_id, worker_targets in worker_queues.items():
            if worker_targets:
                future = executor.submit(_run_backfill_worker_batch, worker_targets)
                futures[future] = (f"worker_{worker_id}", worker_targets)

        completed_targets: set = set()
        for future in as_completed(futures):
            worker_targets = futures[future][1]
            try:
                new_found, processed, was_limited, completed = future.result()
                completed_targets.update(completed)
                backfill_stats["total"] += processed
                backfill_stats["new"] += new_found
                backfill_stats["skipped"] += processed - new_found

                if was_limited:
                    rate_limited_count += 1
            except Exception as e:
                err_str = str(e).lower()
                if "rate" in err_str or "429" in err_str:
                    rate_limited_count += 1
                err_msg = f"Worker batch: {e}"
                logger.error(f"Backfill failed for worker: {err_msg}")
                backfill_errors.append(err_msg)
                ingester_errors_total.labels(error_type="backfill").inc()

    if rate_limited_count > 0:
        logger.warning(
            f"Rate limited {rate_limited_count} times - consider reducing workers"
        )
        # Could auto-reduce workers for next run
        try:
            rd.set("backfill_last_rate_limit", f"{rate_limited_count}")
        except Exception:
            pass

    # Update last_created for all targets that completed at least one pass
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for ttype, name in completed_targets:
        try:
            db = get_db()
            cur = db.cursor()
            cur.execute(
                "UPDATE targets SET last_created=%s WHERE type=%s AND name=%s",
                (now, ttype, name),
            )
            db.commit()
            cur.close()
        except Exception as e:
            logger.error(
                f"Failed to update last_created after backfill for {name}: {e}"
            )

    # Store backfill results in Redis for UI polling
    try:
        result = {
            "status": "done" if not backfill_errors else "partial",
            "total": backfill_stats["total"],
            "new": backfill_stats["new"],
            "skipped": backfill_stats["skipped"],
            "errors": backfill_errors[-20:],  # keep last 20 errors
            "completed": len(completed_targets),
            "targets_total": len(targets) * passes,
            "rate_limited": rate_limited_count,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        rd.setex("backfill_status", 300, json.dumps(result))
        logger.info(f"Backfill result stored: {backfill_stats}")
    except Exception as e:
        logger.error(f"Failed to store backfill status: {e}")

    logger.info("Parallel backfill completed")


def run_cycle(
    force_backfill=False,
    backfill_passes=None,
    backfill_workers=None,
    only_target_type=None,
    only_target_name=None,
):
    """Run a single scrape cycle for all enabled targets (or a single target).

    Args:
        force_backfill: If True, run in backfill mode regardless of BACKFILL_MODE env
        backfill_passes: Number of passes for backfill (default: BACKFILL_PASSES)
        backfill_workers: Number of parallel workers for backfill (default: BACKFILL_WORKERS)
        only_target_type: If set, scrape only this target type ('subreddit'|'user')
        only_target_name: If set, scrape only this target name (used with only_target_type)
    """
    cycle_start = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("Checking targets for new posts...")
    targets_enabled.set(0)

    try:
        db = get_db()
        cur = db.cursor()
        if only_target_type and only_target_name:
            cur.execute(
                "SELECT type,name,last_created FROM targets WHERE enabled=true AND type=%s AND LOWER(name)=LOWER(%s)",
                (only_target_type, only_target_name),
            )
        else:
            cur.execute("SELECT type,name,last_created FROM targets WHERE enabled=true")
        targets = cur.fetchall()
        cur.close()
    except Exception as e:
        logger.error(f"Failed to fetch targets: {e}", exc_info=True)
        return

    logger.info(f"Found {len(targets)} enabled targets")
    targets_enabled.set(len(targets))

    if force_backfill or BACKFILL_MODE:
        logger.info("Running in BACKFILL mode - parallel scraping with multiple passes")
        # Override with provided values or use env defaults
        passes = backfill_passes if backfill_passes else BACKFILL_PASSES
        workers = backfill_workers if backfill_workers else BACKFILL_WORKERS
        run_backfill_parallel(targets, passes, workers)
    else:
        for ttype, name, last in targets:
            new_found, processed, _ = fetch_target_posts(ttype, name, "new")

            try:
                db = get_db()
                cur = db.cursor()
                cur.execute(
                    "UPDATE targets SET last_created=%s WHERE type=%s AND name=%s",
                    (datetime.now(timezone.utc).replace(tzinfo=None), ttype, name),
                )
                db.commit()
                cur.close()
            except Exception as e:
                logger.error(f"Failed to update last_created for {name}: {e}")

    cycle_duration = (
        datetime.now(timezone.utc).replace(tzinfo=None) - cycle_start
    ).total_seconds()
    ingest_cycle_duration.observe(cycle_duration)
    logger.info(f"Ingest cycle completed in {cycle_duration:.2f}s")


def run():
    while True:
        # Check for manual scrape trigger
        scrape_triggered = False
        backfill_triggered = False
        scrape_config = {}
        backfill_config = {}

        try:
            msg = rd.lpop("scrape_trigger")
            if msg:
                scrape_config = json.loads(msg) if msg else {}
                target_info = ""
                if scrape_config.get("target_name"):
                    target_info = f" for {scrape_config['target_type']}:{scrape_config['target_name']}"
                logger.info(
                    f"Manual scrape triggered via UI{target_info} — running cycle now"
                )
                scrape_triggered = True
        except Exception:
            pass

        try:
            msg = rd.lpop("backfill_trigger")
            if msg:
                logger.info("Manual backfill triggered via UI")
                backfill_config = json.loads(msg) if msg else {}
                backfill_triggered = True
        except Exception:
            pass

        # Run cycle with appropriate mode
        if backfill_triggered:
            logger.info(f"Running backfill with config: {backfill_config}")
            passes = backfill_config.get("passes", BACKFILL_PASSES)
            workers = backfill_config.get("workers", BACKFILL_WORKERS)
            only_type = backfill_config.get("target_type")
            only_name = backfill_config.get("target_name")
            run_cycle(
                force_backfill=True,
                backfill_passes=passes,
                backfill_workers=workers,
                only_target_type=only_type,
                only_target_name=only_name,
            )
        elif scrape_triggered:
            only_type = scrape_config.get("target_type")
            only_name = scrape_config.get("target_name")
            run_cycle(only_target_type=only_type, only_target_name=only_name)
        else:
            run_cycle()

        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        # Sleep in small increments so we can respond to scrape_trigger promptly
        if not scrape_triggered and not backfill_triggered:
            # Check for shutdown during sleep
            if _shutdown_requested:
                logger.info("Shutdown requested, exiting gracefully...")
                break
            elapsed = 0
            while elapsed < POLL_INTERVAL:
                time.sleep(min(5, POLL_INTERVAL - elapsed))
                elapsed += 5
                # Check for shutdown signal
                if _shutdown_requested:
                    logger.info("Shutdown requested, exiting gracefully...")
                    break
                # Check for triggers during sleep
                try:
                    msg = rd.lpop("scrape_trigger")
                    if msg:
                        logger.info(
                            "Manual scrape triggered via UI — running cycle now"
                        )
                        break
                    msg = rd.lpop("backfill_trigger")
                    if msg:
                        logger.info("Manual backfill triggered via UI")
                        break
                except Exception:
                    pass

    logger.info("Ingester shutdown complete")


run()
