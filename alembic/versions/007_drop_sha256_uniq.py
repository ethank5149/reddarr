"""Drop sha256 unique constraint (replaced by composite unique on post_id, url).

Revision ID: 007_drop_sha256_uniq
Revises: 006_add_created_at
Create Date: 2026-04-12

"""

from alembic import op

revision = "007_drop_sha256"
down_revision = "006_add_created_at"
branch_labels = None
depends_on = None


def upgrade():
    # Check if unique constraint on sha256 exists and drop it
    # This was replaced with composite unique constraint on (post_id, url)
    op.execute("""
        DO $$
        BEGIN
            -- Try to drop the old unique constraint if it exists
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'media_sha256_key' 
                AND conrelid = 'media'::regclass
            ) THEN
                ALTER TABLE media DROP CONSTRAINT media_sha256_key;
            END IF;
        EXCEPTION
            WHEN undefined_object THEN
                -- Constraint doesn't exist, nothing to do
                NULL;
        END
        $$;
    """)

    # Ensure composite unique constraint exists on (post_id, url)
    # This was added in the initial schema (001_initial.py)
    # but we ensure it exists in case of migration ordering issues
    op.execute("""
        DO $$
        BEGIN
            -- Try to add the constraint if it doesn't exist
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'media_post_id_url_key'
                AND conrelid = 'media'::regclass
            ) THEN
                ALTER TABLE media ADD CONSTRAINT media_post_id_url_key
                UNIQUE (post_id, url);
            END IF;
        EXCEPTION
            WHEN duplicate_object THEN
                -- Constraint already exists, nothing to do
                NULL;
        END
        $$;
    """)


def downgrade():
    # This migration cannot be reversed as we can't restore
    # the sha256 unique constraint without knowing if it existed before
    pass
