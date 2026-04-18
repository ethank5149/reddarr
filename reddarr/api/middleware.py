"""Middleware — metrics collection and rate limiting.

Replaces the inline middleware from web/app.py.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from reddarr.utils.metrics import api_requests, api_latency


class MetricsMiddleware(BaseHTTPMiddleware):
    """Collect Prometheus metrics for all API requests."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()

        response = await call_next(request)

        duration = time.perf_counter() - start
        endpoint = request.url.path

        # Only track /api/ routes to avoid noise from static files
        if endpoint.startswith("/api/") or endpoint == "/metrics":
            api_requests.labels(
                method=request.method,
                endpoint=endpoint,
                status=response.status_code,
            ).inc()
            api_latency.labels(endpoint=endpoint).observe(duration)

        return response
