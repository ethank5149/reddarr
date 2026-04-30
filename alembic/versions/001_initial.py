"""Initial schema - v4.0/v5/v6/v7/v8/v9

Revision ID: 001_initial
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
            PRIMARY KEY (username)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS targets (
            id SERIAL PRIMARY KEY,
            type TEXT,
            name TEXT UNIQUE,
            enabled BOOLEAN DEFAULT true,
            status TEXT DEFAULT 'active',
            last_created TIMESTAMP,
            icon_url TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            subreddit TEXT,
            author TEXT,
            created_utc TIMESTAMP,
            title TEXT,
            selftext TEXT,
            url TEXT,
            media_url TEXT,
            raw JSONB,
            tsv TSVECTOR,
            ingested_at TIMESTAMP DEFAULT now(),
            hidden BOOLEAN DEFAULT false,
            hidden_at TIMESTAMP
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id TEXT PRIMARY KEY,
            post_id TEXT,
            author TEXT,
            body TEXT,
            created_utc TIMESTAMP,
            raw JSONB,
            tsv TSVECTOR
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id SERIAL PRIMARY KEY,
            post_id TEXT,
            url TEXT,
            file_path TEXT,
            thumb_path TEXT,
            sha256 TEXT,
            downloaded_at TIMESTAMP,
            status TEXT,
            retries INTEGER DEFAULT 0,
            UNIQUE(post_id, url)
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS idx_posts_tsv ON posts USING gin (tsv)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_tsv ON comments USING gin (tsv)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_media_post_id ON media (post_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments (post_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts (subreddit)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_posts_author ON posts (author)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_ingested_at ON posts (ingested_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_created_utc ON posts (created_utc)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_targets_enabled ON targets (enabled)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_subreddit_lower ON posts (LOWER(subreddit))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_author_lower ON posts (LOWER(author))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_posts_hidden ON posts (hidden)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_media_status ON media (status)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_subreddit_created ON posts (subreddit, created_utc DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_author_created ON posts (author, created_utc DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_hidden_created ON posts (hidden, created_utc DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS posts_history (
            id SERIAL PRIMARY KEY,
            post_id TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            subreddit TEXT,
            author TEXT,
            created_utc TIMESTAMP,
            title TEXT,
            selftext TEXT,
            url TEXT,
            media_url TEXT,
            raw JSONB,
            is_deleted BOOLEAN DEFAULT false NOT NULL,
            version_hash TEXT,
            captured_at TIMESTAMP DEFAULT now(),
            UNIQUE(post_id, version)
        )
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_history_post_id ON posts_history (post_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_history_version ON posts_history (post_id, version DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS comments_history (
            id SERIAL PRIMARY KEY,
            comment_id TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            post_id TEXT,
            author TEXT,
            body TEXT,
            created_utc TIMESTAMP,
            raw JSONB,
            is_deleted BOOLEAN DEFAULT false NOT NULL,
            version_hash TEXT,
            captured_at TIMESTAMP DEFAULT now(),
            UNIQUE(comment_id, version)
        )
        """
    )

    op.create_index(
        "idx_comments_history_comment_id", "comments_history", ["comment_id"]
    )
    op.create_index(
        "idx_comments_history_version",
        "comments_history",
        [sa.text("(comment_id, version DESC)")],
    )


def downgrade() -> None:
    op.drop_table("comments_history")
    op.drop_table("posts_history")
    op.drop_table("media")
    op.drop_table("comments")
    op.drop_table("posts")
    op.drop_table("targets")
    op.drop_table("users")
