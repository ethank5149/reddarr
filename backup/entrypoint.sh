#!/bin/bash
set -e

echo "=========================================="
echo "Borg Backup Container - Reddarr"
echo "=========================================="

export BORG_PASSPHRASE=$(cat ${BORG_PASSPHRASE_FILE})

if [ -z "${BORG_REPO}" ]; then
    echo "ERROR: BORG_REPO not set"
    exit 1
fi

mkdir -p ${BORG_REPO}

INIT_MODE=${BORG_INIT:-"existing"}
if [ "${INIT_MODE}" = "init" ]; then
    echo "Initializing new Borg repository..."
    borg init --encryption=repokey ${BORG_REPO}
    echo "Repository initialized"
fi

BACKUP_SOURCES=${BACKUP_SOURCES:-"/data"}
BACKUP_NAME=${BACKUP_NAME:-"reddarr"}
SCHEDULE=${SCHEDULE:-"daily"}
PRUNING=${BORG_PRUNING:-"--keep-daily=7 --keep-weekly=4 --keep-monthly=6"}

echo "Configuration:"
echo "  Repository: ${BORG_REPO}"
echo "  Sources: ${BACKUP_SOURCES}"
echo "  Schedule: ${SCHEDULE}"
echo "  Pruning: ${PRUNING}"

borg list ${BORG_REPO} 2>/dev/null || {
    echo "Repository empty or not initialized"
}

run_backup() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local archive_name="${BACKUP_NAME}_${timestamp}"
    
    echo ""
    echo "=========================================="
    echo "Running backup: ${archive_name}"
    echo "=========================================="
    
    borg create \
        --verbose \
        --compression lz4 \
        --exclude-caches \
        --exclude '*/__pycache__/*' \
        --exclude '*.pyc' \
        --exclude '*/.git/*' \
        --exclude '*/node_modules/*' \
        ${BORG_REPO}::${archive_name} \
        ${BACKUP_SOURCES}
    
    echo "Backup created: ${archive_name}"
    
    echo "Running prune..."
    borg prune -v ${BORG_REPO} ${PRUNING}
    
    echo "Listing archives:"
    borg list ${BORG_REPO}
    
    echo "Archive info:"
    borg info ${BORG_REPO}::${archive_name}
}

case "${SCHEDULE}" in
    "hourly")
        echo "Running hourly backups (every 60 minutes)..."
        while true; do
            run_backup
            sleep 3600
        done
        ;;
    "daily")
        echo "Running daily backup at 2 AM..."
        while true; do
            run_backup
            sleep 86400
        done
        ;;
    "once")
        echo "Running single backup..."
        run_backup
        echo "Backup complete, exiting"
        exit 0
        ;;
    *)
        echo "Unknown schedule: ${SCHEDULE}"
        exit 1
        ;;
esac