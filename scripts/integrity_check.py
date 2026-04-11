#!/usr/bin/env python3
"""
Integrity Check Script

Runs data integrity checks and logs results to the database.

Usage:
    python scripts/integrity_check.py [--media] [--audit] [--all]
    python scripts/integrity_check.py --media  # Check media files only
    python scripts/integrity_check.py --audit  # Check audit trail only
    python scripts/integrity_check.py --all     # Run all checks
"""

import argparse
import logging
import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.backup import (
    verify_media_integrity,
    verify_posts_history_audit,
    get_audit_stats,
)
from shared.database import get_connection
from shared.config import get_db_url

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def log_check(
    check_type: str, status: str, total: int = 0, issues: int = 0, details: dict = None
):
    """Log a check result to the database."""
    conn = get_connection()
    cur = conn.cursor()

    if status == "started":
        cur.execute(
            """INSERT INTO integrity_checks (check_type, status, total_items)
               VALUES (%s, %s, %s) RETURNING id""",
            (check_type, status, total),
        )
    else:
        cur.execute(
            """UPDATE integrity_checks 
               SET status = %s, total_items = %s, issues_found = %s, 
                   details = %s, completed_at = %s
               WHERE status = 'started' AND check_type = %s
               RETURNING id""",
            (
                status,
                total,
                issues,
                json.dumps(details),
                datetime.now(timezone.utc),
                check_type,
            ),
        )

    conn.commit()
    cur.close()


def check_media(args):
    """Check media file integrity."""
    logger.info("Starting media integrity check...")

    report = verify_media_integrity()

    logger.info(f"Total media: {report['total_media']}")
    logger.info(f"Missing files: {len(report['missing_files'])}")
    logger.info(f"Hash mismatches: {len(report['hash_mismatches'])}")
    logger.info(f"Orphan files: {len(report['orphan_files'])}")
    logger.info(f"Verified: {report['verified']}")

    if not report["verified"]:
        logger.warning("INTEGRITY ISSUES DETECTED!")
        if report["missing_files"]:
            logger.warning(f"  Missing: {report['missing_files'][:5]}")
        if report["hash_mismatches"]:
            logger.warning(f"  Mismatches: {report['hash_mismatches'][:5]}")

    return report


def check_audit(args):
    """Check audit trail integrity."""
    logger.info("Starting audit trail check...")

    report = verify_posts_history_audit()

    logger.info(f"Posts count: {report['posts_count']}")
    logger.info(f"History count: {report['history_count']}")
    logger.info(f"Issues: {len(report['issues'])}")

    for issue in report["issues"]:
        logger.warning(f"  {issue['type']}: {issue['count']}")

    return report


def report_stats(args):
    """Report audit statistics."""
    logger.info("Getting audit statistics...")

    stats = get_audit_stats()

    for key, val in stats.items():
        logger.info(f"  {key}: {val}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Integrity check script")
    parser.add_argument("--media", action="store_true", help="Check media files")
    parser.add_argument("--audit", action="store_true", help="Check audit trail")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    parser.add_argument("--all", action="store_true", help="Run all checks")

    args = parser.parse_args()

    if not any([args.media, args.audit, args.stats, args.all]):
        parser.print_help()
        return

    if args.all:
        args.media = True
        args.audit = True
        args.stats = True

    results = {}

    if args.media:
        results["media"] = check_media(args)

    if args.audit:
        results["audit"] = check_audit(args)

    if args.stats:
        results["stats"] = report_stats(args)

    logger.info("Integrity check complete")


if __name__ == "__main__":
    main()
