"""Lightweight data refresh helper for live fragments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from providers.registry import list_providers, get_provider
from providers.merger import merge_snapshots
from models.snapshot import MarketSnapshot


def get_section_snapshot(max_age_seconds: int = 30) -> MarketSnapshot:
    """Return a fresh merged snapshot, reusing provider caches when possible.

    Falls back to the last cached snapshot if fresh fetch fails, and marks it
    stale via `data_status` without inventing new values.
    """
    cache_key = "section_snap"
    cache_ts_key = "section_snap_ts"
    now = datetime.now(timezone.utc)

    session = __import__("streamlit").runtime.scriptrunner.get_script_run_ctx().session_state
    cached_snap: Optional[MarketSnapshot] = session.get(cache_key)
    cached_ts = session.get(cache_ts_key)

    if cached_snap is not None and cached_ts is not None:
        age = (now - cached_ts).total_seconds()
        if age < max_age_seconds:
            return cached_snap

    try:
        results = []
        for name in list_providers():
            try:
                provider = get_provider(name)
                snap = provider.fetch()
                if snap and snap.data_status != "UNAVAILABLE":
                    results.append(snap)
            except Exception:
                continue

        if results:
            merged = results[0]
            for additional in results[1:]:
                merged = merge_snapshots(merged, additional)
            session[cache_key] = merged
            session[cache_ts_key] = now
            return merged
    except Exception:
        pass

    if cached_snap is not None:
        try:
            cached_snap = MarketSnapshot(
                snapshot_timestamp=cached_snap.snapshot_timestamp,
                timezone=cached_snap.timezone,
                source=cached_snap.source,
                data_status="STALE",
                missing_fields=cached_snap.missing_fields,
            )
            for attr in [
                "nifty_spot", "nifty_change", "nifty_change_pct", "nifty_open",
                "nifty_high", "nifty_low", "futures_price", "futures_change_pct",
                "futures_oi", "futures_oi_change", "futures_expiry",
                "atm_strike", "call_oi", "put_oi", "pcr", "atm_iv", "max_pain",
                "advances", "declines", "unchanged", "advance_decline_ratio",
                "sector_performance", "india_vix", "crude_price", "usd_inr",
                "us10y_yield", "fii_flow_1d", "fii_flow_5d", "fii_flow_20d",
                "fii_flow_month", "dii_flow_1d", "dii_flow_5d", "dii_flow_20d",
                "dii_flow_month", "fed_rate", "india_policy_rate", "inflation",
                "gdp_growth", "pmi", "earnings_growth", "relative_volume",
                "vwap", "rsi", "atr", "total_option_volume", "greeks_by_strike",
                "expected_move_analysis", "theta_decay_schedules", "suitability_scores",
                "factor_states",
            ]:
                if hasattr(cached_snap, attr):
                    setattr(cached_snap, attr, getattr(cached_snap, attr))
        except Exception:
            pass
        return cached_snap

    return MarketSnapshot(
        source="NONE",
        data_status="UNAVAILABLE",
        missing_fields=["ALL"],
    )
