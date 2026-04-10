Based on a review of the provided codebase, there are two distinct bugs causing these issues. 

### 1. Why new posts don't automatically show up in the app
The frontend relies on Server-Sent Events (SSE) from the `/api/events` endpoint in `web/app.py` to stream live updates. This stream fetches new posts by tracking the `ingested_at` timestamp.

However, when posts with media are initially scraped by `ingester/app.py`, their `ingested_at` timestamp is explicitly set to `NULL` (so the downloader can populate it once the media finishes downloading). 

In PostgreSQL, an `ORDER BY ... DESC` clause sorts `NULL` values **first**. When the SSE stream initializes, it queries for the latest post:
```python
# From web/app.py -> db_new_posts()
cur.execute("SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts WHERE hidden = FALSE ORDER BY ingested_at DESC LIMIT 1")
```
Because `NULL` sorts first, this query always returns a post with a `NULL` timestamp. The `after_dt` tracker becomes `None`, causing the stream loop to endlessly fetch the exact same `NULL` record over and over again rather than advancing to actual new posts.

**The Fix:** In `web/app.py`, modify the queries inside the `db_new_posts(after_dt)` function to include `NULLS LAST`:
```python
if after_dt is None:
    cur.execute(
        "SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts WHERE hidden = FALSE ORDER BY ingested_at DESC NULLS LAST LIMIT 1"
    )
else:
    cur.execute(
        "SELECT id, title, subreddit, author, created_utc, ingested_at FROM posts WHERE hidden = FALSE AND ingested_at > %s ORDER BY ingested_at DESC NULLS LAST LIMIT 20",
        (after_dt,),
    )
```

### 2. Why queuing new downloads seems to have no effect
When you trigger a rescrape, items are correctly pushed to the Redis `media_queue` and picked up by `downloader/app.py`. However, they are crashing silently during the database insertion step.

In the `web/app.py` v8 schema migrations, the database dropped the unique constraint on `sha256` and replaced it with a composite unique constraint on `(post_id, url)`. 

While the primary image and video downloading branches in `downloader/app.py` were updated to reflect this, the branches that handle **preview images** and **external links** were missed. They still attempt to use `ON CONFLICT (sha256)`:
```python
# From downloader/app.py (Preview & External Link branches)
INSERT INTO media(post_id,url,file_path,thumb_path,sha256,downloaded_at,status)
VALUES(%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (sha256) DO UPDATE SET ...
```
Because the `sha256` unique constraint no longer exists, this query throws a `psycopg2.errors.InvalidObjectDefinition` exception. 

The downloader's worker catches this exception, retries the download until it hits `MAX_RETRIES`, and then permanently marks the item as `failed` in the database. Because the function crashes before reaching the end of the block, the post's `ingested_at` timestamp is **never updated**, keeping the post perpetually hidden from the live UI feed.

**The Fix:**
In `downloader/app.py`, find the two instances of `ON CONFLICT (sha256)` (one in the preview image branch, one in the external link branch) and update them to match the new schema:
```python
ON CONFLICT (post_id, url) DO UPDATE SET 
  file_path = EXCLUDED.file_path,
  thumb_path = EXCLUDED.thumb_path,
  sha256 = EXCLUDED.sha256,
  downloaded_at = EXCLUDED.downloaded_at,
  status = EXCLUDED.status
```