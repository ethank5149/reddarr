"""SQLAlchemy ORM models for Reddarr.

Maps to the existing PostgreSQL schema documented in docs/SCHEMA.md.
All existing tables, indexes, triggers, and views are preserved.

Migration from raw psycopg2:
  - Import models from here instead of writing raw SQL
  - Use Session.query(Post) or select(Post) with the async engine
  - Alembic autogenerate now works against these models
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all Reddarr models."""

    pass


# ---------------------------------------------------------------------------
# Core tables
# ---------------------------------------------------------------------------


class User(Base):
    """Authenticated users of the web interface."""

    __tablename__ = "users"

    username = Column(Text, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())


class Target(Base):
    """Subreddits and users to ingest media from."""

    __tablename__ = "targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(Text, nullable=False)  # 'subreddit' | 'user'
    name = Column(Text, unique=True, nullable=False)
    enabled = Column(Boolean, server_default=text("true"), nullable=False)
    status = Column(Text, server_default=text("'active'"))  # active|taken_down|deleted
    last_created = Column(DateTime)
    icon_url = Column(Text)

    __table_args__ = (Index("ix_targets_enabled", "enabled"),)


class Post(Base):
    """Core table storing Reddit posts."""

    __tablename__ = "posts"

    id = Column(Text, primary_key=True)  # Reddit post ID e.g. "abc123"
    subreddit = Column(Text, index=True)
    author = Column(Text, index=True)
    created_utc = Column(DateTime, index=True)
    title = Column(Text)
    selftext = Column(Text)
    url = Column(Text)
    media_url = Column(Text)
    raw = Column(JSONB)
    tsv = Column(TSVECTOR)
    ingested_at = Column(DateTime, server_default=func.now(), index=True)
    hidden = Column(Boolean, server_default=text("false"), nullable=False)
    hidden_at = Column(DateTime)
    excluded = Column(Boolean, server_default=text("false"), nullable=False)

    # Relationships
    comments = relationship("Comment", back_populates="post", lazy="dynamic")
    media = relationship("Media", back_populates="post", lazy="dynamic")

    __table_args__ = (
        Index("ix_posts_subreddit_created", "subreddit", created_utc.desc()),
        Index("ix_posts_author_created", "author", created_utc.desc()),
        Index("ix_posts_hidden_created", "hidden", created_utc.desc()),
        Index("ix_posts_tsv", "tsv", postgresql_using="gin"),
        Index("ix_posts_lower_subreddit", func.lower(subreddit)),
        Index("ix_posts_lower_author", func.lower(author)),
        Index("ix_posts_hidden", "hidden"),
    )


class Comment(Base):
    """Comments on posts."""

    __tablename__ = "comments"

    id = Column(Text, primary_key=True)  # Reddit comment ID
    post_id = Column(Text, ForeignKey("posts.id"), index=True)
    author = Column(Text)
    body = Column(Text)
    created_utc = Column(DateTime)
    raw = Column(JSONB)
    tsv = Column(TSVECTOR)

    post = relationship("Post", back_populates="comments")

    __table_args__ = (Index("ix_comments_tsv", "tsv", postgresql_using="gin"),)


class Media(Base):
    """Downloaded media files."""

    __tablename__ = "media"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Text, ForeignKey("posts.id"), index=True)
    url = Column(Text)
    file_path = Column(Text)
    thumb_path = Column(Text)
    sha256 = Column(Text)
    downloaded_at = Column(DateTime)
    status = Column(Text, index=True)  # done|failed|corrupted|pending
    retries = Column(Integer, server_default=text("0"))
    error_message = Column(Text)

    post = relationship("Post", back_populates="media")

    __table_args__ = (
        UniqueConstraint("post_id", "url", name="uq_media_post_url"),
        Index("ix_media_sha256", "sha256", unique=True, postgresql_where=text("sha256 IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# Audit / history tables
# ---------------------------------------------------------------------------


class PostHistory(Base):
    """Complete version history for posts."""

    __tablename__ = "posts_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default=text("1"))
    subreddit = Column(Text)
    author = Column(Text)
    created_utc = Column(DateTime)
    title = Column(Text)
    selftext = Column(Text)
    url = Column(Text)
    media_url = Column(Text)
    raw = Column(JSONB)
    is_deleted = Column(Boolean, server_default=text("false"))
    version_hash = Column(Text)
    captured_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("post_id", "version", name="uq_posts_history_version"),
        Index("ix_posts_history_post_id", "post_id"),
        Index("ix_posts_history_post_version", "post_id", version.desc()),
        Index("ix_posts_history_captured", "captured_at"),
    )


class CommentHistory(Base):
    """Complete version history for comments."""

    __tablename__ = "comments_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, server_default=text("1"))
    post_id = Column(Text)
    author = Column(Text)
    body = Column(Text)
    created_utc = Column(DateTime)
    raw = Column(JSONB)
    is_deleted = Column(Boolean, server_default=text("false"))
    version_hash = Column(Text)
    captured_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("comment_id", "version", name="uq_comments_history_version"),
        Index("ix_comments_history_comment_id", "comment_id"),
        Index("ix_comments_history_comment_version", "comment_id", version.desc()),
        Index("ix_comments_history_captured", "captured_at"),
    )
