"""Provider Manager — backend-only fallback logic, health tracking.

Responsibilities:
- Track provider health/freshness
- Auto-fallback when primary fails
- Cache last-known-good snapshots during outages
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from threading import Lock
from dataclasses import dataclass

from providers.registry import get_provider, list_providers
from providers.source_registry import registry
from providers.merger import merge_snapshots
from config import (
    PROVIDER_FALLBACK_CHAIN,
    DEFAULT_PRIMARY_PROVIDER,
    AUTO_FALLBACK_AFTER_FAILURES,
    FAILED_PROVIDER_COOLDOWN_SECONDS,
)

log = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    """Tracks health state for a single provider."""
    name: str
    consecutive_failures: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    is_healthy: bool = True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success = datetime.now(timezone.utc)
        self.is_healthy = True
        self.cooldown_until = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure = datetime.now(timezone.utc)
        if self.consecutive_failures >= AUTO_FALLBACK_AFTER_FAILURES:
            self.is_healthy = False
            self.cooldown_until = datetime.now(timezone.utc).timestamp() + FAILED_PROVIDER_COOLDOWN_SECONDS

    def is_in_cooldown(self) -> bool:
        if self.cooldown_until is None:
            return False
        return datetime.now(timezone.utc).timestamp() < self.cooldown_until


class ProviderManager:
    """Manages provider fallback chains and health tracking."""

    def __init__(self):
        self._lock = Lock()
        self._health: dict[str, ProviderHealth] = {}
        self._last_snapshot: Optional[Any] = None
        self._last_snapshot_ts: Optional[datetime] = None

    def _get_health(self, name: str) -> ProviderHealth:
        if name not in self._health:
            self._health[name] = ProviderHealth(name=name)
        return self._health[name]

    def _is_provider_available(self, name: str) -> bool:
        if name not in list_providers():
            return False
        health = self._get_health(name)
        return health.is_healthy and not health.is_in_cooldown()

    def record_success(self, name: str) -> None:
        with self._lock:
            self._get_health(name).record_success()

    def record_failure(self, name: str) -> None:
        with self._lock:
            self._get_health(name).record_failure()

    def get_healthy_providers(self, domain: str = "market_data") -> list[str]:
        """Get list of healthy providers for a domain, in fallback order."""
        chain = PROVIDER_FALLBACK_CHAIN.get(domain, list_providers())
        available = [p for p in chain if self._is_provider_available(p)]
        if not available:
            available = [p for p in list_providers() if self._is_provider_available(p)]
        return available

    def get_primary_for_domain(self, domain: str) -> Optional[str]:
        """Get the primary provider name for a domain."""
        chain = PROVIDER_FALLBACK_CHAIN.get(domain, list_providers())
        for name in chain:
            if self._is_provider_available(name):
                return name
        return None

    def fetch_with_fallback(self, domain: str = "market_data") -> tuple[Optional[Any], str]:
        """Fetch snapshot using fallback chain for domain.

        Returns (snapshot_or_None, source_used).
        """
        candidates = self.get_healthy_providers(domain)

        for name in candidates:
            try:
                provider = get_provider(name)
                snap = provider.fetch()
                if snap and snap.data_status != "UNAVAILABLE":
                    self.record_success(name)
                    self._last_snapshot = snap
                    self._last_snapshot_ts = datetime.now(timezone.utc)
                    return snap, name
                self.record_failure(name)
            except Exception as e:
                log.warning(f"Provider {name} failed: {e}")
                self.record_failure(name)

        # All failed — return cached snapshot if fresh (< 5 min)
        if self._last_snapshot and self._last_snapshot_ts:
            age = (datetime.now(timezone.utc) - self._last_snapshot_ts).total_seconds()
            if age < 300:
                log.info(f"Returning cached snapshot ({age:.0f}s old)")
                return self._last_snapshot, "cached"

        return None, "none"

    def get_provider_stats_display(self) -> list[dict]:
        """Get provider stats for UI display."""
        with self._lock:
            results = []
            raw_stats = registry.get_all_provider_stats()
            for name, stats in raw_stats.items():
                health = self._get_health(name)
                results.append({
                    "name": name,
                    "total": stats.total_fetches,
                    "success": stats.successful_fetches,
                    "failed": stats.failed_fetches,
                    "success_rate": stats.success_rate,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "last_fetch": stats.last_fetch_at,
                    "last_error": stats.last_error,
                    "is_healthy": health.is_healthy,
                    "consecutive_failures": health.consecutive_failures,
                })
            return results


# Global singleton
provider_manager = ProviderManager()
