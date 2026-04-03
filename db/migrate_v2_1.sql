
-- TAGGING
CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS post_tags (
    post_id TEXT REFERENCES posts(id),
    tag_id INT REFERENCES tags(id),
    PRIMARY KEY(post_id, tag_id)
);

-- THUMBNAILS
ALTER TABLE media ADD COLUMN IF NOT EXISTS thumb_path TEXT;

