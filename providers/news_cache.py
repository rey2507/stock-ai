"""News-specific caching with TTL and background refresh support."""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Optional, TypeVar

from models.news_model import NewsSnapshot

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    value: T
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    fetch_duration_ms: float = 0.0
    hit_count: int = 0


class NewsCache:
    """Thread-safe TTL cache for news snapshots."""

    def __init__(
        self,
        default_ttl_seconds: int = 300,  # 5 minutes default
        max_entries: int = 10,
        cleanup_interval_seconds: int = 60,
    ):
        self.default_ttl = default_ttl_seconds
        self.max_entries = max_entries
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._cleanup_interval = cleanup_interval_seconds
        self._last_cleanup = time.time()

    def _make_key(self, query: str, limit: int) -> str:
        return f"news:{query.lower().strip()}:{limit}"

    def get(
        self,
        query: str,
        limit: int = 50,
        fetcher: Optional[Callable[[], NewsSnapshot]] = None,
    ) -> Optional[NewsSnapshot]:
        """Get from cache, optionally fetching if stale/missing."""
        key = self._make_key(query, limit)

        with self._lock:
            self._maybe_cleanup()

            entry = self._cache.get(key)
            if entry is None:
                log.debug(f"Cache miss for {key}")
                return self._fetch_and_store(key, fetcher) if fetcher else None

            now = datetime.now(timezone.utc)
            if entry.expires_at and now > entry.expires_at:
                log.debug(f"Cache expired for {key}")
                return self._fetch_and_store(key, fetcher) if fetcher else None

            entry.hit_count += 1
            log.debug(f"Cache hit for {key} (hits: {entry.hit_count})")
            return entry.value

    def set(self, query: str, limit: int, value: NewsSnapshot, ttl_seconds: Optional[int] = None) -> None:
        """Manually set cache entry."""
        key = self._make_key(query, limit)
        ttl = ttl_seconds or self.default_ttl
        now = datetime.now(timezone.utc)

        with self._lock:
            self._maybe_cleanup()
            self._cache[key] = CacheEntry(
                value=value,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl),
            )
            log.debug(f"Cache set for {key} (TTL: {ttl}s)")

    def invalidate(self, query: str, limit: int = 50) -> bool:
        """Remove entry from cache."""
        key = self._make_key(query, limit)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                log.debug(f"Cache invalidated for {key}")
                return True
        return False

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            log.debug("Cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            now = datetime.now(timezone.utc)
            total = len(self._cache)
            expired = sum(1 for e in self._cache.values() if e.expires_at and now > e.expires_at)
            total_hits = sum(e.hit_count for e in self._cache.values())

            return {
                "total_entries": total,
                "expired_entries": expired,
                "active_entries": total - expired,
                "total_hits": total_hits,
                "max_entries": self.max_entries,
                "default_ttl_seconds": self.default_ttl,
            }

    def _fetch_and_store(self, key: str, fetcher: Callable[[], NewsSnapshot]) -> Optional[NewsSnapshot]:
        """Fetch fresh data and store in cache."""
        if fetcher is None:
            return None

        try:
            start = time.perf_counter()
            value = fetcher()
            duration_ms = (time.perf_counter() - start) * 1000

            if value:
                now = datetime.now(timezone.utc)
                self._cache[key] = CacheEntry(
                    value=value,
                    created_at=now,
                    expires_at=now + timedelta(seconds=self.default_ttl),
                    fetch_duration_ms=duration_ms,
                )
                log.info(f"Fetched and cached news: {len(value.items)} items in {duration_ms:.1f}ms")
                return value
        except Exception as e:
            log.error(f"Cache fetch failed for {key}: {e}")

        return None

    def _maybe_cleanup(self) -> None:
        """Remove expired entries if cleanup interval elapsed."""
        now_ts = time.time()
        if now_ts - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now_ts
        now = datetime.now(timezone.utc)

        expired_keys = [
            k for k, e in self._cache.items()
            if e.expires_at and now > e.expires_at
        ]
        for k in expired_keys:
            del self._cache[k]

        # If still over max, remove oldest by created_at
        if len(self._cache) > self.max_entries:
            sorted_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k].created_at,
            )
            for k in sorted_keys[:len(self._cache) - self.max_entries]:
                del self._cache[k]

        if expired_keys:
            log.debug(f"Cache cleanup: removed {len(expired_keys)} expired entries")


# ─── Global cache instance ──────────────────────────────────────────

_global_cache: Optional[NewsCache] = None


def get_news_cache() -> NewsCache:
    """Get global news cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = NewsCache()
    return _global_cache


def set_news_cache(cache: NewsCache) -> None:
    """Set global news cache instance (for testing)."""
    global _global_cache
    _global_cache = cache