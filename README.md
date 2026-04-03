# Reddit Archive

A self-hosted Reddit data archiving platform that collects posts, comments, and media from specified subreddits and users.

## Architecture

- **db** (PostgreSQL 16) - Primary database for posts, comments, media, and targets
- **redis** (Redis 7) - Message queue for media download tasks
- **ingester** - Polls Reddit API and ingests posts into the database
- **downloader** - Downloads media (images, videos) from queued URLs
- **api** (FastAPI) - REST API for querying and tagging archived content
- **prometheus** - Metrics collection
- **grafana** - Visualization and monitoring dashboards

## Quick Start

1. Create secrets in `secrets/` directory:
   ```
   secrets/
   ├── postgres_password      # PostgreSQL password
   ├── reddit_client_id       # Reddit API client ID
   └── reddit_client_secret   # Reddit API client secret
   ```

2. Configure environment in `.env` (non-sensitive settings):
   ```bash
   cp .env.example .env
   # Edit .env with your preferences
   ```

3. Start services:
   ```bash
   docker-compose up -d
   ```

3. Access the API at `http://localhost:8011`

## Configuration

| Variable | Description |
|----------|-------------|
| `DB_URL` | PostgreSQL connection string |
| `REDIS_HOST` | Redis hostname |
| `REDDIT_CLIENT_ID` | Reddit API client ID |
| `REDDIT_CLIENT_SECRET` | Reddit API client secret |
| `REDDIT_TARGET_SUBREDDITS` | Comma-separated list of subreddits to archive |
| `REDDIT_TARGET_USERS` | Comma-separated list of Reddit users to archive |
| `ARCHIVE_PATH` | Directory for downloaded media |

## API Endpoints

- `GET /api/posts` - List posts with pagination
- `GET /api/search?q=<query>` - Full-text search posts
- `POST /api/tag?post_id=<id>&tag=<name>` - Tag a post

## Monitoring

- Prometheus: `http://localhost:9011`
- Grafana: `http://localhost:3011` (default credentials: admin/admin)