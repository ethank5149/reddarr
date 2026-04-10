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
   ├── postgres_password      # PostgreSQL password (REQUIRED)
   ├── reddit_client_id       # Reddit API client ID (REQUIRED)
   ├── reddit_client_secret   # Reddit API client secret (REQUIRED)
   ├── admin_password         # Web UI admin password (REQUIRED)
   ├── guest_password         # Web UI guest password (optional)
   └── api_key                # API authentication key (optional)
   ```

2. **Security Warning**: Change default passwords before deploying:
   - `admin_password` and `guest_password` in `secrets/` - These secure your web API
   - `postgres_password` in `secrets/` - This secures your database
   - The default `admin/admin` credentials for Grafana should be changed in production

3. Configure environment in `.env`:
   ```bash
   cp .env.example .env
   # Edit .env with your preferences
   ```

4. Start services:
   ```bash
   docker-compose up -d
   ```

Or use the one-shot script for automated build and deploy:
   ```bash
   ./one-shot.sh
   ```

## Accessing Services

Once running, access the following services:

| Service | URL | Default Credentials | Security Note |
|---------|-----|---------------------|----------------|
| **Web UI/API** | http://localhost:8080 | See `secrets/admin_password` | **CHANGE DEFAULTS** |
| **Prometheus** | http://localhost:9090 | N/A | Exposed for monitoring |
| **Grafana** | http://localhost:3000 | admin / admin | **CHANGE IN PRODUCTION** |
| **PostgreSQL** | localhost:5432 | reddit / (see secrets/postgres_password) | Use secrets |
| **Redis** | localhost:6379 | N/A | Internal only |

### Security Considerations

1. **Credentials**: All sensitive credentials are stored in the `secrets/` directory and mounted via Docker secrets. Never commit these to version control.

2. **Default Passwords**: The `admin_password` and `guest_password` secrets default to `admin` and `guest` respectively. Change these before deploying to any production or publicly accessible environment.

3. **Grafana**: Default Grafana credentials are set to `admin`/`admin`. For any deployment beyond local development, either:
   - Change via environment: `GF_SECURITY_ADMIN_PASSWORD=your_strong_password`
   - Or use the secret file approach

4. **Network Exposure**: The API, Prometheus, and Grafana ports are exposed on all interfaces. Use a reverse proxy with TLS in production.

5. **Database**: The database connection URL is constructed from secrets. The Python applications read the postgres password directly from `/run/secrets/` for security.

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