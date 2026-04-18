# Reddarr v2 — Kilo Agent Instructions

## Access URLs
- **Primary URL**: http://192.168.1.13:8011
- Use the LAN IP (192.168.1.13), NOT localhost

## Architecture
- Single Docker image, three roles: api, worker, beat
- Source code: `reddarr/` Python package
- Frontend: `web/src/` (React/Vite, built in Dockerfile)
- Task queue: Celery with Redis broker

## Always Rebuild & Redeploy
After ANY code changes:

### Rebuild all (safe default):
```bash
cd /mnt/user/scripts/reddarr
docker-compose build
docker-compose up -d
```

### Rebuild by component:
```bash
# API routes, auth, middleware, or frontend changes:
docker-compose build api && docker-compose up -d api

# Task logic, providers, or service changes:
docker-compose build worker && docker-compose up -d worker

# Schedule changes (reddarr/tasks/__init__.py):
docker-compose build beat && docker-compose up -d beat

# All three share the same image, so `docker-compose build api`
# rebuilds the image for all roles. But you only need to restart
# the container(s) whose code path changed.
```

### Verify:
```bash
docker ps --format '{{.Names}} {{.Status}}' | grep reddarr
curl -s http://192.168.1.13:8011/health
docker logs reddarr_worker --tail 10
docker logs reddarr_beat --tail 10
```

## Key files
- `reddarr/config.py` — all settings (env + secrets)
- `reddarr/models.py` — SQLAlchemy ORM (Post, Comment, Media, Target, etc.)
- `reddarr/database.py` — engine + session factory
- `reddarr/api/app.py` — FastAPI factory, mounts all route modules
- `reddarr/api/routes/*.py` — route modules (posts, admin, targets, media, system, backups)
- `reddarr/tasks/*.py` — Celery tasks (ingest, download, maintenance)
- `reddarr/services/providers/*.py` — download provider pattern
- `reddarr/utils/metrics.py` — all Prometheus metric definitions

## DB access pattern
In API routes, use FastAPI dependency injection:
```python
from reddarr.database import get_db
@router.get("/endpoint")
def handler(db: Session = Depends(get_db)):
    posts = db.query(Post).filter(...).all()
```

In Celery tasks, use SessionLocal context manager:
```python
from reddarr.database import SessionLocal, init_engine
init_engine()
with SessionLocal() as db:
    posts = db.query(Post).filter(...).all()
```

## Triggering tasks manually
```python
from reddarr.tasks.ingest import ingest_target
ingest_target.delay("subreddit", "python")
```
Or via API:
```bash
curl -X POST http://192.168.1.13:8011/api/admin/trigger-scrape \
  -H "X-Api-Key: KEY" -H "Content-Type: application/json" \
  -d '{"target_type":"subreddit","target_name":"python"}'
```