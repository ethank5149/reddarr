#!/usr/bin/env python3
"""Import targets from targets.txt into the database.

Usage (from repo root inside the container):
    python scripts/import_targets.py [--targets-file /path/to/targets.txt]
"""

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Import targets.txt into the DB")
    parser.add_argument(
        "--targets-file",
        default="targets.txt",
        help="Path to targets.txt (default: targets.txt in cwd)",
    )
    args = parser.parse_args()

    targets_path = Path(args.targets_file)
    if not targets_path.exists():
        print(f"ERROR: {targets_path} not found", file=sys.stderr)
        sys.exit(1)

    from reddarr.database import init_engine, SessionLocal
    from reddarr.models import Target

    init_engine()

    lines = targets_path.read_text().splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        kind, _, name = line.partition(":")
        kind = kind.strip().lower()
        name = name.strip()
        if kind not in ("subreddit", "user") or not name:
            print(f"  SKIP unrecognised line: {line!r}")
            continue
        entries.append((kind, name))

    if not entries:
        print("No entries found in targets.txt")
        return

    added = skipped = 0
    with SessionLocal() as db:
        for kind, name in entries:
            exists = db.query(Target).filter_by(name=name).first()
            if exists:
                skipped += 1
                continue
            db.add(Target(type=kind, name=name, enabled=True, status="active"))
            added += 1
        db.commit()

    print(f"Done: {added} added, {skipped} already existed (skipped)")


if __name__ == "__main__":
    main()
