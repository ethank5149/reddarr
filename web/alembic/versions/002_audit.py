"""Audit and backup tracking tables

Revision ID: 002_audit
Revises: 001_initial
Create Date: 2024-01-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002_audit"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("record_id", sa.Text()),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB()),
        sa.Column("username", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(), server_default="now()"),
    )

    op.create_index("idx_audit_log_table_name", "audit_log", ["table_name"])
    op.create_index("idx_audit_log_record_id", "audit_log", ["record_id"])
    op.create_index("idx_audit_log_created_at", "audit_log", ["created_at"])

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="started"),
        sa.Column("tables", postgresql.JSONB()),
        sa.Column("subreddits", postgresql.JSONB()),
        sa.Column("rows_backed_up", sa.Integer(), server_default="0"),
        sa.Column("media_files", sa.Integer(), server_default="0"),
        sa.Column("media_bytes", sa.BigInteger(), server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.TIMESTAMP(), server_default="now()"),
        sa.Column("completed_at", sa.TIMESTAMP()),
    )

    op.create_index("idx_backup_runs_status", "backup_runs", ["status"])
    op.create_index("idx_backup_runs_started_at", "backup_runs", ["started_at"])

    op.create_table(
        "integrity_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("check_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="started"),
        sa.Column("total_items", sa.Integer(), server_default="0"),
        sa.Column("issues_found", sa.Integer(), server_default="0"),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("started_at", sa.TIMESTAMP(), server_default="now()"),
        sa.Column("completed_at", sa.TIMESTAMP()),
    )

    op.create_index("idx_integrity_checks_status", "integrity_checks", ["status"])
    op.create_index("idx_integrity_checks_type", "integrity_checks", ["check_type"])

    op.execute("""
        CREATE OR REPLACE FUNCTION update_audit_trigger()
        RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit_log (action, table_name, record_id, old_value, new_value)
            VALUES (
                TG_OP,
                TG_TABLE_NAME,
                COALESCE(NEW.id::text, OLD.id::text),
                CASE WHEN TG_OP = 'DELETE' THEN row_to_json(OLD)::jsonb ELSE NULL END,
                CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN row_to_json(NEW)::jsonb ELSE NULL END
            );
            RETURN COALESCE(NEW, OLD);
        END $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.drop_table("integrity_checks")
    op.drop_table("backup_runs")
    op.drop_table("audit_log")
