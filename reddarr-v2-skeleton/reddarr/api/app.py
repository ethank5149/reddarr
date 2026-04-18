"""FastAPI application factory.

Replaces the 2,900-line web/app.py monolith with a clean app factory
that mounts route modules. All routes are split into focused modules
under reddarr/api/routes/.

Usage:
    uvicorn reddarr.api.app:create_app --factory --host 0.0.0.0 --port 8080
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from reddarr.api.middleware import MetricsMiddleware
from reddarr.database import init_engine

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Reddarr",
        description="Self-hosted Reddit media archiver",
        version="2.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Metrics middleware ---
    app.add_middleware(MetricsMiddleware)

    # --- Mount route modules ---
    from reddarr.api.routes import posts, admin, targets, media, system, backups

    app.include_router(posts.router, prefix="/api")
    app.include_router(admin.router, prefix="/api/admin")
    app.include_router(targets.router, prefix="/api/admin")
    app.include_router(media.router)
    app.include_router(system.router)
    app.include_router(backups.router, prefix="/api/admin")

    # --- Lifecycle events ---
    @app.on_event("startup")
    def startup():
        _run_startup()

    @app.on_event("shutdown")
    def shutdown():
        from reddarr.database import _engine
        if _engine:
            _engine.dispose()
            logger.info("Database engine disposed")

    # --- Static files (React build) ---
    dist_dir = os.path.join(os.path.dirname(__file__), "..", "..", "dist")
    if os.path.isdir(dist_dir):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

        @app.get("/")
        def root():
            return FileResponse(os.path.join(dist_dir, "index.html"))

        @app.get("/icon.png")
        def icon():
            icon_path = os.path.join(dist_dir, "icon.png")
            if os.path.exists(icon_path):
                return FileResponse(icon_path, media_type="image/png")
            # Fallback to repo root
            return FileResponse("/app/icon.png", media_type="image/png")

        # SPA catch-all — must be last
        @app.get("/{full_path:path}")
        def spa(full_path: str):
            """Serve index.html for all non-API, non-media routes (SPA routing)."""
            return FileResponse(os.path.join(dist_dir, "index.html"))

    return app


def _run_startup():
    """Run startup tasks: DB init, migrations, initial data."""
    from reddarr.config import get_settings

    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Initialize database
    init_engine()

    # Run Alembic migrations
    try:
        import subprocess
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("Database migrations applied")
        else:
            logger.warning(f"Migration warning: {result.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Could not run migrations: {e}")

    logger.info(f"Reddarr API started on {settings.host}:{settings.port}")


# For direct uvicorn invocation: uvicorn reddarr.api.app:app
app = create_app()
