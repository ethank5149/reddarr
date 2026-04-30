#!/bin/bash
set -euo pipefail
cd /mnt/user/scripts/reddarr

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
curl -sf http://localhost:${REDDARR_PORT:-8011}/health && echo " ✓ API healthy" || echo " ✗ API not responding"

echo "=== Done ==="