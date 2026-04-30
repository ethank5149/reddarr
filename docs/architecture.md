# Architecture

## Overview

Reddarr uses a three-process application model built from a single Docker image. The same image runs as the API server, the Celery task worker, or the Celery Beat scheduler depending on the `command` set in `docker-compose.yml`.

```
                         ┌────────────────────────────────────────────────────┐
                         │                  Single Docker Image               │
                         │                                                    │
  Browser / API client ──┤──► API (uvicorn)      Celery Worker   Beat        │
                         │      FastAPI app         Downloads   Scheduler     │
                         └──────────┬──────────────────┬───────────┬──────────┘
                                    │                  │           │
                              ┌─────▼──────┐    ┌──────▼───┐  ┌───▼────┐
                              │ PostgreSQL │    │  Redis   │  │  Redis │
                              │  (storage) │    │ (broker) │  │(broker)│
                              └────────────┘    └──────────┘  └────────┘
```

---

## Services

| Container | Role | Key dependency |
|---|---|---|
| `reddarr_api` | Serves REST API and React web UI. Runs Alembic migrations at startup. | PostgreSQL, Redis |
| `reddarr_worker` | Executes Celery tasks: ingest, download, maintenance. Concurrency: 4. | PostgreSQL, Redis |
| `reddarr_beat` | Celery Beat scheduler. Dispatches periodic tasks on a fixed schedule. | Redis |
| `reddarr_db` | PostgreSQL 16. Primary data store. | — |
| `reddarr_redis` | Redis 7. Celery broker + result backend. | — |
| `reddarr_prometheus` | Scrapes `/metrics` from the API. Optional. | reddarr_api |
| `reddarr_grafana` | Visualises Prometheus data. Optional. | reddarr_prometheus |

---

## Data Flow

### Ingestion

```
Beat scheduler
    │
    └── run_ingest_cycle()  [every POLL_INTERVAL seconds]
            │
            ├── query enabled targets from PostgreSQL
            │
            └── for each target:
                    ingest_target(type, name)
                            │
                            ├── PRAW fetches posts from Reddit API
                            │
                            ├── upsert posts → PostgreSQL (posts, comments)
                            │
                            ├── detect content changes → append PostHistory / CommentHistory
                            │
                            └── for each media URL:
                                    download_media_item(post_id, url)  [enqueued to download queue]
```

### Download

```
download_media_item(post_id, url)
        │
        ├── check if already downloaded (deduplicate by post_id + url)
        │
        ├── select provider by URL domain:
        │       YouTube  → yt-dlp
        │       RedGifs  → RedGifs API token + direct download
        │       Reddit   → v.redd.it / i.redd.it direct
        │       Imgur    → direct download
        │       Generic  → HTTP GET with redirects
        │
        ├── save file to {ARCHIVE_PATH}/{subreddit}/{post_id}/
        │
        ├── compute SHA-256 hash (detect duplicates across posts)
        │
        ├── generate thumbnail with ffmpeg (320px wide)
        │
        └── write Media record to PostgreSQL (status = done | failed)
```

### Read path

```
Browser → GET /api/posts → FastAPI → SQLAlchemy → PostgreSQL
Browser → GET /media/...  → FastAPI → FileResponse (direct file serve)
Browser → GET /api/events → FastAPI SSE → polls DB every 5s → streams JSON
```

---

## Database Schema

The primary tables and their relationships:

```
Target
  id, type (subreddit|user), name, enabled, status, icon_url, last_created

Post
  id (Reddit ID), subreddit, author, title, selftext, url, media_url
  created_utc, ingested_at, hidden, excluded
  raw (JSONB — full Reddit API response)
  tsv (TSVECTOR — full-text search index)
  
  ├── Media (post_id → Post.id)
  │     id, url, file_path, thumb_path, sha256
  │     status (pending|done|failed|corrupted|missing|abandoned)
  │     downloaded_at, retries, error_message
  │
  └── Comment (post_id → Post.id)
        id (Reddit ID), author, body, created_utc
        raw (JSONB), tsv (TSVECTOR)

PostHistory      — append-only; one row per detected change
  post_id, version, title, selftext, author, url, is_deleted, captured_at

CommentHistory   — same pattern for comments
  comment_id, version, body, author, is_deleted, captured_at
```

### Key indexes

- `Post(subreddit, created_utc DESC)` — subreddit browsing
- `Post(tsv)` GIN — full-text search
- `Post(hidden, created_utc DESC)` — filtering hidden posts
- `Media(post_id)` — loading post media
- `Media(status)` — queue queries
- `Media(sha256) WHERE sha256 IS NOT NULL` (unique) — deduplication

### Migrations

Managed by Alembic. Migrations live in `alembic/versions/` and run automatically when the API container starts (`alembic upgrade head`).

---

## Task Queue

### Queues

Tasks are routed to named queues. The worker subscribes to all three.

| Queue | Tasks |
|---|---|
| `ingest` | `run_ingest_cycle`, `ingest_target`, `trigger_backfill` |
| `download` | `download_media_item`, `requeue_failed`, `generate_thumbnails` |
| `default` | `refresh_target_icons`, `cleanup_failed_downloads`, `integrity_check` |

### Beat schedule

| Task | Interval |
|---|---|
| `run_ingest_cycle` | Every `POLL_INTERVAL` seconds (default: 300) |
| `refresh_target_icons` | Every 6 hours |
| `cleanup_failed_downloads` | Every 1 hour |

`cleanup_failed_downloads` marks media items as `abandoned` once they exceed `max_retries` (default: 10) to stop them being retried indefinitely.

### Retry behaviour

| Task | Max retries | Retry delay |
|---|---|---|
| `ingest_target` | 3 | 60 s |
| `download_media_item` | 3 | 30 s |
| `run_ingest_cycle` | 1 | — |

Download tasks use `acks_late=True` — the task is only acknowledged by Redis after it completes, so a worker crash will not silently drop a download.

---

## Configuration & Secrets

Settings are loaded once at startup into a frozen `Settings` dataclass and cached. The load priority for each value is:

1. Docker secret at `/run/secrets/{name}`
2. Environment variable `{NAME_UPPER}`
3. Hard-coded default (if any)

This means secrets always win over environment variables, which is intentional — it prevents accidentally overriding production credentials with a misconfigured `.env`.

---

## Authentication

If `secrets/api_key` exists and is non-empty, all `/api/admin/*` and `/api/targets*` routes require the `X-Api-Key` header. If the file is absent or empty, those endpoints are open. Public browsing and media serving endpoints never require a key.

---

## Media Storage Layout

```
{ARCHIVE_PATH}/
  {subreddit}/
    {post_id}/
      r_{subreddit}_{title_slug}_{post_id}_{hash}.{ext}

{THUMB_PATH}/
  {subreddit}/
    {post_id}/
      r_{subreddit}_{title_slug}_{post_id}_{hash}.jpg   ← 320px wide thumbnail

{ARCHIVE_MEDIA_PATH}/                                    ← excluded/archived posts
  ...same structure...
  .thumbs/
    ...
```

Filenames are deterministic: `make_filename(post_id, url)` hashes the URL to produce a stable 8-character suffix, preventing collisions when a post has multiple media items.

---

## Metrics

The API server exposes a `/metrics` endpoint in Prometheus text format via the `prometheus_client` library. A `MetricsMiddleware` on every request records latency and status code. Individual tasks record their own counters and histograms directly.

Grafana dashboards and Prometheus scrape config are provisioned automatically from `grafana/provisioning/` and `prometheus/prometheus.yml` when the optional monitoring containers start.

---

## Frontend

The React app is built at image build time (`npm run build` inside the Dockerfile's Node stage) and the output is copied into the Python image under `reddarr/static/`. FastAPI mounts this directory and serves it as a SPA — any path that does not match an API route falls through to `index.html`.

The frontend connects to `/api/events` on load to open a persistent SSE connection. Dashboard stats, target progress, and recent activity all update from this stream without polling individual endpoints.
