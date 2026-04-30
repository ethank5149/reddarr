"""Redis-backed log aggregation for multi-container log streaming.

Each container (api, worker, beat) installs a RedisLogHandler that publishes
log records to a shared Redis pub/sub channel. The API SSE endpoint subscribes
and fuses them into a single stream for the frontend log viewer.

A ring buffer (Redis list, max 500 entries) lets new clients catch up on
recent history before subscribing for live updates.
"""

import json
import logging
import os
import time


CHANNEL = "reddarr:logs"
BUFFER_KEY = "reddarr:log_buffer"
BUFFER_MAX = 500


class RedisLogHandler(logging.Handler):
    """Logging handler that publishes records to Redis pub/sub."""

    def __init__(self, redis_url: str, level=logging.DEBUG):
        super().__init__(level)
        import redis as _redis
        self._redis = _redis.Redis.from_url(redis_url)
        self._source = os.environ.get("CONTAINER_ROLE", "api")
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord):
        try:
            entry = json.dumps({
                "ts": time.time(),
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
                "source": self._source,
            })
            pipe = self._redis.pipeline(transaction=False)
            pipe.publish(CHANNEL, entry)
            pipe.lpush(BUFFER_KEY, entry)
            pipe.ltrim(BUFFER_KEY, 0, BUFFER_MAX - 1)
            pipe.execute()
        except Exception:
            pass  # never let logging errors propagate


def install(redis_url: str, level=logging.INFO):
    """Attach a RedisLogHandler to the root logger.

    Safe to call multiple times — installs at most one handler.
    Filters noisy third-party loggers to WARNING to keep the stream clean.
    """
    root = logging.getLogger()
    if any(isinstance(h, RedisLogHandler) for h in root.handlers):
        return

    handler = RedisLogHandler(redis_url, level=level)
    root.addHandler(handler)

    # Reduce noise from libraries we don't care about in the log viewer
    for noisy in ("urllib3", "prawcore", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
