import logging
import os
from typing import Optional

import redis

logger = logging.getLogger(__name__)

MEDIA_CHANNEL = "media:new"
SCRAPE_TRIGGER_CHANNEL = "scrape:trigger"
BACKFILL_TRIGGER_CHANNEL = "backfill:trigger"

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """Get the global Redis client, initializing if needed."""
    global _redis_client
    if _redis_client is None:
        _redis_client = init_redis()
    return _redis_client


def init_redis() -> redis.Redis:
    """Initialize Redis connection."""
    global _redis_client
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    password = os.getenv("REDIS_PASSWORD")

    _redis_client = redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=False,
    )
    logger.info(f"Redis client initialized: {host}:{port}/{db}")
    return _redis_client


def close_redis():
    """Close the Redis connection."""
    global _redis_client
    if _redis_client is not None:
        _redis_client.close()
        _redis_client = None
        logger.info("Redis connection closed")


class PubSubPublisher:
    """Publisher for Redis Pub/Sub messages."""

    def __init__(self, client: Optional[redis.Redis] = None):
        self.client = client or get_redis_client()

    def publish(self, channel: str, message: bytes) -> int:
        """Publish a message to a channel."""
        return self.client.publish(channel, message)

    def publish_media(self, data: dict) -> int:
        """Publish a media item to the media channel."""
        import json

        return self.publish(MEDIA_CHANNEL, json.dumps(data).encode())

    def publish_scrape_trigger(self, config: dict) -> int:
        """Publish a scrape trigger."""
        import json

        return self.publish(SCRAPE_TRIGGER_CHANNEL, json.dumps(config).encode())

    def publish_backfill_trigger(self, config: dict) -> int:
        """Publish a backfill trigger."""
        import json

        return self.publish(BACKFILL_TRIGGER_CHANNEL, json.dumps(config).encode())


class PubSubSubscriber:
    """Subscriber for Redis Pub/Sub messages with reconnection support."""

    def __init__(self, client: Optional[redis.Redis] = None):
        self.client = client or get_redis_client()
        self.pubsub: Optional[redis.client.PubSub] = None

    def subscribe(self, channels: list):
        """Subscribe to one or more channels."""
        self.pubsub = self.client.pubsub()
        for channel in channels:
            self.pubsub.subscribe(channel)
        logger.info(f"Subscribed to channels: {channels}")

    def listen(self):
        """Listen for messages on subscribed channels."""
        if self.pubsub is None:
            raise RuntimeError("Not subscribed to any channels")
        return self.pubsub.listen()

    def unsubscribe(self):
        """Unsubscribe from all channels and close the connection."""
        if self.pubsub:
            self.pubsub.unsubscribe()
            self.pubsub.close()
            self.pubsub = None
            logger.info("Unsubscribed from all channels")
