"""Database operations for ingester.

Uses the shared connection pool from shared.database.
"""

import os
import logging

from shared.database import init_pool, get_connection, get_cursor, close_pool

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
    from shared.database import get_pool

    return get_pool().getconn()


def init_connection():
    """Initialize the connection pool."""
    return init_db()
