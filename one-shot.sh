#!/bin/bash
set -e

echo "=== Reddit Archive - Build & Deploy ==="

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

ARCHIVE_PATH="$(grep '^ARCHIVE_PATH=' .env | cut -d= -f2-)"
THUMB_PATH="$(grep '^THUMB_PATH=' .env | cut -d= -f2-)"
ARCHIVE_MEDIA_PATH="$(grep '^ARCHIVE_MEDIA_PATH=' .env | cut -d= -f2-)"
REDDIT_ARCHIVE_API_PORT="$(grep '^REDDIT_ARCHIVE_API_PORT=' .env | cut -d= -f2-)"
REDDIT_ARCHIVE_PROMETHEUS_PORT="$(grep '^REDDIT_ARCHIVE_PROMETHEUS_PORT=' .env | cut -d= -f2-)"
REDDIT_ARCHIVE_GRAFANA_PORT="$(grep '^REDDIT_ARCHIVE_GRAFANA_PORT=' .env | cut -d= -f2-)"

ARCHIVE_PATH="${ARCHIVE_PATH:-/mnt/user/Archive/reddit}"
THUMB_PATH="${THUMB_PATH:-/mnt/user/Archive/reddit/.thumbs}"
ARCHIVE_MEDIA_PATH="${ARCHIVE_MEDIA_PATH:-/mnt/user/Archive/reddit/.archive}"
REDDIT_ARCHIVE_API_PORT="${REDDIT_ARCHIVE_API_PORT:-8080}"
REDDIT_ARCHIVE_PROMETHEUS_PORT="${REDDIT_ARCHIVE_PROMETHEUS_PORT:-9090}"
REDDIT_ARCHIVE_GRAFANA_PORT="${REDDIT_ARCHIVE_GRAFANA_PORT:-3000}"

for secret in secrets/postgres_password secrets/reddit_client_id secrets/reddit_client_secret secrets/api_key; do
    if [ ! -f "$secret" ]; then
        echo "Warning: $secret not found (some services may fail)"
    fi
done

mkdir -p pgdata redisdata

echo "Building and starting containers..."
docker-compose build --no-cache
docker-compose up -d --force-recreate

echo ""
echo "=== Services ==="
docker-compose ps

echo ""
echo "=== URLs ==="
echo "API:        http://localhost:${REDDIT_ARCHIVE_API_PORT}"
echo "Prometheus: http://localhost:${REDDIT_ARCHIVE_PROMETHEUS_PORT}"
echo "Grafana:    http://localhost:${REDDIT_ARCHIVE_GRAFANA_PORT}"
echo ""
echo "Grafana login: admin / admin"
echo ""
echo "=== Direct Usage URLs ==="
echo "API Health:   curl http://localhost:${REDDIT_ARCHIVE_API_PORT}/health"
echo "Prometheus:   http://localhost:${REDDIT_ARCHIVE_PROMETHEUS_PORT}/graph"
echo "Grafana:      http://localhost:${REDDIT_ARCHIVE_GRAFANA_PORT}/dashboard"