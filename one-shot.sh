#!/bin/bash
set -euo pipefail
cd /mnt/user/scripts/reddarr

# ── Helpers ──────────────────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "--- $* ---"; }

wait_healthy() {
  local name=$1 attempts=0 max=60
  echo -n "Waiting for $name to be healthy..."
  until docker inspect --format='{{.State.Health.Status}}' "$name" 2>/dev/null | grep -q "^healthy$"; do
    (( attempts++ )) || true
    [ "$attempts" -ge "$max" ] && echo "" && die "$name did not become healthy after ${max}s"
    echo -n "."
    sleep 1
  done
  echo " ready"
}

# ── Pre-flight checks ─────────────────────────────────────────────────────────
info "Pre-flight checks"

# 1. Required secret files must exist and be non-empty
for secret in postgres_password reddit_client_id reddit_client_secret api_key admin_password guest_password; do
  [ -s "secrets/$secret" ] || die "secrets/$secret is missing or empty — populate it before running"
done
echo "    secrets: OK"

# 2. POSTGRES_PASSWORD in .env must match secrets/postgres_password so the
#    DB container (POSTGRES_PASSWORD_FILE) and app containers (DB_URL) agree.
[ -f .env ] || die ".env file not found"
ENV_PW=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
SECRET_PW=$(cat secrets/postgres_password)
[ -n "$ENV_PW" ]       || die "POSTGRES_PASSWORD not set in .env"
[ "$ENV_PW" = "$SECRET_PW" ] || die "POSTGRES_PASSWORD in .env ('$ENV_PW') does not match secrets/postgres_password — fix one to match the other"
echo "    password: OK"

# 3. Archive bind-mount paths must exist on the host; Docker would silently
#    create them as root:root which breaks write permissions in the container.
# shellcheck source=.env
source .env
ARCHIVE_PATH=${ARCHIVE_PATH:-/mnt/user/Archive/reddit}
THUMB_PATH=${THUMB_PATH:-/mnt/user/Archive/reddit/.thumbs}
ARCHIVE_MEDIA_PATH=${ARCHIVE_MEDIA_PATH:-/mnt/user/Archive/reddit/.archive}

mkdir -p "$ARCHIVE_PATH" "$THUMB_PATH" "$ARCHIVE_MEDIA_PATH"
echo "    archive paths: OK ($ARCHIVE_PATH)"

# 4. Prometheus config must exist or prometheus won't start.
[ -f prometheus/prometheus.yml ] || die "prometheus/prometheus.yml not found"
echo "    prometheus config: OK"

# ── Build & deploy ────────────────────────────────────────────────────────────
echo ""
echo "=== Reddarr v2 Full Rebuild ==="

info "Stopping services"
docker-compose down --remove-orphans

info "Building image"
docker-compose build --no-cache

info "Starting DB + Redis"
docker-compose up -d db redis
wait_healthy reddarr_db
wait_healthy reddarr_redis

info "Starting API (runs migrations)"
# Run API separately first so alembic output is visible and failures are fatal.
# The || true in the compose command is removed — see compose file note.
docker-compose up -d api

# Wait for the API to actually serve (not just the container to start)
echo -n "Waiting for API..."
attempts=0
until curl -sf "http://localhost:${REDDARR_PORT:-8011}/health" >/dev/null 2>&1; do
  (( attempts++ )) || true
  [ "$attempts" -ge 60 ] && echo "" && die "API did not become healthy after 60s — check: docker-compose logs api"
  echo -n "."
  sleep 1
done
echo " ready"

info "Starting Celery worker + beat"
docker-compose up -d worker beat

info "Starting observability stack"
docker-compose up -d prometheus grafana

# ── Verify ───────────────────────────────────────────────────────────────────
info "Final status"
sleep 2
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep reddarr || true
echo ""

# API health
curl -sf "http://localhost:${REDDARR_PORT:-8011}/health" \
  && echo "    ✓ API healthy (http://localhost:${REDDARR_PORT:-8011})" \
  || echo "    ✗ API not responding"

# Warn if any reddarr container exited
EXITED=$(docker ps -a --filter 'name=reddarr' --filter 'status=exited' --format '{{.Names}}')
if [ -n "$EXITED" ]; then
  echo ""
  echo "WARNING: these containers exited unexpectedly:"
  echo "$EXITED" | sed 's/^/    /'
  echo "Run: docker-compose logs <name>  to investigate"
fi

echo ""
echo "=== Done ==="
