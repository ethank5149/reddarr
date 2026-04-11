#!/usr/bin/env python3
"""
Database Diagnostic Script - Comprehensive health check.

Usage:
    python scripts/db_diag.py [--full] [--json]

Outputs:
    - Connection status
    - Schema validation
    - Index usage statistics
    - Table sizes
    - Integrity check results
    - Missing indexes detection
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.database import get_connection
from shared.backup import (
    verify_media_integrity,
    verify_posts_history_audit,
    get_audit_stats,
    get_database_size,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class DiagnosticReport:
    def __init__(self):
        self.results: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
        }

    def add_check(self, name: str, status: str, details: Any = None):
        self.results["checks"][name] = {
            "status": status,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def print_summary(self):
        print("\n" + "=" * 60)
        print("DATABASE DIAGNOSTIC REPORT")
        print("=" * 60)

        passed = 0
        failed = 0
        warnings = 0

        for name, check in self.results["checks"].items():
            status = check["status"]
            icon = "✓" if status == "pass" else "✗" if status == "fail" else "⚠"

            if status == "pass":
                passed += 1
            elif status == "fail":
                failed += 1
            else:
                warnings += 1

            print(f"  {icon} {name}: {status.upper()}")

            if check["details"] and isinstance(check["details"], dict):
                for k, v in check["details"].items():
                    print(f"      {k}: {v}")
            elif check["details"]:
                print(f"      {check['details']}")

        print("-" * 60)
        print(f"  PASSED: {passed}  FAILED: {failed}  WARNINGS: {warnings}")
        print("=" * 60)

    def to_json(self) -> str:
        return json.dumps(self.results, indent=2, default=str)


def check_connection() -> Dict[str, Any]:
    """Check database connectivity."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        cur.execute("SELECT current_database()")
        db_name = cur.fetchone()[0]
        cur.close()

        return {
            "status": "connected",
            "database": db_name,
            "version": version.split(",")[0],
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def check_schema_integrity(report: DiagnosticReport):
    """Verify schema structure."""
    required_tables = [
        "posts",
        "comments",
        "media",
        "targets",
        "users",
        "posts_history",
        "comments_history",
        "audit_log",
        "backup_runs",
        "integrity_checks",
        "schema_version",
    ]

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    existing = {row[0] for row in cur.fetchall()}

    missing = [t for t in required_tables if t not in existing]
    extra = list(existing - set(required_tables))

    cur.close()

    if missing:
        report.add_check("schema_tables", "fail", {"missing": missing})
    else:
        report.add_check("schema_tables", "pass", {"found": len(required_tables)})


def check_indexes(report: DiagnosticReport):
    """Analyze index usage and health."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            schemaname,
            tablename,
            indexname,
            idx_scan,
            idx_tup_read,
            idx_tup_fetch,
            pg_size_pretty(pg_relation_size(indexrelid)) as index_size
        FROM pg_stat_user_indexes
        WHERE schemaname = 'public'
        ORDER BY pg_relation_size(indexrelid) DESC
        LIMIT 20
    """)

    indexes = []
    total_scans = 0
    unused_indexes = []

    for row in cur.fetchall():
        scans = row[3] or 0
        total_scans += scans
        indexes.append(
            {"table": row[1], "index": row[2], "scans": scans, "size": row[6]}
        )
        if scans == 0:
            unused_indexes.append(row[2])

    cur.close()

    report.add_check(
        "indexes",
        "pass",
        {
            "total": len(indexes),
            "total_scans": total_scans,
            "unused": len(unused_indexes),
            "unused_names": unused_indexes[:10],
        },
    )


def check_foreign_keys(report: DiagnosticReport):
    """Verify foreign key constraints."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            tc.table_name,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name,
            rc.delete_rule
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
        JOIN information_schema.referential_constraints AS rc
            ON rc.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' 
            AND tc.table_schema = 'public'
    """)

    fks = []
    for row in cur.fetchall():
        fks.append(
            {
                "table": row[0],
                "column": row[1],
                "references": f"{row[2]}.{row[3]}",
                "on_delete": row[4],
            }
        )

    cur.close()

    report.add_check("foreign_keys", "pass", {"total": len(fks), "fks": fks})


def check_constraints(report: DiagnosticReport):
    """Check constraint violations."""
    conn = get_connection()
    cur = conn.cursor()

    issues = []

    cur.execute("""
        SELECT COUNT(*) FROM posts 
        WHERE created_utc > now() + interval '1 day'
    """)
    future_posts = cur.fetchone()[0]
    if future_posts > 0:
        issues.append(f"posts: {future_posts} rows with future created_utc")

    cur.execute("""
        SELECT COUNT(*) FROM media WHERE retries < 0
    """)
    negative_retries = cur.fetchone()[0]
    if negative_retries > 0:
        issues.append(f"media: {negative_retries} rows with negative retries")

    cur.execute("""
        SELECT status, COUNT(*) FROM targets 
        WHERE status NOT IN ('active', 'taken_down', 'deleted')
        GROUP BY status
    """)
    invalid_targets = cur.fetchall()
    if invalid_targets:
        for row in invalid_targets:
            issues.append(f"targets: {row[1]} rows with invalid status '{row[0]}'")

    cur.close()

    if issues:
        report.add_check("constraints", "fail", {"violations": issues})
    else:
        report.add_check("constraints", "pass", {"all_valid": True})


def check_data_integrity(report: DiagnosticReport, full: bool = False):
    """Run integrity checks."""
    try:
        media_report = verify_media_integrity()

        if media_report["verified"]:
            report.add_check(
                "media_integrity", "pass", {"total_media": media_report["total_media"]}
            )
        else:
            report.add_check(
                "media_integrity",
                "fail",
                {
                    "missing": len(media_report["missing_files"]),
                    "mismatches": len(media_report["hash_mismatches"]),
                },
            )

        audit_report = verify_posts_history_audit()

        if not audit_report["issues"]:
            report.add_check(
                "audit_trail",
                "pass",
                {
                    "posts": audit_report["posts_count"],
                    "history": audit_report["history_count"],
                },
            )
        else:
            report.add_check(
                "audit_trail",
                "fail",
                {"issues": [i["type"] for i in audit_report["issues"]]},
            )

    except Exception as e:
        report.add_check("data_integrity", "fail", {"error": str(e)})


def check_table_sizes(report: DiagnosticReport):
    """Report on table sizes."""
    try:
        sizes = get_database_size()
        report.add_check("table_sizes", "pass", sizes)
    except Exception as e:
        report.add_check("table_sizes", "fail", {"error": str(e)})


def check_orphaned_records(report: DiagnosticReport):
    """Find orphaned records."""
    conn = get_connection()
    cur = conn.cursor()

    issues = []

    cur.execute("""
        SELECT COUNT(*) FROM comments c
        WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.id = c.post_id)
    """)
    orphaned_comments = cur.fetchone()[0]
    if orphaned_comments > 0:
        issues.append(f"comments without posts: {orphaned_comments}")

    cur.execute("""
        SELECT COUNT(*) FROM media m
        WHERE NOT EXISTS (SELECT 1 FROM posts p WHERE p.id = m.post_id)
    """)
    orphaned_media = cur.fetchone()[0]
    if orphaned_media > 0:
        issues.append(f"media without posts: {orphaned_media}")

    cur.execute("""
        SELECT COUNT(*) FROM posts p
        WHERE subreddit IS NOT NULL 
        AND NOT EXISTS (SELECT 1 FROM targets t WHERE t.name = p.subreddit)
    """)
    posts_no_target = cur.fetchone()[0]
    if posts_no_target > 0:
        issues.append(f"posts without matching target: {posts_no_target}")

    cur.close()

    if issues:
        report.add_check("orphaned_records", "warning", {"found": issues})
    else:
        report.add_check("orphaned_records", "pass", {"none_found": True})


def check_vacuum_status(report: DiagnosticReport):
    """Check table vacuum/analyze status."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            schemaname || '.' || tablename as table_name,
            last_vacuum,
            last_autovacuum,
            last_analyze,
            last_autoanalyze,
            n_dead_tup,
            n_live_tup,
            CASE 
                WHEN n_live_tup > 0 
                THEN round(100.0 * n_dead_tup / n_live_tup, 2)
                ELSE 0 
            END as dead_tuple_percent
        FROM pg_stat_user_tables
        WHERE schemaname = 'public'
        ORDER BY dead_tuple_percent DESC
    """)

    tables = []
    needs_vacuum = []

    for row in cur.fetchall():
        tables.append(
            {
                "table": row[0],
                "dead_percent": row[7],
                "dead_tuples": row[5],
                "last_vacuum": str(row[1]) if row[1] else "never",
                "last_analyze": str(row[3]) if row[3] else "never",
            }
        )

        if row[5] and row[5] > 1000:
            needs_vacuum.append(row[0])

    cur.close()

    if needs_vacuum:
        report.add_check("vacuum_status", "warning", {"needs_vacuum": needs_vacuum})
    else:
        report.add_check("vacuum_status", "pass", {"tables_checked": len(tables)})


def check_audit_log_stats(report: DiagnosticReport):
    """Check audit log statistics."""
    try:
        stats = get_audit_stats()
        report.add_check("audit_stats", "pass", stats)
    except Exception as e:
        report.add_check("audit_stats", "fail", {"error": str(e)})


def run_full_diagnostics(full: bool = False) -> DiagnosticReport:
    """Run all diagnostic checks."""
    report = DiagnosticReport()

    logger.info("Checking database connection...")
    conn_check = check_connection()
    if conn_check["status"] == "connected":
        report.add_check("connection", "pass", conn_check)
    else:
        report.add_check("connection", "fail", conn_check)
        return report

    logger.info("Checking schema integrity...")
    check_schema_integrity(report)

    logger.info("Checking indexes...")
    check_indexes(report)

    logger.info("Checking foreign keys...")
    check_foreign_keys(report)

    logger.info("Checking constraints...")
    check_constraints(report)

    if full:
        logger.info("Checking orphaned records...")
        check_orphaned_records(report)

        logger.info("Checking vacuum status...")
        check_vacuum_status(report)

        logger.info("Checking data integrity...")
        check_data_integrity(report, full=True)

        logger.info("Checking audit stats...")
        check_audit_log_stats(report)

    logger.info("Checking table sizes...")
    check_table_sizes(report)

    return report


def main():
    parser = argparse.ArgumentParser(description="Database diagnostics")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run full diagnostic including integrity checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    report = run_full_diagnostics(full=args.full)

    if args.json:
        print(report.to_json())
    else:
        report.print_summary()

    failed = sum(1 for c in report.results["checks"].values() if c["status"] == "fail")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
