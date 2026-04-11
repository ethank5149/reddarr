#!/bin/bash
# Log collection script - run via cron every 5 minutes
# */5 * * * * /mnt/user/scripts/reddarr/logs/collect-logs.sh

LOGS_DIR="/mnt/user/scripts/reddarr/logs"
CONTAINERS="db redis ingester downloader api grafana prometheus postgres-exporter redis-exporter node-exporter"

mkdir -p "$LOGS_DIR"

for name in $CONTAINERS; do
    container="reddit_archive_$name"
    if docker ps -q -f "name=$container" > /dev/null 2>&1; then
        docker logs --tail 500 --timestamps "$container" > "$LOGS_DIR/$name.log" 2>&1
        chmod 644 "$LOGS_DIR/$name.log"
    fi
done

echo "Log collection complete at $(date)" >> "$LOGS_DIR/collector.log"