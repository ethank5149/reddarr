"""Add excluded and archived columns to posts

Revision ID: 003_terminology
Revises: 002_audit
Create Date: 2026-04-11 00:00:00.000000

This migration replaces the confusing 'hidden' terminology with clear terms:
- 'excluded' = post is excluded/blacklisted from public view (was 'hidden')
- 'archived' = post is archived for long-term preservation

Also adds indexes for the new columns.
"""

from alembic import op
import sqlalchemy as sa


revision = "003_terminology"
down_revision = "002_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns if they don't exist
    op.execute("""
        ALTER TABLE posts 
        ADD COLUMN IF NOT EXISTS excluded BOOLEAN DEFAULT FALSE NOT NULL,
        ADD COLUMN IF NOT EXISTS excluded_at TIMESTAMP,
        ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE NOT NULL,
        ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP
    """)

    # Migrate data from legacy 'hidden' column if it exists
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'posts' AND column_name = 'hidden'
            ) THEN
                -- Migrate hidden to excluded
                UPDATE posts SET excluded = COALESCE(hidden, FALSE) 
                WHERE excluded IS NULL OR excluded = FALSE;
                
                -- Also set archived for already-hidden posts to preserve behavior
                UPDATE posts SET archived = hidden, archived_at = hidden_at
                WHERE archived IS NULL OR archived = FALSE;
            END IF;
        END $$
    """)

    # Create new indexes for the clear terminology
    op.create_index("idx_posts_excluded", "posts", ["excluded"])
    op.create_index("idx_posts_archived", "posts", ["archived"])
    op.create_index(
        "idx_posts_excluded_created", "posts", ["excluded", sa.text("created_utc DESC")]
    )
    op.create_index(
        "idx_posts_archived_created", "posts", ["archived", sa.text("created_utc DESC")]
    )


def downgrade() -> None:
    # Drop new indexes
    op.drop_index("idx_posts_archived_created", table_name="posts")
    op.drop_index("idx_posts_excluded_created", table_name="posts")
    op.drop_index("idx_posts_archived", table_name="posts")
    op.drop_index("idx_posts_excluded", table_name="posts")

    # Note: We can't fully remove the columns as data would be lost
    # But we can warn about this in documentation
    pass
