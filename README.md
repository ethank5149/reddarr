# Reddarr

A self-hosted Reddit archiving platform. Collects posts, comments, and media from specified subreddits and users into a searchable local archive.

**Stack:** FastAPI · PostgreSQL · Celery · Redis · React · Docker

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Secrets](#secrets)
- [Managing Targets](#managing-targets)
- [Web UI](#web-ui)
- [API](#api)
- [Monitoring](#monitoring)
- [Operations](#operations)
- [Development](#development)

---

## Features

- Archive posts, comments, and media from subreddits and user profiles
- Download images and videos (Reddit native, YouTube, RedGifs, Imgur, and generic URLs)
- Full-text search with PostgreSQL tsvector
- Version history for edited/deleted posts and comments
- Real-time dashboard via Server-Sent Events
- Prometheus metrics and Grafana dashboards
- Database backup and restore via the admin API
- Celery-based task queue — concurrent downloads, retries, scheduled ingest cycles

---

## Quick Start

### 1. Create secrets

```bash
mkdir -p secrets/
echo "strongpassword"      > secrets/postgres_password
echo "your_reddit_id"      > secrets/reddit_client_id
echo "your_reddit_secret"  > secrets/reddit_client_secret
echo "adminpass"           > secrets/admin_password
echo "guestpass"           > secrets/guest_password   # optional
echo "yourapikey"          > secrets/api_key           # optional, enables auth
echo "backuppass"          > secrets/backup_passphrase # optional
```

Reddit OAuth credentials are obtained by creating an app at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps). Use the **script** app type.

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set ARCHIVE_PATH
```

### 3. Start

```bash
docker-compose up -d
```

The web UI is available at **http://localhost:8011**.

### 4. Add your first target

```bash
curl -X POST http://localhost:8011/api/targets \
  -H "X-Api-Key: yourapikey" \
  -H "Content-Type: application/json" \
  -d '{"type": "subreddit", "name": "earthporn", "enabled": true}'
```

Ingest runs automatically every `POLL_INTERVAL` seconds (default: 5 minutes). To trigger one immediately:

```bash
curl -X POST http://localhost:8011/api/admin/trigger-scrape \
  -H "X-Api-Key: yourapikey" \
  -H "Content-Type: application/json" \
  -d '{"target_type": "subreddit", "target_name": "earthporn"}'
```

---

## Configuration

All configuration is done via `.env`. Docker Compose reads this file automatically.

| Variable | Default | Description |
|---|---|---|
| `ARCHIVE_PATH` | `/mnt/user/Archive/reddit` | Where downloaded media files are stored |
| `THUMB_PATH` | `/mnt/user/Archive/reddit/.thumbs` | Where generated thumbnails are stored |
| `ARCHIVE_MEDIA_PATH` | `/mnt/user/Archive/reddit/.archive` | Directory for excluded/archived media |
| `POLL_INTERVAL` | `300` | Seconds between automatic ingest cycles |
| `SCRAPE_LIMIT` | `500` | Max posts fetched per target per ingest cycle |
| `REDDIT_USER_AGENT` | — | User-agent string sent to Reddit API |
| `REDDARR_PORT` | `8011` | Host port for the web UI and API |
| `PROMETHEUS_PORT` | `9090` | Host port for Prometheus |
| `GRAFANA_PORT` | `3000` | Host port for Grafana |
| `LOG_LEVEL` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `POSTGRES_PASSWORD` | `changeme` | Overridden by `secrets/postgres_password` at runtime |

> **Note:** `DB_URL` and `REDIS_HOST` are set automatically by Docker Compose and should not be changed in `.env`.

---

## Secrets

Sensitive values are stored as plain text files in `secrets/` and mounted into containers via Docker secrets. At runtime the app reads them from `/run/secrets/` first, then falls back to the matching environment variable.

| File | Required | Purpose |
|---|---|---|
| `secrets/postgres_password` | Yes | PostgreSQL password |
| `secrets/reddit_client_id` | Yes | Reddit OAuth app client ID |
| `secrets/reddit_client_secret` | Yes | Reddit OAuth app client secret |
| `secrets/admin_password` | Yes | Web UI and API admin password |
| `secrets/guest_password` | No | Read-only guest access password |
| `secrets/api_key` | No | Bearer token for API calls (`X-Api-Key` header). If absent, admin endpoints are unauthenticated. |
| `secrets/backup_passphrase` | No | Encryption passphrase for Borg backups |

**Never commit the `secrets/` directory to version control.** It is already listed in `.gitignore`.

---

## Managing Targets

Targets are subreddits or user profiles to archive. They are stored in the database and managed via the API.

### Add a target

```bash
# Archive a subreddit
curl -X POST http://localhost:8011/api/targets \
  -H "X-Api-Key: yourapikey" \
  -H "Content-Type: application/json" \
  -d '{"type": "subreddit", "name": "itookapicture", "enabled": true}'

# Archive a user
curl -X POST http://localhost:8011/api/targets \
  -H "X-Api-Key: yourapikey" \
  -H "Content-Type: application/json" \
  -d '{"type": "user", "name": "gallowboob", "enabled": true}'
```

### List targets

```bash
curl http://localhost:8011/api/targets -H "X-Api-Key: yourapikey"
```

### Enable / disable a target

```bash
curl -X PATCH "http://localhost:8011/api/targets/1?enabled=false" \
  -H "X-Api-Key: yourapikey"
```

### Backfill historical posts

For a new target, you may want to fetch older posts beyond what the regular ingest cycle covers:

```bash
curl -X POST http://localhost:8011/api/admin/trigger-backfill \
  -H "X-Api-Key: yourapikey" \
  -H "Content-Type: application/json" \
  -d '{"target_type": "subreddit", "target_name": "itookapicture", "sort": "top", "time_filter": "all"}'
```

---

## Web UI

The React web UI is served at the API port (default `http://localhost:8011`).

| Page | Path | Description |
|---|---|---|
| Library | `/library` | Browse all archived posts |
| Subreddits | `/subreddits` | Subreddit index with media counts and progress |
| Users | `/users` | User profile index |
| Archive | `/archive` | Excluded/archived posts |
| System | `/system` | Real-time stats dashboard |
| Activity | `/activity` | Recent ingestion and download activity |
| Backup | `/backup` | Database backup management |

The API key (if configured) is stored in `localStorage` and sent automatically with admin requests.

---

## API

Full API documentation: [docs/api.md](docs/api.md)

### Base URL

```
http://localhost:8011/api
```

### Authentication

If `secrets/api_key` is set, include the key as a header on all `/admin/*` requests:

```
X-Api-Key: yourapikey
```

Public endpoints (browsing, searching, media serving) do not require authentication.

### Endpoint summary

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/posts` | — | List posts with filtering and pagination |
| GET | `/post/{id}` | — | Post detail with comments and media |
| GET | `/post/{id}/history` | — | Post edit history |
| GET | `/comment/{id}/history` | — | Comment edit history |
| GET | `/search` | — | Full-text search |
| POST | `/post/{id}/hide` | — | Soft-hide a post |
| POST | `/post/{id}/unhide` | — | Unhide a post |
| POST | `/post/{id}/delete` | Key | Delete post and optionally its media |
| GET | `/media/{path}` | — | Serve a media file |
| GET | `/thumb/{path}` | — | Serve a thumbnail |
| GET | `/events` | — | SSE stream for real-time updates |
| GET | `/health` | — | Health check |
| GET | `/metrics` | — | Prometheus metrics |
| GET | `/targets` | Key | List targets |
| POST | `/targets` | Key | Add a target |
| PATCH | `/targets/{id}` | Key | Update a target |
| DELETE | `/targets/{id}` | Key | Delete a target |
| GET | `/admin/stats` | Key | Archive statistics |
| GET | `/admin/activity` | Key | Recent activity |
| GET | `/admin/queue` | Key | Download queue status |
| GET | `/admin/health` | Key | Service health (DB, Redis) |
| POST | `/admin/trigger-scrape` | Key | Manually trigger an ingest |
| POST | `/admin/trigger-backfill` | Key | Trigger a historical backfill |
| POST | `/admin/requeue-failed` | Key | Re-queue failed downloads |
| POST | `/admin/integrity-check` | Key | Verify files exist on disk |
| GET | `/admin/backup/list` | Key | List database backups |
| POST | `/admin/backup/create` | Key | Create a database backup |
| POST | `/admin/backup/restore` | Key | Restore from a backup |

---

## Monitoring

### Services

| Service | URL | Default credentials |
|---|---|---|
| Web UI / API | http://localhost:8011 | See `secrets/admin_password` |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin / admin |

> Change the Grafana default password before exposing it on a network.

### Key Prometheus metrics

| Metric | Type | Description |
|---|---|---|
| `reddarr_posts_total` | Gauge | Total archived posts |
| `reddarr_media_total{status}` | Gauge | Media counts by status (done/pending/failed) |
| `reddarr_posts_ingested_total{subreddit}` | Counter | Posts ingested per subreddit |
| `reddarr_media_downloaded_total{provider}` | Counter | Downloads by provider |
| `reddarr_media_failed_total{provider}` | Counter | Failures by provider |
| `reddarr_download_seconds` | Histogram | Download duration |
| `reddarr_ingest_cycle_seconds` | Histogram | Ingest cycle duration |
| `reddarr_api_requests_total` | Counter | API requests by method/endpoint/status |
| `reddarr_api_latency_seconds` | Histogram | API response latency |

---

## Operations

### Check service status

```bash
docker-compose ps
```

### View logs

```bash
docker-compose logs -f api       # API server
docker-compose logs -f worker    # Celery worker (downloads)
docker-compose logs -f beat      # Celery beat (scheduler)
```

### Re-queue failed downloads

```bash
curl -X POST http://localhost:8011/api/admin/requeue-failed \
  -H "X-Api-Key: yourapikey"
```

### Run a media integrity check

Marks any database records where the file no longer exists on disk:

```bash
curl -X POST http://localhost:8011/api/admin/integrity-check \
  -H "X-Api-Key: yourapikey"
```

### Database backup

```bash
# Create a backup
curl -X POST http://localhost:8011/api/admin/backup/create \
  -H "X-Api-Key: yourapikey"

# List backups
curl http://localhost:8011/api/admin/backup/list \
  -H "X-Api-Key: yourapikey"
```

### Run migrations manually

Migrations run automatically on API startup. To run them manually:

```bash
docker-compose exec api alembic upgrade head
```

### Full rebuild

```bash
./one-shot.sh
```

This stops all containers, rebuilds the image from scratch, and starts everything back up.

---

## Development

### Requirements

- Docker and Docker Compose
- Python 3.11+ (for running outside Docker)
- Node 20+ (for frontend development)

### Run tests

```bash
docker-compose -f docker-compose.test.yml up --build
```

### Frontend development

```bash
cd web/
npm install
npm run dev   # Dev server at http://localhost:5173, proxies API to localhost:8011
```

### Linting

```bash
pip install ruff
ruff check reddarr/
```

### Adding a database migration

```bash
# After modifying models.py:
docker-compose exec api alembic revision --autogenerate -m "describe the change"
docker-compose exec api alembic upgrade head
```

### Architecture

See [docs/architecture.md](docs/architecture.md) for a detailed explanation of the system design and data flow.
