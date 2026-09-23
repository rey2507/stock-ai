"""Source Registry for provenance tracking.

Tracks which provider produced each field, when it was fetched,
and the quality/reliability of each source.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from threading import Lock


@dataclass
class SourceRecord:
    """Provenance record for a single data point."""
    field_name: str
    provider: str
    source_system: str  # SmartAPI | YahooFinance | NSE | Manual
    observed_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    status: str = "LIVE"  # LIVE | DELAYED | HISTORICAL | MOCK | UNAVAILABLE
    freshness_seconds: Optional[float] = None
    confidence: float = 1.0  # 0.0 to 1.0


@dataclass
class ProviderStats:
    """Statistics for a data provider."""
    name: str
    total_fetches: int = 0
    successful_fetches: int = 0
    failed_fetches: int = 0
    avg_latency_ms: float = 0.0
    last_fetch_at: Optional[datetime] = None
    last_error: Optional[str] = None

    @property
    def success_rate(self) -> float:
        if self.total_fetches == 0:
            return 0.0
        return self.successful_fetches / self.total_fetches

    @property
    def is_healthy(self) -> bool:
        return self.success_rate >= 0.8 and self.failed_fetches < 3


class SourceRegistry:
    """Tracks provenance and reliability of all data sources."""

    def __init__(self):
        self._records: dict[str, SourceRecord] = {}
        self._provider_stats: dict[str, ProviderStats] = {}
        self._lock = Lock()

    def record_fetch(
        self,
        field_name: str,
        provider: str,
        source_system: str,
        observed_at: Optional[datetime] = None,
        status: str = "LIVE",
        confidence: float = 1.0,
    ):
        """Record that a field was fetched from a provider."""
        now = datetime.now(timezone.utc)
        freshness = (now - observed_at).total_seconds() if observed_at else None

        with self._lock:
            self._records[field_name] = SourceRecord(
                field_name=field_name,
                provider=provider,
                source_system=source_system,
                observed_at=observed_at or now,
                fetched_at=now,
                status=status,
                freshness_seconds=freshness,
                confidence=confidence,
            )

            # Update provider stats
            if provider not in self._provider_stats:
                self._provider_stats[provider] = ProviderStats(name=provider)
            stats = self._provider_stats[provider]
            stats.total_fetches += 1
            stats.successful_fetches += 1
            stats.last_fetch_at = now

    def record_failure(self, provider: str, error: str):
        """Record a failed fetch from a provider."""
        with self._lock:
            if provider not in self._provider_stats:
                self._provider_stats[provider] = ProviderStats(name=provider)
            stats = self._provider_stats[provider]
            stats.total_fetches += 1
            stats.failed_fetches += 1
            stats.last_error = error
            stats.last_fetch_at = datetime.now(timezone.utc)

    def record_latency(self, provider: str, latency_ms: float):
        """Record fetch latency for a provider."""
        with self._lock:
            if provider not in self._provider_stats:
                self._provider_stats[provider] = ProviderStats(name=provider)
            stats = self._provider_stats[provider]
            # Exponential moving average
            if stats.avg_latency_ms == 0:
                stats.avg_latency_ms = latency_ms
            else:
                stats.avg_latency_ms = 0.8 * stats.avg_latency_ms + 0.2 * latency_ms

    def get_field_source(self, field_name: str) -> Optional[SourceRecord]:
        """Get provenance info for a specific field."""
        with self._lock:
            return self._records.get(field_name)

    def get_provider_stats(self, provider: str) -> Optional[ProviderStats]:
        """Get statistics for a provider."""
        with self._lock:
            return self._provider_stats.get(provider)

    def get_all_provider_stats(self) -> dict[str, ProviderStats]:
        """Get statistics for all providers."""
        with self._lock:
            return dict(self._provider_stats)

    def get_field_sources_snapshot(self) -> dict[str, dict]:
        """Get provenance info for all fields (for UI display)."""
        with self._lock:
            result = {}
            for name, record in self._records.items():
                result[name] = {
                    "provider": record.provider,
                    "source": record.source_system,
                    "observed_at": record.observed_at.isoformat() if record.observed_at else None,
                    "fetched_at": record.fetched_at.isoformat() if record.fetched_at else None,
                    "status": record.status,
                    "freshness": round(record.freshness_seconds, 1) if record.freshness_seconds else None,
                    "confidence": record.confidence,
                }
            return result

    def is_provider_healthy(self, provider: str) -> bool:
        """Check if a provider is considered healthy."""
        stats = self.get_provider_stats(provider)
        if stats is None:
            return True  # No stats = assume healthy (first run)
        return stats.is_healthy


# Global registry instance
registry = SourceRegistry()
