"""Database operations for downloader.

Uses the shared connection pool from shared.database.
"""

import os
import logging
import time

from shared.database import init_pool, get_connection, get_cursor, close_pool, get_pool

logger = logging.getLogger(__name__)

DB_URL = os.getenv("DB_URL")


def init_db():
    """Initialize the connection pool."""
    if not DB_URL:
        raise ValueError("DB_URL environment variable is not set")
    return init_pool(minconn=1, maxconn=5)


def get_connection_pool():
    """Get the connection pool (initializes if needed)."""
    return init_db()


def get_connection():
    """Context manager for pooled connection."""
    return get_connection()


def get_cursor():
    """Context manager for pooled cursor."""
    return get_cursor()


def close():
    """Close the connection pool."""
    close_pool()


def get_db():
    """Return a connection from the pool (for compatibility)."""
    return get_pool().getconn()


def wait_for_db(max_attempts: int = 10, delay: int = 3):
    """Wait for database connection with retry."""
    for attempt in range(max_attempts):
        try:
            init_db()
            logger.info("DB initial connection established")
            return True
        except Exception as e:
            logger.warning(
                f"DB connection attempt {attempt + 1}/{max_attempts} failed: {e}"
            )
            time.sleep(delay)
    logger.error("Could not connect to DB after max attempts, exiting")
    return False
