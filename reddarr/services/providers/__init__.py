"""Download provider registry.

Preserves the provider pattern from the old downloader/providers.py.
Each provider handles downloading from a specific source (Reddit images,
Reddit videos, YouTube, RedGifs, generic URLs).

Usage:
    provider = get_provider(url)
    result = provider.download(url, post_id, post_dir, session)
"""

from typing import Optional

from reddarr.services.providers.base import DownloadProvider
from reddarr.services.providers.reddit import RedditImageProvider, RedditVideoProvider
from reddarr.services.providers.youtube import YouTubeProvider
from reddarr.services.providers.redgifs import RedGifsProvider
from reddarr.services.providers.generic import GenericProvider

# Ordered by specificity — first match wins, generic is the catch-all
PROVIDERS: list[DownloadProvider] = [
    RedditVideoProvider(),
    RedGifsProvider(),
    YouTubeProvider(),
    RedditImageProvider(),
    GenericProvider(),  # catch-all, must be last
]


def get_provider(url: str) -> DownloadProvider:
    """Get the appropriate download provider for a URL."""
    for provider in PROVIDERS:
        if provider.match(url):
            return provider
    return PROVIDERS[-1]
