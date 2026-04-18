"""Database engine and session management.

Replaces the old shared/database.py hand-rolled connection pool with
SQLAlchemy's built-in pool + session factory.

Usage in API routes (FastAPI dependency injection):
    from reddarr.database import get_db

    @router.get("/posts")
    def list_posts(db: Session = Depends(get_db)):
        return db.query(Post).all()

Usage in Celery tasks:
    from reddarr.database import SessionLocal

    with SessionLocal() as db:
        posts = db.query(Post).all()
"""

import logging
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from reddarr.config import get_settings
from reddarr.models import Base

logger = logging.getLogger(__name__)

_engine = None
SessionLocal: sessionmaker[Session] = None  # type: ignore


def init_engine():
    """Initialize the SQLAlchemy engine and session factory.

    Called once at app startup. Safe to call multiple times.
    """
    global _engine, SessionLocal

    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.db_url,
            pool_size=settings.db_pool_min,
            max_overflow=settings.db_pool_max - settings.db_pool_min,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=settings.log_level == "DEBUG",
        )
        logger.info("Database engine initialized")

    if SessionLocal is None:
        SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    return _engine


def get_engine():
    """Get the global engine, initializing if needed."""
    if _engine is None:
        init_engine()
    return _engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session.

    Commits on success, rolls back on error, always closes.
    """
    if SessionLocal is None:
        init_engine()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_tables():
    """Create all tables (for testing / first-run). In production, use Alembic."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")
