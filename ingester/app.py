import os, time, json, threading
import praw, psycopg2, redis
from datetime import datetime, timezone
import logging
from prometheus_client import Counter, Gauge, Histogram, generate_latest

import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

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

reddit = praw.Reddit(
    client_id=open("/run/secrets/reddit_client_id").read().strip(),
    client_secret=open("/run/secrets/reddit_client_secret").read().strip(),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)

logger.info(f"Reddit client initialized (read only: {reddit.read_only})")


_db_lock = threading.Lock()


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
        _tls.conn.cursor().execute("SELECT 1")
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

subreddits = os.getenv("REDDIT_TARGET_SUBREDDITS", "").split(",")
users = os.getenv("REDDIT_TARGET_USERS", "").split(",")
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
        comments.append(
            {
                "id": comment.id,
                "author": str(comment.author),
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


_DIRECT_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_DIRECT_MEDIA_HOSTS = (
    "i.redd.it",
    "v.redd.it",
    "youtube.com",
    "youtu.be",
    "i.imgur.com",
)


def _is_direct_media_url(url: str) -> bool:
    lower = url.lower().split("?")[0]
    if any(lower.endswith(ext) for ext in _DIRECT_IMAGE_EXTS):
        return True
    return any(host in url for host in _DIRECT_MEDIA_HOSTS)


def extract_media_urls(post):
    urls = []
    data = post.__dict__

    has_media_metadata = bool(data.get("media_metadata"))

    if has_media_metadata:
        for img_id, img_data in data["media_metadata"].items():
            if "s" in img_data:
                u = img_data["s"].get("u")
            elif img_data.get("p"):
                u = img_data["p"][-1].get("u")
            else:
                u = None
            if u:
                urls.append(u)
    else:
        post_url = getattr(post, "url", None)
        if post_url and _is_direct_media_url(post_url):
            urls.append(post_url)

        if not urls and "preview" in data:
            imgs = data["preview"].get("images", [])
            for img in imgs:
                u = img.get("source", {}).get("url")
                if u:
                    urls.append(u)
                # Also get variants (nsfw, gif, etc)
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

    if "crosspost_parent_list" in data:
        for cp in data.get("crosspost_parent_list", []):
            for img_id, img_data in cp.get("media_metadata", {}).items():
                if "s" in img_data:
                    u = img_data["s"].get("u")
                    if u:
                        urls.append(u)

    seen: set = set()
    unique_urls = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return unique_urls


def ingest_post(db, p):
    """Insert a single post + its comments. Returns True if it was new."""
    cur = db.cursor()
    try:
        cur.execute("SELECT id FROM posts WHERE id=%s", (p.id,))
        if cur.fetchone() is not None:
            cur.close()
            return False  # already exists

        created = datetime.fromtimestamp(p.created_utc, tz=timezone.utc).replace(
            tzinfo=None
        )
        cur.execute(
            """INSERT INTO posts(id,subreddit,author,created_utc,title,selftext,url,media_url,raw)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                p.id,
                str(p.subreddit).lower(),
                str(p.author).lower(),
                created,
                p.title,
                p.selftext,
                p.url,
                p.url,
                json.dumps(p.__dict__, default=str),
            ),
        )
        db.commit()
        cur.close()
        logger.info(f"New post: {p.id} - {p.title[:50]}")
        posts_ingested.labels(subreddit=str(p.subreddit)).inc()
    except Exception:
        db.rollback()
        cur.close()
        raise

    # Queue media
    media_urls = extract_media_urls(p)
    urls_queued = 0
    for url in media_urls:
        if url:
            rd.lpush(
                "media_queue",
                json.dumps(
                    {
                        "post_id": p.id,
                        "url": url,
                        "subreddit": str(p.subreddit),
                        "author": str(p.author),
                        "title": p.title,
                    }
                ),
            )
            urls_queued += 1
            media_queued.inc()
    if urls_queued > 0:
        logger.info(f"Queued {urls_queued} media URLs for post {p.id}")

    # Insert comments
    try:
        comments = fetch_comments(p)
        cur = db.cursor()
        for c in comments:
            cur.execute(
                """INSERT INTO comments(id,post_id,author,body,created_utc,raw)
                   VALUES(%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    c["id"],
                    p.id,
                    c["author"],
                    c["body"],
                    datetime.fromtimestamp(c["created_utc"], tz=timezone.utc).replace(
                        tzinfo=None
                    ),
                    json.dumps(c, default=str),
                ),
            )
            comments_ingested.labels(subreddit=str(p.subreddit)).inc()
        db.commit()
        cur.close()
        logger.info(f"Archived {len(comments)} comments for {p.id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Comments error for {p.id}: {e}", exc_info=True)

    return True


def scrape_target(ttype, name, sort_method="new"):
    """Scrape a single target with the specified sort method."""
    db = get_db()
    new_posts_found = 0
    posts_processed = 0
    oldest_seen = None

    try:
        if ttype == "subreddit":
            sr = reddit.subreddit(name)
            if sort_method == "top_all":
                src = sr.top(time_filter="all", limit=SCRAPE_LIMIT)
            elif sort_method == "top_year":
                src = sr.top(time_filter="year", limit=SCRAPE_LIMIT)
            elif sort_method == "top_month":
                src = sr.top(time_filter="month", limit=SCRAPE_LIMIT)
            else:
                src = sr.new(limit=SCRAPE_LIMIT)
        else:
            user = reddit.redditor(name)
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
                db = get_db()
                is_new = ingest_post(db, p)
                if is_new:
                    new_posts_found += 1
            except Exception as e:
                logger.error(f"Failed to ingest post {p.id}: {e}")

            if oldest_seen is None or created < oldest_seen:
                oldest_seen = created

        logger.info(
            f"[{sort_method}] {ttype}:{name} - {posts_processed} processed, {new_posts_found} new"
        )

    except Exception as e:
        logger.error(f"Error scraping {ttype}:{name} ({sort_method}): {e}")

    return new_posts_found, posts_processed


def run_backfill_parallel(targets, passes=None, workers=None):
    """Run backfill on targets in parallel using multiple workers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    passes = passes if passes else BACKFILL_PASSES
    workers = workers if workers else BACKFILL_WORKERS

    logger.info(f"Starting parallel backfill with {workers} workers, {passes} passes")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for ttype, name, last in targets:
            for pass_num in range(passes):
                sort_method = "top_all" if pass_num == 0 else "new"
                future = executor.submit(scrape_target, ttype, name, sort_method)
                futures[future] = (ttype, name, sort_method)

        completed_targets: set = set()
        for future in as_completed(futures):
            ttype, name, sort_method = futures[future]
            try:
                new_found, processed = future.result()
                completed_targets.add((ttype, name))
            except Exception as e:
                logger.error(f"Backfill failed for {ttype}:{name} ({sort_method}): {e}")

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

    logger.info("Parallel backfill completed")


def run_cycle(force_backfill=False, backfill_passes=None, backfill_workers=None):
    """Run a single scrape cycle for all enabled targets.

    Args:
        force_backfill: If True, run in backfill mode regardless of BACKFILL_MODE env
        backfill_passes: Number of passes for backfill (default: BACKFILL_PASSES)
        backfill_workers: Number of parallel workers for backfill (default: BACKFILL_WORKERS)
    """
    cycle_start = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("Checking targets for new posts...")
    targets_enabled.set(0)

    try:
        db = get_db()
        cur = db.cursor()
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
            new_found, processed = scrape_target(ttype, name, "new")

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
        backfill_config = {}

        try:
            msg = rd.lpop("scrape_trigger")
            if msg:
                logger.info("Manual scrape triggered via UI — running cycle now")
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
            run_cycle(
                force_backfill=True, backfill_passes=passes, backfill_workers=workers
            )
        else:
            run_cycle()

        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        # Sleep in small increments so we can respond to scrape_trigger promptly
        if not scrape_triggered and not backfill_triggered:
            elapsed = 0
            while elapsed < POLL_INTERVAL:
                time.sleep(min(5, POLL_INTERVAL - elapsed))
                elapsed += 5
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


run()
