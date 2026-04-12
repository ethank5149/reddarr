"""Database operations for ingester.

Provides thread-local database connections with automatic reconnection.
"""

import os
import threading
import psycopg2
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_URL = os.getenv("DB_URL")

_tls = threading.local()


def get_db():
    """Return a live DB connection for the current thread.

    Uses a thread-local connection so that parallel backfill workers never
    share a single psycopg2 connection (which is not thread-safe).
    """
    if not hasattr(_tls, "conn") or _tls.conn is None:
        _tls.conn = psycopg2.connect(DB_URL)
        logger.debug("Thread-local DB connection created")
        return _tls.conn

    try:
        with _tls.conn.cursor() as cur:
            cur.execute("SELECT 1")
        return _tls.conn
    except Exception:
        logger.warning("DB connection lost in thread, reconnecting...")
        try:
            _tls.conn.close()
        except Exception:
            pass
        _tls.conn = psycopg2.connect(DB_URL)
        logger.info("DB reconnected in thread")
        return _tls.conn


def get_connection():
    """Context manager for thread-local connection."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_cursor():
    """Context manager for thread-local cursor."""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def close():
    """Close the thread-local connection if any."""
    if hasattr(_tls, "conn") and _tls.conn is not None:
        try:
            _tls.conn.close()
        except Exception:
            pass
        _tls.conn = None


def init_connection():
    """Initial connection for main thread."""
    global _tls
    _tls.conn = psycopg2.connect(DB_URL)
    return _tls.conn
