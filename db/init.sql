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
ALTER TABLE targets ADD COLUMN IF NOT EXISTS icon_url TEXT;

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
    hidden BOOLEAN DEFAULT FALSE NOT NULL,
    hidden_at TIMESTAMP
);

-- Add columns to existing installations
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMP DEFAULT now();
ALTER TABLE posts ADD COLUMN IF NOT EXISTS hidden BOOLEAN DEFAULT FALSE NOT NULL;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMP;

-- Migrate legacy 'archived' column if it exists
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'posts' AND column_name = 'archived'
    ) THEN
        UPDATE posts SET hidden = archived WHERE hidden IS NULL;
    END IF;
END $$;

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
    sha256 TEXT, -- Removed UNIQUE here, adding it as a combined index or keeping it separate
    downloaded_at TIMESTAMP,
    status TEXT,
    retries INT DEFAULT 0,
    error_message TEXT,
    UNIQUE(post_id, url)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_media_sha256 ON media(sha256) WHERE sha256 IS NOT NULL;

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
CREATE INDEX IF NOT EXISTS idx_posts_subreddit_lower ON posts(LOWER(subreddit));
CREATE INDEX IF NOT EXISTS idx_posts_author_lower ON posts(LOWER(author));
CREATE INDEX IF NOT EXISTS idx_posts_hidden ON posts(hidden);
CREATE INDEX IF NOT EXISTS idx_media_status ON media(status);
CREATE INDEX IF NOT EXISTS idx_posts_subreddit_created ON posts(subreddit, created_utc DESC);
CREATE INDEX IF NOT EXISTS idx_posts_author_created ON posts(author, created_utc DESC);
CREATE INDEX IF NOT EXISTS idx_posts_hidden_created ON posts(hidden, created_utc DESC);

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

-- POSTS_HISTORY: stores ALL versions of posts for audit trail
-- This ensures we never lose data when Reddit users edit/delete their posts in protest
CREATE TABLE IF NOT EXISTS posts_history (
    id SERIAL PRIMARY KEY,
    post_id TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    subreddit TEXT,
    author TEXT,
    created_utc TIMESTAMP,
    title TEXT,
    selftext TEXT,
    url TEXT,
    media_url TEXT,
    raw JSONB,
    is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
    version_hash TEXT,
    captured_at TIMESTAMP DEFAULT now(),
    UNIQUE(post_id, version)
);

CREATE INDEX IF NOT EXISTS idx_posts_history_post_id ON posts_history(post_id);
CREATE INDEX IF NOT EXISTS idx_posts_history_version ON posts_history(post_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_posts_history_captured_at ON posts_history(captured_at);

-- COMMENTS_HISTORY: stores ALL versions of comments for audit trail
CREATE TABLE IF NOT EXISTS comments_history (
    id SERIAL PRIMARY KEY,
    comment_id TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    post_id TEXT,
    author TEXT,
    body TEXT,
    created_utc TIMESTAMP,
    raw JSONB,
    is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
    version_hash TEXT,
    captured_at TIMESTAMP DEFAULT now(),
    UNIQUE(comment_id, version)
);

CREATE INDEX IF NOT EXISTS idx_comments_history_comment_id ON comments_history(comment_id);
CREATE INDEX IF NOT EXISTS idx_comments_history_version ON comments_history(comment_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_comments_history_captured_at ON comments_history(captured_at);

-- Function to compute hash for change detection
CREATE OR REPLACE FUNCTION compute_content_hash(title TEXT, selftext TEXT, url TEXT, body TEXT)
RETURNS TEXT AS $$
BEGIN
    RETURN encode(sha256(concat(coalesce(title,''), '|', coalesce(selftext,''), '|', coalesce(url,''), '|', coalesce(body,''))::bytea), 'hex');
END $$ LANGUAGE plpgsql IMMUTABLE;

-- View for latest post version (current behavior)
CREATE OR REPLACE VIEW posts_latest AS
SELECT DISTINCT ON (ph.post_id)
    ph.post_id,
    ph.subreddit,
    ph.author,
    ph.created_utc,
    ph.title,
    ph.selftext,
    ph.url,
    ph.media_url,
    ph.raw,
    ph.is_deleted,
    ph.version as latest_version,
    ph.captured_at
FROM posts_history ph
ORDER BY ph.post_id, ph.version DESC;

-- View for latest comment version
CREATE OR REPLACE VIEW comments_latest AS
SELECT DISTINCT ON (ch.comment_id)
    ch.comment_id,
    ch.post_id,
    ch.author,
    ch.body,
    ch.created_utc,
    ch.raw,
    ch.is_deleted,
    ch.version as latest_version,
    ch.captured_at
FROM comments_history ch
ORDER BY ch.comment_id, ch.version DESC;

-- AUDIT LOG: General audit trail for all data changes
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    action TEXT NOT NULL,
    table_name TEXT NOT NULL,
    record_id TEXT,
    old_value JSONB,
    new_value JSONB,
    username TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_table_name ON audit_log(table_name);
CREATE INDEX IF NOT EXISTS idx_audit_log_record_id ON audit_log(record_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);

-- BACKUP RUNS: Track backup operations
CREATE TABLE IF NOT EXISTS backup_runs (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'started',
    tables JSONB,
    subreddits JSONB,
    rows_backed_up INT DEFAULT 0,
    media_files INT DEFAULT 0,
    media_bytes BIGINT DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP DEFAULT now(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_backup_runs_status ON backup_runs(status);
CREATE INDEX IF NOT EXISTS idx_backup_runs_started_at ON backup_runs(started_at);

-- INTEGRITY CHECKS: Track integrity verification runs
CREATE TABLE IF NOT EXISTS integrity_checks (
    id SERIAL PRIMARY KEY,
    check_type TEXT NOT NULL,
    status TEXT DEFAULT 'started',
    total_items INT DEFAULT 0,
    issues_found INT DEFAULT 0,
    details JSONB,
    started_at TIMESTAMP DEFAULT now(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_integrity_checks_status ON integrity_checks(status);
CREATE INDEX IF NOT EXISTS idx_integrity_checks_type ON integrity_checks(check_type);

-- SCHEMA_VERSION: Track migration state
CREATE TABLE IF NOT EXISTS schema_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT now()
);

-- Function to create audit entries for table changes
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

-- SCRAPE_FAILURES: Track failed post scrapes per target for debugging
CREATE TABLE IF NOT EXISTS scrape_failures (
    id SERIAL PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_name TEXT NOT NULL,
    sort_method TEXT,
    post_id TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scrape_failures_target ON scrape_failures(target_type, target_name);
CREATE INDEX IF NOT EXISTS idx_scrape_failures_post_id ON scrape_failures(post_id);
CREATE INDEX IF NOT EXISTS idx_scrape_failures_created_at ON scrape_failures(created_at);
