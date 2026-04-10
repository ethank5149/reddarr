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
REDDIT_ARCHIVE_API_PORT="${REDDIT_ARCHIVE_API_PORT:-8011}"
REDDIT_ARCHIVE_PROMETHEUS_PORT="${REDDIT_ARCHIVE_PROMETHEUS_PORT:-9011}"
REDDIT_ARCHIVE_GRAFANA_PORT="${REDDIT_ARCHIVE_GRAFANA_PORT:-3011}"

for secret in secrets/postgres_password secrets/reddit_client_id secrets/reddit_client_secret secrets/api_key; do
    if [ ! -f "$secret" ]; then
        echo "Warning: $secret not found - some services may fail"
    fi
done

if [ ! -f secrets/admin_password ]; then
    echo "admin" > secrets/admin_password
fi

if [ ! -f secrets/guest_password ]; then
    echo "guest" > secrets/guest_password
fi

mkdir -p pgdata redisdata

echo "Building and starting containers..."
docker-compose build
docker-compose up -d

echo ""
echo "=== Services ==="
docker-compose ps

echo ""
echo "=== URLs ==="
echo "Web UI/API: http://localhost:${REDDIT_ARCHIVE_API_PORT}"
echo "Prometheus: http://localhost:${REDDIT_ARCHIVE_PROMETHEUS_PORT}"
echo "Grafana:    http://localhost:${REDDIT_ARCHIVE_GRAFANA_PORT}"
echo ""
echo "Grafana login: admin / admin"
echo ""
echo "=== Security Note ==="
echo "Default credentials are set for admin/guest. Change these in secrets/ for production!"
echo ""
echo "=== Direct Usage URLs ==="
DOCKER_BRIDGE=$(docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')
echo "API Health:   curl http://${DOCKER_BRIDGE}:${REDDIT_ARCHIVE_API_PORT}/health"
echo "Prometheus:   http://${DOCKER_BRIDGE}:${REDDIT_ARCHIVE_PROMETHEUS_PORT}/graph"
echo "Grafana:      http://${DOCKER_BRIDGE}:${REDDIT_ARCHIVE_GRAFANA_PORT}/dashboard"
echo ""
echo "(If localhost doesn't work, use the Docker bridge IP above)"