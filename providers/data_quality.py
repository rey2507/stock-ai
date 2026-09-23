"""Data Quality Engine.

Tracks staleness, validates field freshness, computes quality scores.
Every field in MarketSnapshot passes through this engine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from models.snapshot import MarketSnapshot, FieldMeta


@dataclass
class FieldHealth:
    """Health assessment for a single field."""
    name: str
    status: str  # LIVE | DELAYED | STALE | UNAVAILABLE
    quality: str  # GOOD | PARTIAL | INVALID
    freshness_seconds: Optional[float]
    source: str
    message: str = ""


@dataclass
class DataHealthReport:
    """Complete health assessment of a MarketSnapshot."""
    overall_status: str  # LIVE | PARTIAL | STALE | UNAVAILABLE
    overall_quality: str  # GOOD | PARTIAL | POOR
    fields: list[FieldHealth] = field(default_factory=list)
    live_count: int = 0
    delayed_count: int = 0
    stale_count: int = 0
    unavailable_count: int = 0
    total_fields: int = 0

    @property
    def health_pct(self) -> float:
        """Percentage of fields that are LIVE + GOOD."""
        if self.total_fields == 0:
            return 0.0
        return (self.live_count / self.total_fields) * 100


class DataQualityEngine:
    """Validates and scores field freshness for a MarketSnapshot."""

    # Freshness thresholds by field type (seconds)
    THRESHOLDS = {
        # Real-time: 0-30s = LIVE, 30-120s = DELAYED, >120s = STALE
        "nifty_spot": (30, 120),
        "nifty_change": (30, 120),
        "nifty_change_pct": (30, 120),
        "nifty_open": (30, 120),
        "nifty_high": (30, 120),
        "nifty_low": (30, 120),
        "futures_price": (30, 120),
        "futures_change_pct": (30, 120),
        "vwap": (60, 300),
        "rsi": (60, 300),
        # Option chain: 0-60s = LIVE, 60-300s = DELAYED, >300s = STALE
        "pcr": (60, 300),
        "futures_oi": (60, 300),
        "call_oi": (60, 300),
        "put_oi": (60, 300),
        "call_oi_change": (60, 300),
        "put_oi_change": (60, 300),
        "atm_strike": (60, 300),
        "atm_iv": (120, 600),
        "max_pain": (120, 600),
        # Market stats
        "advances": (120, 600),
        "declines": (120, 600),
        "advance_decline_ratio": (120, 600),
        "india_vix": (300, 1800),
        # Macro: longer windows
        "crude_price": (600, 3600),
        "usd_inr": (300, 1800),
        "us10y_yield": (300, 1800),
        "fii_flow_1d": (3600, 86400),
        "dii_flow_1d": (3600, 86400),
    }

    def assess(self, snapshot: MarketSnapshot) -> DataHealthReport:
        """Assess the health of all fields in a snapshot."""
        report = DataHealthReport(
            overall_status="UNAVAILABLE",
            overall_quality="POOR",
        )

        all_fields = [
            f for f in dir(snapshot)
            if isinstance(getattr(snapshot, f, None), FieldMeta)
        ]

        report.total_fields = len(all_fields)

        for field_name in all_fields:
            fm: FieldMeta = getattr(snapshot, field_name)
            health = self._assess_field(field_name, fm)
            report.fields.append(health)

            if health.status == "LIVE":
                report.live_count += 1
            elif health.status == "DELAYED":
                report.delayed_count += 1
            elif health.status == "STALE":
                report.stale_count += 1
            else:
                report.unavailable_count += 1

        # Compute overall
        if report.total_fields > 0:
            live_pct = report.live_count / report.total_fields
            good_fields = sum(
                1 for f in report.fields
                if f.quality in ("GOOD", "PARTIAL") and f.status != "UNAVAILABLE"
            )
            good_pct = good_fields / report.total_fields

            if live_pct >= 0.5 and good_pct >= 0.7:
                report.overall_status = "LIVE"
                report.overall_quality = "GOOD"
            elif live_pct >= 0.2 or good_pct >= 0.4:
                report.overall_status = "PARTIAL"
                report.overall_quality = "PARTIAL"
            else:
                report.overall_status = "STALE"
                report.overall_quality = "POOR"

        return report

    def _assess_field(self, name: str, fm: FieldMeta) -> FieldHealth:
        """Assess health of a single field."""
        # Unavailable fields
        if fm.status == "UNAVAILABLE" or fm.value is None:
            return FieldHealth(
                name=name,
                status="UNAVAILABLE",
                quality="INVALID",
                freshness_seconds=None,
                source=fm.source,
                message="No data available",
            )

        # Fields without timestamps - use snapshot timestamp or assume fresh
        freshness = fm.freshness_seconds
        if freshness is None and fm.timestamp:
            freshness = (datetime.now(timezone.utc) - fm.timestamp).total_seconds()

        # Determine status from freshness
        thresholds = self.THRESHOLDS.get(name, (60, 300))
        live_threshold, delayed_threshold = thresholds

        if freshness is not None:
            if freshness <= live_threshold:
                status = "LIVE"
            elif freshness <= delayed_threshold:
                status = "DELAYED"
            else:
                status = "STALE"
        else:
            # No freshness info - assume DELAYED
            status = "DELAYED"

        # Determine quality from field quality tag
        quality = fm.quality

        # Build message
        messages = {
            "LIVE": f"Fresh ({freshness:.0f}s ago)" if freshness else "Fresh",
            "DELAYED": f"Delayed ({freshness:.0f}s ago)" if freshness else "Delayed",
            "STALE": f"Stale ({freshness:.0f}s ago)" if freshness else "Stale data",
        }

        return FieldHealth(
            name=name,
            status=status,
            quality=quality,
            freshness_seconds=freshness,
            source=fm.source,
            message=messages.get(status, ""),
        )

    def validate_verdict_input(self, snapshot: MarketSnapshot) -> dict:
        """Check if snapshot has enough quality data for verdict generation.

        Returns: {can_produce: bool, missing_critical: list[str], quality_score: float}
        """
        critical_fields = snapshot.critical_fields_missing()
        health = self.assess(snapshot)

        return {
            "can_produce": len(critical_fields) == 0 and health.overall_status != "UNAVAILABLE",
            "missing_critical": critical_fields,
            "quality_score": health.health_pct,
            "overall_status": health.overall_status,
        }
