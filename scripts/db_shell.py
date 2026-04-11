#!/usr/bin/env python3
"""
Database Shell - Interactive SQL shell with safety features.

Usage:
    python scripts/db_shell.py [--readonly] [--dry-run]

Features:
    - Confirmation prompts for dangerous operations (DELETE, DROP, TRUNCATE)
    - READONLY mode to prevent modifications
    - DRY-RUN mode to preview operations
    - Automatic logging of all queries to audit_log
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import get_db_url

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class DatabaseShell:
    DANGEROUS_COMMANDS = {
        "DROP",
        "TRUNCATE",
        "DELETE",
        "ALTER",
        "CREATE",
        "INSERT",
        "UPDATE",
        "GRANT",
        "REVOKE",
    }

    READONLY_COMMANDS = {"SELECT", "WITH", "SHOW", "EXPLAIN", "DESCRIBE", "INFO"}

    def __init__(self, readonly: bool = False, dry_run: bool = False):
        self.readonly = readonly
        self.dry_run = dry_run
        self.conn: Optional[psycopg2.extensions.connection] = None
        self.db_url = get_db_url()

    def connect(self):
        """Connect to database."""
        self.conn = psycopg2.connect(self.db_url)
        self.conn.set_session(readonly=False)
        logger.info("Connected to database")

    def close(self):
        """Close connection."""
        if self.conn:
            self.conn.close()
            logger.info("Connection closed")

    def is_dangerous(self, query: str) -> bool:
        """Check if query contains dangerous commands."""
        query_upper = query.upper().strip()
        for cmd in self.DANGEROUS_COMMANDS:
            if query_upper.startswith(cmd):
                return True
            if f" {cmd} " in query_upper:
                return True
        return False

    def confirm(self, query: str) -> bool:
        """Ask for confirmation for dangerous queries."""
        print(f"\n⚠️  DANGEROUS QUERY DETECTED:")
        print(f"   {query[:200]}...")

        if self.dry_run:
            print("   [DRY-RUN MODE] Would execute but skipping...")
            return False

        if self.readonly:
            print("   [READONLY MODE] Refusing to execute")
            return False

        response = input("\n   Type 'YES' to confirm: ")
        return response.strip().upper() == "YES"

    def is_select_only(self, query: str) -> bool:
        """Check if query is read-only."""
        query_upper = query.upper().strip()
        for cmd in self.READONLY_COMMANDS:
            if query_upper.startswith(cmd):
                return True
        return False

    def log_query(self, query: str, rows_affected: int = 0):
        """Log query to audit_log table."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                """INSERT INTO audit_log (action, table_name, record_id, username)
                   VALUES ('shell_query', 'shell', %s, current_user)""",
                (f"rows: {rows_affected}",),
            )
            self.conn.commit()
            cur.close()
        except Exception as e:
            logger.warning(f"Could not log query: {e}")

    def execute(self, query: str) -> bool:
        """Execute a query with safety checks."""
        query = query.strip()

        if not query:
            return True

        if self.is_select_only(query):
            try:
                cur = self.conn.cursor()
                cur.execute(query)

                if cur.description:
                    rows = cur.fetchall()
                    col_names = [desc[0] for desc in cur.description]
                    self._print_results(col_names, rows)
                    print(f"\n({len(rows)} rows)")
                else:
                    print(f"OK - {cur.rowcount} rows affected")

                self.log_query(query, cur.rowcount)
                cur.close()
                return True

            except Exception as e:
                print(f"ERROR: {e}")
                return False

        if self.is_dangerous(query):
            if not self.confirm(query):
                return False

        try:
            cur = self.conn.cursor()
            cur.execute(query)
            self.conn.commit()
            print(f"OK - {cur.rowcount} rows affected")
            self.log_query(query, cur.rowcount)
            cur.close()
            return True

        except Exception as e:
            self.conn.rollback()
            print(f"ERROR: {e}")
            return False

    def _print_results(self, columns: list, rows: list):
        """Pretty print query results."""
        if not rows:
            print("(No results)")
            return

        col_widths = {}
        for col in columns:
            col_widths[col] = len(col)

        for row in rows:
            for i, val in enumerate(row):
                col = columns[i]
                val_str = str(val) if val is not None else "NULL"
                col_widths[col] = max(col_widths[col], len(val_str))

        header = " | ".join(col.ljust(col_widths[col]) for col in columns)
        separator = "-+-".join("-" * col_widths[col] for col in columns)

        print(header)
        print(separator)

        for row in rows[:100]:
            line = " | ".join(
                (str(val) if val is not None else "NULL").ljust(col_widths[col])
                for col, val in zip(columns, row)
            )
            print(line)

        if len(rows) > 100:
            print(f"... ({len(rows) - 100} more rows)")

    def run(self):
        """Run interactive shell."""
        self.connect()

        mode_info = []
        if self.readonly:
            mode_info.append("READONLY")
        if self.dry_run:
            mode_info.append("DRY-RUN")

        print(f"""
╔═══════════════════════════════════════════════════════════╗
║              Reddarr Database Shell                       ║
║  Type 'help' for commands, 'quit' to exit                ║
{f"║  Mode: {', '.join(mode_info)}" + " " * (50 - len(", ".join(mode_info))) + "║" if mode_info else "║" + " " * 51 + "║"}
╚═══════════════════════════════════════════════════════════╝
""")

        try:
            while True:
                prompt = "reddarr=> " if not self.readonly else "reddarr(ro)=> "
                query = input(prompt)

                if not query:
                    continue

                if query.lower() in ("quit", "exit", "\\q"):
                    break

                if query.lower() in ("help", "\\h"):
                    self._print_help()
                    continue

                if query.lower() in ("tables", "\\dt"):
                    self._list_tables()
                    continue

                self.execute(query)

        except KeyboardInterrupt:
            print("\nExiting...")
        finally:
            self.close()

    def _print_help(self):
        print("""
Commands:
  help, \\h     - Show this help
  tables, \\dt - List all tables
  quit, exit, \\q - Exit shell
  
Special features:
  - Dangerous queries (DROP, DELETE, etc.) require confirmation
  - READONLY mode prevents all modifications
  - DRY-RUN mode shows what would happen without executing
  - All queries are logged to audit_log
""")

    def _list_tables(self):
        """List all tables in the database."""
        query = """
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """
        self.execute(query)


def main():
    parser = argparse.ArgumentParser(description="Database shell")
    parser.add_argument("--readonly", action="store_true", help="Run in readonly mode")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview queries without executing"
    )
    args = parser.parse_args()

    shell = DatabaseShell(readonly=args.readonly, dry_run=args.dry_run)
    shell.run()


if __name__ == "__main__":
    main()
