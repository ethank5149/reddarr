"""Reddit API client wrapper.

Provides a configured Reddit API client with proper auth.
"""

import os
import logging
import praw
from typing import Optional

logger = logging.getLogger(__name__)


def _read_secret(path: str) -> str:
    """Read a secret from file."""
    with open(path) as f:
        return f.read().strip()


def get_reddit_client() -> praw.Reddit:
    """Create and return a configured Reddit API client."""
    return praw.Reddit(
        client_id=_read_secret("/run/secrets/reddit_client_id"),
        client_secret=_read_secret("/run/secrets/reddit_client_secret"),
        user_agent=os.getenv("REDDIT_USER_AGENT"),
    )


class RedditClient:
    """Reddit client wrapper with helper methods."""

    def __init__(self):
        self._client = get_reddit_client()
        logger.info(f"Reddit client initialized (read only: {self._client.read_only})")

    @property
    def client(self) -> praw.Reddit:
        return self._client

    def subreddit(self, name: str):
        """Get a subreddit object."""
        return self._client.subreddit(name)

    def redditor(self, name: str):
        """Get a redditor object."""
        return self._client.redditor(name)

    def is_rate_limited(self, error: Exception) -> bool:
        """Check if an error is due to rate limiting."""
        err_str = str(error).lower()
        return "rate" in err_str or "429" in err_str or "too many" in err_str


# Global client instance
_reddit_client: Optional[RedditClient] = None


def get_reddit() -> RedditClient:
    """Get the global Reddit client, initializing if needed."""
    global _reddit_client
    if _reddit_client is None:
        _reddit_client = RedditClient()
    return _reddit_client
