"""Drop unique constraint on media.sha256 to allow duplicate files.

Revision ID: 007_drop_sha256_unique_constraint
Revises: 006_add_created_at
Create Date: 2026-04-12 11:00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "007_drop_sha256_unique_constraint"
down_revision = "006_add_created_at"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE media DROP CONSTRAINT IF EXISTS media_sha256_key")


def downgrade():
    op.execute("ALTER TABLE media ADD CONSTRAINT media_sha256_key UNIQUE (sha256)")
