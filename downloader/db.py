"""Database operations for downloader.

Provides thread-local database connections and utilities.
"""

import os
import psycopg2
import logging
import threading

logger = logging.getLogger(__name__)

_DB_URL = os.getenv("DB_URL")
_tls = threading.local()


def get_db():
    """Get a thread-local database connection."""
    if not hasattr(_tls, "conn") or _tls.conn is None:
        _tls.conn = psycopg2.connect(_DB_URL)
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
        _tls.conn = psycopg2.connect(_DB_URL)
        logger.info("DB reconnected in thread")
        return _tls.conn


def wait_for_db(max_attempts: int = 10, delay: int = 3):
    """Wait for database connection with retry."""
    for attempt in range(max_attempts):
        try:
            _tls.conn = psycopg2.connect(_DB_URL)
            logger.info("DB initial connection established")
            return True
        except Exception as e:
            logger.warning(
                f"DB connection attempt {attempt + 1}/{max_attempts} failed: {e}"
            )
            import time

            time.sleep(delay)
    logger.error("Could not connect to DB after max attempts, exiting")
    return False


def get_connection():
    """Context manager for connection."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_cursor():
    """Context manager for cursor."""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
