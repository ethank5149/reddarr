import logging
import os
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool

from .config import get_db_url

logger = logging.getLogger(__name__)

_connection_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def init_pool(
    minconn: int = 5, maxconn: int = 50
) -> psycopg2.pool.ThreadedConnectionPool:
    """Initialize the global connection pool."""
    global _connection_pool

    with _pool_lock:
        if _connection_pool is not None:
            return _connection_pool

        db_url = get_db_url()
        _connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=minconn, maxconn=maxconn, dsn=db_url
        )
        logger.info("Database connection pool initialized")
        return _connection_pool


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Get the global connection pool, initializing if needed."""
    global _connection_pool
    if _connection_pool is None:
        return init_pool()
    return _connection_pool


def close_pool():
    """Close all connections in the pool."""
    global _connection_pool
    if _connection_pool is not None:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("Database connection pool closed")


@contextmanager
def get_connection():
    """Get a connection from the pool.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
            conn.commit()
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor():
    """Get a cursor from a pooled connection.

    Usage:
        with get_cursor() as cur:
            cur.execute(...)
    """
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


class ThreadLocalDB:
    """Thread-local database connection for high-throughput scenarios.

    Uses thread-local storage to maintain a connection per thread,
    similar to how psycopg2's simple connection works but with pooling.
    """

    def __init__(self, db_url: Optional[str] = None):
        self._db_url = db_url or get_db_url()
        self._tls = threading.local()

    def get_connection(self) -> psycopg2._psycopg.connection:
        """Get or create a connection for this thread."""
        if not hasattr(self._tls, "conn") or self._tls.conn is None:
            self._tls.conn = psycopg2.connect(self._db_url)
            logger.debug("Thread-local DB connection created")
            return self._tls.conn

        try:
            with self._tls.conn.cursor() as cur:
                cur.execute("SELECT 1")
            return self._tls.conn
        except Exception:
            logger.warning("DB connection lost in thread, reconnecting...")
            try:
                self._tls.conn.close()
            except Exception:
                pass
            self._tls.conn = psycopg2.connect(self._db_url)
            logger.info("DB reconnected in thread")
            return self._tls.conn

    @contextmanager
    def connection(self):
        """Context manager for thread-local connection."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @contextmanager
    def cursor(self):
        """Context manager for thread-local cursor."""
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def close(self):
        """Close the thread-local connection if any."""
        if hasattr(self._tls, "conn") and self._tls.conn is not None:
            try:
                self._tls.conn.close()
            except Exception:
                pass
            self._tls.conn = None


def create_thread_local_db() -> ThreadLocalDB:
    """Factory function to create a ThreadLocalDB instance."""
    return ThreadLocalDB()
