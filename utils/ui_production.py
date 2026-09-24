"""Production Sidebar — Market state, data health, navigation.

No mock mode, no decorative icons. Prioritizes:
1. Market state
2. Data health
3. Source transparency
4. Navigation
"""

from __future__ import annotations
import streamlit as st
from datetime import datetime, timezone
from typing import Optional

from models.snapshot import MarketSnapshot, FieldMeta
from utils.market_hours import market
from providers.source_registry import registry
from providers.cache import cache
from providers.ws_health import ws_monitor, StreamState
from providers.conflict_detector import conflict_detector


def render_production_sidebar(snap: Optional[MarketSnapshot] = None):
    """Render the production sidebar with market state, data health, nav."""

    with st.sidebar:
        # ─── Brand ──────────────────────────────────────────────
        st.markdown("### NIFTY 50")
        st.caption("Market Intelligence")
        st.markdown("---")

        # ─── Market State ───────────────────────────────────────
        market_info = market.get_state_display()
        state_color = {
            "OPEN": "🟢",
            "PRE_MARKET": "🟠",
            "CLOSING": "🟡",
            "CLOSED": "🔴",
        }.get(market_info["label"], "⚪")

        st.markdown("**MARKET**")
        st.markdown(f"{state_color} {market_info['label']}")

        time_str = market_info.get("time", "")
        next_event = market_info.get("next_event", "")
        if time_str and next_event:
            st.caption(f"{time_str} · {next_event}")
        elif time_str:
            st.caption(time_str)
        elif next_event:
            st.caption(next_event)

        st.markdown("---")

        # ─── Navigation ─────────────────────────────────────────
        st.markdown("**VIEWS**")
        page = st.radio(
            "Dashboard",
            ["Intraday", "Weekly", "Expiry", "Factor Monitor"],
            index=0,
            horizontal=False,
            label_visibility="collapsed",
        )

        st.markdown("---")

        # ─── System Status ──────────────────────────────────────
        st.markdown("**SYSTEM STATUS**")
        try:
            provider_stats = registry.get_all_provider_stats()
            total = len(provider_stats)
            healthy = sum(1 for s in provider_stats.values() if s.is_healthy)

            if total > 0:
                status_emoji = "🟢" if healthy == total else "🟡" if healthy > 0 else "🔴"
                st.caption(f"{status_emoji} {healthy}/{total} sources healthy")
            else:
                st.caption("⚪ No source data yet")

            ws_health = ws_monitor.health
            if ws_health.connected_at or ws_health.state != StreamState.DISCONNECTED:
                stream_emoji = ws_health.state.emoji
                if ws_health.state == StreamState.CONNECTED:
                    stream_label = "Live connection"
                elif ws_health.state == StreamState.DELAYED:
                    stream_label = "Stream delayed"
                elif ws_health.state == StreamState.RECONNECTING:
                    stream_label = "Reconnecting..."
                elif ws_health.state == StreamState.FAILED:
                    stream_label = "Connection failed"
                else:
                    stream_label = ws_health.state.label
                st.caption(f"{stream_emoji} {stream_label}")

                if ws_health.state == StreamState.FAILED:
                    st.warning("WebSocket unavailable; using polling fallback")
                elif ws_health.state == StreamState.RECONNECTING:
                    st.info("WebSocket reconnecting...")

            all_last_fetch = [s.last_fetch_at for s in provider_stats.values() if s.last_fetch_at]
            if all_last_fetch:
                latest = max(all_last_fetch)
                st.caption(f"Updated {latest.strftime('%H:%M:%S')}")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔄 Refresh", key="sidebar_refresh", width='stretch'):
                    cache.clear()
                    st.rerun()
            with col2:
                auto_refresh = st.checkbox("Auto", value=False, key="auto_refresh", help="Auto-refresh every 30s")

            if auto_refresh:
                import time
                time.sleep(30)
                st.rerun()

            with st.expander("▸ Source Details", expanded=False):
                field_sources = registry.get_field_sources_snapshot()
                for field_name, info in list(field_sources.items())[:20]:
                    provider = info.get("provider", "?")
                    if provider == "AngelBroking":
                        provider = "Angel One"
                    source = info.get("source", "?")
                    status = info.get("status", "?")
                    freshness = info.get("freshness")
                    freshness_str = f" ({freshness:.0f}s)" if freshness else ""
                    fetch_count = info.get("fetch_count", 0)
                    fetch_str = f" · #{fetch_count}" if fetch_count else ""
                    st.caption(f"{field_name}: {provider} ({source}) [{status}]{freshness_str}{fetch_str}")
        except Exception as e:
            st.caption(f"Source info unavailable: {e}")

        st.markdown("---")

        # ─── Footer ─────────────────────────────────────────────
        st.caption("Research only. Not financial advice.")

        return page


def render_field_detail(fm: FieldMeta, field_name: str = ""):
    """Render detailed field info for the diagnostics panel."""
    if fm.status == "UNKNOWN" and fm.value is None:
        return

    status_emoji = {
        "LIVE": "🟢",
        "DELAYED": "🟡",
        "HISTORICAL": "🔵",
        "TEMPORARILY_UNAVAILABLE": "🟠",
        "STALE": "🟠",
        "UNSUPPORTED": "⚫",
        "UNAVAILABLE": "⚫",
        "MARKET_CLOSED": "⚪",
    }.get(fm.status, "❓")

    with st.expander(f"{status_emoji} {field_name or fm.source}", expanded=False):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(f"**Value:** {fm.value if fm.value is not None else '—'}")
            st.markdown(f"**Status:** {fm.status}")
            st.markdown(f"**Source:** {fm.source}")
            st.markdown(f"**Quality:** {fm.quality}")

        with col2:
            if fm.observed_at:
                st.markdown(f"**Observed:** {fm.observed_at.strftime('%H:%M:%S')}")
            if fm.freshness_seconds is not None:
                st.markdown(f"**Age:** {fm.freshness_seconds:.1f}s")
            if fm.provider_latency_ms is not None:
                st.markdown(f"**Latency:** {fm.provider_latency_ms:.0f}ms")

        if fm.diagnostic_reason and fm.diagnostic_reason != "ok":
            st.markdown(f"**Reason:** {fm.diagnostic_message}")


def render_data_health_panel(snap: MarketSnapshot):
    """Render the full data health panel with field-level details."""
    st.subheader("Data Health")

    # Overall
    quality = snap.compute_data_quality()
    st.markdown(f"### {quality['emoji']} {quality['level']}")
    st.caption(f"Fields: {quality.get('live', 0)} live / {quality['good']} good / {quality['partial']} partial / {quality['total']} total")

    # Field categories
    categories = {
        "Market": ["nifty_spot", "nifty_change", "nifty_change_pct", "nifty_open", "nifty_high", "nifty_low"],
        "Futures": ["futures_price", "futures_change_pct", "futures_oi", "futures_oi_change", "futures_expiry"],
        "Options": ["atm_strike", "call_oi", "put_oi", "pcr", "atm_iv", "max_pain"],
        "Momentum": ["vwap", "rsi", "atr", "relative_volume"],
        "Participation": ["advances", "declines", "advance_decline_ratio", "sector_performance"],
        "Macro": ["crude_price", "usd_inr", "us10y_yield", "india_vix"],
        "Capital Flows": ["fii_flow_1d", "dii_flow_1d"],
    }

    for cat_name, fields in categories.items():
        with st.expander(f"**{cat_name}**", expanded=False):
            for fname in fields:
                fm: FieldMeta = getattr(snap, fname, None)
                if isinstance(fm, FieldMeta):
                    render_field_detail(fm, fname)
