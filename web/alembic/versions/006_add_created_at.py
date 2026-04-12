"""Add created_at column to posts for tracking post capture time.

Revision ID: 006_add_created_at
Revises: 005_performance_indexes
Create Date: 2026-04-12

"""
from alembic import op

revision = "006_add_created_at"
down_revision = "005_performance_indexes"
branch_labels = None
depends_on = None


def upgrade():
    # Add created_at column - this tracks when the post was first captured
    # (independent of when media was downloaded)
    op.execute("""
        ALTER TABLE posts ADD COLUMN IF NOT EXISTS created_at TIMESTAMP
        DEFAULT now() NOT NULL
    """)
    
    # Create index for SSE queries that detect new posts
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_created_at
        ON posts(created_at DESC)
    """)
    
    # Backfill created_at from existing posts using ingested_at or created_utc
    # This ensures backward compatibility
    op.execute("""
        UPDATE posts 
        SET created_at = COALESCE(ingested_at, created_utc)
        WHERE created_at IS NULL
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_posts_created_at")
    op.execute("ALTER TABLE posts DROP COLUMN IF EXISTS created_at")
