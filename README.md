# Reddarr

A self-hosted Reddit data archiving platform that collects posts, comments, and media from specified subreddits and users.

## Architecture

- **db** (PostgreSQL 16) - Primary database for posts, comments, media, and targets
- **redis** (Redis 7) - Message queue for media download tasks
- **ingester** - Polls Reddit API and ingests posts into the database
- **downloader** - Downloads media (images, videos) from queued URLs
- **api** (FastAPI + React) - REST API and web UI for querying and tagging archived content
- **prometheus** - Metrics collection
- **grafana** - Visualization and monitoring dashboards
- **postgres-exporter** - PostgreSQL metrics for Prometheus
- **redis-exporter** - Redis metrics for Prometheus
- **node-exporter** - System metrics for Prometheus

## Quick Start

1. Create secrets in `secrets/` directory:
   ```
   secrets/
   ├── postgres_password      # PostgreSQL password
   ├── reddit_client_id       # Reddit API client ID
   ├── reddit_client_secret   # Reddit API client secret
   └── api_key               # API authentication key (optional)
   ```

2. Configure environment in `.env`:
   ```bash
   cp .env.example .env
   # Edit .env with your preferences
   ```

3. Start services:
   ```bash
   docker-compose up -d
   ```

Or use the one-shot script for automated build and deploy:
   ```bash
   ./one-shot.sh
   ```

## Accessing Services

Once running, access the following services:

| Service | URL | Default Credentials |
|---------|-----|---------------------|
| **Web UI/API** | http://localhost:8080 | N/A |
| **Prometheus** | http://localhost:9090 | N/A |
| **Grafana** | http://localhost:3000 | admin / admin |
| **PostgreSQL** | localhost:5432 | reddit / (see secrets/postgres_password) |
| **Redis** | localhost:6379 | N/A |

### Grafana Setup

1. Login to Grafana at http://localhost:3000
2. Default credentials: `admin` / `admin`
3. Prometheus is already configured as a data source via provisioning

## Configuration

Create a `.env` file with the following variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL password | (required) |
| `REDDIT_USER_AGENT` | User agent for Reddit API requests | (required) |
| `REDDIT_TARGET_SUBREDDITS` | Comma-separated list of subreddits to archive | (none) |
| `REDDIT_TARGET_USERS` | Comma-separated list of Reddit users to archive | (none) |
| `ARCHIVE_PATH` | Directory for downloaded media | /mnt/user/Archive/reddit |
| `THUMB_PATH` | Directory for thumbnails | /mnt/user/Archive/reddit/.thumbs |
| `ARCHIVE_MEDIA_PATH` | Directory for archived media | /mnt/user/Archive/reddit/.archive |
| `POLL_INTERVAL` | Seconds between Reddit API polls | 300 |
| `REDDIT_ARCHIVE_API_PORT` | API/Web UI port | 8080 |
| `REDDIT_ARCHIVE_PROMETHEUS_PORT` | Prometheus port | 9090 |
| `REDDIT_ARCHIVE_GRAFANA_PORT` | Grafana port | 3000 |

### Example .env

```
REDDIT_USER_AGENT=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36
TARGETS_FILE=/app/targets.txt
ARCHIVE_PATH=/path/to/archive
POLL_INTERVAL=300
REDDIT_ARCHIVE_API_PORT=8080
REDDIT_ARCHIVE_PROMETHEUS_PORT=9090
REDDIT_ARCHIVE_GRAFANA_PORT=3000
```

## API Endpoints

- `GET /api/posts` - List posts with pagination
- `GET /api/search?q=<query>` - Full-text search posts
- `POST /api/tag?post_id=<id>&tag=<name>` - Tag a post

The web UI is available at the API port and provides a graphical interface for browsing and searching archived content.