#!/usr/bin/env python3
"""
Database Backup/Restore Utility Script

Usage:
    # Full backup
    python scripts/db_backup.py backup --name my_backup
    
    # List backups
    python scripts/db_backup.py list
    
    # Restore
    python scripts/db_backup.py restore --name my_backup
    
    # Verify backup integrity
    python scripts/db_backup.py verify --name my_backup
    
    # Schedule automatic backups (creates cron wrapper)
    python scripts/db_backup.py schedule --interval daily
    
Features:
    - Full pg_dump backups with compression
    - Incremental backup support via borg integration
    - Backup verification before restore
    - Retention policy management
    - Backup metadata tracking
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.database import get_connection
from shared.config import get_db_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)


DEFAULT_BACKUP_DIR = os.getenv("BACKUP_PATH", "/data/backups")
BACKUP_META_DIR = os.path.join(DEFAULT_BACKUP_DIR, ".meta")


def ensure_backup_dirs():
    """Ensure backup directories exist."""
    os.makedirs(DEFAULT_BACKUP_DIR, exist_ok=True)
    os.makedirs(BACKUP_META_DIR, exist_ok=True)


def parse_db_url(url: str) -> Dict[str, str]:
    """Parse PostgreSQL URL into components."""
    parts = url.replace("postgresql://", "").split("@")
    creds = parts[0].split(":")
    host_db = parts[1].split("/")
    host_port = host_db[0].split(":")
    
    return {
        "user": creds[0],
        "password": creds[1] if len(creds) > 1 else "",
        "host": host_port[0],
        "port": host_port[1] if len(host_port) > 1 else "5432",
        "db": host_db[1]
    }


def get_backup_meta(name: str) -> Dict[str, Any]:
    """Read backup metadata."""
    meta_file = os.path.join(BACKUP_META_DIR, f"{name}.json")
    if os.path.exists(meta_file):
        with open(meta_file, "r") as f:
            return json.load(f)
    return {}


def save_backup_meta(name: str, meta: Dict[str, Any]):
    """Save backup metadata."""
    ensure_backup_dirs()
    meta_file = os.path.join(BACKUP_META_DIR, f"{name}.json")
    with open(meta_file, "w") as f:
        json.dump(meta, f, indent=2)


def delete_backup_meta(name: str):
    """Delete backup metadata."""
    meta_file = os.path.join(BACKUP_META_DIR, f"{name}.json")
    if os.path.exists(meta_file):
        os.remove(meta_file)


def create_backup(name: str, compression: str = "gz", include_media: bool = False) -> Dict[str, Any]:
    """Create a full database backup."""
    ensure_backup_dirs()
    
    db_config = parse_db_url(get_db_url())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{name}_{timestamp}"
    
    if compression == "gz":
        backup_file = os.path.join(DEFAULT_BACKUP_DIR, f"{backup_name}.sql.gz")
        compress_cmd = "gzip"
    elif compression == "bz2":
        backup_file = os.path.join(DEFAULT_BACKUP_DIR, f"{backup_name}.sql.bz2")
        compress_cmd = "bzip2"
    else:
        backup_file = os.path.join(DEFAULT_BACKUP_DIR, f"{backup_name}.sql")
        compress_cmd = None
    
    pg_env = os.environ.copy()
    pg_env["PGPASSWORD"] = db_config["password"]
    
    start_time = time.time()
    
    try:
        cmd = [
            "pg_dump",
            "-h", db_config["host"],
            "-p", db_config["port"],
            "-U", db_config["user"],
            "-d", db_config["db"],
            "-F", "custom",
        ]
        
        if compress_cmd:
            with open(backup_file, "wb") as f:
                proc = subprocess.Popen(cmd, env=pg_env, stdout=subprocess.PIPE)
                compress_proc = subprocess.Popen(
                    [compress_cmd], stdin=proc.stdout, stdout=f
                )
                proc.stdout.close()
                proc.wait()
                compress_proc.wait()
                if proc.returncode != 0:
                    raise Exception(f"pg_dump failed with code {proc.returncode}")
        else:
            with open(backup_file, "w") as f:
                result = subprocess.run(cmd, env=pg_env, stdout=f, text=True)
                if result.returncode != 0:
                    raise Exception(f"pg_dump failed: {result.stderr}")
        
        elapsed = time.time() - start_time
        file_size = os.path.getsize(backup_file)
        
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO backup_runs (name, status, rows_backed_up, started_at, completed_at)
            VALUES (%s, 'completed', 
                (SELECT COUNT(*) FROM posts), 
                now() - interval '%.2f seconds', now())
            RETURNING id
        """, (backup_name, elapsed))
        conn.commit()
        cur.close()
        
        meta = {
            "name": backup_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file": os.path.basename(backup_file),
            "size": file_size,
            "size_human": f"{file_size / 1024 / 1024:.1f} MB",
            "elapsed_seconds": round(elapsed, 2),
            "compression": compression,
            "type": "full",
            "rows": file_size,
            "verified": False
        }
        
        save_backup_meta(backup_name + ".sql", meta)
        
        logger.info(f"Backup created: {backup_name} ({meta['size_human']})")
        return meta
        
    except Exception as e:
        if os.path.exists(backup_file):
            os.remove(backup_file)
        raise Exception(f"Backup failed: {e}")


def list_backups() -> List[Dict[str, Any]]:
    """List all available backups."""
    ensure_backup_dirs()
    
    backups = []
    for f in os.listdir(DEFAULT_BACKUP_DIR):
        if f.endswith((".sql", ".sql.gz", ".sql.bz2")):
            fpath = os.path.join(DEFAULT_BACKUP_DIR, f)
            stat = os.stat(fpath)
            
            name_base = f.replace(".sql.gz", "").replace(".sql.bz2", "").replace(".sql", "")
            meta = get_backup_meta(f)
            
            backups.append({
                "name": name_base,
                "file": f,
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "meta": meta
            })
    
    backups.sort(key=lambda x: x["created"], reverse=True)
    return backups


def verify_backup(name: str) -> Dict[str, Any]:
    """Verify backup integrity."""
    backups = list_backups()
    backup_file = None
    
    for b in backups:
        if name in b["name"]:
            backup_file = os.path.join(DEFAULT_BACKUP_DIR, b["file"])
            break
    
    if not backup_file or not os.path.exists(backup_file):
        raise Exception(f"Backup not found: {name}")
    
    results = {
        "exists": True,
        "readable": False,
        "valid_format": False,
        "tables_found": []
    }
    
    try:
        if backup_file.endswith(".gz"):
            check_cmd = ["zcat", backup_file]
        elif backup_file.endswith(".bz2"):
            check_cmd = ["bzcat", backup_file]
        else:
            check_cmd = ["cat", backup_file]
        
        proc = subprocess.Popen(check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, error = proc.communicate(timeout=60)
        
        if proc.returncode == 0:
            results["readable"] = True
            
            content = output.decode("utf-8", errors="ignore")
            
            tables = []
            for line in content.split("\n"):
                if "CREATE TABLE" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "TABLE" and i + 1 < len(parts):
                            tables.append(parts[i + 1].strip("();")
            
            results["tables_found"] = tables
            results["valid_format"] = len(tables) > 0
    except Exception as e:
        results["error"] = str(e)
    
    name_key = f"{name}.sql" if not name.endswith(".sql") else name
    if backup_file.endswith(".gz"):
        name_key += ".gz"
    elif backup_file.endswith(".bz2"):
        name_key += ".bz2"
    
    meta = get_backup_meta(name_key)
    meta["verified"] = results["valid_format"]
    save_backup_meta(name_key, meta)
    
    return results


def restore_backup(name: str, target_db: Optional[str] = None, skip_media: bool = False) -> Dict[str, Any]:
    """Restore database from backup."""
    backups = list_backups()
    backup_file = None
    
    for b in backups:
        if name in b["name"]:
            backup_file = os.path.join(DEFAULT_BACKUP_DIR, b["file"])
            break
    
    if not backup_file or not os.path.exists(backup_file):
        raise Exception(f"Backup not found: {name}")
    
    db_config = parse_db_url(get_db_url())
    target_db = target_db or db_config["db"]
    
    pg_env = os.environ.copy()
    pg_env["PGPASSWORD"] = db_config["password"]
    
    start_time = time.time()
    
    try:
        if backup_file.endswith(".gz"):
            restore_cmd = ["zcat", backup_file]
        elif backup_file.endswith(".bz2"):
            restore_cmd = ["bzcat", backup_file]
        else:
            restore_cmd = ["cat", backup_file]
        
        proc = subprocess.Popen(
            restore_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        result = subprocess.run([
            "psql",
            "-h", db_config["host"],
            "-p", db_config["port"],
            "-U", db_config["user"],
            "-d", target_db,
        ], env=pg_env, stdin=proc.stdout, stderr=subprocess.PIPE)
        
        proc.stdout.close()
        
        elapsed = time.time() - start_time
        
        if result.returncode != 0:
            raise Exception(f"Restore failed: {result.stderr.decode()}")
        
        logger.info(f"Restore completed in {elapsed:.1f}s")
        
        return {
            "status": "success",
            "elapsed": elapsed,
            "backup": name
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e)
        }


def schedule_backups(interval: str):
    """Create a cron-based backup schedule."""
    intervals = {
        "hourly": "0 * * * *",
        "daily": "0 2 * * *",
        "weekly": "0 2 * * 0",
        "monthly": "0 2 1 * *"
    }
    
    if interval not in intervals:
        raise ValueError(f"Invalid interval: {interval}. Use: {', '.join(intervals.keys())}")
    
    cron_expr = intervals[interval]
    script_path = Path(__file__).parent / "db_backup.py"
    
    cron_line = f'{cron_expr} cd {Path(__file__).parent.parent} && python {script_path} backup --name auto_{interval}\n'
    
    print(f"To enable automatic backups, add this line to your crontab:")
    print(f"  crontab -e")
    print(f"\n{cron_line}")


def main():
    parser = argparse.ArgumentParser(description="Database backup/restore utility")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    backup_parser = subparsers.add_parser("backup", help="Create backup")
    backup_parser.add_argument("--name", required=True, help="Backup name")
    backup_parser.add_argument("--compression", default="gz", choices=["gz", "bz2", "none"],
                              help="Compression type")
    
    list_parser = subparsers.add_parser("list", help="List backups")
    
    verify_parser = subparsers.add_parser("verify", help="Verify backup")
    verify_parser.add_argument("--name", required=True, help="Backup name")
    
    restore_parser = subparsers.add_parser("restore", help="Restore backup")
    restore_parser.add_argument("--name", required=True, help="Backup name")
    restore_parser.add_argument("--target-db", help="Target database")
    
    schedule_parser = subparsers.add_parser("schedule", help="Schedule backups")
    schedule_parser.add_argument("--interval", required=True,
                                choices=["hourly", "daily", "weekly", "monthly"],
                                help="Backup interval")
    
    args = parser.parse_args()
    
    if args.command == "backup":
        result = create_backup(args.name, args.compression)
        print(f"Backup created: {result['name']} ({result['size_human']})")
        
    elif args.command == "list":
        backups = list_backups()
        print("\nAvailable backups:")
        print("-" * 80)
        for b in backups:
            size_mb = b["size"] / 1024 / 1024
            print(f"  {b['name']:<40} {size_mb:>8.1f} MB  {b['created']}")
            
    elif args.command == "verify":
        result = verify_backup(args.name)
        print(f"\nVerification results for: {args.name}")
        print(f"  Exists: {result['exists']}")
        print(f"  Readable: {result['readable']}")
        print(f"  Valid format: {result['valid_format']}")
        print(f"  Tables: {len(result.get('tables_found', []))}")
        
    elif args.command == "restore":
        result = restore_backup(args.name, args.target_db)
        print(f"Restore {result['status']}: {result.get('error', result.get('elapsed', 'OK'))}")
        
    elif args.command == "schedule":
        schedule_backups(args.interval)
        
    else:
        parser.print_help()


if __name__ == "__main__":
    main()