"""Backup API routes - database backup, restore, and management.

Extracts backup-related endpoints from the old web/app.py.
All routes require API key authentication.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from reddarr.api.auth import require_api_key
from reddarr.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["backups"], dependencies=[Depends(require_api_key)])

BACKUP_DIR = os.environ.get("BACKUP_DIR", "/data/backups")

# Validate backup directory on import
if not os.path.isabs(BACKUP_DIR):
    raise ValueError(f"BACKUP_DIR must be an absolute path: {BACKUP_DIR}")
if os.path.exists(BACKUP_DIR) and not os.path.isdir(BACKUP_DIR):
    raise ValueError(f"BACKUP_DIR exists but is not a directory: {BACKUP_DIR}")


@router.get("/backup/list")
def backup_list():
    """List available database backups."""
    if not os.path.isdir(BACKUP_DIR):
        return {"backups": []}

    backups = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.endswith(".sql") or f.endswith(".sql.gz") or f.endswith(".dump"):
            full = os.path.join(BACKUP_DIR, f)
            stat = os.stat(full)
            backups.append({
                "name": f,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

    return {"backups": backups}


@router.post("/backup/create")
def backup_create(label: str = ""):
    """Create a new database backup using pg_dump."""
    # Validate label parameter to prevent path traversal
    import re
    if not re.match(r'^[a-zA-Z0-9_-]{0,50}$', label):
        raise HTTPException(status_code=400, detail="Invalid label: only alphanumeric, underscore, and hyphen allowed")

    settings = get_settings()
    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    filename = f"reddarr_{timestamp}{suffix}.sql.gz"
    filepath = os.path.join(BACKUP_DIR, filename)

    try:
        # Use pg_dump piped to gzip (no shell=True for security)
        with open(filepath, "wb") as f:
            proc = subprocess.run(
                ["pg_dump", settings.db_url],
                stdout=subprocess.PIPE,
                timeout=600,
            )
            subprocess.run(
                ["gzip"],
                input=proc.stdout,
                stdout=f,
                timeout=600,
            )

        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:200])

        size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        return {"status": "created", "name": filename, "size": size}

    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@router.post("/backup/restore")
def backup_restore(name: str, confirm: Optional[str] = None):
    """Restore a database from backup.

    Requires confirm=YES to actually execute.
    """
    if confirm != "YES":
        return {
            "warning": "This will overwrite the current database!",
            "confirm": "Add ?confirm=YES to proceed",
        }

    # Validate name parameter to prevent path traversal
    import re
    if not re.match(r'^[a-zA-Z0-9_.-]{1,100}$', name):
        raise HTTPException(status_code=400, detail="Invalid backup name")

    filepath = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Backup not found")

    # Add path traversal protection for backup_restore
    real = os.path.realpath(filepath)
    if not real.startswith(os.path.realpath(BACKUP_DIR)):
        raise HTTPException(status_code=403, detail="Access denied")

    settings = get_settings()

    try:
        if name.endswith(".gz"):
            with open(filepath, "rb") as f:
                proc1 = subprocess.run(
                    ["gunzip", "-c"],
                    input=f.read(),
                    capture_output=True,
                    timeout=600,
                )
                proc = subprocess.run(
                    ["psql", settings.db_url],
                    input=proc1.stdout,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
        else:
            with open(filepath, "r") as f:
                proc = subprocess.run(
                    ["psql", settings.db_url],
                    stdin=f,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )

        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[:200])

        return {"status": "restored", "name": name}

    except Exception as e:
        logger.error(f"Restore failed: {e}")
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")


@router.delete("/backup/{backup_name}")
def backup_delete(backup_name: str):
    """Delete a backup file."""
    filepath = os.path.join(BACKUP_DIR, backup_name)

    # Path traversal protection
    real = os.path.realpath(filepath)
    if not real.startswith(os.path.realpath(BACKUP_DIR)):
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Backup not found")

    os.remove(filepath)
    return {"status": "deleted", "name": backup_name}


@router.get("/backup/stats")
def backup_stats():
    """Backup directory statistics."""
    if not os.path.isdir(BACKUP_DIR):
        return {"count": 0, "total_size": 0}

    files = [
        f for f in os.listdir(BACKUP_DIR)
        if f.endswith((".sql", ".sql.gz", ".dump"))
    ]

    total_size = sum(
        os.path.getsize(os.path.join(BACKUP_DIR, f)) for f in files
    )

    return {
        "count": len(files),
        "total_size": total_size,
        "directory": BACKUP_DIR,
    }
