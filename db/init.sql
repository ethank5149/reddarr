-- Consolidated Database Schema v4.0
-- Tags system removed; archive feature added

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- USERS table
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT now()
);

-- TARGETS table
CREATE TABLE IF NOT EXISTS targets (
    id SERIAL PRIMARY KEY,
    type TEXT,
    name TEXT UNIQUE,
    enabled BOOLEAN DEFAULT true,
    status TEXT DEFAULT 'active', -- active | taken_down | deleted
    last_created TIMESTAMP
);

-- Add status column to existing installations
ALTER TABLE targets ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';

-- POSTS table
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
    tsv tsvector,
    ingested_at TIMESTAMP DEFAULT now(),
    archived BOOLEAN DEFAULT FALSE NOT NULL,
    archived_at TIMESTAMP
);

-- Add columns to existing installations
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMP DEFAULT now();
ALTER TABLE posts ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE NOT NULL;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP;

-- COMMENTS table
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT,
    author TEXT,
    body TEXT,
    created_utc TIMESTAMP,
    raw JSONB,
    tsv tsvector
);

-- MEDIA table
CREATE TABLE IF NOT EXISTS media (
    id SERIAL PRIMARY KEY,
    post_id TEXT,
    url TEXT,
    file_path TEXT,
    thumb_path TEXT,
    sha256 TEXT UNIQUE,
    downloaded_at TIMESTAMP,
    status TEXT,
    retries INT DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_posts_tsv ON posts USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_comments_tsv ON comments USING GIN(tsv);
CREATE INDEX IF NOT EXISTS idx_media_post_id ON media(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author);
CREATE INDEX IF NOT EXISTS idx_posts_ingested_at ON posts(ingested_at);
CREATE INDEX IF NOT EXISTS idx_posts_created_utc ON posts(created_utc);
CREATE INDEX IF NOT EXISTS idx_targets_enabled ON targets(enabled);
CREATE INDEX IF NOT EXISTS idx_posts_archived ON posts(archived);

-- Full-text search triggers
CREATE OR REPLACE FUNCTION posts_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('english', coalesce(NEW.title,'') || ' ' || coalesce(NEW.selftext,''));
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS posts_tsv_update ON posts;
CREATE TRIGGER posts_tsv_update
BEFORE INSERT OR UPDATE ON posts
FOR EACH ROW EXECUTE FUNCTION posts_tsv_trigger();

CREATE OR REPLACE FUNCTION comments_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('english', coalesce(NEW.body,''));
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS comments_tsv_update ON comments;
CREATE TRIGGER comments_tsv_update
BEFORE INSERT OR UPDATE ON comments
FOR EACH ROW EXECUTE FUNCTION comments_tsv_trigger();
