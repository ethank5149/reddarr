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
docker compose build
docker compose up -d --remove-orphans

echo "Waiting for database and redis to be ready..."
sleep 15

echo ""
echo "=== Services ==="
docker compose ps

echo ""
echo "=== URLs ==="
echo "Web UI/API:  http://localhost:${REDDIT_ARCHIVE_API_PORT}"
echo "Prometheus:  http://localhost:${REDDIT_ARCHIVE_PROMETHEUS_PORT}"
echo "Grafana:     http://localhost:${REDDIT_ARCHIVE_GRAFANA_PORT}"
echo "Backup UI:   http://localhost:${ARCHIVE_BACKUP_PORT}"
echo ""
echo "Grafana login: admin / admin"
echo ""
echo "=== Security Note ==="
echo "Default credentials are set for admin/guest. Change these in secrets/ for production!"
echo ""
echo "=== Direct Usage URLs ==="
DOCKER_BRIDGE=$(docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')
echo "API Health:   curl http://${DOCKER_BRIDGE}:${REDDIT_ARCHIVE_API_PORT}/health"
echo "Prometheus:    http://${DOCKER_BRIDGE}:${REDDIT_ARCHIVE_PROMETHEUS_PORT}/graph"
echo "Grafana:       http://${DOCKER_BRIDGE}:${REDDIT_ARCHIVE_GRAFANA_PORT}/dashboard"
echo ""
echo "(If localhost doesn't work, use the Docker bridge IP above)"