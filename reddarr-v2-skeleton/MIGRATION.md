# Reddarr v2 — Architecture Refactor

## What changed (TubeArchivist-inspired consolidation)

### Before (v1): 3 containers, 3 monoliths
```
ingester/app.py   (945 lines)  — PRAW polling loop, raw psycopg2
downloader/app.py (1038 lines) — BLPOP worker loop, raw Redis queue
web/app.py        (2918 lines) — FastAPI monolith, SSE thread, everything else
web/src/App.jsx   (3077 lines) — entire React SPA in one file
shared/           — copied into each container's build context
```

### After (v2): 1 image, 3 roles
```
reddarr/
├── config.py          — centralized settings (env + Docker secrets)
├── models.py          — SQLAlchemy ORM (replaces raw psycopg2 everywhere)
├── database.py        — engine + session factory
├── api/
│   ├── app.py         — FastAPI factory (~100 lines)
│   ├── auth.py        — API key dependency
│   ├── middleware.py   — metrics middleware
│   └── routes/
│       ├── posts.py   — /api/posts, /api/post/{id}, /api/search
│       ├── admin.py   — stats, triggers, queue management
│       ├── targets.py — CRUD for subreddits/users (replaces targets.txt)
│       ├── media.py   — /media/*, /thumb/* file serving
│       ├── system.py  — /health, /metrics, /api/events (SSE)
│       └── backups.py — backup/restore management
├── tasks/
│   ├── __init__.py    — Celery app + beat schedule
│   ├── ingest.py      — Reddit polling (replaces ingester/app.py)
│   ├── download.py    — Media downloading (replaces downloader/app.py)
│   └── maintenance.py — Icon refresh, cleanup, integrity checks
├── services/
│   ├── reddit.py      — PRAW client + post fetching
│   ├── media.py       — URL extraction + classification
│   └── providers/     — download providers (preserved from v1)
│       ├── base.py
│       ├── reddit.py
│       ├── youtube.py
│       ├── redgifs.py
│       └── generic.py
└── utils/
    ├── media.py       — hashing, thumbnails, filenames
    └── metrics.py     — all Prometheus metrics in one place
```

Docker Compose runs **one image** with three entrypoints:
- `api` → uvicorn (web UI + REST API)
- `worker` → celery worker (ingest + download tasks)
- `beat` → celery beat (scheduled polling)

## Key architectural wins

| Area | v1 | v2 |
|------|----|----|
| Task dispatch | Raw `RPUSH`/`BLPOP` + PubSub | Celery with per-task retry, rate limiting, Flower UI |
| DB access | Raw `psycopg2`, hand-rolled pool | SQLAlchemy ORM, Alembic autogenerate |
| Target config | `targets.txt` flat file | DB-driven CRUD via API |
| Scheduling | `while True: sleep(N)` in ingester | Celery Beat with configurable intervals |
| Shared code | Copied into each container | Single Python package, one Dockerfile |
| API structure | 2918-line monolith | ~6 route modules, ~100 lines each |
| Observability | Scattered Prometheus counters | Centralized metrics.py + Flower |

## Migration steps

### Phase 1: Drop-in replacement (no data migration needed)

The v2 schema is **identical** to v1. SQLAlchemy models map to the same tables,
columns, indexes, and constraints. No `ALTER TABLE` needed.

1. **Backup your database** (mandatory):
   ```bash
   # From your current v1 setup
   docker exec reddit_archive_db pg_dump -U reddit reddit | gzip > backup_pre_v2.sql.gz
   ```

2. **Stop v1 services**:
   ```bash
   docker-compose down
   ```

3. **Replace project files**:
   - Drop the `reddarr/` package, `Dockerfile`, `docker-compose.yml`,
     `pyproject.toml`, `alembic/` from this tarball into your repo root
   - Keep your existing: `pgdata/`, `redisdata/`, `secrets/`, `grafana/`,
     `.env`, `icon.png`

4. **Migrate targets.txt → database**:
   ```bash
   # After starting v2, import your old targets
   while IFS=: read -r type name; do
     curl -X POST "http://localhost:8011/api/admin/targets" \
       -H "X-Api-Key: YOUR_KEY" \
       -H "Content-Type: application/json" \
       -d "{\"type\": \"$type\", \"name\": \"$name\"}"
   done < targets.txt
   ```

5. **Start v2**:
   ```bash
   docker-compose up -d --build
   ```

6. **Verify**:
   - Web UI: http://localhost:8011
   - Health: http://localhost:8011/health
   - Metrics: http://localhost:8011/metrics
   - Celery tasks should appear in logs:
     ```bash
     docker logs reddarr_worker
     docker logs reddarr_beat
     ```

### Phase 2: React frontend split (separate effort)

The 3077-line `App.jsx` still works as-is inside the `web/` directory.
Split it incrementally by extracting pages into `web/src/pages/` and
components into `web/src/components/`. The API contract is unchanged.

### Phase 3: Optional enhancements

- **Flower**: Uncomment in docker-compose.yml for task monitoring UI
- **Celery concurrency**: Tune `-c 4` in the worker command
- **Separate queues**: Run dedicated workers per queue type:
  ```yaml
  worker-download:
    command: celery -A reddarr.tasks worker -l info -c 8 -Q download
  worker-ingest:
    command: celery -A reddarr.tasks worker -l info -c 2 -Q ingest
  ```

## File mapping (v1 → v2)

| v1 file | v2 replacement | Notes |
|---------|---------------|-------|
| `shared/config.py` | `reddarr/config.py` | Frozen dataclass, `@lru_cache` singleton |
| `shared/database.py` | `reddarr/database.py` | SQLAlchemy engine + sessionmaker |
| `shared/pubsub.py` | `reddarr/tasks/__init__.py` | Celery replaces PubSub entirely |
| `shared/media_utils.py` | `reddarr/services/media.py` + `reddarr/utils/media.py` | Split extraction vs manipulation |
| `ingester/app.py` | `reddarr/tasks/ingest.py` + `reddarr/services/reddit.py` | Tasks + service layer |
| `ingester/targets.py` | `reddarr/api/routes/targets.py` | DB-driven, no flat file |
| `ingester/media.py` | `reddarr/services/media.py` | Consolidated |
| `ingester/requeue_gifs.py` | `reddarr/tasks/download.py::requeue_failed()` | Celery task |
| `downloader/app.py` | `reddarr/tasks/download.py` | Per-item Celery tasks |
| `downloader/providers.py` | `reddarr/services/providers/` | Same pattern, split into files |
| `downloader/media_utils.py` | `reddarr/utils/media.py` | Consolidated |
| `web/app.py` (2918 lines) | `reddarr/api/routes/*.py` | 6 focused route modules |
| `web/src/App.jsx` | `web/src/` (future split) | Unchanged for now |
