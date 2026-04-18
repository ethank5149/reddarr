# =============================================================================
# Reddarr v2 — Unified Dockerfile
#
# Single image serves all roles: API server, Celery worker, Celery beat.
# The role is selected by the CMD/entrypoint in docker-compose.yml.
# =============================================================================

# --- Stage 1: Build React frontend ---
FROM node:20-slim AS frontend

WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci --silent

COPY web/index.html web/vite.config.js ./
COPY web/src ./src
COPY web/public ./public
RUN npm run build


# --- Stage 2: Python runtime ---
FROM python:3.11-slim

# System dependencies for media processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e '.[all]' 2>/dev/null || \
    pip install --no-cache-dir \
    fastapi uvicorn[standard] \
    sqlalchemy psycopg2-binary alembic \
    celery[redis] redis \
    praw requests yt-dlp Pillow \
    prometheus-client \
    python-dotenv

# Copy application code
COPY reddarr ./reddarr

# Copy built frontend
COPY --from=frontend /build/dist ./dist

# Copy Alembic config
COPY alembic.ini ./
COPY alembic ./alembic

# Copy static assets
COPY icon.png ./

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: run the API server
# Override in docker-compose.yml for worker/beat roles
EXPOSE 8080
CMD ["uvicorn", "reddarr.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
