import os, time, json
import praw, psycopg2, redis
from datetime import datetime
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

logger.info(f"POLL_INTERVAL set to: {POLL_INTERVAL}")

reddit = praw.Reddit(
    client_id=open("/run/secrets/reddit_client_id").read().strip(),
    client_secret=open("/run/secrets/reddit_client_secret").read().strip(),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)

logger.info(f"Reddit client initialized (read only: {reddit.read_only})")


def get_db():
    """Return a live DB connection, reconnecting if necessary."""
    global _db
    try:
        _db.cursor().execute("SELECT 1")
        return _db
    except Exception:
        logger.warning("DB connection lost, reconnecting...")
        try:
            _db.close()
        except Exception:
            pass
        _db = psycopg2.connect(DB_URL)
        logger.info("DB reconnected")
        return _db


# Initial connection
_db = psycopg2.connect(DB_URL)

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

        created = datetime.utcfromtimestamp(p.created_utc)
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
                    datetime.utcfromtimestamp(c["created_utc"]),
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


def run_cycle():
    """Run a single scrape cycle for all enabled targets."""
    cycle_start = datetime.utcnow()
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

    for ttype, name, last in targets:
        logger.info(f"Processing {ttype}: {name}")
        try:
            db = get_db()

            if ttype == "subreddit":
                src = reddit.subreddit(name).new(limit=SCRAPE_LIMIT)
            else:
                src = reddit.redditor(name).submissions.new(limit=SCRAPE_LIMIT)

            oldest_seen = None
            posts_processed = 0
            new_posts_found = 0

            for p in src:
                posts_processed += 1
                created = datetime.utcfromtimestamp(p.created_utc)

                try:
                    db = get_db()
                    is_new = ingest_post(db, p)
                    if is_new:
                        new_posts_found += 1
                except Exception as e:
                    logger.error(f"Failed to ingest post {p.id}: {e}", exc_info=True)

                if oldest_seen is None or created < oldest_seen:
                    oldest_seen = created

            logger.info(
                f"Processed {posts_processed} posts for {name}, {new_posts_found} new"
            )

            # Always stamp last_created with now() so the admin panel
            # shows an accurate "Last scraped" time after every cycle.
            try:
                db = get_db()
                cur = db.cursor()
                cur.execute(
                    "UPDATE targets SET last_created=%s WHERE type=%s AND name=%s",
                    (datetime.utcnow(), ttype, name),
                )
                db.commit()
                cur.close()
                logger.info(f"Updated last_scraped for {ttype}:{name}")
            except Exception as e:
                logger.error(f"Failed to update last_created for {name}: {e}")

        except Exception as e:
            logger.error(f"Error processing {name}: {e}", exc_info=True)
            try:
                get_db().rollback()
            except Exception:
                pass

    cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
    ingest_cycle_duration.observe(cycle_duration)
    logger.info(f"Ingest cycle completed in {cycle_duration:.2f}s")


def run():
    while True:
        run_cycle()

        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        # Sleep in small increments so we can respond to scrape_trigger promptly
        elapsed = 0
        while elapsed < POLL_INTERVAL:
            time.sleep(min(5, POLL_INTERVAL - elapsed))
            elapsed += 5
            # Check for a manual scrape trigger from the web UI
            try:
                msg = rd.lpop("scrape_trigger")
                if msg:
                    logger.info("Manual scrape triggered via UI — running cycle now")
                    break
            except Exception:
                pass


run()
