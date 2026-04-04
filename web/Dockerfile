FROM python:3.11-slim

# Install Node.js for building frontend
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./
RUN npm ci --silent

# Copy React source and build
COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public
RUN npm run build

# Copy Python requirements and app
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
COPY app.py .

# Serve React static files from dist/
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
