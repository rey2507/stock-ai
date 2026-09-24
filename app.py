"""Nifty 50 Verdict Dashboard — Live Data Only.

Single-page app with sidebar navigation between Intraday and Weekly views.
No mock mode exposed to user. No separate pages/ directory.
"""

import streamlit as st
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dotenv import load_dotenv

log = logging.getLogger(__name__)

from config import APP_NAME, APP_ICON
from models.snapshot import MarketSnapshot, FieldMeta
from providers.registry import get_provider, list_providers
from providers.merger import merge_snapshots
from providers.streaming import get_streaming_manager
from utils.market_hours import market
from utils.ui_production import render_production_sidebar
from utils.provider_manager import provider_manager
from utils.ui import data_source_banner, verdict_panel, component_table, evidence_detail, contribution_panel, _field_display_value, colored_metric, render_verdict_header, data_quality_tooltip, render_related_indices_section, render_conclusion_bar, render_what_changed, render_evidence_group, render_diagnostics
from utils.history_ui import verdict_history_panel, what_changed_panel, compute_persistence, compute_expiry_context, compute_market_regime, compute_trend_strength
from utils.expiry_ui import render_expiry_dashboard
from utils.factor_card import render_factor_monitor, get_latest_factor_snapshot
from models.news_model import NewsItem
from providers.history_manager import history_manager
from ui.news import render_news_section

load_dotenv()

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Start SmartAPI WebSocket streaming in background ---
_stream_mgr = None
try:
    _angel = get_provider("AngelProvider")
    if not _angel._client.is_connected:
        _angel._client.connect()
    _stream_mgr = get_streaming_manager(_angel)
    if _stream_mgr and not _stream_mgr.is_running:
        _stream_mgr.start()
except Exception:
    pass

# --- News Providers Initialization (Phase 1A) ---
_news_providers = None
_news_aggregator = None
try:
    from providers.news_provider import get_default_providers
    from providers.news_aggregator import NewsAggregator
    from config import NEWS_ENABLED, NEWS_QUERY, NEWS_MAX_AGE_HOURS, NEWS_MAX_HEADLINES

    if NEWS_ENABLED:
        _news_providers = get_default_providers()
        _news_aggregator = NewsAggregator(
            providers=_news_providers,
            max_age_hours=NEWS_MAX_AGE_HOURS,
            limit_per_provider=NEWS_MAX_HEADLINES,
        )
except Exception:
    _news_providers = None
    _news_aggregator = None

# --- Background live fetch fragment (no full-page rerun) ---
@st.fragment(run_every=10)
def _live_fetch() -> None:
    def _fetch_provider(name: str):
        try:
            provider = get_provider(name)
            snap = provider.fetch()
            if snap and snap.data_status != "UNAVAILABLE":
                provider_manager.record_success(name)
                return (name, snap)
            provider_manager.record_failure(name)
        except Exception:
            provider_manager.record_failure(name)
        return (name, None)

    snapshots = []
    for domain in ["market_data", "options", "futures", "macro", "capital_flows", "sector", "greeks", "factors"]:
        snap, source = provider_manager.fetch_with_fallback(domain)
        if snap is not None:
            snapshots.append(snap)

    tried = set()
    for snap in snapshots:
        if hasattr(snap, 'source'):
            tried.add(snap.source)
    remaining = [p for p in list_providers() if p not in tried]
    for name in remaining:
        _, snap = _fetch_provider(name)
        if snap is not None:
            snapshots.append(snap)

    if snapshots:
        merged = snapshots[0]
        for additional in snapshots[1:]:
            merged = merge_snapshots(merged, additional)
        st.session_state["merged_snapshot"] = merged
        provider_manager._last_snapshot = merged
        provider_manager._last_snapshot_ts = datetime.now(timezone.utc)
    else:
        st.session_state["merged_snapshot"] = MarketSnapshot(source="NONE", data_status="UNAVAILABLE", missing_fields=["ALL"])

    # --- News fetch (non-blocking) ---
    if _news_aggregator:
        try:
            from config import NEWS_QUERY, NEWS_MAX_AGE_HOURS, NEWS_MAX_HEADLINES
            news_snap = _news_aggregator.fetch_and_aggregate(query=NEWS_QUERY, limit=NEWS_MAX_HEADLINES)
            st.session_state["news_snapshot"] = news_snap
        except Exception as e:
            log.warning(f"News fetch failed: {e}")
            st.session_state["news_snapshot"] = None

_live_fetch()

# --- News render fragment ---
@st.fragment(run_every=60)
def _render_news_fragment() -> None:
    news_snap = st.session_state.get("news_snapshot")
    if news_snap and news_snap.items:
        render_news_section(news_snap)

# --- Sidebar Navigation ---
page = render_production_sidebar()

# --- Stable app shell that reads from session_state ---
snap = st.session_state.get("merged_snapshot")
if snap is None:
    snap = MarketSnapshot(source="NONE", data_status="UNAVAILABLE", missing_fields=["ALL"])

@st.fragment(run_every=30)
def _render_candlestick_chart() -> None:
    """Render interactive candlestick chart with VWAP and ATR."""
    from utils.data_refresh import get_section_snapshot
    snap = get_section_snapshot(max_age_seconds=30)
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from providers.registry import get_provider
    from providers.cache import cache

    # Try to get candles from AngelProvider cache or fetch fresh
    candles = None
    try:
        angel = get_provider("AngelProvider")
        for key in list(cache._store.keys()):
            if key.startswith("candles_") and "FIVE_MINUTE" in key and "5d" in key:
                entry = cache.get(key)
                if entry and entry.value:
                    candles = entry.value
                    break
    except Exception:
        pass

    if not candles or len(candles) < 2:
        st.caption("Insufficient candle data for chart. Need at least 2 candles.")
        return

    df_data = []
    for c in candles:
        try:
            ts = c[0]
            o = float(c[1])
            h = float(c[2])
            l = float(c[3])
            cl = float(c[4])
            v = float(c[5]) if len(c) > 5 else 0
            df_data.append({
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": cl,
                "volume": v,
            })
        except (IndexError, ValueError, TypeError):
            continue

    if not df_data:
        st.caption("No valid candle data available.")
        return

    df = pd.DataFrame(df_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp")

    if len(df) < 2:
        st.caption("Insufficient valid candle data for chart.")
        return

    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
    df["cum_tp_vol"] = (df["tp"] * df["volume"]).cumsum()
    df["cum_vol"] = df["volume"].cumsum()
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, float("nan"))

    df["prev_close"] = df["close"].shift(1)
    df["tr"] = df.apply(
        lambda row: max(
            row["high"] - row["low"],
            abs(row["high"] - row["prev_close"]) if pd.notna(row["prev_close"]) else 0,
            abs(row["low"] - row["prev_close"]) if pd.notna(row["prev_close"]) else 0,
        ),
        axis=1,
    )
    df["atr"] = df["tr"].rolling(window=14, min_periods=1).mean()

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
        subplot_titles=("NIFTY Price", "Volume"),
    )

    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="NIFTY",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=df["timestamp"],
            y=df["vwap"],
            name="VWAP",
            line=dict(color="#2196f3", width=2),
        ),
        row=1, col=1,
    )

    latest_close = df["close"].iloc[-1]
    latest_atr = df["atr"].iloc[-1] if pd.notna(df["atr"].iloc[-1]) else 0
    if latest_atr > 0:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["close"] + latest_atr,
                name="ATR Upper",
                line=dict(color="rgba(255,165,0,0.5)", width=1, dash="dot"),
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["close"] - latest_atr,
                name="ATR Lower",
                line=dict(color="rgba(255,165,0,0.5)", width=1, dash="dot"),
                fill="tonexty",
                fillcolor="rgba(255,165,0,0.1)",
            ),
            row=1, col=1,
        )

    colors = [
        "#26a69a" if df["close"].iloc[i] >= df["open"].iloc[i] else "#ef5350"
        for i in range(len(df))
    ]
    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["volume"],
            name="Volume",
            marker_color=colors,
            opacity=0.7,
        ),
        row=2, col=1,
    )

    fig.update_layout(
        height=500,
        xaxis_rangeslider_visible=False,
        showlegend=True,
        margin=dict(l=0, r=0, t=30, b=0),
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="Time", row=2, col=1)
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)

    st.plotly_chart(fig, width='stretch')


def _render_flow_group(items: list[tuple[str, FieldMeta, str, str]]) -> None:
    """Render a group of flow metrics (FII or DII) with consistent formatting.
    
    items: list of (label, field_meta, prefix, suffix)
    """
    for label, fm, prefix, suffix in items:
        display, status, color = _field_display_value(fm)
        if "Unav" in display:
            colored_metric(label, display, "gray", status)
        else:
            colored_metric(label, f"{prefix}{display} {suffix}", color, status)


@st.fragment(run_every=15)
def _render_related_indices_fragment() -> None:
    from utils.data_refresh import get_section_snapshot
    snap = get_section_snapshot(max_age_seconds=15)
    render_related_indices_section(snap)


@st.fragment(run_every=30)
def _render_expiry_dashboard_fragment() -> None:
    from utils.data_refresh import get_section_snapshot
    snap = get_section_snapshot(max_age_seconds=30)
    render_expiry_dashboard(snap)


def _render_intraday(snap: MarketSnapshot):
    from engines.intraday_verdict_v2 import compute_verdict
    import pandas as pd

    st.header("Intraday Dashboard")
    data_source_banner(snap)

    if snap.data_status == "UNAVAILABLE":
        st.error("No live data available.")
        return

    # ── 1. VERDICT ───────────────────────────────────────────────
    result = compute_verdict(snap)

    # Save snapshot to history
    try:
        history_manager.save_snapshot(snap)
    except Exception as e:
        log.warning(f"Failed to save snapshot: {e}")

    # Save verdict to history (only if score changed)
    try:
        history_manager.save_verdict(result)
    except Exception as e:
        log.warning(f"Failed to save verdict: {e}")

    # Determine persistence from recent verdict history
    result.persistence = compute_persistence(result)

    # Determine expiry context
    result.expiry_context = compute_expiry_context(snap)

    # Determine market regime
    result.market_regime = compute_market_regime(snap)

    # Determine trend strength
    result.trend_strength = compute_trend_strength(result)

    # Conclusion bar
    render_conclusion_bar(
        state=result.display_label or result.direction or "UNKNOWN",
        evidence=result.data_quality or "UNKNOWN",
        regime=result.market_regime or "UNKNOWN",
        persistence=result.persistence or "UNKNOWN",
        summary=" ".join(result.reasons) if result.reasons else "",
        risk="; ".join(result.timeframe_conflicts) if result.timeframe_conflicts else "",
        trend_score=result.trend_strength,
    )

    # What changed
    what_changed_panel(result)

    st.markdown("---")

    # ── 2. WHY (EVIDENCE) ────────────────────────────────────────

    # Price & Structure
    price_bullets = []
    _spot_display, _spot_status, _ = _field_display_value(snap.nifty_spot)
    if _stream_mgr and _stream_mgr.is_running and _stream_mgr.latest_tick and _stream_mgr.latest_tick.ltp:
        _spot_display = f"{_stream_mgr.latest_tick.ltp:,.2f}"
    price_bullets.append(f"Spot: {_spot_display}")
    display, status, _ = _field_display_value(snap.futures_price)
    price_bullets.append(f"Futures: {display}")
    display, status, color = _field_display_value(snap.nifty_change_pct)
    price_bullets.append(f"Nifty Change: {display}")
    display, status, _ = _field_display_value(snap.india_vix)
    price_bullets.append(f"India VIX: {display}")
    render_evidence_group("▼ PRICE & STRUCTURE", price_bullets, expanded=True)

    # Derivatives
    deriv_bullets = []
    display, status, _ = _field_display_value(snap.call_oi)
    deriv_bullets.append(f"Call OI: {display}")
    display, status, _ = _field_display_value(snap.put_oi)
    deriv_bullets.append(f"Put OI: {display}")
    display, status, color = _field_display_value(snap.pcr)
    deriv_bullets.append(f"PCR: {display}")
    display, status, _ = _field_display_value(snap.atm_iv)
    deriv_bullets.append(f"ATM IV: {display}")
    render_evidence_group("▼ DERIVATIVES", deriv_bullets, expanded=False)

    # Participation
    part_bullets = []
    display, status, _ = _field_display_value(snap.advances)
    part_bullets.append(f"Advances: {display}")
    display, status, _ = _field_display_value(snap.declines)
    part_bullets.append(f"Declines: {display}")
    display, status, color = _field_display_value(snap.advance_decline_ratio)
    part_bullets.append(f"A/D Ratio: {display}")
    render_evidence_group("▼ PARTICIPATION", part_bullets, expanded=False)

    # Factor context
    if result.factor_evidence:
        render_evidence_group("▼ FACTOR CONTEXT", result.factor_evidence, expanded=False)

    st.markdown("---")

    # ── 3. SUPPORTING DATA ───────────────────────────────────────

    # Price Action
    st.subheader("Price Action")
    try:
        _render_candlestick_chart()
    except Exception as e:
        st.caption(f"Chart unavailable: {e}")

    # Futures + Options summary
    st.subheader("Futures & Options")
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(snap.futures_oi)
        colored_metric("Futures OI", display, "gray", status)
    with c2:
        display, status, color = _field_display_value(snap.futures_oi_change)
        colored_metric("Change in OI", display, color, status)
    with c3:
        pchg = snap.get("futures_change_pct")
        oichg = snap.get("futures_oi_change")
        if pchg is not None and oichg is not None:
            if pchg > 0 and oichg > 0:
                rel = "Price up + OI up = Bullish positioning"
            elif pchg > 0 and oichg < 0:
                rel = "Price up + OI down = Short-covering"
            elif pchg < 0 and oichg > 0:
                rel = "Price down + OI up = Bearish positioning"
            else:
                rel = "Price down + OI down = Long-unwinding"
        else:
            rel = "UNAVAILABLE"
        st.metric("Price/OI Relationship", rel)

    # Volume + Momentum as compact tables
    st.subheader("Momentum & Volume")
    momentum_rows = []
    display, status, _ = _field_display_value(snap.vwap)
    momentum_rows.append({"Indicator": "VWAP", "Value": display, "Status": status})
    display, status, _ = _field_display_value(snap.rsi)
    momentum_rows.append({"Indicator": "RSI (14)", "Value": display, "Status": status})
    display, status, _ = _field_display_value(snap.relative_volume)
    momentum_rows.append({"Indicator": "Volume vs Avg", "Value": display, "Status": status})
    price = snap.get("nifty_spot")
    vwap = snap.get("vwap")
    above = "Yes" if price is not None and vwap is not None and price > vwap else ("No" if price is not None and vwap is not None else "UNAVAILABLE")
    momentum_rows.append({"Indicator": "Price Above VWAP", "Value": above, "Status": "LIVE" if above != "UNAVAILABLE" else "UNAVAILABLE"})
    if momentum_rows:
        mom_df = pd.DataFrame(momentum_rows)
        st.dataframe(mom_df, width='stretch', hide_index=True, height=200)

    # Sector Performance
    sectors = snap.get("sector_performance")
    if sectors:
        st.markdown("**Sector Performance:**")
        sector_data = sectors.get("sectors", sectors) if isinstance(sectors, dict) else {}
        rows = []
        for s, v in sector_data.items():
            if isinstance(v, dict):
                pct = v.get("pChange", v.get("percentChange", 0))
            else:
                pct = v
            try:
                pct_f = float(pct)
                rows.append({"Sector": s, "Change %": f"{pct_f:+.2f}%"})
            except (TypeError, ValueError):
                rows.append({"Sector": s, "Change %": "UNAVAILABLE"})
        if rows:
            df = pd.DataFrame(rows)
            styled = df.style.map(
                lambda v: "color: gray" if v == "UNAVAILABLE" else (
                    f"color: {'green' if float(str(v).replace('%','').replace('+','')) > 0 else 'red' if float(str(v).replace('%','').replace('+','')) < 0 else 'gray'}"
                ),
                subset=["Change %"]
            )
            st.dataframe(styled, width='stretch', hide_index=True)
    else:
        st.caption("Sector performance unavailable")

    st.markdown("---")

    # News
    _render_news_fragment()

    st.markdown("---")

    # ── 4. HISTORY & DIAGNOSTICS ────────────────────────────────
    with st.expander("Verdict History", expanded=False):
        verdict_history_panel(limit=10)

    diag_rows = []
    try:
        from providers.registry import get_provider
        provider = get_provider("AngelProvider")
        diag = provider.diagnostics
        diag_rows.append({"Source": "Angel One", "Status": "🟢 Connected" if diag.get('angel_connected') else "🔴 Error", "Timestamp": "", "Detail": f"Token {diag.get('futures_token')}" if diag.get('futures_contract_discovered') else "Not discovered"})
        diag_rows.append({"Source": "Expiry", "Status": "🟢" if diag.get('expiry') else "🔴", "Timestamp": "", "Detail": diag.get('expiry') or "None"})
        diag_rows.append({"Source": "ATM Strike", "Status": "🟢" if diag.get('atm_strike') else "🔴", "Timestamp": "", "Detail": str(diag.get('atm_strike') or "None")})
        diag_rows.append({"Source": "Strikes", "Status": "🟢" if diag.get('strikes_count', 0) > 0 else "🔴", "Timestamp": "", "Detail": f"{diag.get('strikes_count', 0)} (CE: {diag.get('ce_count', 0)}, PE: {diag.get('pe_count', 0)})"})
    except Exception:
        pass
    render_diagnostics(diag_rows)


# ─── Weekly View ───────────────────────────────────────────────

def _render_weekly(snap: MarketSnapshot):
    from engines.weekly_verdict_v2 import compute_verdict
    import pandas as pd

    st.header("Weekly Dashboard")
    data_source_banner(snap)

    if snap.data_status == "UNAVAILABLE":
        st.error("No live data available.")
        return

    # ── 1. VERDICT ───────────────────────────────────────────────
    result = compute_verdict(snap)

    # Save verdict to history (only if score changed)
    try:
        history_manager.save_verdict(result)
    except Exception as e:
        log.warning(f"Failed to save verdict: {e}")

    # Determine persistence from recent verdict history
    result.persistence = compute_persistence(result)

    # Determine expiry context
    result.expiry_context = compute_expiry_context(snap)

    # Determine market regime
    result.market_regime = compute_market_regime(snap)

    # Determine trend strength
    result.trend_strength = compute_trend_strength(result)

    # Conclusion bar
    render_conclusion_bar(
        state=result.display_label or result.direction or "UNKNOWN",
        evidence=result.data_quality or "UNKNOWN",
        regime=result.market_regime or "UNKNOWN",
        persistence=result.persistence or "UNKNOWN",
        summary=" ".join(result.reasons) if result.reasons else "",
        risk="; ".join(result.timeframe_conflicts) if result.timeframe_conflicts else "",
        trend_score=result.trend_strength,
    )

    # What changed
    what_changed_panel(result)

    st.markdown("---")

    # ── 2. WHY (EVIDENCE) ────────────────────────────────────────

    # Market Structure
    structure_bullets = []
    display, status, _ = _field_display_value(snap.nifty_spot)
    structure_bullets.append(f"Nifty Spot: {display}")
    display, status, _ = _field_display_value(snap.futures_price)
    structure_bullets.append(f"Futures: {display}")
    display, status, color = _field_display_value(snap.advance_decline_ratio)
    structure_bullets.append(f"A/D Ratio: {display}")
    display, status, _ = _field_display_value(snap.india_vix)
    structure_bullets.append(f"India VIX: {display}")
    render_evidence_group("▼ MARKET STRUCTURE", structure_bullets, expanded=True)

    # Capital Flows
    flow_bullets = []
    display, status, _ = _field_display_value(snap.fii_flow_1d)
    flow_bullets.append(f"FII Daily: {display}")
    display, status, _ = _field_display_value(snap.dii_flow_1d)
    flow_bullets.append(f"DII Daily: {display}")
    display, status, _ = _field_display_value(snap.fii_flow_5d)
    flow_bullets.append(f"FII 5-Day: {display}")
    render_evidence_group("▼ CAPITAL FLOWS", flow_bullets, expanded=False)

    # Macro & Economy
    macro_bullets = []
    display, status, _ = _field_display_value(snap.usd_inr)
    macro_bullets.append(f"USD/INR: {display}")
    display, status, _ = _field_display_value(snap.crude_price)
    macro_bullets.append(f"Brent Crude: {display}")
    display, status, _ = _field_display_value(snap.us10y_yield)
    macro_bullets.append(f"US 10Y Yield: {display}")
    display, status, _ = _field_display_value(snap.inflation)
    macro_bullets.append(f"Inflation (CPI): {display}")
    render_evidence_group("▼ MACRO & ECONOMY", macro_bullets, expanded=False)

    # Derivatives
    deriv_bullets = []
    display, status, color = _field_display_value(snap.pcr)
    deriv_bullets.append(f"Weekly PCR: {display}")
    display, status, color = _field_display_value(snap.futures_oi_change)
    deriv_bullets.append(f"Futures OI Change: {display}")
    display, status, _ = _field_display_value(snap.atm_iv)
    deriv_bullets.append(f"ATM IV: {display}")
    render_evidence_group("▼ DERIVATIVES", deriv_bullets, expanded=False)

    # Factor context
    if result.factor_evidence:
        render_evidence_group("▼ FACTOR CONTEXT", result.factor_evidence, expanded=False)

    st.markdown("---")

    # ── 3. SUPPORTING DATA ───────────────────────────────────────

    # Related indices
    _render_related_indices_fragment()

    # Sector Performance
    sectors = snap.get("sector_performance")
    if sectors:
        st.markdown("**Sector Performance:**")
        sector_data = sectors.get("sectors", sectors) if isinstance(sectors, dict) else {}
        rows = []
        for s, v in sector_data.items():
            if isinstance(v, dict):
                pct = v.get("pChange", v.get("percentChange", 0))
            else:
                pct = v
            try:
                pct_f = float(pct)
                rows.append({"Sector": s, "Change %": f"{pct_f:+.2f}%"})
            except (TypeError, ValueError):
                rows.append({"Sector": s, "Change %": "UNAVAILABLE"})
        if rows:
            df = pd.DataFrame(rows)
            styled = df.style.map(
                lambda v: "color: gray" if v == "UNAVAILABLE" else (
                    f"color: {'green' if float(str(v).replace('%','').replace('+','')) > 0 else 'red' if float(str(v).replace('%','').replace('+','')) < 0 else 'gray'}"
                ),
                subset=["Change %"]
            )
            st.dataframe(styled, width='stretch', hide_index=True)
    else:
        st.caption("Sector performance unavailable")

    st.markdown("---")

    # News
    _render_news_fragment()

    st.markdown("---")

    # ── 4. HISTORY & DIAGNOSTICS ────────────────────────────────
    with st.expander("Verdict History", expanded=False):
        verdict_history_panel(limit=10)

    diag_rows = []
    try:
        from providers.registry import get_provider
        provider = get_provider("AngelProvider")
        diag = provider.diagnostics
        diag_rows.append({"Source": "Angel One", "Status": "🟢 Connected" if diag.get('angel_connected') else "🔴 Error", "Timestamp": "", "Detail": f"Token {diag.get('futures_token')}" if diag.get('futures_contract_discovered') else "Not discovered"})
        diag_rows.append({"Source": "Expiry", "Status": "🟢" if diag.get('expiry') else "🔴", "Timestamp": "", "Detail": diag.get('expiry') or "None"})
        diag_rows.append({"Source": "ATM Strike", "Status": "🟢" if diag.get('atm_strike') else "🔴", "Timestamp": "", "Detail": str(diag.get('atm_strike') or "None")})
        diag_rows.append({"Source": "Strikes", "Status": "🟢" if diag.get('strikes_count', 0) > 0 else "🔴", "Timestamp": "", "Detail": f"{diag.get('strikes_count', 0)} (CE: {diag.get('ce_count', 0)}, PE: {diag.get('pe_count', 0)})"})
    except Exception:
        pass
    render_diagnostics(diag_rows)


# ─── Route ────────────────────────────────────────────────

if page == "Intraday":
    _render_intraday(snap)
elif page == "Expiry":
    _render_expiry_dashboard_fragment()
elif page == "Factor Monitor":
    render_factor_monitor()
else:
    _render_weekly(snap)
