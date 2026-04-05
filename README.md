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

## Accessing Services

Once running, access the following services:

| Service | URL | Default Credentials |
|---------|-----|---------------------|
| **API** | http://localhost:8011 | N/A |
| **Prometheus** | http://localhost:9011 | N/A |
| **Grafana** | http://localhost:3011 | admin / admin |
| **PostgreSQL** | localhost:5432 | reddit / (see secrets/postgres_password) |
| **Redis** | localhost:6379 | N/A |

### Grafana Setup

1. Login to Grafana at http://localhost:3011
2. Default credentials: `admin` / `admin`
3. Add Prometheus as a data source:
   - URL: `http://prometheus:9090`
   - Access: Server (default)

## Configuration

| Variable | Description |
|----------|-------------|
| `DB_URL` | PostgreSQL connection string |
| `REDIS_HOST` | Redis hostname |
| `REDDIT_CLIENT_ID` | Reddit API client ID |
| `REDDIT_CLIENT_SECRET` | Reddit API client secret |
| `TARGETS_FILE` | Path to targets file (required) |
| `ARCHIVE_PATH` | Directory for downloaded media |

### Targets File Format

Targets are loaded from the file specified by `TARGETS_FILE`. Format is one target per line:

```
# Lines starting with # are comments
subreddit:python
subreddit:learnprogramming
user:spez
```

## API Endpoints

- `GET /api/posts` - List posts with pagination
- `GET /api/search?q=<query>` - Full-text search posts
- `POST /api/tag?post_id=<id>&tag=<name>` - Tag a post