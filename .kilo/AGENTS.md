# Reddit Archive Project - Kilo Agent Instructions

## Access URLs
- **Primary URL**: http://192.168.1.13:8011
- Use the LAN IP (192.168.1.13) when testing or accessing the application from browser
- Do NOT use localhost or 127.0.0.1 - it won't work from external access

## Always Rebuild & Redeploy
After ANY code changes, you MUST rebuild and redeploy the affected service(s):

### Rebuild all services (safe default):
```bash
cd /mnt/user/scripts/reddit-archive
docker-compose build
docker-compose up -d
```

### Rebuild specific services:
```bash
# API (web app) - for changes to web/app.py, web/src/App.jsx, etc.
docker-compose build api
docker-compose up -d api

# Ingester - for changes to ingester/app.py
docker-compose build ingester
docker-compose up -d ingester

# Downloader - for changes to downloader/app.py
docker-compose build downloader
docker-compose up -d downloader
```

### Verify deployment:
```bash
docker ps | grep reddit_archive
```

This applies to changes in:
- `web/app.py` or `web/src/*` → rebuild `api`
- `ingester/app.py` → rebuild `ingester`
- `downloader/app.py` → rebuild `downloader`
- `docker-compose.yml` → rebuild all
- Any configuration or dependency changes