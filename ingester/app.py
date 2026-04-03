import os, time, json
import praw, psycopg2, redis
from datetime import datetime
import logging

import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

logger.info("Starting ingester...")

db = psycopg2.connect(os.getenv("DB_URL"))
rd = redis.Redis(host=os.getenv("REDIS_HOST"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 300))

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


def extract_media_urls(post):
    """Extract all media URLs from a post"""
    urls = []

    data = post.__dict__
    logger.debug(f"Extracting media from post {post.id}, keys: {list(data.keys())}")

    if hasattr(post, "url") and post.url:
        logger.debug(f"Post URL: {post.url}")
        urls.append(post.url)

    if "media_metadata" in data:
        logger.debug(f"Found media_metadata with {len(data['media_metadata'])} items")
        for img_id, img_data in data.get("media_metadata", {}).items():
            if "s" in img_data:
                urls.append(img_data["s"].get("u"))
            elif "p" in img_data:
                for p in img_data["p"]:
                    urls.append(p.get("u"))

    if "preview" in data:
        imgs = data.get("preview", {}).get("images", [])
        logger.debug(f"Found preview with {len(imgs)} images")
        for img in imgs:
            if "source" in img:
                urls.append(img["source"].get("url"))
            if "resolutions" in img:
                for res in img["resolutions"]:
                    urls.append(res.get("url"))

    if "crosspost_parent_list" in data:
        logger.debug(f"Found crosspost_parent_list")
        for cp in data.get("crosspost_parent_list", []):
            if "media_metadata" in cp:
                for img_id, img_data in cp.get("media_metadata", {}).items():
                    if "s" in img_data:
                        urls.append(img_data["s"].get("u"))

    seen = set()
    unique_urls = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            unique_urls.append(u)

    logger.debug(f"Extracted {len(unique_urls)} unique media URLs")
    return unique_urls


def run():
    while True:
        logger.info("Checking targets for new posts...")
        with db.cursor() as cur:
            cur.execute("SELECT type,name,last_created FROM targets WHERE enabled=true")
            targets = cur.fetchall()
            logger.info(f"Found {len(targets)} enabled targets")

            for ttype, name, last in targets:
                logger.info(f"Processing {ttype}: {name}")
                try:
                    if ttype == "subreddit":
                        src = reddit.subreddit(name).new(limit=100)
                    else:
                        src = reddit.redditor(name).submissions.new(limit=100)

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

                            media_urls = extract_media_urls(p)
                            urls_queued = 0
                            for url in media_urls:
                                if url:
                                    rd.lpush(
                                        "media_queue",
                                        json.dumps({"post_id": p.id, "url": url}),
                                    )
                                    urls_queued += 1
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
        logger.info(f"Sleeping for {POLL_INTERVAL} seconds")
        time.sleep(POLL_INTERVAL)


run()
