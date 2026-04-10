# Stage 1: Build frontend
FROM node:20-slim AS frontend

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --silent

COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

COPY shared ./shared

COPY --from=frontend /app/dist ./dist

COPY app.py .

ENV PYTHONPATH=/app

CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]