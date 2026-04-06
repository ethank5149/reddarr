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
echo "API:        http://localhost:${REDDIT_ARCHIVE_API_PORT:-8080}"
echo "Prometheus: http://localhost:${REDDIT_ARCHIVE_PROMETHEUS_PORT:-9090}"
echo "Grafana:    http://localhost:${REDDIT_ARCHIVE_GRAFANA_PORT:-3000}"
echo ""
echo "Grafana login: admin / admin"