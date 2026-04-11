#!/bin/bash
set -e

echo "=========================================="
echo "Borg Restore Utility - Reddarr"
echo "=========================================="

export BORG_PASSPHRASE=$(cat ${BORG_PASSPHRASE_FILE:-/run/secrets/backup_passphrase})

BORG_REPO=${BORG_REPO:-/backups/reddarr}

show_usage() {
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  list              List all backups"
    echo "  info [archive]    Show info about backup archive"
    echo "  restore [archive] [target_path]"
    echo "                   Restore files from archive"
    echo "  extract [archive] [target_path]"
    echo "                   Extract archive to target directory"
    echo ""
    echo "Examples:"
    echo "  $0 list                           # List all archives"
    echo "  $0 info reddarr_20260101_120000  # Show archive details"
    echo "  $0 extract reddarr_20260101_120000 /tmp/restore"
}

cmd_list() {
    echo "Available archives in ${BORG_REPO}:"
    echo ""
    borg list ${BORG_REPO}
    echo ""
    echo "Disk usage:"
    borg info ${BORG_REPO}
}

cmd_info() {
    local archive=${1:-}
    if [ -z "${archive}" ]; then
        echo "Available archives:"
        borg list ${BORG_REPO}
        echo ""
        echo "Pass archive name to get details"
        exit 1
    fi
    echo "Archive info: ${archive}"
    borg info ${BORG_REPO}::${archive}
}

cmd_extract() {
    local archive=${1:-}
    local target=${2:-/tmp/restore}
    
    if [ -z "${archive}" ]; then
        echo "ERROR: archive name required"
        show_usage
        exit 1
    fi
    
    echo "Extracting ${archive} to ${target}..."
    mkdir -p ${target}
    borg extract ${BORG_REPO}::${archive} ${target}
    echo "Extracted to: ${target}"
}

cmd_restore() {
    local archive=${1:-}
    local target=${2:-/tmp/restore}
    
    if [ -z "${archive}" ]; then
        echo "ERROR: archive name required"
        show_usage
        exit 1
    fi
    
    echo "Restoring ${archive} to ${target}..."
    mkdir -p ${target}
    
    echo "Step 1: List archive contents first..."
    borg list ${BORG_REPO}::${archive}
    
    echo ""
    echo "Step 2: Extracting..."
    borg extract --progress ${BORG_REPO}::${archive} ${target}
    
    echo "Restore complete to: ${target}"
    
    echo ""
    echo "Files restored:"
    find ${target} -type f | head -20
}

case "${1:-}" in
    list)
        cmd_list
        ;;
    info)
        cmd_info ${2:-}
        ;;
    extract)
        cmd_extract ${2:-} ${3:-}
        ;;
    restore)
        cmd_restore ${2:-} ${3:-}
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        show_usage
        ;;
esac