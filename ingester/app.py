import os, time, json
import praw, psycopg2, redis
from datetime import datetime

import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

db = psycopg2.connect(os.getenv("DB_URL"))
rd = redis.Redis(host=os.getenv("REDIS_HOST"))
cur = db.cursor()

reddit = praw.Reddit(
    client_id=open("/run/secrets/reddit_client_id").read().strip(),
    client_secret=open("/run/secrets/reddit_client_secret").read().strip(),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)

subreddits = os.getenv("REDDIT_TARGET_SUBREDDITS", "").split(",")
users = os.getenv("REDDIT_TARGET_USERS", "").split(",")

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

    if hasattr(post, "url") and post.url:
        urls.append(post.url)

    if "media_metadata" in data:
        for img_id, img_data in data.get("media_metadata", {}).items():
            if "s" in img_data:
                urls.append(img_data["s"].get("u"))
            elif "p" in img_data:
                for p in img_data["p"]:
                    urls.append(p.get("u"))

    if "preview" in data:
        imgs = data.get("preview", {}).get("images", [])
        for img in imgs:
            if "source" in img:
                urls.append(img["source"].get("url"))
            if "resolutions" in img:
                for res in img["resolutions"]:
                    urls.append(res.get("url"))

    if "crosspost_parent_list" in data:
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

    return unique_urls


def run():
    while True:
        cur = db.cursor()
        cur.execute("SELECT type,name,last_created FROM targets WHERE enabled=true")
        for ttype, name, last in cur.fetchall():
            if ttype == "subreddit":
                src = reddit.subreddit(name).new(limit=None)
            else:
                src = reddit.redditor(name).submissions.new(limit=None)

            oldest_seen = None
            for p in src:
                created = datetime.utcfromtimestamp(p.created_utc)

                cur.execute(
                    """INSERT INTO posts(id,subreddit,author,created_utc,title,selftext,url,media_url,raw)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT (id) DO NOTHING""",
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

                media_urls = extract_media_urls(p)
                for url in media_urls:
                    if url:
                        rd.lpush(
                            "media_queue", json.dumps({"post_id": p.id, "url": url})
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
                    print(f"Archived {len(comments)} comments for {p.id}")
                except Exception as e:
                    print(f"Comments error for {p.id}: {e}")

                if oldest_seen is None or created < oldest_seen:
                    oldest_seen = created

            if oldest_seen is not None:
                if last is None or oldest_seen < last:
                    cur.execute(
                        "UPDATE targets SET last_created=%s WHERE name=%s",
                        (oldest_seen, name),
                    )

        db.commit()
        time.sleep(300)


run()
