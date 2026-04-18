# Plan: Reddarr v2 Architecture Refactor (TubeArchivist-style consolidation)

## Overview

Refactor Reddarr from 3 separate container monoliths (ingester/downloader/api) into a single unified Python package with Celery task dispatch, SQLAlchemy ORM, and modular FastAPI routes. The v2 skeleton has been pre-written and delivered as `reddarr-v2-skeleton.tar.gz`. This plan implements the migration step-by-step.

**Working directory**: `/mnt/user/scripts/reddit-archive`
**Access URL**: http://192.168.1.13:8011
**CRITICAL**: Back up the database before starting any step.

---

## Pre-flight: Backup and extract skeleton

### Step 0a: Create a full backup

```bash
cd /mnt/user/scripts/reddit-archive
docker exec reddit_archive_db pg_dump -U reddit reddit | gzip > backup_pre_v2_$(date +%Y%m%d_%H%M%S).sql.gz
cp docker-compose.yml docker-compose.yml.v1.bak
cp -r shared shared.v1.bak
cp -r ingester ingester.v1.bak
cp -r downloader downloader.v1.bak
cp -r web/app.py web/app.py.v1.bak
```

### Step 0b: Extract the v2 skeleton tarball

The tarball `reddarr-v2-skeleton.tar.gz` should be placed at the repo root. Extract it:

```bash
cd /mnt/user/scripts/reddit-archive
tar xzf reddarr-v2-skeleton.tar.gz
```

This creates the `reddarr/` package directory and overwrites `docker-compose.yml`, `Dockerfile`, `alembic/`, `alembic.ini`, `pyproject.toml`, `prometheus/prometheus.yml`, and `.env.example`. It does NOT touch `web/`, `pgdata/`, `redisdata/`, `secrets/`, `grafana/`, `icon.png`, or `.env`.

### Step 0c: Verify the extracted structure

```bash
find reddarr -name '*.py' | sort
```

Expected output:
```
reddarr/__init__.py
reddarr/api/__init__.py
reddarr/api/app.py
reddarr/api/auth.py
reddarr/api/middleware.py
reddarr/api/routes/__init__.py
reddarr/api/routes/admin.py
reddarr/api/routes/backups.py
reddarr/api/routes/media.py
reddarr/api/routes/posts.py
reddarr/api/routes/system.py
reddarr/api/routes/targets.py
reddarr/config.py
reddarr/database.py
reddarr/models.py
reddarr/services/__init__.py
reddarr/services/media.py
reddarr/services/providers/__init__.py
reddarr/services/providers/base.py
reddarr/services/providers/generic.py
reddarr/services/providers/reddit.py
reddarr/services/providers/redgifs.py
reddarr/services/providers/youtube.py
reddarr/services/reddit.py
reddarr/tasks/__init__.py
reddarr/tasks/download.py
reddarr/tasks/ingest.py
reddarr/tasks/maintenance.py
reddarr/utils/__init__.py
reddarr/utils/media.py
reddarr/utils/metrics.py
```

---

## Phase 1: Wire up the skeleton — get it building and booting

The skeleton is pre-written but needs to be connected to the existing codebase. Work through these steps in order. After each step, verify before proceeding.

### Step 1: Update .env for v2 variables

Open `.env` and ensure these variables exist (add any missing ones):

```env
POSTGRES_PASSWORD=<your existing password>
REDDARR_PORT=8011
PROMETHEUS_PORT=9090
GRAFANA_PORT=3000
```

The new `docker-compose.yml` uses `REDDARR_PORT` instead of `REDDIT_ARCHIVE_API_PORT`. If `.env` has the old name, add the new one.

**Verify**: `grep REDDARR_PORT .env` returns a value.

---

### Step 2: Fix the Dockerfile dist path

The new `Dockerfile` copies `web/` frontend sources for the Node build stage. The existing `web/` directory already has `package.json`, `src/`, `public/`, etc. Verify the Dockerfile's COPY paths match the actual `web/` layout:

```bash
ls web/package.json web/src/App.jsx web/vite.config.js web/index.html
```

All four should exist. If `web/public/` doesn't exist, create it:
```bash
mkdir -p web/public
cp icon.png web/public/icon.png 2>/dev/null || true
```

**Verify**: All four files exist.

---

### Step 3: Fix the FastAPI app factory dist path

Open `reddarr/api/app.py`. The `dist_dir` variable computes a relative path. In the Docker container, the built frontend will be at `/app/dist`. Update the path:

```python
# In create_app(), replace:
dist_dir = os.path.join(os.path.dirname(__file__), "..", "..", "dist")

# With:
dist_dir = os.environ.get("DIST_DIR", "/app/dist")
```

**Verify**: The file parses cleanly: `python3 -c "import ast; ast.parse(open('reddarr/api/app.py').read())"`

---

### Step 4: Stop v1 services

```bash
cd /mnt/user/scripts/reddit-archive
docker-compose down
```

**Verify**: `docker ps | grep reddit_archive` shows nothing running.

---

### Step 5: Build and start v2

```bash
cd /mnt/user/scripts/reddit-archive
docker-compose build --no-cache
docker-compose up -d db redis
sleep 5
docker-compose up -d api
sleep 5
docker-compose up -d worker beat
```

**Verify**:
```bash
docker ps --format '{{.Names}} {{.Status}}' | grep reddarr
```
Expected: `reddarr_db`, `reddarr_redis`, `reddarr_api`, `reddarr_worker`, `reddarr_beat` all Up.

```bash
curl -s http://192.168.1.13:8011/health | python3 -m json.tool
```
Expected: `{"status": "ok", "timestamp": "..."}`

---

### Step 6: Verify database connectivity

```bash
curl -s http://192.168.1.13:8011/metrics | head -5
```

Should show Prometheus metrics. If you see `reddarr_posts_total` with a nonzero value, the ORM is reading from the existing database.

```bash
curl -s 'http://192.168.1.13:8011/api/posts?per_page=3' | python3 -m json.tool | head -20
```

Should return your existing posts.

---

### Step 7: Import targets from targets.txt

The old `targets.txt` file needs to be imported into the database via the new API:

```bash
API_KEY=$(cat secrets/api_key)

while IFS=: read -r type name; do
  # Skip comments and empty lines
  [[ -z "$name" || "$type" == \#* ]] && continue
  type=$(echo "$type" | tr -d ' ')
  name=$(echo "$name" | tr -d ' ')

  echo "Adding $type:$name..."
  curl -s -X POST "http://192.168.1.13:8011/api/admin/targets" \
    -H "X-Api-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"type\": \"$type\", \"name\": \"$name\"}" | python3 -m json.tool
done < targets.txt
```

**Verify**:
```bash
curl -s "http://192.168.1.13:8011/api/admin/targets" \
  -H "X-Api-Key: $(cat secrets/api_key)" | python3 -m json.tool
```

Should list all your targets with post counts.

---

### Step 8: Verify Celery workers are running

```bash
docker logs reddarr_worker --tail 20
```

Should show `celery@<hostname> ready` and task registrations.

```bash
docker logs reddarr_beat --tail 20
```

Should show the beat schedule with `ingest-cycle` and other periodic tasks.

---

### Step 9: Trigger a test ingest

```bash
API_KEY=$(cat secrets/api_key)
curl -s -X POST "http://192.168.1.13:8011/api/admin/trigger-scrape" \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"target_type": "subreddit", "target_name": "YOUR_FIRST_SUBREDDIT"}' | python3 -m json.tool
```

Replace `YOUR_FIRST_SUBREDDIT` with one of your actual targets.

Then check the worker logs:
```bash
docker logs reddarr_worker --tail 30
```

Should show `ingest_target` task received and posts being ingested.

---

### Step 10: Verify media downloads are working

After the ingest above, check for download tasks:
```bash
docker logs reddarr_worker --tail 50 | grep download_media
```

Check queue status:
```bash
curl -s "http://192.168.1.13:8011/api/admin/queue" \
  -H "X-Api-Key: $(cat secrets/api_key)" | python3 -m json.tool
```

---

## Phase 2: Fix issues and fill gaps

After the skeleton is running, there will be gaps between the skeleton's simplified logic and the full v1 behavior. Address these in order:

### Step 11: Port the media URL extraction logic

The skeleton's `reddarr/services/media.py::extract_media_urls()` is a simplified version. Compare it against the old `shared/media_utils.py` and `ingester/media.py` and port any missing extraction logic, particularly:

- RedGifs HTML embed extraction
- Gallery metadata edge cases
- v.redd.it fallback_url dash playlist handling
- Cross-post media resolution

Files to compare:
- OLD: `shared.v1.bak/media_utils.py` (466 lines)
- OLD: `ingester.v1.bak/media.py` (316 lines)
- NEW: `reddarr/services/media.py` (198 lines)

Port missing logic into the new file, keeping the clean function signatures.

---

### Step 12: Port the download worker logic

The skeleton's `reddarr/tasks/download.py::download_media_item()` is simplified. Compare against the old `downloader.v1.bak/app.py::process_item()` (the 500+ line function) and port:

- Image corruption detection and retry logic
- Domain-specific rate limiting (the old `RateLimiter` class)
- Reddit preview image high-res upgrade logic
- Imgur album handling
- RedGifs token refresh and retry
- The `check_existing_media_batch()` batch dedup optimization

The old provider pattern is already preserved in `reddarr/services/providers/`. Enhance individual providers as needed.

Files to compare:
- OLD: `downloader.v1.bak/app.py` lines 366-886 (`process_item`)
- NEW: `reddarr/tasks/download.py` + `reddarr/services/providers/*.py`

---

### Step 13: Port the SSE event stream payload

The skeleton's `reddarr/api/routes/system.py::_build_sse_payload()` is simplified. Compare against the old `web/app.py.v1.bak` lines 2633-2790 (`_run_sse_polling_loop`) and port:

- Recent posts list (new posts since last SSE push)
- Recent media downloads list
- Per-target detailed stats (rate, ETA, progress_percent)
- The `new_posts` and `new_media` arrays the frontend expects

The frontend `App.jsx` parses this SSE payload — the shape must match what it expects.

Files to compare:
- OLD: `web/app.py.v1.bak` lines 2633-2790
- NEW: `reddarr/api/routes/system.py` lines 100-170

---

### Step 14: Port the post listing query

The skeleton's `reddarr/api/routes/posts.py::list_posts()` is simplified. The old endpoint (web/app.py lines 684-993) had:

- Video URL extraction from raw JSON
- Media URL building with excluded-media path support
- Subreddit/author filtering with case-insensitive matching
- Comment count as a subquery
- Tag support
- Multiple sort options (score, comments, media_count)

Compare OLD `web/app.py.v1.bak` lines 684-993 with NEW `reddarr/api/routes/posts.py` and port missing query logic.

---

### Step 15: Port admin endpoints

The old `web/app.py` had many admin endpoints not yet in the skeleton. Check which the frontend actually calls and port them:

```bash
grep -oP "fetch\(['\"]([^'\"]+)" web/src/App.jsx | sort -u
```

This shows every API endpoint the frontend hits. Cross-reference with the route modules in `reddarr/api/routes/` and add any missing endpoints.

---

## Phase 3: Update AGENTS.md and one-shot.sh

### Step 16: Update `.kilo/AGENTS.md`

Replace the contents of `.kilo/AGENTS.md` with:

```markdown
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
cd /mnt/user/scripts/reddit-archive
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
```

---

### Step 17: Update one-shot.sh

Replace the contents of `one-shot.sh` with:

```bash
#!/bin/bash
set -euo pipefail
cd /mnt/user/scripts/reddit-archive

echo "=== Reddarr v2 Full Rebuild ==="

echo "--- Stopping services ---"
docker-compose down

echo "--- Building image ---"
docker-compose build --no-cache

echo "--- Starting DB + Redis ---"
docker-compose up -d db redis
echo "Waiting for DB..."
sleep 8

echo "--- Starting API (runs migrations) ---"
docker-compose up -d api
sleep 5

echo "--- Starting Celery worker + beat ---"
docker-compose up -d worker beat

echo "--- Starting observability stack ---"
docker-compose up -d prometheus grafana

echo "--- Verifying ---"
sleep 3
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep reddarr
echo ""
curl -sf http://192.168.1.13:8011/health && echo " ✓ API healthy" || echo " ✗ API not responding"

echo "=== Done ==="
```

---

### Step 18: Clean up old files

Once v2 is stable and verified, remove the old v1 code:

```bash
cd /mnt/user/scripts/reddit-archive
rm -rf ingester/ downloader/ shared/ db/
rm -f docker-compose.prod.yml docker-compose.test.yml
rm -f Dockerfile.tests one-shot.tests.sh
rm -f patch_modal.py code-feedback.md
rm -rf ingester.v1.bak downloader.v1.bak shared.v1.bak web/app.py.v1.bak
```

Keep `web/` (frontend source), `web/app.py.v1.bak` until Phase 2 porting is complete.

---

## Verification checklist

After completing all steps, verify each of these works:

- [ ] `curl http://192.168.1.13:8011/health` → `{"status": "ok"}`
- [ ] `curl http://192.168.1.13:8011/api/posts?per_page=5` → returns posts
- [ ] `curl http://192.168.1.13:8011/api/search?q=test` → returns search results
- [ ] `curl http://192.168.1.13:8011/api/admin/stats` (with API key) → returns stats
- [ ] `curl http://192.168.1.13:8011/api/admin/targets` (with API key) → lists targets
- [ ] Web UI loads at http://192.168.1.13:8011
- [ ] `docker logs reddarr_worker` shows tasks being processed
- [ ] `docker logs reddarr_beat` shows scheduled tasks
- [ ] Trigger a scrape → worker processes ingest → download tasks dispatched
- [ ] Media files appear on disk at `$ARCHIVE_PATH`
- [ ] Thumbnails generated at `$THUMB_PATH`
- [ ] Prometheus metrics at http://192.168.1.13:8011/metrics
- [ ] Grafana dashboards still work at http://192.168.1.13:3000

---

## Notes

- The schema is unchanged — same tables, columns, indexes. No migration needed.
- `alembic upgrade head` runs automatically on API startup. If no migration files exist in `alembic/versions/`, it's a no-op (the tables already exist).
- The old `targets.txt` file is not used by v2. Targets are managed via the `/api/admin/targets` endpoints.
- Celery Flower (task monitoring UI) is available by uncommenting the `flower` service in `docker-compose.yml`.
- All three roles (api, worker, beat) use the same Docker image — `docker-compose build api` rebuilds for all.
