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

db = psycopg2.connect(os.getenv("DB_URL"))
rd = redis.Redis(host=os.getenv("REDIS_HOST"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 300))
SCRAPE_LIMIT = os.getenv("SCRAPE_LIMIT")  # None = no cap (PRAW paginates fully)
SCRAPE_LIMIT = int(SCRAPE_LIMIT) if SCRAPE_LIMIT else None

logger.info(f"POLL_INTERVAL set to: {POLL_INTERVAL}")

reddit = praw.Reddit(
    client_id=open("/run/secrets/reddit_client_id").read().strip(),
    client_secret=open("/run/secrets/reddit_client_secret").read().strip(),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)

logger.info(f"Reddit client initialized (read only: {reddit.read_only})")

subreddits = os.getenv("REDDIT_TARGET_SUBREDDITS", "").split(",")
users = os.getenv("REDDIT_TARGET_USERS", "").split(",")
logger.info(f"Target subreddits: {subreddits}")
logger.info(f"Target users: {users}")

with db.cursor() as cur:
    for s in subreddits:
        if s.strip():
            cur.execute(
                """
            INSERT INTO targets(type,name)
            VALUES('subreddit',%s)
            ON CONFLICT (name) DO NOTHING
            """,
                (s.strip(),),
            )

    for u in users:
        if u.strip():
            cur.execute(
                """
            INSERT INTO targets(type,name)
            VALUES('user',%s)
            ON CONFLICT (name) DO NOTHING
            """,
                (u.strip(),),
            )

    db.commit()

logger.info("Initial targets registered in database")


def fetch_comments(post):
    """Recursively fetch all comments from a post"""
    post.comments.replace_more(limit=None)
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
    """Return True if the URL points directly to a media file (not a page/gallery)."""
    lower = url.lower().split("?")[0]
    if any(lower.endswith(ext) for ext in _DIRECT_IMAGE_EXTS):
        return True
    return any(host in url for host in _DIRECT_MEDIA_HOSTS)


def extract_media_urls(post):
    """Extract the highest-resolution unique media URLs from a post.

    Selection rules (in priority order):
    1. Gallery posts (has ``media_metadata``):  use the per-image source ("s")
       from media_metadata.  ``post.url`` is the gallery landing page and is
       *not* a downloadable asset.  ``preview`` images are redundant with
       media_metadata and are skipped to avoid downloading the same content
       twice at lower quality.
    2. Single-image / video posts: use ``post.url`` when it is a direct media
       URL (i.redd.it, image extension, video host …).  ``preview`` images are
       *not* queued because ``preview.redd.it`` URLs are re-encoded (often
       WebP-compressed) copies of the same asset already available at full
       quality via ``post.url``.
    3. Fallback: if neither of the above yields any URL, fall back to the
       ``preview`` source as a last resort (e.g. external-link posts where
       Reddit generated a preview but the original URL is not a media file).
    """
    urls = []
    data = post.__dict__
    logger.debug(f"Extracting media from post {post.id}, keys: {list(data.keys())}")

    has_media_metadata = bool(data.get("media_metadata"))

    if has_media_metadata:
        # Gallery post — media_metadata is the authoritative source for all images.
        # preview.images would duplicate (at least) the first image; skip it.
        logger.debug(f"Found media_metadata with {len(data['media_metadata'])} items")
        for img_id, img_data in data["media_metadata"].items():
            if "s" in img_data:
                u = img_data["s"].get("u")
            elif img_data.get("p"):
                # No source available; fall back to the largest preview variant
                u = img_data["p"][-1].get("u")
            else:
                u = None
            if u:
                urls.append(u)
    else:
        # Not a gallery — post.url is the primary asset.
        post_url = getattr(post, "url", None)
        if post_url and _is_direct_media_url(post_url):
            logger.debug(f"Direct media URL: {post_url}")
            urls.append(post_url)

        # Only fall back to preview when post.url is not a downloadable asset
        # (e.g. external link posts where Reddit cached a preview image).
        if not urls and "preview" in data:
            imgs = data["preview"].get("images", [])
            logger.debug(f"Falling back to preview with {len(imgs)} image(s)")
            for img in imgs:
                u = img.get("source", {}).get("url")
                if u:
                    urls.append(u)
                    break  # One preview source per post is sufficient

    # Crosspost gallery metadata (always full-res, never duplicated by preview)
    if "crosspost_parent_list" in data:
        logger.debug("Found crosspost_parent_list")
        for cp in data.get("crosspost_parent_list", []):
            for img_id, img_data in cp.get("media_metadata", {}).items():
                if "s" in img_data:
                    u = img_data["s"].get("u")
                    if u:
                        urls.append(u)

    # Deduplicate while preserving order
    seen: set = set()
    unique_urls = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)

    logger.debug(f"Extracted {len(unique_urls)} unique media URLs")
    return unique_urls


def run():
    while True:
        cycle_start = datetime.utcnow()
        logger.info("Checking targets for new posts...")

        targets_enabled.set(0)

        with db.cursor() as cur:
            cur.execute("SELECT type,name,last_created FROM targets WHERE enabled=true")
            targets = cur.fetchall()
            logger.info(f"Found {len(targets)} enabled targets")
            targets_enabled.set(len(targets))

            for ttype, name, last in targets:
                logger.info(f"Processing {ttype}: {name}")
                try:
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

                        cur.execute("SELECT id FROM posts WHERE id=%s", (p.id,))
                        is_new = cur.fetchone() is None

                        if is_new:
                            new_posts_found += 1
                            cur.execute(
                                """INSERT INTO posts(id,subreddit,author,created_utc,title,selftext,url,media_url,raw)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (
                                    p.id,
                                    str(p.subreddit),
                                    str(p.author),
                                    created,
                                    p.title,
                                    p.selftext,
                                    p.url,
                                    p.url,
                                    json.dumps(p.__dict__, default=str),
                                ),
                            )
                            logger.info(f"New post: {p.id} - {p.title[:50]}")
                            posts_ingested.labels(subreddit=str(p.subreddit)).inc()

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
                                logger.info(
                                    f"Queued {urls_queued} media URLs for post {p.id}"
                                )

                            try:
                                comments = fetch_comments(p)
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
                                    comments_ingested.labels(
                                        subreddit=str(p.subreddit)
                                    ).inc()
                                logger.info(
                                    f"Archived {len(comments)} comments for {p.id}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"Comments error for {p.id}: {e}", exc_info=True
                                )

                        if oldest_seen is None or created < oldest_seen:
                            oldest_seen = created

                    logger.info(
                        f"Processed {posts_processed} posts for {name}, {new_posts_found} new"
                    )

                    if oldest_seen is not None:
                        if last is None or oldest_seen < last:
                            cur.execute(
                                "UPDATE targets SET last_created=%s WHERE name=%s",
                                (oldest_seen, name),
                            )
                            logger.info(
                                f"Updated last_created for {name} to {oldest_seen}"
                            )
                except Exception as e:
                    logger.error(f"Error processing {name}: {e}", exc_info=True)

        db.commit()

        cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
        ingest_cycle_duration.observe(cycle_duration)
        logger.info(f"Ingest cycle completed in {cycle_duration:.2f}s")

        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        time.sleep(POLL_INTERVAL)


run()
