"""Prometheus metrics - single source of truth for all Reddarr metrics.

Replaces the scattered metric definitions across web/app.py,
ingester/app.py, and downloader/app.py.
"""

from prometheus_client import Counter, Gauge, Histogram

# --- Ingestion ---
posts_ingested = Counter(
    "reddarr_posts_ingested_total",
    "Total posts ingested",
    ["subreddit"],
)
comments_ingested = Counter(
    "reddarr_comments_ingested_total",
    "Total comments ingested",
    ["subreddit"],
)
media_queued = Counter(
    "reddarr_media_queued_total",
    "Total media items queued for download",
)
ingest_cycle_duration = Histogram(
    "reddarr_ingest_cycle_seconds",
    "Duration of an ingest cycle",
)
posts_skipped = Counter(
    "reddarr_posts_skipped_total",
    "Posts already in DB (skipped)",
    ["subreddit"],
)

# --- Downloads ---
media_downloaded = Counter(
    "reddarr_media_downloaded_total",
    "Total media items successfully downloaded",
    ["provider"],
)
media_failed = Counter(
    "reddarr_media_failed_total",
    "Total media download failures",
    ["provider"],
)
download_duration = Histogram(
    "reddarr_download_seconds",
    "Duration of a single media download",
)

# --- Database ---
posts_total = Gauge(
    "reddarr_posts_total",
    "Total posts in database",
)
comments_total = Gauge(
    "reddarr_comments_total",
    "Total comments in database",
)
media_total = Gauge(
    "reddarr_media_total",
    "Total media records in database",
    ["status"],
)
targets_enabled = Gauge(
    "reddarr_targets_enabled",
    "Number of enabled targets",
)

# --- API ---
api_requests = Counter(
    "reddarr_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"],
)
api_latency = Histogram(
    "reddarr_api_latency_seconds",
    "API request latency",
    ["endpoint"],
)

# --- Errors ---
errors_total = Counter(
    "reddarr_errors_total",
    "Total errors by category",
    ["component", "error_type"],
)
