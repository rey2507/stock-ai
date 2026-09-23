"""Canonical MarketSnapshot data contract.

Every provider (Mock, NSE, Angel, Broker) must produce this exact structure.
All downstream consumers (indicators, verdict engine, UI) read only from this.

FieldMeta now carries:
- Granular status (LIVE | TEMPORARILY_UNAVAILABLE | UNSUPPORTED | etc.)
- Diagnostic reason (why the field has this status)
- Retry count and next retry time
- Full latency tracking (observed, fetched, processed)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class FieldMeta:
    """Metadata for a single observation field.

    Carries full provenance: value, timestamps, source, status, diagnostics.
    Never silently convert None to zero or invent values.
    """
    # ─── Core data ──────────────────────────────────────────────
    value: Optional[float | str | int | dict | list] = None

    # ─── Source provenance ──────────────────────────────────────
    source: str = "UNKNOWN"                     # Provider name
    source_endpoint: str = ""                   # Specific API endpoint used

    # ─── Timestamps ─────────────────────────────────────────────
    observed_at: Optional[datetime] = None      # When the data was observed at source
    fetched_at: Optional[datetime] = None       # When we fetched it
    processed_at: Optional[datetime] = None     # When we normalized it
    timestamp: Optional[datetime] = None        # Legacy: use observed_at

    # ─── Freshness ──────────────────────────────────────────────
    freshness_seconds: Optional[float] = None   # Seconds since observed
    expected_freshness_sec: int = 300           # Expected freshness window

    # ─── Status (granular) ──────────────────────────────────────
    status: str = "UNKNOWN"                     # FieldStatus enum value
    quality: str = "INVALID"                    # GOOD | PARTIAL | INVALID

    # ─── Diagnostics ────────────────────────────────────────────
    diagnostic_reason: str = "ok"               # DiagnosticReason enum value
    diagnostic_message: str = ""                # Human-readable explanation
    retry_count: int = 0                        # Number of retries attempted
    max_retries: int = 3                        # Maximum allowed retries
    next_retry_at: Optional[datetime] = None    # When to retry next
    last_error: str = ""                        # Last error message

    # ─── Latency tracking ───────────────────────────────────────
    provider_latency_ms: Optional[float] = None   # Time for provider to respond
    processing_latency_ms: Optional[float] = None  # Time to normalize/process
    total_latency_ms: Optional[float] = None       # Total fetch-to-process time

    def __post_init__(self):
        """Ensure timestamps are consistent."""
        # Legacy compatibility: map timestamp to observed_at
        if self.timestamp is not None and self.observed_at is None:
            self.observed_at = self.timestamp
        if self.observed_at is not None and self.timestamp is None:
            self.timestamp = self.observed_at

    @property
    def age_seconds(self) -> Optional[float]:
        """Seconds since observed, or None if no timestamp."""
        if self.observed_at is None:
            return None
        return (datetime.now(timezone.utc) - self.observed_at).total_seconds()

    @property
    def is_fresh(self) -> bool:
        """Check if data is within expected freshness window."""
        if self.freshness_seconds is None:
            return False
        return self.freshness_seconds <= self.expected_freshness_sec

    @property
    def is_stale(self) -> bool:
        """Check if data has exceeded freshness window."""
        if self.freshness_seconds is None:
            return True
        return self.freshness_seconds > self.expected_freshness_sec

    @property
    def can_retry(self) -> bool:
        """Check if this field can be retried."""
        return self.retry_count < self.max_retries

    def mark_fetched(self, provider_latency_ms: Optional[float] = None):
        """Mark field as just fetched."""
        now = datetime.now(timezone.utc)
        self.fetched_at = now
        if self.observed_at is None:
            self.observed_at = now
        self.provider_latency_ms = provider_latency_ms
        self.freshness_seconds = 0.0

    def mark_processed(self):
        """Mark field as just processed/normalized."""
        self.processed_at = datetime.now(timezone.utc)
        if self.fetched_at and self.provider_latency_ms is not None:
            self.processing_latency_ms = (
                (self.processed_at - self.fetched_at).total_seconds() * 1000
            )
        if self.provider_latency_ms and self.processing_latency_ms:
            self.total_latency_ms = self.provider_latency_ms + self.processing_latency_ms

    def mark_unavailable(self, reason: str = "unavailable", message: str = ""):
        """Mark field as unavailable with diagnostic info."""
        self.value = None
        self.status = "UNAVAILABLE"
        self.quality = "INVALID"
        self.diagnostic_reason = reason
        self.diagnostic_message = message

    def mark_temporarily_unavailable(self, reason: str = "retry_scheduled", message: str = ""):
        """Mark field as temporarily unavailable (retryable)."""
        self.status = "TEMPORARILY_UNAVAILABLE"
        self.diagnostic_reason = reason
        self.diagnostic_message = message
        self.retry_count += 1

    def to_dict(self) -> dict:
        """Serialize for debugging/display."""
        return {
            "value": self.value,
            "source": self.source,
            "status": self.status,
            "quality": self.quality,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "freshness_seconds": self.freshness_seconds,
            "diagnostic_reason": self.diagnostic_reason,
            "diagnostic_message": self.diagnostic_message,
            "retry_count": self.retry_count,
            "provider_latency_ms": self.provider_latency_ms,
            "total_latency_ms": self.total_latency_ms,
        }


@dataclass
class MarketSnapshot:
    """Single canonical data contract for the entire application.

    Every field follows the FieldMeta contract:
    value, timestamp, source, freshness, status, quality, diagnostics.

    No provider-specific names leak past this boundary.
    """

    # --- Metadata ---
    snapshot_timestamp: Optional[datetime] = None
    timezone: str = "Asia/Kolkata"
    source: str = "UNKNOWN"
    data_status: str = "UNAVAILABLE"  # LIVE | DELAYED | UNAVAILABLE
    missing_fields: list[str] = field(default_factory=list)

    # --- Market ---
    nifty_spot: FieldMeta = field(default_factory=FieldMeta)
    nifty_change: FieldMeta = field(default_factory=FieldMeta)
    nifty_change_pct: FieldMeta = field(default_factory=FieldMeta)
    nifty_open: FieldMeta = field(default_factory=FieldMeta)
    nifty_high: FieldMeta = field(default_factory=FieldMeta)
    nifty_low: FieldMeta = field(default_factory=FieldMeta)
    nifty_volume: FieldMeta = field(default_factory=FieldMeta)

    # --- Futures ---
    futures_price: FieldMeta = field(default_factory=FieldMeta)
    futures_change_pct: FieldMeta = field(default_factory=FieldMeta)
    futures_oi: FieldMeta = field(default_factory=FieldMeta)
    futures_oi_change: FieldMeta = field(default_factory=FieldMeta)
    futures_expiry: FieldMeta = field(default_factory=FieldMeta)

    # --- Options ---
    atm_strike: FieldMeta = field(default_factory=FieldMeta)
    call_oi: FieldMeta = field(default_factory=FieldMeta)
    put_oi: FieldMeta = field(default_factory=FieldMeta)
    call_oi_change: FieldMeta = field(default_factory=FieldMeta)
    put_oi_change: FieldMeta = field(default_factory=FieldMeta)
    pcr: FieldMeta = field(default_factory=FieldMeta)
    atm_iv: FieldMeta = field(default_factory=FieldMeta)
    max_pain: FieldMeta = field(default_factory=FieldMeta)
    total_option_volume: FieldMeta = field(default_factory=FieldMeta)

    # --- Participation ---
    advances: FieldMeta = field(default_factory=FieldMeta)
    declines: FieldMeta = field(default_factory=FieldMeta)
    unchanged: FieldMeta = field(default_factory=FieldMeta)
    advance_decline_ratio: FieldMeta = field(default_factory=FieldMeta)
    sector_performance: FieldMeta = field(default_factory=FieldMeta)

    # --- Volatility / Momentum ---
    india_vix: FieldMeta = field(default_factory=FieldMeta)
    vwap: FieldMeta = field(default_factory=FieldMeta)
    rsi: FieldMeta = field(default_factory=FieldMeta)
    atr: FieldMeta = field(default_factory=FieldMeta)
    relative_volume: FieldMeta = field(default_factory=FieldMeta)

    # --- Capital Flows ---
    fii_flow_1d: FieldMeta = field(default_factory=FieldMeta)
    fii_flow_5d: FieldMeta = field(default_factory=FieldMeta)
    fii_flow_20d: FieldMeta = field(default_factory=FieldMeta)
    fii_flow_month: FieldMeta = field(default_factory=FieldMeta)
    dii_flow_1d: FieldMeta = field(default_factory=FieldMeta)
    dii_flow_5d: FieldMeta = field(default_factory=FieldMeta)
    dii_flow_20d: FieldMeta = field(default_factory=FieldMeta)
    dii_flow_month: FieldMeta = field(default_factory=FieldMeta)

    # --- Macro ---
    crude_price: FieldMeta = field(default_factory=FieldMeta)
    usd_inr: FieldMeta = field(default_factory=FieldMeta)
    us10y_yield: FieldMeta = field(default_factory=FieldMeta)
    fed_rate: FieldMeta = field(default_factory=FieldMeta)
    india_policy_rate: FieldMeta = field(default_factory=FieldMeta)

    # --- Economy ---
    inflation: FieldMeta = field(default_factory=FieldMeta)
    gdp_growth: FieldMeta = field(default_factory=FieldMeta)
    pmi: FieldMeta = field(default_factory=FieldMeta)

    # --- Earnings ---
    earnings_growth: FieldMeta = field(default_factory=FieldMeta)

    # --- Factor Intelligence (Phase A) ---
    factor_states: Optional[dict[str, list["FactorState"]]] = None
    # Format: {factor_name: [FactorState for intraday, 5d, 20d]}

    # --- Greeks (Phase C) ---
    greeks_by_strike: Optional[dict[float, dict[str, list["GreeksResult"]]]] = None
    # Format: {strike: {expiry_date: [GreeksResult for CALL, GreeksResult for PUT]}}

    # --- Expected Move / Theta (Phase D) ---
    expected_move_analysis: Optional[dict[float, dict[str, list["ExpectedMoveAnalysis"]]]] = None
    # Format: {strike: {expiry_date: [ExpectedMoveAnalysis for CALL, ExpectedMoveAnalysis for PUT]}}
    theta_decay_schedules: Optional[dict[str, list["ThetaDecayDay"]]] = None
    # Format: {"NIFTY_{strike}{CE/PE}_{expiry}": [ThetaDecayDay, ...]}

    # --- Suitability Scoring (Phase E) ---
    suitability_scores: Optional[List["SuitabilityScore"]] = None

    def is_field_available(self, field_name: str) -> bool:
        """Check if a field has valid, available data."""
        fm: FieldMeta = getattr(self, field_name, None)
        if fm is None or not isinstance(fm, FieldMeta):
            return False
        return fm.quality in ("GOOD", "PARTIAL") and fm.status not in (
            "UNAVAILABLE", "UNKNOWN", "UNSUPPORTED", "INVALID_INSTRUMENT",
            "API_ERROR", "AUTH_ERROR", "MARKET_CLOSED",
        )

    def get(self, field_name: str, default=None):
        """Get a field's value if available, else default."""
        if self.is_field_available(field_name):
            return getattr(self, field_name).value
        return default

    def critical_fields_missing(self) -> list[str]:
        """Return list of critical fields that are unavailable.

        These are the minimum fields needed for the verdict engine to
        produce any directional output. If these are missing, the
        verdict must be INSUFFICIENT DATA.

        Derived metrics (PCR, OI changes, A/D ratio, VWAP, RSI, RV)
        are NOT critical infrastructure fields. Their components
        score 0 when unavailable rather than failing the verdict.
        """
        critical = [
            "nifty_spot",
        ]
        return [f for f in critical if not self.is_field_available(f)]

    def field_status_summary(self) -> dict[str, dict]:
        """Get status summary for all fields (for data health panel)."""
        result = {}
        for fname in self._field_names():
            fm: FieldMeta = getattr(self, fname)
            if isinstance(fm, FieldMeta) and fm.status != "UNKNOWN":
                result[fname] = {
                    "value": fm.value,
                    "status": fm.status,
                    "source": fm.source,
                    "quality": fm.quality,
                    "freshness": fm.freshness_seconds,
                    "diagnostic": fm.diagnostic_reason,
                    "latency_ms": fm.provider_latency_ms,
                }
        return result

    def _field_names(self) -> list[str]:
        """Get all FieldMeta field names."""
        return [f for f in dir(self) if isinstance(getattr(self, f, None), FieldMeta)]

    def compute_data_quality(self) -> dict:
        """Evaluate overall data quality from all fields."""
        all_fields = self._field_names()
        total = len(all_fields)
        if total == 0:
            return {"level": "Poor", "emoji": "🔴", "good": 0, "partial": 0, "total": 0}

        good = sum(1 for f in all_fields if getattr(self, f).quality == "GOOD")
        partial = sum(1 for f in all_fields if getattr(self, f).quality == "PARTIAL")
        live = sum(1 for f in all_fields if getattr(self, f).status == "LIVE")

        if good >= total * 0.7:
            return {"level": "Good", "emoji": "🟢", "good": good, "partial": partial, "total": total, "live": live}
        elif good + partial >= total * 0.5:
            return {"level": "Partial", "emoji": "🟡", "good": good, "partial": partial, "total": total, "live": live}
        else:
            return {"level": "Poor", "emoji": "🔴", "good": good, "partial": partial, "total": total, "live": live}
