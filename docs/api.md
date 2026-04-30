# API Reference

**Base URL:** `http://localhost:8011/api`

## Authentication

If `secrets/api_key` is configured, admin endpoints require:

```
X-Api-Key: <your-api-key>
```

Public endpoints (browsing posts, search, media serving) require no authentication.

---

## Posts

### List posts

```
GET /posts
```

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | `1` | Page number (≥ 1) |
| `per_page` | int | `50` | Results per page (1–200) |
| `subreddit` | string | — | Filter by subreddit name |
| `author` | string | — | Filter by author username |
| `sort` | string | `newest` | `newest` · `oldest` · `score` · `comments` · `media_count` |
| `show_hidden` | bool | `false` | Include hidden posts |
| `has_media` | bool | — | `true` = only posts with downloaded media; `false` = only posts without |
| `media_type` | string | — | `video` · `image` · `text` |
| `nsfw` | string | `include` | `include` · `exclude` |

**Response:**

```json
{
  "posts": [
    {
      "id": "abc123",
      "title": "Example post",
      "selftext": "",
      "subreddit": "earthporn",
      "author": "username",
      "created_utc": "2024-01-15T10:30:00+00:00",
      "ingested_at": "2024-01-15T11:00:00+00:00",
      "image_url": "/media/earthporn/abc123/file.jpg",
      "image_urls": ["/media/earthporn/abc123/file.jpg"],
      "video_url": null,
      "video_urls": [],
      "is_video": false,
      "thumb_url": "/thumb/earthporn/abc123/file.jpg",
      "preview_url": "/thumb/earthporn/abc123/file.jpg",
      "excluded": false
    }
  ],
  "total": 4820,
  "page": 1,
  "per_page": 50,
  "pages": 97
}
```

---

### Get post

```
GET /post/{post_id}
```

Returns the full post with all associated media and comments.

**Response:**

```json
{
  "id": "abc123",
  "subreddit": "earthporn",
  "author": "username",
  "title": "Example post",
  "selftext": "",
  "url": "https://i.redd.it/example.jpg",
  "media_url": "https://i.redd.it/example.jpg",
  "created_utc": "2024-01-15T10:30:00+00:00",
  "ingested_at": "2024-01-15T11:00:00+00:00",
  "hidden": false,
  "media": [
    {
      "id": 42,
      "url": "https://i.redd.it/example.jpg",
      "file_path": "/mnt/user/Archive/reddit/earthporn/abc123/file.jpg",
      "thumb_path": "/mnt/user/Archive/reddit/.thumbs/earthporn/abc123/file.jpg",
      "status": "done",
      "media_url": "/media/earthporn/abc123/file.jpg",
      "thumb_url": "/thumb/earthporn/abc123/file.jpg"
    }
  ],
  "media_count": 1,
  "comments": [
    {
      "id": "cmt456",
      "author": "commenter",
      "body": "Great photo!",
      "created_utc": "2024-01-15T10:45:00+00:00"
    }
  ]
}
```

---

### Get post history

```
GET /post/{post_id}/history
```

Returns all recorded versions of a post (captured when the content changes).

**Response:**

```json
{
  "post_id": "abc123",
  "versions": [
    {
      "version": 2,
      "title": "Updated title",
      "selftext": "Edited body text",
      "author": "username",
      "is_deleted": false,
      "captured_at": "2024-01-16T08:00:00+00:00"
    },
    {
      "version": 1,
      "title": "Original title",
      "selftext": "Original body text",
      "author": "username",
      "is_deleted": false,
      "captured_at": "2024-01-15T11:00:00+00:00"
    }
  ]
}
```

---

### Get comment history

```
GET /comment/{comment_id}/history
```

Same structure as post history but for a comment.

---

### Search

```
GET /search
```

Full-text search using PostgreSQL `tsvector` / `tsquery`. Results are ranked by relevance.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | — | **Required.** Search query |
| `page` | int | `1` | Page number |
| `per_page` | int | `50` | Results per page (1–200) |
| `subreddit` | string | — | Restrict search to one subreddit |

**Response:** Same structure as `GET /posts`.

---

### Hide a post

```
POST /post/{post_id}/hide
```

Soft-hides a post. Hidden posts are excluded from `GET /posts` unless `show_hidden=true`.

**Response:**

```json
{"status": "hidden", "post_id": "abc123"}
```

---

### Unhide a post

```
POST /post/{post_id}/unhide
```

**Response:**

```json
{"status": "visible", "post_id": "abc123"}
```

---

### Delete a post

```
POST /post/{post_id}/delete
```

**Requires API key.**

Removes the post from the database. Optionally also deletes the media files from disk.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `delete_media` | bool | `true` | Also delete downloaded files from disk |

**Response:**

```json
{"status": "deleted", "post_id": "abc123"}
```

---

## Media

Media files are served directly. Path traversal attacks are blocked — the path must resolve within the configured archive directory.

### Serve media file

```
GET /media/{path}
```

Returns the file at `{ARCHIVE_PATH}/{path}`.

### Serve thumbnail

```
GET /thumb/{path}
```

Returns the thumbnail at `{THUMB_PATH}/{path}`.

### Serve excluded media

```
GET /excluded-media/{path}
```

Returns a file from `{ARCHIVE_MEDIA_PATH}/{path}`.

### Serve excluded thumbnail

```
GET /excluded-thumb/{path}
```

Returns a thumbnail from `{ARCHIVE_MEDIA_PATH}/.thumbs/{path}`.

---

## Targets

Targets are subreddits or user profiles configured for archiving.

**All target endpoints require an API key.**

---

### List targets

```
GET /targets
```

**Response:**

```json
{
  "targets": [
    {
      "id": 1,
      "type": "subreddit",
      "name": "earthporn",
      "enabled": true,
      "status": "active",
      "icon_url": "https://...",
      "last_created": "2024-01-15T10:30:00+00:00",
      "post_count": 1240
    }
  ]
}
```

---

### Add a target

```
POST /targets
```

**Request body:**

```json
{
  "type": "subreddit",
  "name": "earthporn",
  "enabled": true
}
```

`type` must be `subreddit` or `user`.

**Response:** The created target object.

---

### Update a target

```
PATCH /targets/{target_id}
```

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `enabled` | bool | Enable or disable the target |

---

### Delete a target (by ID)

```
DELETE /targets/{target_id}
```

Removes the target. Does not delete any archived posts or media.

---

### Get target stats

```
GET /target/{target_type}/{target_name}/stats
```

**Response:**

```json
{
  "type": "subreddit",
  "name": "earthporn",
  "posts": 1240,
  "media_total": 2480,
  "media_downloaded": 2350,
  "media_failed": 30,
  "media_pending": 100
}
```

---

### Toggle target

```
POST /target/{target_type}/{target_name}/toggle
```

Flips the `enabled` flag.

---

### Set target status

```
POST /target/{target_type}/{target_name}/status?new_status={status}
```

Valid statuses: `active`, `taken_down`, `deleted`.

---

### Rescan target

```
POST /target/{target_type}/{target_name}/rescan
```

Queues a fresh ingest for this target immediately.

---

### Delete target (by name)

```
DELETE /target/{target_type}/{target_name}
```

---

### Audit target

```
GET /target/{target_type}/{target_name}/audit
```

Reports on posts without media, failed downloads, etc.

**Response:**

```json
{
  "posts": 1240,
  "media": 2480,
  "downloaded": 2350,
  "posts_without_media": 62
}
```

---

## Admin

**All admin endpoints require an API key.**

---

### Archive statistics

```
GET /admin/stats
```

**Response:**

```json
{
  "posts": 8200,
  "comments": 24600,
  "media": {
    "total": 16400,
    "downloaded": 15800,
    "pending": 400,
    "failed": 200
  },
  "targets_enabled": 12
}
```

---

### Recent activity

```
GET /admin/activity
```

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `hours` | int | `24` | Lookback window (1–168) |

**Response:**

```json
{
  "period_hours": 24,
  "posts_ingested": 320,
  "media_downloaded": 180,
  "media_failed": 8
}
```

---

### Queue status

```
GET /admin/queue
```

Returns pending/failed download counts plus Celery worker info.

**Response:**

```json
{
  "db_pending": 400,
  "db_failed": 200,
  "celery": {
    "active_tasks": 4,
    "reserved_tasks": 12,
    "workers": ["celery@worker-1"]
  }
}
```

---

### Service health

```
GET /admin/health
```

**Response:**

```json
{
  "api": "ok",
  "db": "ok",
  "redis": "ok"
}
```

---

### Trigger ingest

```
POST /admin/trigger-scrape
```

Immediately queues an ingest cycle for a specific target.

**Request body:**

```json
{
  "target_type": "subreddit",
  "target_name": "earthporn",
  "sort": "new"
}
```

`sort` options: `new`, `hot`, `top`, `rising`.

**Response:**

```json
{"status": "queued", "task_id": "abc123-uuid"}
```

---

### Trigger backfill

```
POST /admin/trigger-backfill
```

Fetches older posts beyond the normal ingest window. Useful when first adding a target.

**Request body:**

```json
{
  "target_type": "subreddit",
  "target_name": "earthporn",
  "sort": "top",
  "time_filter": "all",
  "passes": 1
}
```

`time_filter` options: `hour`, `day`, `week`, `month`, `year`, `all`.

---

### Re-queue failed downloads

```
POST /admin/requeue-failed
```

Resets all failed media items back to `pending` so they will be retried.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_retries` | int | `5` | Only requeue items with fewer than this many retries |

---

### Backfill status

```
GET /admin/backfill-status
```

Reports whether a backfill task is currently running.

---

### Generate thumbnails

```
POST /admin/thumbnails/backfill
```

Generates thumbnails for any downloaded media that is missing one.

---

### Thumbnail stats

```
GET /admin/thumbnails/stats
```

**Response:**

```json
{
  "total_done": 15800,
  "with_thumbnails": 14200,
  "missing_thumbnails": 1600
}
```

---

### Media integrity check

```
POST /admin/integrity-check
```

Scans all `done` media records and marks any whose files are missing from disk as `missing`.

---

### Clear pending queue

```
DELETE /admin/queue
```

Removes all `pending` media items from the database.

---

### Full reset

```
DELETE /admin/reset?confirm=RESET
```

**Destructive.** Clears all posts, comments, media, and targets. The `confirm=RESET` query parameter is required.

---

## Backups

**All backup endpoints require an API key.**

---

### List backups

```
GET /admin/backup/list
```

**Response:**

```json
{
  "backups": [
    {
      "name": "reddarr_20240115_120000.sql.gz",
      "size": 5242880,
      "created": "2024-01-15T12:00:00+00:00"
    }
  ]
}
```

---

### Create backup

```
POST /admin/backup/create
```

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `label` | string | Optional label appended to the filename |

**Response:**

```json
{
  "status": "created",
  "name": "reddarr_20240115_120000.sql.gz",
  "size": 5242880
}
```

---

### Restore from backup

```
POST /admin/backup/restore?name={filename}&confirm=YES
```

The `confirm=YES` query parameter is required to execute the restore.

---

### Delete backup

```
DELETE /admin/backup/{backup_name}
```

---

### Backup directory stats

```
GET /admin/backup/stats
```

**Response:**

```json
{
  "count": 7,
  "total_size": 36700160,
  "directory": "/data/backups"
}
```

---

## System

### Health check

```
GET /health
```

**Response:**

```json
{"status": "ok", "timestamp": "2024-01-15T12:00:00+00:00"}
```

---

### Prometheus metrics

```
GET /metrics
```

Returns metrics in Prometheus text exposition format. Scraped automatically by Prometheus if configured.

---

### Real-time event stream

```
GET /events
```

Server-Sent Events stream. The frontend connects to this on load to receive live dashboard updates.

Events are emitted roughly every 5 seconds and contain:

```json
{
  "stats": {
    "total_posts": 8200,
    "hidden_posts": 40,
    "total_comments": 24600,
    "downloaded_media": 15800,
    "pending_media": 400,
    "total_media": 16400
  },
  "targets": [
    {
      "type": "subreddit",
      "name": "earthporn",
      "enabled": true,
      "status": "active",
      "post_count": 1240,
      "total_media": 2480,
      "downloaded_media": 2350,
      "pending_media": 100,
      "progress_percent": 94.8,
      "rate_per_second": 0.003,
      "eta_seconds": 33333
    }
  ],
  "new_posts": [...],
  "new_media": [...],
  "timestamp": "2024-01-15T12:00:00+00:00"
}
```

---

## Debug

### Post debug info

```
GET /debug/{post_id}
```

**Requires API key.** Returns the full raw Reddit API payload alongside internal state.
