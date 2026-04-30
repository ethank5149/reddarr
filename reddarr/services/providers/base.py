"""Base class for download providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class DownloadResult:
    """Result of a download attempt."""

    path: Optional[str] = None
    thumb: Optional[str] = None
    hash: Optional[str] = None
    status: str = "failed"  # done|failed|corrupted|skipped
    error: Optional[str] = None


class DownloadProvider(ABC):
    """Base class for media download providers.

    Each provider handles a specific type of media source.
    Providers are checked in order - first match wins.
    """

    @abstractmethod
    def match(self, url: str) -> bool:
        """Return True if this provider handles the given URL."""
        ...

    @abstractmethod
    def download(
        self,
        url: str,
        post_id: str,
        post_dir: str,
        session: requests.Session,
    ) -> dict:
        """Download media and return a result dict.

        Returns:
            dict with keys: path, thumb, hash, status, error
        """
        ...
