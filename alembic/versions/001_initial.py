"""Initial schema - v4.0/v5/v6/v7/v8/v9

Revision ID: 001_initial
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')

    op.create_table('users',
        sa.Column('username', sa.Text(), primary_key=True),
        sa.Column('created_at', sa TIMESTAMP(), server_default='now()')
    )

    op.create_table('targets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('type', sa.Text()),
        sa.Column('name', sa.Text(), unique=True),
        sa.Column('enabled', sa.Boolean(), server_default='true'),
        sa.Column('status', sa.Text(), server_default='active'),
        sa.Column('last_created', sa TIMESTAMP()),
        sa.Column('icon_url', sa.Text())
    )

    op.create_table('posts',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('subreddit', sa.Text()),
        sa.Column('author', sa.Text()),
        sa.Column('created_utc', sa TIMESTAMP()),
        sa.Column('title', sa.Text()),
        sa.Column('selftext', sa.Text()),
        sa.Column('url', sa.Text()),
        sa.Column('media_url', sa.Text()),
        sa.Column('raw', postgresql.JSONB()),
        sa.Column('tsv', tsvector()),
        sa.Column('ingested_at', sa TIMESTAMP(), server_default='now()'),
        sa.Column('hidden', sa.Boolean(), server_default='false'),
        sa.Column('hidden_at', sa TIMESTAMP())
    )

    op.create_table('comments',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('post_id', sa.Text()),
        sa.Column('author', sa.Text()),
        sa.Column('body', sa.Text()),
        sa.Column('created_utc', sa TIMESTAMP()),
        sa.Column('raw', postgresql.JSONB()),
        sa.Column('tsv', tsvector())
    )

    op.create_table('media',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('post_id', sa.Text()),
        sa.Column('url', sa.Text()),
        sa.Column('file_path', sa.Text()),
        sa.Column('thumb_path', sa.Text()),
        sa.Column('sha256', sa.Text()),
        sa.Column('downloaded_at', sa TIMESTAMP()),
        sa.Column('status', sa.Text()),
        sa.Column('retries', sa.Integer(), server_default='0'),
        sa.UniqueConstraint('post_id', 'url', name='media_post_id_url_key')
    )

    op.create_index('idx_posts_tsv', 'posts', ['tsv'], postgresql_using='gin')
    op.create_index('idx_comments_tsv', 'comments', ['tsv'], postgresql_using='gin')
    op.create_index('idx_media_post_id', 'media', ['post_id'])
    op.create_index('idx_comments_post_id', 'comments', ['post_id'])
    op.create_index('idx_posts_subreddit', 'posts', ['subreddit'])
    op.create_index('idx_posts_author', 'posts', ['author'])
    op.create_index('idx_posts_ingested_at', 'posts', ['ingested_at'])
    op.create_index('idx_posts_created_utc', 'posts', ['created_utc'])
    op.create_index('idx_targets_enabled', 'targets', ['enabled'])
    op.create_index('idx_posts_subreddit_lower', 'posts', [sa.text('LOWER(subreddit)')])
    op.create_index('idx_posts_author_lower', 'posts', [sa.text('LOWER(author)')])
    op.create_index('idx_posts_hidden', 'posts', ['hidden'])
    op.create_index('idx_media_status', 'media', ['status'])
    op.create_index('idx_posts_subreddit_created', 'posts', ['subreddit', sa.text('created_utc DESC')])
    op.create_index('idx_posts_author_created', 'posts', ['author', sa.text('created_utc DESC')])
    op.create_index('idx_posts_hidden_created', 'posts', ['hidden', sa.text('created_utc DESC')])

    op.create_table('posts_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('post_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1'),
        sa.Column('subreddit', sa.Text()),
        sa.Column('author', sa.Text()),
        sa.Column('created_utc', sa TIMESTAMP()),
        sa.Column('title', sa.Text()),
        sa.Column('selftext', sa.Text()),
        sa.Column('url', sa.Text()),
        sa.Column('media_url', sa.Text()),
        sa.Column('raw', postgresql.JSONB()),
        sa.Column('is_deleted', sa.Boolean(), server_default='false'),
        sa.Column('version_hash', sa.Text()),
        sa.Column('captured_at', sa TIMESTAMP(), server_default='now()'),
        sa.UniqueConstraint('post_id', 'version', name='posts_history_post_id_version_key')
    )

    op.create_index('idx_posts_history_post_id', 'posts_history', ['post_id'])
    op.create_index('idx_posts_history_version', 'posts_history', [sa.text('(post_id, version DESC)')])

    op.create_table('comments_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('comment_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1'),
        sa.Column('post_id', sa.Text()),
        sa.Column('author', sa.Text()),
        sa.Column('body', sa.Text()),
        sa.Column('created_utc', sa TIMESTAMP()),
        sa.Column('raw', postgresql.JSONB()),
        sa.Column('is_deleted', sa.Boolean(), server_default='false'),
        sa.Column('version_hash', sa.Text()),
        sa.Column('captured_at', sa TIMESTAMP(), server_default='now()'),
        sa.UniqueConstraint('comment_id', 'version', name='comments_history_comment_id_version_key')
    )

    op.create_index('idx_comments_history_comment_id', 'comments_history', ['comment_id'])
    op.create_index('idx_comments_history_version', 'comments_history', [sa.text('(comment_id, version DESC)')])


def downgrade() -> None:
    op.drop_table('comments_history')
    op.drop_table('posts_history')
    op.drop_table('media')
    op.drop_table('comments')
    op.drop_table('posts')
    op.drop_table('targets')
    op.drop_table('users')