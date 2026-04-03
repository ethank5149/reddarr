
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS targets (
    id SERIAL PRIMARY KEY,
    type TEXT,
    name TEXT UNIQUE,
    enabled BOOLEAN DEFAULT true,
    last_created TIMESTAMP
);

ALTER TABLE posts ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS selftext TEXT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS tsv tsvector;

CREATE INDEX IF NOT EXISTS idx_posts_tsv ON posts USING GIN(tsv);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT,
    author TEXT,
    body TEXT,
    created_utc TIMESTAMP,
    raw JSONB,
    tsv tsvector
);

CREATE INDEX IF NOT EXISTS idx_comments_tsv ON comments USING GIN(tsv);

ALTER TABLE media ADD COLUMN IF NOT EXISTS sha256 TEXT;

DO $$
BEGIN
IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'media_sha256_key'
) THEN
    ALTER TABLE media ADD CONSTRAINT media_sha256_key UNIQUE (sha256);
END IF;
END$$;

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

