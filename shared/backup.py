"""
Backup, Restore, and Integrity Module for Reddarr

Provides:
- Partial backup of specific tables or date ranges
- Restore from backups with verification
- Data integrity checking (SHA-256 verification, missing file detection)
- Audit trail utilities

Usage:
    from shared.backup import (
        backup_tables, restore_backup, verify_integrity,
        get_audit_stats, check_media_files
    )
"""

import hashlib
import json
import logging
import os
import tarfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .database import get_connection
from .config import get_db_url

logger = logging.getLogger(__name__)


@contextmanager
def get_backup_cursor():
    """Get a cursor suitable for backup/restore operations."""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def get_table_row_count(table: str) -> int:
    """Get the total row count for a table."""
    with get_backup_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def get_table_columns(table: str) -> List[str]:
    """Get column names for a table."""
    with get_backup_cursor() as cur:
        cur.execute(
            """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = %s AND table_schema = 'public'
            ORDER BY ordinal_position
        """,
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def export_table(
    table: str,
    output_path: Path,
    where_clause: Optional[str] = None,
    limit: Optional[int] = None,
) -> int:
    """Export a table to JSONL format."""
    columns = get_table_columns(table)

    query = f"SELECT {','.join(columns)} FROM {table}"

    if where_clause:
        query += f" WHERE {where_clause}"

    if limit:
        query += f" LIMIT {limit}"

    with get_backup_cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

    with open(output_path, "w") as f:
        for row in rows:
            record = dict(zip(columns, row))
            f.write(json.dumps(record) + "\n")

    return len(rows)


def import_table(
    table: str,
    input_path: Path,
    conflict_action: str = "update",
) -> int:
    """Import data from JSONL format."""
    imported = 0

    with get_backup_cursor() as cur:
        for line in open(input_path):
            if not line.strip():
                continue
            record = json.loads(line)
            cols = list(record.keys())
            vals = list(record.values())

            placeholders = ",".join(["%s"] * len(cols))
            col_names = ",".join(f'"{c}"' for c in cols)

            if conflict_action == "update":
                set_clause = ",".join(f'"{c}=EXCLUDED.{c}' for c in cols if c != "id")
                query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO UPDATE SET {set_clause}"
            elif conflict_action == "skip":
                query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            else:
                query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            cur.execute(query, vals)
            imported += 1

    return imported


def backup_media_directory(
    media_dir: str,
    output_path: Path,
    include_thumbs: bool = False,
) -> Tuple[int, int]:
    """Create a tar archive of media files."""
    media_path = Path(media_dir)
    excluded_dirs = {".thumbs"} if not include_thumbs else set()

    file_count = 0
    total_bytes = 0

    with tarfile.open(output_path, "w:gz") as tar:
        for root, dirs, files in os.walk(media_path):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]

            for fname in files:
                fpath = Path(root) / fname
                try:
                    tar.add(fpath, arcname=fpath.relative_to(media_path))
                    file_count += 1
                    total_bytes += fpath.stat().st_size
                except (OSError, IOError) as e:
                    logger.warning(f"Could not archive {fpath}: {e}")

    return file_count, total_bytes


def create_partial_backup(
    output_dir: Path,
    tables: Optional[List[str]] = None,
    subreddits: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    include_media: bool = True,
    media_dir: str = "/data",
) -> Dict[str, Any]:
    """Create a partial backup of the database."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if tables is None:
        tables = [
            "posts",
            "comments",
            "media",
            "targets",
            "posts_history",
            "comments_history",
        ]

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "media": None,
    }

    where_parts = []
    if subreddits:
        subreddit_filter = " OR ".join(f"subreddit = '{s}'" for s in subreddits)
        where_parts.append(f"({subreddit_filter})")

    if date_from:
        where_parts.append(f"created_utc >= '{date_from.isoformat()}'")

    if date_to:
        where_parts.append(f"created_utc <= '{date_to.isoformat()}'")

    where_clause = " AND ".join(where_parts) if where_parts else None

    for table in tables:
        output_file = output_dir / f"{table}.jsonl"
        logger.info(f"Backing up table: {table}")

        row_count = export_table(
            table,
            output_file,
            where_clause=where_clause if table in ("posts", "comments") else None,
        )

        metadata["tables"][table] = {
            "file": str(output_file.name),
            "rows": row_count,
        }
        logger.info(f"  Exported {row_count} rows")

    if include_media:
        media_tar = output_dir / "media.tar.gz"
        logger.info("Backing up media files...")
        file_count, total_bytes = backup_media_directory(media_dir, media_tar)
        metadata["media"] = {
            "file": str(media_tar.name),
            "files": file_count,
            "bytes": total_bytes,
        }
        logger.info(f"  Archived {file_count} files ({total_bytes:,} bytes)")

    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def restore_partial_backup(
    backup_dir: Path,
    tables: Optional[List[str]] = None,
    restore_media: bool = True,
    media_dir: str = "/data",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Restore from a partial backup."""
    backup_dir = Path(backup_dir)

    with open(backup_dir / "metadata.json") as f:
        metadata = json.load(f)

    if tables is None:
        tables = list(metadata["tables"].keys())

    results = {}

    for table in tables:
        if table not in metadata["tables"]:
            logger.warning(f"Table {table} not in backup, skipping")
            continue

        input_file = backup_dir / metadata["tables"][table]["file"]
        if not input_file.exists():
            logger.warning(f"Backup file not found: {input_file}, skipping")
            continue

        logger.info(f"Restoring table: {table}")

        if dry_run:
            logger.info(f"  [DRY RUN] Would import {input_file}")
            results[table] = {"action": "dry_run", "file": str(input_file)}
        else:
            row_count = import_table(table, input_file, conflict_action="update")
            results[table] = {"imported": row_count}
            logger.info(f"  Imported {row_count} rows")

    if restore_media and metadata.get("media"):
        media_tar = backup_dir / metadata["media"]["file"]
        if media_tar.exists():
            logger.info("Restoring media files...")

            if dry_run:
                logger.info(f"  [DRY RUN] Would extract {media_tar}")
            else:
                with tarfile.open(media_tar, "r:gz") as tar:
                    tar.extractall(media_dir)

            results["media"] = {"restored": not dry_run}
            logger.info("  Media restored")

    return results


def compute_file_hash(path: str) -> Optional[str]:
    """Compute SHA-256 hash of a file."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(131072), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError) as e:
        logger.warning(f"Could not hash {path}: {e}")
        return None


def verify_media_integrity() -> Dict[str, Any]:
    """Verify integrity of all media files."""
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_media": 0,
        "total_files": 0,
        "missing_files": [],
        "hash_mismatches": [],
        "orphan_files": [],
        "verified": True,
    }

    db_files: Dict[str, Tuple[str, str]] = {}

    with get_backup_cursor() as cur:
        cur.execute("""
            SELECT id, file_path, sha256 
            FROM media 
            WHERE status = 'done' AND file_path IS NOT NULL
        """)
        for row in cur.fetchall():
            db_files[row[1]] = (row[0], row[2])

    report["total_media"] = len(db_files)

    missing = []
    mismatches = []

    for fpath, (media_id, expected_hash) in db_files.items():
        if not fpath or fpath == "None":
            continue

        if not os.path.exists(fpath):
            missing.append({"media_id": media_id, "path": fpath})
            report["verified"] = False
            continue

        actual_hash = compute_file_hash(fpath)
        if actual_hash and expected_hash and actual_hash != expected_hash:
            mismatches.append(
                {
                    "media_id": media_id,
                    "path": fpath,
                    "expected": expected_hash,
                    "actual": actual_hash,
                }
            )
            report["verified"] = False

    report["missing_files"] = missing
    report["hash_mismatches"] = mismatches

    db_paths = set(db_files.keys())
    all_media_dirs = set()

    for fpath in db_paths:
        all_media_dirs.add(os.path.dirname(fpath))

    orphans = []
    for mdir in all_media_dirs:
        if os.path.exists(mdir):
            for root, _, files in os.walk(mdir):
                for fname in files:
                    full_path = os.path.join(root, fname)
                    if full_path not in db_paths:
                        orphans.append(full_path)

    report["orphan_files"] = orphans
    report["total_files"] = len(db_files) + len(orphans)

    logger.info(
        f"Integrity check: {len(missing)} missing, {len(mismatches)} mismatches, {len(orphans)} orphans"
    )

    return report


def verify_posts_history_audit() -> Dict[str, Any]:
    """Verify the audit trail for posts_history."""
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "posts_count": 0,
        "history_count": 0,
        "issues": [],
    }

    with get_backup_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM posts")
        report["posts_count"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM posts_history")
        report["history_count"] = cur.fetchone()[0]

        cur.execute("""
            SELECT p.id 
            FROM posts p
            LEFT JOIN posts_history ph ON p.id = ph.post_id
            WHERE ph.id IS NULL
        """)
        no_history = [row[0] for row in cur.fetchall()]

        if no_history:
            report["issues"].append(
                {
                    "type": "posts_without_history",
                    "count": len(no_history),
                    "post_ids": no_history[:100],
                }
            )

        cur.execute("""
            SELECT post_id, MAX(version) as max_ver, COUNT(*) as ver_count
            FROM posts_history
            GROUP BY post_id
            HAVING MAX(version) != COUNT(*)
        """)
        non_contiguous = [
            {"post_id": row[0], "max_version": row[1], "count": row[2]}
            for row in cur.fetchall()
        ]

        if non_contiguous:
            report["issues"].append(
                {
                    "type": "non_contiguous_versions",
                    "count": len(non_contiguous),
                    "details": non_contiguous[:100],
                }
            )

    return report


def check_media_files(
    status: Optional[str] = None,
    limit: int = 100,
) -> Iterator[Dict[str, Any]]:
    """Iterate over media files with their status."""
    with get_backup_cursor() as cur:
        query = """
            SELECT id, post_id, url, file_path, thumb_path, 
                   sha256, downloaded_at, status, retries, error_message
            FROM media
        """

        if status:
            query += " WHERE status = %s"
            cur.execute(query + " LIMIT %s", (status, limit))
        else:
            cur.execute(query + " LIMIT %s", (limit,))

        for row in cur.fetchall():
            yield {
                "id": row[0],
                "post_id": row[1],
                "url": row[2],
                "file_path": row[3],
                "thumb_path": row[4],
                "sha256": row[5],
                "downloaded_at": row[6].isoformat() if row[6] else None,
                "status": row[7],
                "retries": row[8],
                "error_message": row[9],
            }


def get_audit_stats() -> Dict[str, Any]:
    """Get audit trail statistics."""
    stats = {
        "posts_history": {},
        "comments_history": {},
    }

    with get_backup_cursor() as cur:
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT post_id) as unique_posts,
                MIN(captured_at) as oldest,
                MAX(captured_at) as newest
            FROM posts_history
        """)
        row = cur.fetchone()
        stats["posts_history"] = {
            "total_entries": row[0],
            "unique_posts": row[1],
            "oldest_entry": row[2].isoformat() if row[2] else None,
            "newest_entry": row[3].isoformat() if row[3] else None,
        }

        cur.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT comment_id) as unique_comments,
                MIN(captured_at) as oldest,
                MAX(captured_at) as newest
            FROM comments_history
        """)
        row = cur.fetchone()
        stats["comments_history"] = {
            "total_entries": row[0],
            "unique_comments": row[1],
            "oldest_entry": row[2].isoformat() if row[2] else None,
            "newest_entry": row[3].isoformat() if row[3] else None,
        }

        cur.execute("""
            SELECT status, COUNT(*) 
            FROM media 
            GROUP BY status
        """)
        stats["media_by_status"] = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute("SELECT COUNT(*) FROM posts WHERE hidden = true")
        stats["hidden_posts"] = cur.fetchone()[0]

    return stats


def vacuum_analyze(tables: Optional[List[str]] = None) -> None:
    """Run VACUUM ANALYZE on specified tables."""
    if tables is None:
        tables = [
            "posts",
            "comments",
            "media",
            "targets",
            "posts_history",
            "comments_history",
        ]

    with get_backup_cursor() as cur:
        for table in tables:
            cur.execute(f"VACUUM ANALYZE {table}")
            logger.info(f"Vacuumed and analyzed: {table}")


def get_database_size() -> Dict[str, Any]:
    """Get database size information."""
    sizes = {}

    with get_backup_cursor() as cur:
        cur.execute("""
            SELECT 
                pg_size_pretty(pg_database_size(current_database())),
                pg_database_size(current_database())
        """)
        row = cur.fetchone()
        sizes["database"] = {
            "pretty": row[0],
            "bytes": row[1],
        }

        for table in [
            "posts",
            "comments",
            "media",
            "posts_history",
            "comments_history",
        ]:
            cur.execute(f"SELECT pg_size_pretty(pg_total_relation_size('{table}'))")
            row = cur.fetchone()
            sizes[table] = row[0]

    return sizes


class BackupManager:
    """Manager class for backup operations."""

    def __init__(self, backup_root: str = "/backups"):
        self.backup_root = Path(backup_root)

    def create_backup(
        self,
        name: str,
        tables: Optional[List[str]] = None,
        subreddits: Optional[List[str]] = None,
        include_media: bool = True,
        media_dir: str = "/data",
    ) -> Path:
        """Create a named backup."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"{name}_{timestamp}"
        backup_dir = self.backup_root / backup_name

        create_partial_backup(
            backup_dir,
            tables=tables,
            subreddits=subreddits,
            include_media=include_media,
            media_dir=media_dir,
        )

        return backup_dir

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all backups."""
        backups = []

        if not self.backup_root.exists():
            return backups

        for backup_dir in sorted(self.backup_root.iterdir()):
            if not backup_dir.is_dir():
                continue

            metadata_file = backup_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file) as f:
                    backups.append(json.load(f))

        return backups

    def restore_backup(
        self,
        name: str,
        tables: Optional[List[str]] = None,
        restore_media: bool = True,
        media_dir: str = "/data",
    ) -> Dict[str, Any]:
        """Restore a named backup."""
        backup_dir = None

        for d in sorted(self.backup_root.iterdir()):
            if d.is_dir() and name in d.name:
                backup_dir = d
                break

        if not backup_dir:
            raise ValueError(f"Backup not found: {name}")

        return restore_partial_backup(
            backup_dir,
            tables=tables,
            restore_media=restore_media,
            media_dir=media_dir,
        )
