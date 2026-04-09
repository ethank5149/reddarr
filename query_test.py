import psycopg2
import time
from urllib.parse import urlparse

# get db connection
import os
from dotenv import load_dotenv
load_dotenv('.env')

conn = psycopg2.connect(
    dbname=os.environ.get("POSTGRES_DB", "reddarr"),
    user=os.environ.get("POSTGRES_USER", "reddarr"),
    password=os.environ.get("POSTGRES_PASSWORD", "reddarr"),
    host="127.0.0.1",
    port=5432
)

old_query = """
SELECT
    t.type,
    t.name,
    t.enabled,
    t.status,
    t.icon_url,
    t.last_created,
    COUNT(DISTINCT p.id) AS post_count,
    COUNT(DISTINCT p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d,
    COUNT(DISTINCT m.id) AS total_media,
    COUNT(DISTINCT CASE WHEN m.status = 'done' THEN m.id END) AS downloaded_media,
    COUNT(DISTINCT CASE WHEN m.status = 'pending' THEN m.id END) AS pending_media
FROM targets t
LEFT JOIN posts p ON (t.type = 'subreddit' AND LOWER(p.subreddit) = LOWER(t.name))
                  OR (t.type = 'user'      AND LOWER(p.author)    = LOWER(t.name))
LEFT JOIN media m ON m.post_id = p.id
GROUP BY t.type, t.name, t.enabled, t.status, t.icon_url, t.last_created
ORDER BY t.type, t.name
"""

new_query = """
WITH sub_stats AS (
    SELECT 
        LOWER(p.subreddit) as name,
        COUNT(p.id) as post_count,
        COUNT(p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d
    FROM targets t
    JOIN posts p ON LOWER(p.subreddit) = LOWER(t.name)
    WHERE t.type = 'subreddit'
    GROUP BY LOWER(p.subreddit)
),
user_stats AS (
    SELECT 
        LOWER(p.author) as name,
        COUNT(p.id) as post_count,
        COUNT(p.id) FILTER (WHERE p.created_utc > now() - INTERVAL '7 days') AS posts_7d
    FROM targets t
    JOIN posts p ON LOWER(p.author) = LOWER(t.name)
    WHERE t.type = 'user'
    GROUP BY LOWER(p.author)
),
sub_media_stats AS (
    SELECT 
        LOWER(p.subreddit) as name,
        COUNT(m.id) as total_media,
        COUNT(m.id) FILTER (WHERE m.status = 'done') AS downloaded_media,
        COUNT(m.id) FILTER (WHERE m.status = 'pending') AS pending_media
    FROM targets t
    JOIN posts p ON LOWER(p.subreddit) = LOWER(t.name)
    JOIN media m ON m.post_id = p.id
    WHERE t.type = 'subreddit'
    GROUP BY LOWER(p.subreddit)
),
user_media_stats AS (
    SELECT 
        LOWER(p.author) as name,
        COUNT(m.id) as total_media,
        COUNT(m.id) FILTER (WHERE m.status = 'done') AS downloaded_media,
        COUNT(m.id) FILTER (WHERE m.status = 'pending') AS pending_media
    FROM targets t
    JOIN posts p ON LOWER(p.author) = LOWER(t.name)
    JOIN media m ON m.post_id = p.id
    WHERE t.type = 'user'
    GROUP BY LOWER(p.author)
)
SELECT
    t.type,
    t.name,
    t.enabled,
    t.status,
    t.icon_url,
    t.last_created,
    COALESCE(CASE WHEN t.type = 'subreddit' THEN ss.post_count ELSE us.post_count END, 0) AS post_count,
    COALESCE(CASE WHEN t.type = 'subreddit' THEN ss.posts_7d ELSE us.posts_7d END, 0) AS posts_7d,
    COALESCE(CASE WHEN t.type = 'subreddit' THEN sms.total_media ELSE ums.total_media END, 0) AS total_media,
    COALESCE(CASE WHEN t.type = 'subreddit' THEN sms.downloaded_media ELSE ums.downloaded_media END, 0) AS downloaded_media,
    COALESCE(CASE WHEN t.type = 'subreddit' THEN sms.pending_media ELSE ums.pending_media END, 0) AS pending_media
FROM targets t
LEFT JOIN sub_stats ss ON t.type = 'subreddit' AND LOWER(t.name) = ss.name
LEFT JOIN user_stats us ON t.type = 'user' AND LOWER(t.name) = us.name
LEFT JOIN sub_media_stats sms ON t.type = 'subreddit' AND LOWER(t.name) = sms.name
LEFT JOIN user_media_stats ums ON t.type = 'user' AND LOWER(t.name) = ums.name
ORDER BY t.type, t.name
"""

cur = conn.cursor()

print("Testing old query...")
t0 = time.time()
cur.execute(old_query)
r1 = cur.fetchall()
print(f"Old query took {time.time() - t0:.2f}s")

print("Testing new query...")
t0 = time.time()
cur.execute(new_query)
r2 = cur.fetchall()
print(f"New query took {time.time() - t0:.2f}s")

print(f"Results match? {r1 == r2}")
if r1 != r2:
    print(r1[:2])
    print(r2[:2])
