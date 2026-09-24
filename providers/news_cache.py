"""News cache with TTL support."""

from __future__ import annotations

import time
import logging
from typing import Optional
from threading import Lock

from models.news_model import NewsItem

log = logging.getLogger(__name__)


class NewsCache:
    """Simple in-memory TTL cache for news items per source."""

    def __init__(self, ttl_seconds: int = 900):
        self.cache: dict[str, tuple[list[NewsItem], float]] = {}
        self.ttl = ttl_seconds
        self._lock = Lock()

    def get(self, source: str) -> Optional[list[NewsItem]]:
        with self._lock:
            entry = self.cache.get(source)
            if entry is None:
                return None
            items, ts = entry
            if time.time() - ts > self.ttl:
                self.cache.pop(source, None)
                return None
            return items

    def set(self, source: str, items: list[NewsItem]) -> None:
        with self._lock:
            self.cache[source] = (items, time.time())

    def clear(self) -> None:
        with self._lock:
            self.cache.clear()


news_cache = NewsCache()
