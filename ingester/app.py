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


def run():
    while True:
        cur = db.cursor()
        cur.execute("SELECT type,name,last_created FROM targets WHERE enabled=true")
        for ttype, name, last in cur.fetchall():
            src = (
                reddit.subreddit(name).new(limit=100)
                if ttype == "subreddit"
                else reddit.redditor(name).new(limit=100)
            )
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

                rd.lpush("media_queue", json.dumps({"post_id": p.id, "url": p.url}))

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
