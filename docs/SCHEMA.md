# Reddarr Database Schema Documentation

## Overview

Reddarr is a Reddit media archiving system that ingests posts/comments from specified subreddits and users, downloads associated media (images, videos, GIFs), and provides a web API for browsing and searching the archive.

## Database: PostgreSQL 16

**Database Name:** `reddit`  
**Connection:** Configured via `DB_URL` environment variable or component breakdown

---

## Core Tables

### 1. `users`

Stores authenticated users of the web interface.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `username` | `TEXT` | **PRIMARY KEY** | Unique username |
| `created_at` | `TIMESTAMP` | DEFAULT `now()` | Account creation timestamp |

**Indexes:**
- Primary key on `username`

---

### 2. `targets`

Subreddits and users to ingest media from.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `SERIAL` | **PRIMARY KEY** | Auto-incrementing ID |
| `type` | `TEXT` | | Target type (subreddit/user) |
| `name` | `TEXT` | **UNIQUE** | Target name (e.g., "python", "spez") |
| `enabled` | `BOOLEAN` | DEFAULT `true` | Whether target is active |
| `status` | `TEXT` | DEFAULT `'active'` | Status: `active`, `taken_down`, `deleted` |
| `last_created` | `TIMESTAMP` | | Last post created timestamp |
| `icon_url` | `TEXT` | | Custom icon URL |

**Indexes:**
- Unique index on `name`
- Index on `enabled`

---

### 3. `posts`

Core table storing Reddit posts.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `TEXT` | **PRIMARY KEY** | Reddit post ID (e.g., "abc123") |
| `subreddit` | `TEXT` | | Subreddit name (e.g., "python") |
| `author` | `TEXT` | | Reddit username |
| `created_utc` | `TIMESTAMP` | | Post creation time (UTC) |
| `title` | `TEXT` | | Post title |
| `selftext` | `TEXT` | | Post body text |
| `url` | `TEXT` | | Reddit post URL |
| `media_url` | `TEXT` | | Direct media URL if applicable |
| `raw` | `JSONB` | | Full Reddit API response |
| `tsv` | `tsvector` | | Full-text search vector |
| `ingested_at` | `TIMESTAMP` | DEFAULT `now()` | When post was ingested |
| `hidden` | `BOOLEAN` | DEFAULT `FALSE` NOT NULL | Soft-delete flag |
| `hidden_at` | `TIMESTAMP` | | When post was hidden |

**Indexes:**
- Primary key on `id`
- GIN index on `tsv` (full-text search)
- Index on `subreddit`
- Index on `author`
- Index on `ingested_at`
- Index on `created_utc`
- Index on `hidden`
- Composite: `(subreddit, created_utc DESC)`
- Composite: `(author, created_utc DESC)`
- Composite: `(hidden, created_utc DESC)`
- Functional: `LOWER(subreddit)`
- Functional: `LOWER(author)`

**Triggers:**
- `posts_tsv_update`: Auto-generates `tsv` from title + selftext on INSERT/UPDATE

---

### 4. `comments`

Comments on posts.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `TEXT` | **PRIMARY KEY** | Reddit comment ID |
| `post_id` | `TEXT` | | Foreign key to posts.id |
| `author` | `TEXT` | | Comment author |
| `body` | `TEXT` | | Comment text |
| `created_utc` | `TIMESTAMP` | | Comment creation time |
| `raw` | `JSONB` | | Full Reddit API response |
| `tsv` | `tsvector` | | Full-text search vector |

**Indexes:**
- Primary key on `id`
- GIN index on `tsv`
- Index on `post_id`

**Triggers:**
- `comments_tsv_update`: Auto-generates `tsv` from body on INSERT/UPDATE

---

### 5. `media`

Downloaded media files.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `SERIAL` | **PRIMARY KEY** | Auto-incrementing ID |
| `post_id` | `TEXT` | | Foreign key to posts.id |
| `url` | `TEXT` | | Original media URL |
| `file_path` | `TEXT` | | Local file path |
| `thumb_path` | `TEXT` | | Thumbnail file path |
| `sha256` | `TEXT` | | SHA-256 hash (deduplication) |
| `downloaded_at` | `TIMESTAMP` | | Download timestamp |
| `status` | `TEXT` | | Status: `done`, `failed`, `corrupted` |
| `retries` | `INT` | DEFAULT `0` | Retry count |
| `error_message` | `TEXT` | | Error details if failed |

**Constraints:**
- Unique constraint: `(post_id, url)`

**Indexes:**
- Unique index on `sha256` WHERE `sha256 IS NOT NULL`
- Index on `post_id`
- Index on `status`

---

## Audit/History Tables

These tables store all versions of posts/comments for complete audit trails, ensuring data is never lost even when Reddit users edit or delete their content.

### 6. `posts_history`

Complete version history for posts.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `SERIAL` | **PRIMARY KEY** | Auto-incrementing ID |
| `post_id` | `TEXT` | **NOT NULL** | Reddit post ID |
| `version` | `INT` | **NOT NULL** DEFAULT `1` | Version number |
| `subreddit` | `TEXT` | | Subreddit at capture time |
| `author` | `TEXT` | | Author at capture time |
| `created_utc` | `TIMESTAMP` | | Creation time |
| `title` | `TEXT` | | Title at capture time |
| `selftext` | `TEXT` | | Body at capture time |
| `url` | `TEXT` | | URL at capture time |
| `media_url` | `TEXT` | | Media URL at capture time |
| `raw` | `JSONB` | | Full response at capture |
| `is_deleted` | `BOOLEAN` | DEFAULT `FALSE` | Soft-deleted flag |
| `version_hash` | `TEXT` | | Hash for change detection |
| `captured_at` | `TIMESTAMP` | DEFAULT `now()` | When version was captured |

**Constraints:**
- Unique constraint: `(post_id, version)`

**Indexes:**
- Index on `post_id`
- Composite: `(post_id, version DESC)`
- Index on `captured_at`

---

### 7. `comments_history`

Complete version history for comments.

| Column | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `SERIAL` | **PRIMARY KEY** | Auto-incrementing ID |
| `comment_id` | `TEXT` | **NOT NULL** | Reddit comment ID |
| `version` | `INT` | **NOT NULL** DEFAULT `1` | Version number |
| `post_id` | `TEXT` | | Associated post |
| `author` | `TEXT` | | Author at capture time |
| `body` | `TEXT` | | Body at capture time |
| `created_utc` | `TIMESTAMP` | | Creation time |
| `raw` | `JSONB` | | Full response at capture |
| `is_deleted` | `BOOLEAN` | DEFAULT `FALSE` | Soft-deleted flag |
| `version_hash` | `TEXT` | | Hash for change detection |
| `captured_at` | `TIMESTAMP` | DEFAULT `now()` | When version was captured |

**Constraints:**
- Unique constraint: `(comment_id, version)`

**Indexes:**
- Index on `comment_id`
- Composite: `(comment_id, version DESC)`
- Index on `captured_at`

---

## Views

### `posts_latest`

Returns the most recent version of each post:

```sql
SELECT DISTINCT ON (post_id)
    post_id, subreddit, author, created_utc, title, selftext,
    url, media_url, raw, is_deleted, latest_version, captured_at
FROM posts_history
ORDER BY post_id, version DESC;
```

### `comments_latest`

Returns the most recent version of each comment:

```sql
SELECT DISTINCT ON (comment_id)
    comment_id, post_id, author, body, created_utc,
    raw, is_deleted, latest_version, captured_at
FROM comments_history
ORDER BY comment_id, version DESC;
```

---

## Utility Functions

### `compute_content_hash(title, selftext, url, body)`

Computes a SHA-256 hash for change detection:

```sql
SELECT compute_content_hash('Title', 'Body', 'url.com', 'comment');
-- Returns: sha256 hex string
```

---

## Data Flow

```
┌─────────────┐     ┌───────���─��───┐     ┌─────────────┐
│  Reddit API │────>│   Ingester  │────>│  Database   │
└─────────────┘     └─────────────┘     └─────────────┘
                                                │
                    ┌─────────────┐             │
                    │  Downloader │<────────────┘
                    └─────────────┘
                           │
                           v
                    ┌─────────────┐     ┌─────────────┐
                    │  Media Dir  │<────│    File     │
                    └─────────────┘     └─────────────┘
```

1. **Ingester** queries Reddit API for target subreddits/users
2. Stores raw post data in `posts` table
3. Creates version in `posts_history` for audit trail
4. **Downloader** picks up new posts from queue
5. Downloads media to local filesystem
6. Records file info in `media` table (deduplicated by SHA-256)

---

## Retention & Cleanup

| Component | Strategy |
|-----------|----------|
| Posts | Never deleted (soft-hidden via `hidden` flag) |
| Comments | Never deleted (versioned in history) |
| Media | Hard-linked via SHA-256 for deduplication |
| History | Full version history preserved |
| Failed Downloads | Tracked in Redis, then in `media` table |

---

## Security

- Connection credentials via Docker secrets or environment
- API authentication via `api_key` header
- Admin/guest user separation in web interface
- No direct Reddit credentials stored (OAuth client ID/secret only)