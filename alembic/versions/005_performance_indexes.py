"""Add performance indexes for version history and media tables.

Revision ID: 005_performance_indexes
Revises: 004_post_migrations_v10
Create Date: 2026-04-12

"""

from alembic import op

revision = "005_performance_indexes"
down_revision = "004_post_migrations_v10"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_history_latest
        ON posts_history(post_id, version DESC);
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_comments_history_latest
        ON comments_history(comment_id, version DESC);
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_subreddit_created
        ON posts(subreddit, created_utc DESC);
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_media_status_created
        ON media(status, downloaded_at DESC);
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_posts_history_latest")
    op.execute("DROP INDEX IF EXISTS idx_comments_history_latest")
    op.execute("DROP INDEX IF EXISTS idx_posts_subreddit_created")
    op.execute("DROP INDEX IF EXISTS idx_media_status_created")
