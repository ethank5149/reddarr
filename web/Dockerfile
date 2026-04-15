# Stage 1: Build frontend
FROM node:20-slim AS frontend

WORKDIR /app

COPY web/package.json web/package-lock.json ./
RUN npm ci --silent

COPY web/index.html web/vite.config.js ./
COPY web/src ./src
COPY web/public ./public
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

COPY shared ./shared

COPY --from=frontend /app/dist ./dist

COPY web/app.py .

ENV PYTHONPATH=/app

COPY alembic.ini ./
COPY alembic ./alembic

RUN chmod +x /app/alembic/versions 2>/dev/null || true

CMD ["sh", "-c", "alembic upgrade head || true && uvicorn app:app --host 0.0.0.0 --port 8080"]