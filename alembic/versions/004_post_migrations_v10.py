"""Add scrape_failures table and performance indexes

Revision ID: 004_post_migrations_v10
Revises: 003_terminology
Create Date: 2026-04-12 00:00:00.000000

This migration consolidates the inline migrations from web/app.py that were run at startup:
- scrape_failures table for tracking failed scrapes
- schema_version table
- Performance indexes
- Media constraint migration

Important: These migrations used to run inline on every app startup.
Now they run exactly once via Alembic.
"""

from alembic import op
import sqlalchemy as sa


revision = "004_post_migrations_v10"
down_revision = "003_terminology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # v10: scrape_failures table for tracking failed scrapes
    op.execute("""
        CREATE TABLE IF NOT EXISTS scrape_failures (
            id SERIAL PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_name TEXT NOT NULL,
            sort_method TEXT,
            post_id TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
    """)
    op.create_index(
        "idx_scrape_failures_target", "scrape_failures", ["target_type", "target_name"]
    )
    op.create_index("idx_scrape_failures_post_id", "scrape_failures", ["post_id"])
    op.create_index("idx_scrape_failures_created_at", "scrape_failures", ["created_at"])

    # v10b: schema_version table
    op.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT now()
        )
    """)
    op.execute(
        "INSERT INTO schema_version (version) VALUES ('v10') ON CONFLICT (version) DO NOTHING"
    )

    # Additional performance indexes (v9 patterns)
    op.create_index("idx_media_status", "media", ["status"])
    op.create_index(
        "idx_posts_subreddit_created",
        "posts",
        [sa.text("subreddit"), sa.text("created_utc DESC")],
    )
    op.create_index(
        "idx_posts_author_created",
        "posts",
        [sa.text("author"), sa.text("created_utc DESC")],
    )

    # Ensure posts has ingested_at for tracking when posts were downloaded
    op.execute(
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMP DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS excluded BOOLEAN DEFAULT FALSE NOT NULL"
    )
    op.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS excluded_at TIMESTAMP")
    op.create_index("idx_posts_excluded", "posts", ["excluded"])
    op.create_index("idx_posts_ingested_at", "posts", ["ingested_at"])

    # v8: media unique constraint migration
    # First remove duplicates
    op.execute("""
        DELETE FROM media a USING media b 
        WHERE a.id > b.id AND a.post_id = b.post_id AND a.url = b.url
    """)
    # Then add unique constraint (will fail if duplicates exist)
    try:
        op.execute(
            "ALTER TABLE media ADD CONSTRAINT media_post_id_url_key UNIQUE (post_id, url)"
        )
    except Exception:
        pass  # Constraint may already exist

    # Additional media indexes
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_post_id_url ON media(post_id, url)"
    )
    op.execute("DROP INDEX IF EXISTS idx_media_sha256")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_sha256_non_unique ON media(sha256)"
    )

    # Case-insensitive indexes
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_subreddit_lower ON posts(LOWER(subreddit))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_author_lower ON posts(LOWER(author))"
    )

    # targets table columns
    op.execute(
        "ALTER TABLE targets ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'"
    )
    op.execute("ALTER TABLE targets ADD COLUMN IF NOT EXISTS icon_url TEXT")

    # History tables (if not already created by 001_initial)
    op.execute("""
        CREATE TABLE IF NOT EXISTS posts_history (
            id SERIAL PRIMARY KEY,
            post_id TEXT NOT NULL,
            version INT NOT NULL DEFAULT 1,
            subreddit TEXT,
            author TEXT,
            created_utc TIMESTAMP,
            title TEXT,
            selftext TEXT,
            url TEXT,
            media_url TEXT,
            raw JSONB,
            is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
            version_hash TEXT,
            captured_at TIMESTAMP DEFAULT now(),
            UNIQUE(post_id, version)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_history_post_id ON posts_history(post_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_history_version ON posts_history(post_id, version DESC)"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS comments_history (
            id SERIAL PRIMARY KEY,
            comment_id TEXT NOT NULL,
            version INT NOT NULL DEFAULT 1,
            post_id TEXT,
            author TEXT,
            body TEXT,
            created_utc TIMESTAMP,
            raw JSONB,
            is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
            version_hash TEXT,
            captured_at TIMESTAMP DEFAULT now(),
            UNIQUE(comment_id, version)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_history_comment_id ON comments_history(comment_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_history_version ON comments_history(comment_id, version DESC)"
    )


def downgrade() -> None:
    op.drop_index("idx_comments_history_version", table_name="comments_history")
    op.drop_index("idx_comments_history_comment_id", table_name="comments_history")
    op.drop_table("comments_history")
    op.drop_index("idx_posts_history_version", table_name="posts_history")
    op.drop_index("idx_posts_history_post_id", table_name="posts_history")
    op.drop_table("posts_history")
    op.drop_column("targets", "icon_url")
    op.drop_column("targets", "status")
    op.drop_index("idx_posts_author_lower", table_name="posts")
    op.drop_index("idx_posts_subreddit_lower", table_name="posts")
    op.drop_index("idx_media_sha256_non_unique", table_name="media")
    op.drop_index("idx_media_post_id_url", table_name="media")
    try:
        op.execute("ALTER TABLE media DROP CONSTRAINT media_post_id_url_key")
    except Exception:
        pass
    op.drop_index("idx_posts_ingested_at", table_name="posts")
    op.drop_index("idx_posts_excluded", table_name="posts")
    op.drop_index("idx_posts_author_created", table_name="posts")
    op.drop_index("idx_posts_subreddit_created", table_name="posts")
    op.drop_index("idx_media_status", table_name="media")
    op.drop_table("schema_version")
    op.drop_index("idx_scrape_failures_created_at", table_name="scrape_failures")
    op.drop_index("idx_scrape_failures_post_id", table_name="scrape_failures")
    op.drop_index("idx_scrape_failures_target", table_name="scrape_failures")
    op.drop_table("scrape_failures")
