#!/usr/bin/env python3
"""Re-queue GIF/animated media that was incorrectly scraped as static images.

Scans posts.raw JSONB for AnimatedImage media_metadata and gif preview
variants, extracts the correct URLs, and pushes any not-yet-downloaded
ones to the media_queue for the downloader to pick up.

Usage:
    # Dry run (default) — just print what would be queued
    docker compose exec ingester python /app/requeue_gifs.py

    # Actually queue items
    docker compose exec ingester python /app/requeue_gifs.py --execute

    # Or run standalone (needs REDIS_HOST, POSTGRES_* env vars):
    python requeue_gifs.py --execute
"""

import argparse
import json
import logging
import os
import sys

import psycopg2
import redis

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("POSTGRES_DB", "reddit")
DB_USER = os.getenv("POSTGRES_USER", "reddit")

def get_db_password():
    secret_path = os.getenv("POSTGRES_PASSWORD_FILE", "/run/secrets/postgres_password")
    if os.path.exists(secret_path):
        return open(secret_path).read().strip()
    return os.getenv("POSTGRES_PASSWORD", "reddit")


def extract_gif_urls_from_raw(raw: dict) -> list[str]:
    """Extract GIF/animated URLs that the old code missed."""
    urls = []

    # 1. media_metadata with "gif" or "mp4" in "s"
    for img_id, img_data in raw.get("media_metadata", {}).items():
        if "s" in img_data:
            s = img_data["s"]
            gif_url = s.get("gif")
            mp4_url = s.get("mp4")
            if gif_url:
                urls.append(gif_url)
            if mp4_url:
                urls.append(mp4_url)

    # 2. preview.images[].variants.gif / variants.mp4
    for img in raw.get("preview", {}).get("images", []):
        variants = img.get("variants", {})
        for var_type in ("gif", "mp4"):
            var_data = variants.get(var_type, {})
            if isinstance(var_data, dict):
                src_url = var_data.get("source", {}).get("url")
                if src_url:
                    urls.append(src_url)

    # 3. Crosspost sources
    for cp in raw.get("crosspost_parent_list", []):
        urls.extend(extract_gif_urls_from_raw(cp))

    # De-escape Reddit's HTML-encoded URLs
    return [u.replace("&amp;", "&") for u in urls if u]


def main():
    parser = argparse.ArgumentParser(description="Re-queue missed GIF media")
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually push to Redis. Without this flag, dry-run only.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max posts to process (0 = all)",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=get_db_password()
    )
    rd = redis.Redis(host=os.getenv("REDIS_HOST", "redis"))

    # Find posts whose raw JSON contains animated content indicators
    query = """
        SELECT p.id, p.subreddit, p.author, p.title, p.raw
        FROM posts p
        WHERE p.raw IS NOT NULL
          AND (
            -- media_metadata entries with gif/mp4 URLs in "s"
            (
              jsonb_typeof(p.raw->'media_metadata') = 'object'
              AND EXISTS (
                SELECT 1 FROM jsonb_each(p.raw->'media_metadata') AS mm(k,v)
                WHERE v->'s'->>'gif' IS NOT NULL
                   OR v->'s'->>'mp4' IS NOT NULL
              )
            )
            -- or preview variants containing a "gif" or "mp4" key
            OR p.raw->'preview'->'images'->0->'variants'->>'gif' IS NOT NULL
            OR p.raw->'preview'->'images'->0->'variants'->>'mp4' IS NOT NULL
          )
    """
    if args.limit:
        query += f" LIMIT {args.limit}"

    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    logger.info(f"Found {len(rows)} posts with animated content in raw JSON")

    # Get all URLs already in the media table
    cur.execute("SELECT DISTINCT url FROM media")
    existing_urls = {r[0] for r in cur.fetchall()}
    logger.info(f"Media table has {len(existing_urls)} existing URLs")

    total_queued = 0
    total_skipped = 0

    for post_id, subreddit, author, title, raw in rows:
        if not raw:
            continue

        gif_urls = extract_gif_urls_from_raw(raw)
        for url in gif_urls:
            if url in existing_urls:
                total_skipped += 1
                continue

            item = json.dumps({
                "post_id": post_id,
                "url": url,
                "subreddit": subreddit or "",
                "author": author or "",
                "title": title or "",
            })

            if args.execute:
                rd.lpush("media_queue", item)

            logger.info(
                f"{'QUEUED' if args.execute else 'WOULD QUEUE'}: "
                f"post={post_id} url={url[:80]}"
            )
            total_queued += 1

    mode = "Queued" if args.execute else "Would queue (dry run)"
    logger.info(f"{mode}: {total_queued} new GIF/animated URLs")
    logger.info(f"Skipped (already downloaded): {total_skipped}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()