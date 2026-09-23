"""Cache layer with freshness tracking.

Every cached entry stores: field, value, source, observed_at, fetched_at, expiry, status.
Never allows stale data to appear as LIVE.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Any, Optional


@dataclass
class CacheEntry:
    """Single cached data point with full provenance."""
    value: Any
    source: str
    observed_at: datetime
    fetched_at: datetime
    expires_at: Optional[datetime] = None
    status: str = "LIVE"  # LIVE | DELAYED | STALE | UNAVAILABLE
    quality: str = "GOOD"  # GOOD | PARTIAL | INVALID

    @property
    def age_seconds(self) -> float:
        """Seconds since fetched."""
        now = datetime.now(timezone.utc)
        return (now - self.fetched_at).total_seconds()

    @property
    def is_stale(self) -> bool:
        """Check if entry has exceeded its freshness window."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def freshness_seconds(self) -> Optional[float]:
        """Seconds since observed, or None if unavailable."""
        if self.observed_at is None:
            return None
        return (datetime.now(timezone.utc) - self.observed_at).total_seconds()

    def effective_status(self) -> str:
        """Return status accounting for staleness."""
        if self.is_stale:
            return "STALE"
        return self.status


# Freshness windows by field category (seconds)
FRESHNESS_WINDOWS = {
    # Streaming / real-time
    "price": 5,
    "volume": 5,
    "ltp": 5,
    # Intraday derived
    "vwap": 30,
    "rsi": 30,
    "atr": 60,
    "relative_volume": 60,
    # Option chain
    "oi": 30,
    "pcr": 30,
    "iv": 60,
    "greeks": 60,
    # Market stats
    "advances_declines": 120,
    "market_depth": 30,
    # Macro (daily/intraday)
    "macro_intraday": 300,
    "macro_daily": 86400,
    # Capital flows
    "fii_dii": 86400,
    # Fundamentals
    "earnings": 86400 * 7,
    "macro_indicator": 86400,
}


def get_freshness_window(field_category: str) -> int:
    """Get the freshness window in seconds for a field category."""
    return FRESHNESS_WINDOWS.get(field_category, 300)


class DataCache:
    """Thread-safe in-memory cache with freshness tracking."""

    def __init__(self):
        self._store: dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[CacheEntry]:
        """Get a cached entry. Returns None if missing or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_stale:
                return None
            return entry

    def get_value(self, key: str, default=None) -> Any:
        """Get just the value from cache, or default if missing/stale."""
        entry = self.get(key)
        if entry is None:
            return default
        return entry.value

    def put(
        self,
        key: str,
        value: Any,
        source: str,
        observed_at: Optional[datetime] = None,
        freshness_window: int = 300,
        status: str = "LIVE",
        quality: str = "GOOD",
    ):
        """Store a value in cache with metadata."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._store[key] = CacheEntry(
                value=value,
                source=source,
                observed_at=observed_at or now,
                fetched_at=now,
                expires_at=now + timedelta(seconds=freshness_window),
                status=status,
                quality=quality,
            )

    def invalidate(self, key: str):
        """Remove a specific cache entry."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._store.clear()

    def snapshot_status(self) -> dict[str, dict]:
        """Return status of all cached fields for the data health panel."""
        with self._lock:
            result = {}
            for key, entry in self._store.items():
                result[key] = {
                    "value": entry.value,
                    "source": entry.source,
                    "observed_at": entry.observed_at.isoformat() if entry.observed_at else None,
                    "fetched_at": entry.fetched_at.isoformat() if entry.fetched_at else None,
                    "age_seconds": round(entry.age_seconds, 1),
                    "status": entry.effective_status(),
                    "quality": entry.quality,
                    "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                }
            return result


# Global cache instance
cache = DataCache()
