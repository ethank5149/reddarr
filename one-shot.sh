#!/bin/bash
set -e

echo "=== Reddarr - Build & Deploy ==="

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env
        echo "Please edit .env and fill in required values before continuing."
        exit 1
    else
        echo "Error: .env file not found"
        exit 1
    fi
fi

source .env

ARCHIVE_PATH="${ARCHIVE_PATH:-/mnt/user/Archive/reddit}"
THUMB_PATH="${THUMB_PATH:-/mnt/user/Archive/reddit/.thumbs}"
ARCHIVE_MEDIA_PATH="${ARCHIVE_MEDIA_PATH:-/mnt/user/Archive/reddit/.archive}"
ARCHIVE_BACKUP_PORT="${REDDIT_ARCHIVE_BACKUP_PORT:-8091}"
REDDIT_ARCHIVE_API_PORT="${REDDIT_ARCHIVE_API_PORT:-8090}"
REDDIT_ARCHIVE_PROMETHEUS_PORT="${REDDIT_ARCHIVE_PROMETHEUS_PORT:-9090}"
REDDIT_ARCHIVE_GRAFANA_PORT="${REDDIT_ARCHIVE_GRAFANA_PORT:-3000}"

echo "Checking secrets..."
for secret in secrets/postgres_password secrets/reddit_client_id secrets/reddit_client_secret secrets/api_key; do
    if [ ! -f "$secret" ]; then
        echo "Warning: $secret not found - some services may fail"
    fi
done

echo "Checking Redis Pub/Sub..."
if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
    docker compose exec -T redis redis-cli PUBSUB CHANNELS >/dev/null 2>&1 && echo "Redis Pub/Sub: available"
fi

if [ ! -f secrets/admin_password ]; then
    echo "admin" > secrets/admin_password
fi

if [ ! -f secrets/guest_password ]; then
    echo "guest" > secrets/guest_password
fi

if [ ! -f secrets/backup_passphrase ]; then
    openssl rand -base64 32 > secrets/backup_passphrase
fi

echo "Creating required directories..."
mkdir -p pgdata redisdata backups-borg borg-cache borgmatic grafana/data prometheus/data logs

if [ -f docker-compose.override.yml ]; then
    if grep -q '^services:[[:space:]]*$' docker-compose.override.yml 2>/dev/null; then
        echo "Removing empty override file..."
        rm docker-compose.override.yml
    fi
fi

if [ ! -f targets.txt ]; then
    echo "Warning: targets.txt not found - ingester may not work"
fi

if [ ! -f borgmatic/config.yml ]; then
    if [ -f borgmatic/config.yaml ]; then
        cp borgmatic/config.yaml borgmatic/config.yml
    else
        echo "Warning: borgmatic config not found"
    fi
fi

if [ ! -f prometheus/prometheus.yml ]; then
    if [ -f prometheus/prometheus.yml.example ]; then
        cp prometheus/prometheus.yml.example prometheus/prometheus.yml
    else
        echo "Warning: prometheus config not found"
    fi
fi

echo "Building and starting containers..."
docker compose build --no-cache
docker compose up -d --remove-orphans

echo "Waiting for database and redis to be ready..."
sleep 10

DB_OK=false
REDIS_OK=false
for i in 1 2 3 4 5; do
    if docker compose exec -T db pg_isready -U reddit >/dev/null 2>&1; then
        DB_OK=true
        echo "Database is ready"
        break
    fi
    sleep 2
done

for i in 1 2 3 4 5; do
    if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
        REDIS_OK=true
        echo "Redis is ready"
        break
    fi
    sleep 2
done

if [ "$DB_OK" = false ] || [ "$REDIS_OK" = false ]; then
    echo "Warning: database or redis not ready"
fi

echo "Verifying API endpoints..."
API_CONTAINER=$(docker compose ps -q api 2>/dev/null)
if [ -n "$API_CONTAINER" ]; then
    API_PORT=$(docker port "$API_CONTAINER" 2>/dev/null | head -1 | cut -d: -f2)
    echo "Testing API via internal container..."
    if docker exec "$API_CONTAINER" python -c "
import urllib.request, json, sys
req = urllib.request.Request(
    'http://127.0.0.1:8080/api/admin/targets',
    data=json.dumps({'type':'subreddit','name':'one_shot_health_check'}).encode(),
    headers={'Content-Type': 'application/json', 'X-API-Key': os.environ.get('API_KEY', '')},
    method='POST'
)
resp = urllib.request.urlopen(req)
print('Target add: SUCCESS')
" 2>&1; then
        echo "API is working (target add verified)"
    else
        echo "Warning: Could not verify target add"
    fi
else
    echo "Warning: API container not found"
fi

add_target() {
    TYPE="$1"
    NAME="$2"
    if [ -z "$TYPE" ] || [ -z "$NAME" ]; then
        echo "Usage: add_target <subreddit|user> <name>"
        return 1
    fi
    DB_CONTAINER=$(docker compose ps -q db 2>/dev/null)
    if [ -n "$DB_CONTAINER" ]; then
        docker exec "$DB_CONTAINER" psql -U reddit -d reddit -t -c "INSERT INTO targets (type, name, enabled) VALUES ('$TYPE', '$NAME', true) ON CONFLICT (name) DO UPDATE SET enabled = true RETURNING name, type;" 2>/dev/null && echo "Added: $TYPE/$NAME"
    else
        echo "Error: DB container not found"
        return 1
    fi
}

add_target_http() {
    TYPE="$1"
    NAME="$2"
    API_CONTAINER=$(docker compose ps -q api 2>/dev/null)
    if [ -n "$API_CONTAINER" ]; then
        docker exec "$API_CONTAINER" python -c "
import urllib.request, json
urllib.request.urlopen(urllib.request.Request(
    'http://127.0.0.1:8080/api/admin/targets',
    data=json.dumps({'type':'$TYPE','name':'$NAME'}).encode(),
    headers={'Content-Type': 'application/json', 'X-API-Key': os.environ.get('API_KEY', '')},
    method='POST'
))
print('Added: $TYPE/$NAME')
" 2>/dev/null && echo "Added: $TYPE/$NAME"
    else
        echo "Error: API container not found"
        return 1
    fi
}

echo ""
echo "=== Services ==="
docker compose ps

echo ""
echo "=== URLs ==="
echo "Web UI/API:"
echo "  - Local: http://localhost:8011/"
echo ""
echo "=== Add Target Command ==="
echo "Usage: add_target <subreddit|user> <name>"
echo "Example: add_target subreddit funny"
echo "Example: add_target user spez"
echo ""
echo "=== Direct Usage URLs ==="
echo "API Health:   docker exec reddit_archive_api python -c 'import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8080/health\").read().decode()[:100])'"
echo "Prometheus:  http://localhost:${REDDIT_ARCHIVE_PROMETHEUS_PORT}/graph"
echo "Grafana:    http://localhost:${REDDIT_ARCHIVE_GRAFANA_PORT}"
echo "Backup UI:  http://localhost:${ARCHIVE_BACKUP_PORT}"
echo ""
echo "=== Security Note ==="
echo "Default credentials are set for admin/guest. Change these in secrets/ for production!"