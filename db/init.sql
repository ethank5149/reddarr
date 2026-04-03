CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE targets (
    id SERIAL PRIMARY KEY,
    type TEXT,
    name TEXT UNIQUE,
    enabled BOOLEAN DEFAULT true,
    last_created TIMESTAMP
);

CREATE TABLE posts (
    id TEXT PRIMARY KEY,
    subreddit TEXT,
    author TEXT,
    created_utc TIMESTAMP,
    title TEXT,
    selftext TEXT,
    url TEXT,
    media_url TEXT,
    raw JSONB,
    tsv tsvector
);

CREATE INDEX idx_posts_tsv ON posts USING GIN(tsv);

CREATE TABLE comments (
    id TEXT PRIMARY KEY,
    post_id TEXT,
    author TEXT,
    body TEXT,
    created_utc TIMESTAMP,
    raw JSONB,
    tsv tsvector
);

CREATE INDEX idx_comments_tsv ON comments USING GIN(tsv);

CREATE TABLE media (
    id SERIAL PRIMARY KEY,
    post_id TEXT,
    url TEXT,
    file_path TEXT,
    sha256 TEXT UNIQUE,
    downloaded_at TIMESTAMP,
    status TEXT,
    retries INT DEFAULT 0
);

CREATE FUNCTION posts_tsv_trigger() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('english', coalesce(NEW.title,'') || ' ' || coalesce(NEW.selftext,''));
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER posts_tsv_update
BEFORE INSERT OR UPDATE ON posts
FOR EACH ROW EXECUTE FUNCTION posts_tsv_trigger();
