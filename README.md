# Reddarr

A self-hosted Reddit data archiving platform that collects posts, comments, and media from specified subreddits and users.

## Goals

- **Archive Reddit content** - Collect and store posts, comments, and media from specified subreddits and users
- **Media management** - Download and organize images, videos, and thumbnails
- **Search & discovery** - Full-text search and tagging for archived content
- **Monitoring** - Prometheus metrics and Grafana dashboards for system health
- **Backup** - Automated Borg backups for disaster recovery

## Architecture

| Component | Description |
|-----------|-------------|
| **db** | PostgreSQL 16 - Primary database for posts, comments, media, and targets |
| **redis** | Redis 7 - Message queue for media download tasks |
| **ingester** | Polls Reddit API and ingests posts into the database |
| **downloader** | Downloads media (images, videos) from queued URLs |
| **api** | FastAPI + React - REST API and web UI for querying and tagging archived content |
| **backup** | Borg UI - Web interface for managing Borg backups |
| **prometheus** | Metrics collection |
| **grafana** | Visualization and monitoring dashboards |
| **postgres-exporter** | PostgreSQL metrics for Prometheus |
| **redis-exporter** | Redis metrics for Prometheus |
| **node-exporter** | System metrics for Prometheus |

## Quick Start

1. Create secrets in `secrets/` directory:
   ```
   secrets/
   ├── postgres_password      # PostgreSQL password (REQUIRED)
   ├── reddit_client_id       # Reddit API client ID (REQUIRED)
   ├── reddit_client_secret   # Reddit API client secret (REQUIRED)
   ├── admin_password         # Web UI admin password (REQUIRED)
   ├── guest_password         # Web UI guest password (optional)
   ├── api_key                # API authentication key (optional)
   ├── backup_passphrase      # Borg backup encryption passphrase (optional)
   ```

2. **Security Warning**: Change default passwords before deploying:
   - `admin_password` and `guest_password` in `secrets/` - These secure your web API
   - `postgres_password` in `secrets/` - This secures your database
   - The default `admin/admin` credentials for Grafana should be changed in production

3. Configure targets in `targets.txt`:
   ```
   subreddit:funny
   subreddit:technology
   user:username1
   user:username2
   ```

4. Configure environment in `.env`:
   ```bash
   cp .env.example .env
   # Edit .env with your preferences
   ```

5. Start services:
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
| **Web UI/API** | http://localhost:8090 | See `secrets/admin_password` | **CHANGE DEFAULTS** |
| **Backup UI** | http://localhost:8091 | None (configured via secrets) | Internal network |
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

4. **Network Exposure**: The API, Prometheus, Grafana, and Backup ports are exposed on all interfaces. Use a reverse proxy with TLS in production.

5. **Database**: The database connection URL is constructed from secrets. The Python applications read the postgres password directly from `/run/secrets/` for security.

### Grafana Setup

1. Login to Grafana at http://localhost:3000
2. Default credentials: `admin` / `admin`
3. Prometheus is already configured as a data source via provisioning

### Backup UI

1. Access the backup UI at http://localhost:8091
2. The backup passphrase is stored in `secrets/backup_passphrase`
3. Backups are configured via `borgmatic/config.yml`

## Configuration

Create a `.env` file with the following variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_PASSWORD` | PostgreSQL password | (required) |
| `REDDIT_USER_AGENT` | User agent for Reddit API requests | (required) |
| `ARCHIVE_PATH` | Directory for downloaded media | /mnt/user/Archive/reddit |
| `THUMB_PATH` | Directory for thumbnails | /mnt/user/Archive/reddit/.thumbs |
| `ARCHIVE_MEDIA_PATH` | Directory for archived media | /mnt/user/Archive/reddit/.archive |
| `POLL_INTERVAL` | Seconds between Reddit API polls | 300 |
| `SCRAPE_LIMIT` | Maximum posts to fetch per poll | 500 |
| `REDDIT_ARCHIVE_API_PORT` | API/Web UI port | 8090 |
| `REDDIT_ARCHIVE_BACKUP_PORT` | Backup UI port | 8091 |
| `REDDIT_ARCHIVE_PROMETHEUS_PORT` | Prometheus port | 9090 |
| `REDDIT_ARCHIVE_GRAFANA_PORT` | Grafana port | 3000 |

### Example .env

```
REDDIT_USER_AGENT=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36
ARCHIVE_PATH=/mnt/user/Archive/reddit
THUMB_PATH=/mnt/user/Archive/reddit/.thumbs
ARCHIVE_MEDIA_PATH=/mnt/user/Archive/reddit/.archive
POLL_INTERVAL=300
SCRAPE_LIMIT=500
REDDIT_ARCHIVE_API_PORT=8090
REDDIT_ARCHIVE_BACKUP_PORT=8091
REDDIT_ARCHIVE_PROMETHEUS_PORT=9090
REDDIT_ARCHIVE_GRAFANA_PORT=3000
```

### Targets Configuration

Edit `targets.txt` to specify which subreddits and users to archive:

```
subreddit:funny
subreddit:technology
subreddit:programming
user:spez
user:automoderator
```

## API Endpoints

- `GET /api/posts` - List posts with pagination
- `GET /api/posts/<id>` - Get single post details
- `GET /api/comments/<post_id>` - Get comments for a post
- `GET /api/search?q=<query>` - Full-text search posts
- `POST /api/tag?post_id=<id>&tag=<name>` - Tag a post
- `GET /api/media/queue` - View queued media downloads

The web UI is available at the API port and provides a graphical interface for browsing and searching archived content.

## Scripts

- `scripts/db_shell.py` - Interactive database shell
- `scripts/db_diag.py` - Database diagnostics
- `scripts/db_backup.py` - Manual database backup
- `scripts/integrity_check.py` - Verify media file integrity
- `ingester/requeue_gifs.py` - Re-queue failed GIF downloads

## Testing

Run tests with:
```bash
docker-compose -f docker-compose.test.yml up --build
```

## Production Deployment

For production deployments, use `docker-compose.prod.yml` which includes additional security hardening and production-specific configurations.