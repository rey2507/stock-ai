"""Nifty 50 Verdict Dashboard — Live Data Only.

Single-page app with sidebar navigation between Intraday and Weekly views.
No mock mode exposed to user. No separate pages/ directory.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from dotenv import load_dotenv

log = logging.getLogger(__name__)

from config import APP_NAME, APP_ICON
from models.snapshot import MarketSnapshot, FieldMeta
from providers.registry import get_provider, list_providers
from providers.merger import merge_snapshots
from providers.streaming import get_streaming_manager
from utils.market_hours import market
from utils.ui_production import render_production_sidebar
from utils.ui import data_source_banner, verdict_panel, component_table, evidence_detail, contribution_panel, _field_display_value, colored_metric, render_verdict_header, data_quality_tooltip, render_related_indices_section
from utils.history_ui import verdict_history_panel, what_changed_panel, compute_persistence, compute_expiry_context, compute_market_regime, compute_trend_strength
from utils.expiry_ui import render_expiry_dashboard
from utils.factor_card import render_factor_monitor, get_latest_factor_snapshot
from models.factor_state import FactorDirection
from providers.history_manager import history_manager

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

# --- Fetch live data from all providers ---
snapshots = []
for name in list_providers():
    try:
        provider = get_provider(name)
        snap = provider.fetch()
        if snap and snap.data_status != "UNAVAILABLE":
            snapshots.append(snap)
    except Exception:
        pass

if snapshots:
    merged = snapshots[0]
    for additional in snapshots[1:]:
        merged = merge_snapshots(merged, additional)
    snap = merged
else:
    snap = MarketSnapshot(source="NONE", data_status="UNAVAILABLE", missing_fields=["ALL"])


# ─── Intraday View ─────────────────────────────────────────────

def _render_candlestick_chart(snap: MarketSnapshot) -> None:
    """Render interactive candlestick chart with VWAP and ATR."""
    from providers.registry import get_provider
    from providers.cache import cache

    # Try to get candles from AngelProvider cache or fetch fresh
    candles = None
    try:
        angel = get_provider("AngelProvider")
        # Access the internal cache for candles
        # Candles are cached with key like "candles_99926000_FIVE_MINUTE_5d"
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

    # Parse candles into DataFrame
    import pandas as pd
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

    # Compute VWAP
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
    df["cum_tp_vol"] = (df["tp"] * df["volume"]).cumsum()
    df["cum_vol"] = df["volume"].cumsum()
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, float("nan"))

    # Compute ATR (14-period)
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

    # Create subplots: price + volume
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
        subplot_titles=("NIFTY Price", "Volume"),
    )

    # Candlestick
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

    # VWAP line
    fig.add_trace(
        go.Scatter(
            x=df["timestamp"],
            y=df["vwap"],
            name="VWAP",
            line=dict(color="#2196f3", width=2),
        ),
        row=1, col=1,
    )

    # ATR bands (upper/lower)
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

    # Volume bars
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

    # Layout
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

    st.plotly_chart(fig, use_container_width=True)


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


def _render_intraday(snap: MarketSnapshot):
    from engines.intraday_verdict_v2 import compute_verdict

    st.header("Intraday Dashboard")
    data_source_banner(snap)

    if snap.data_status == "UNAVAILABLE":
        st.error("No live data available.")
        return

    # Market snapshot
    st.subheader("Market")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        # Prefer live WebSocket tick if available
        _spot_display, _spot_status, _ = _field_display_value(snap.nifty_spot)
        if _stream_mgr:
            _tick = _stream_mgr.latest_tick
            if _tick and _tick.ltp:
                _spot_display = f"{_tick.ltp:,.2f}"
                _spot_status = "LIVE"
        st.metric("Nifty Spot (LIVE)" if _stream_mgr and _stream_mgr.is_running else "Nifty Spot", _spot_display, _spot_status)
    with c2:
        display, status, _ = _field_display_value(snap.futures_price)
        colored_metric("Nifty Futures", display, "gray", status)
    with c3:
        display, status, color = _field_display_value(snap.nifty_change_pct)
        colored_metric("Nifty Change %", display, color, status)
    with c4:
        display, status, _ = _field_display_value(snap.india_vix)
        colored_metric("India VIX", display, "gray", status)

    # Related indices
    render_related_indices_section(snap)

    st.markdown("---")
    
    # ── 1. VERDICT ───────────────────────────────────────────────
    st.subheader("Dashboard State")
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

    # Primary verdict header
    render_verdict_header(result, snap)

    # What changed
    what_changed_panel(result)

    # Why this conclusion
    contribution_panel(result)
    verdict_panel(result)

    # Factor context
    if result.factor_evidence:
        with st.expander("Factor Context", expanded=False):
            for evidence in result.factor_evidence:
                st.caption(evidence)

    st.markdown("---")
    
    # ── 2. TECHNICAL / DERIVATIVES ──────────────────────────────
    
    # Candlestick chart
    st.subheader("Price Action")
    try:
        _render_candlestick_chart(snap)
    except Exception as e:
        st.caption(f"Chart unavailable: {e}")
    
    # Futures + Options
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

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        display, status, _ = _field_display_value(snap.call_oi)
        colored_metric("Call OI", display, "gray", status)
        chg_display, chg_status, chg_color = _field_display_value(snap.call_oi_change)
        colored_metric("Call OI Change", chg_display, chg_color, chg_status)
    with c2:
        display, status, _ = _field_display_value(snap.put_oi)
        colored_metric("Put OI", display, "gray", status)
        chg_display, chg_status, chg_color = _field_display_value(snap.put_oi_change)
        colored_metric("Put OI Change", chg_display, chg_color, chg_status)
    with c3:
        display, status, color = _field_display_value(snap.pcr)
        colored_metric("PCR", display, color, status)
    with c4:
        display, status, _ = _field_display_value(snap.atm_iv)
        colored_metric("ATM IV", display, "gray", status)
    
    c1, c2 = st.columns(2)
    with c1:
        display, status, _ = _field_display_value(snap.max_pain)
        colored_metric("Max Pain", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.total_option_volume)
        colored_metric("Options Volume", display, "gray", status)
    
    # Volume
    st.subheader("Volume")
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(snap.nifty_volume)
        colored_metric("NIFTY Volume (EOD)", display, "gray", status)
        st.caption("Source: nselib (daily)")
    with c2:
        display, status, _ = _field_display_value(snap.total_option_volume)
        colored_metric("Options Volume (Intraday)", display, "gray", status)
        st.caption("Source: NSE option chain")
    with c3:
        rel_vol = snap.get("relative_volume")
        if rel_vol is not None:
            display, status, color = _field_display_value(snap.relative_volume)
            colored_metric("Relative Volume", display, color, status)
            st.caption("vs 20-period avg (candles)")
        else:
            st.caption("Relative volume: UNAVAILABLE")
    
    # Participation
    st.subheader("Participation")
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(snap.advances)
        colored_metric("Advances", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.declines)
        colored_metric("Declines", display, "gray", status)
    with c3:
        display, status, color = _field_display_value(snap.advance_decline_ratio)
        colored_metric("A/D Ratio", display, color, status)
    
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
            st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.caption("Sector performance unavailable")
    
    # Momentum
    st.subheader("Momentum")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        display, status, _ = _field_display_value(snap.vwap)
        colored_metric("VWAP", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.rsi)
        colored_metric("RSI", display, "gray", status)
    with c3:
        display, status, _ = _field_display_value(snap.relative_volume)
        colored_metric("Volume vs Avg", display, "gray", status)
    with c4:
        price = snap.get("nifty_spot")
        vwap = snap.get("vwap")
        if price is not None and vwap is not None:
            above = "Yes" if price > vwap else "No"
        else:
            above = "UNAVAILABLE"
        st.metric("Price Above VWAP", above)
    
    st.markdown("---")
    
    # ── 3. HISTORY & DIAGNOSTICS ────────────────────────────────
    st.subheader("Verdict History")
    verdict_history_panel(limit=10)
    st.markdown("---")
    
    with st.expander("Diagnostics", expanded=False):
        st.caption("Non-sensitive pipeline status")
        try:
            from providers.registry import get_provider
            provider = get_provider("AngelProvider")
            diag = provider.diagnostics
            st.markdown(f"**Angel One:** {'🟢 Connected' if diag.get('angel_connected') else '🔴 Error'}")
            st.markdown(f"**Futures contract:** {'✅ ' + str(diag.get('futures_token')) if diag.get('futures_contract_discovered') else '❌ Not discovered'}")
            st.markdown(f"**Expiry:** {diag.get('expiry') or '❌ None'}")
            st.markdown(f"**ATM strike:** {diag.get('atm_strike') or '❌ None'}")
            st.markdown(f"**Strikes:** {diag.get('strikes_count', 0)} (CE: {diag.get('ce_count', 0)}, PE: {diag.get('pe_count', 0)})")
        except Exception as e:
            st.caption(f"Diagnostics unavailable: {e}")


# ─── Weekly View ───────────────────────────────────────────────

def _render_weekly(snap: MarketSnapshot):
    from engines.weekly_verdict_v2 import compute_verdict

    st.header("Weekly Dashboard")
    data_source_banner(snap)

    if snap.data_status == "UNAVAILABLE":
        st.error("No live data available.")
        return

    st.markdown("---")
    
    # ── 1. VERDICT ───────────────────────────────────────────────
    st.subheader("Market Verdict")
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

    # Primary verdict header
    render_verdict_header(result, snap)

    # What changed
    what_changed_panel(result)

    # Why this conclusion
    contribution_panel(result)
    verdict_panel(result)

    # Factor context
    if result.factor_states:
        with st.expander("Factor Context", expanded=False):
            for factor, states in result.factor_states.items():
                for state in states:
                    if state.timeframe == "20d" and state.direction != FactorDirection.INSUFFICIENT_DATA:
                        st.caption(f"{factor} (20d): {state.direction.value} — {state.nifty_interpretation}")

    st.markdown("---")
    
    # ── 2. SUPPORTING MARKET DATA ───────────────────────────────
    
    # Capital Flows
    st.subheader("Capital Flows")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**FII**")
        _render_flow_group([
            ("Daily", snap.fii_flow_1d, "₹", "Cr"),
            ("5-Day", snap.fii_flow_5d, "₹", "Cr"),
            ("20-Day", snap.fii_flow_20d, "₹", "Cr"),
            ("Monthly", snap.fii_flow_month, "₹", "Cr"),
        ])
    with c2:
        st.markdown("**DII**")
        _render_flow_group([
            ("Daily", snap.dii_flow_1d, "₹", "Cr"),
            ("5-Day", snap.dii_flow_5d, "₹", "Cr"),
            ("20-Day", snap.dii_flow_20d, "₹", "Cr"),
            ("Monthly", snap.dii_flow_month, "₹", "Cr"),
        ])
    
    # Macro + Economy
    st.subheader("Macro & Economy")
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(snap.crude_price)
        colored_metric("Brent Crude", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.usd_inr)
        colored_metric("USD/INR", display, "gray", status)
    with c3:
        display, status, _ = _field_display_value(snap.us10y_yield)
        colored_metric("US 10Y Yield", display, "gray", status)
    
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(snap.fed_rate)
        colored_metric("Fed Rate", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.india_policy_rate)
        colored_metric("India Policy Rate", display, "gray", status)
    with c3:
        display, status, _ = _field_display_value(snap.inflation)
        colored_metric("Inflation (CPI)", display, "gray", status)
    
    c1, c2 = st.columns(2)
    with c1:
        display, status, _ = _field_display_value(snap.gdp_growth)
        colored_metric("GDP Growth", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.pmi)
        colored_metric("PMI", display, "gray", status)
    
    display, status, _ = _field_display_value(snap.earnings_growth)
    colored_metric("Nifty Earnings Growth", display, "gray", status)
    
    # Market + Participation
    st.subheader("Market & Participation")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        display, status, color = _field_display_value(snap.nifty_spot)
        colored_metric("Nifty Spot", display, color, status)
    with c2:
        display, status, _ = _field_display_value(snap.futures_price)
        colored_metric("Nifty Futures", display, "gray", status)
    with c3:
        display, status, color = _field_display_value(snap.advance_decline_ratio)
        colored_metric("A/D Ratio", display, color, status)
    with c4:
        display, status, _ = _field_display_value(snap.india_vix)
        colored_metric("India VIX", display, "gray", status)
    
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(snap.advances)
        colored_metric("Advances", display, "gray", status)
    with c2:
        display, status, _ = _field_display_value(snap.declines)
        colored_metric("Declines", display, "gray", status)
    with c3:
        display, status, color = _field_display_value(snap.nifty_change_pct)
        colored_metric("Nifty Change %", display, color, status)

    # Related indices
    render_related_indices_section(snap)
    
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
            st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.caption("Sector performance unavailable")
    
    # Derivatives
    st.subheader("Derivatives")
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, color = _field_display_value(snap.pcr)
        colored_metric("Weekly PCR", display, color, status)
    with c2:
        display, status, color = _field_display_value(snap.futures_oi_change)
        colored_metric("Futures OI Change", display, color, status)
    with c3:
        display, status, _ = _field_display_value(snap.atm_iv)
        colored_metric("ATM IV", display, "gray", status)
    
    c1, c2 = st.columns(2)
    with c1:
        display, status, _ = _field_display_value(snap.call_oi)
        colored_metric("Call OI", display, "gray", status)
        chg_display, chg_status, chg_color = _field_display_value(snap.call_oi_change)
        colored_metric("Call OI Change", chg_display, chg_color, chg_status)
    with c2:
        display, status, _ = _field_display_value(snap.put_oi)
        colored_metric("Put OI", display, "gray", status)
        chg_display, chg_status, chg_color = _field_display_value(snap.put_oi_change)
        colored_metric("Put OI Change", chg_display, chg_color, chg_status)
    
    display, status, _ = _field_display_value(snap.max_pain)
    colored_metric("Max Pain", display, "gray", status)
    
    # Volume
    st.subheader("Volume")
    c1, c2 = st.columns(2)
    with c1:
        display, status, _ = _field_display_value(snap.nifty_volume)
        colored_metric("NIFTY Volume (EOD)", display, "gray", status)
        st.caption("Source: nselib (daily)")
    with c2:
        display, status, _ = _field_display_value(snap.total_option_volume)
        colored_metric("Options Volume (Intraday)", display, "gray", status)
        st.caption("Source: NSE option chain")
    
    st.markdown("---")
    
    # ── 3. HISTORY & DIAGNOSTICS ────────────────────────────────
    st.subheader("Verdict History")
    verdict_history_panel(limit=10)
    st.markdown("---")
    
    with st.expander("Diagnostics", expanded=False):
        st.caption("Non-sensitive pipeline status")
        try:
            from providers.registry import get_provider
            provider = get_provider("AngelProvider")
            diag = provider.diagnostics
            st.markdown(f"**Angel One:** {'🟢 Connected' if diag.get('angel_connected') else '🔴 Error'}")
            st.markdown(f"**Futures contract:** {'✅ ' + str(diag.get('futures_token')) if diag.get('futures_contract_discovered') else '❌ Not discovered'}")
            st.markdown(f"**Expiry:** {diag.get('expiry') or '❌ None'}")
            st.markdown(f"**ATM strike:** {diag.get('atm_strike') or '❌ None'}")
            st.markdown(f"**Strikes:** {diag.get('strikes_count', 0)} (CE: {diag.get('ce_count', 0)}, PE: {diag.get('pe_count', 0)})")
        except Exception as e:
            st.caption(f"Diagnostics unavailable: {e}")


# ─── Sidebar + Route ───────────────────────────────────────────

page = render_production_sidebar(snap)

if page == "Intraday":
    _render_intraday(snap)
elif page == "Expiry":
    render_expiry_dashboard(snap)
elif page == "Factor Monitor":
    from providers.registry import get_provider
    from models.factor_state import FactorSnapshot

    factor_snap = None

    if snap and hasattr(snap, "factor_states") and snap.factor_states:
        try:
            from datetime import datetime
            factor_snap = FactorSnapshot(
                timestamp=snap.snapshot_timestamp or datetime.now(),
                factors=snap.factor_states,
            )
        except Exception:
            factor_snap = None

    if factor_snap is None:
        try:
            provider = get_provider("FactorDirection")
            provider_snap = provider.fetch()
            if provider_snap and provider_snap.factor_states:
                from datetime import datetime
                factor_snap = FactorSnapshot(
                    timestamp=provider_snap.snapshot_timestamp or datetime.now(),
                    factors=provider_snap.factor_states,
                )
        except Exception:
            factor_snap = None

    if factor_snap is None:
        try:
            factor_snap = get_latest_factor_snapshot()
        except Exception:
            factor_snap = None

    render_factor_monitor(factor_snap)
else:
    _render_weekly(snap)
