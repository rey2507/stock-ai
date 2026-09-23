"""Shared UI helpers. Consumes only MarketSnapshot and Verdict."""

import streamlit as st
from models.snapshot import MarketSnapshot, FieldMeta
from models.verdict import Verdict
from providers.data_quality import DataQualityEngine
from utils.history_ui import trend_strength_label


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


def data_quality_tooltip(snap: MarketSnapshot) -> str:
    """Generate a brief explanation of data quality based on actual field health.
    
    Returns a compact string like:
    'Most primary fields live. 2 macro/earnings fields stale or unavailable.'
    """
    if not snap or snap.data_status == "UNAVAILABLE":
        return "No data available."
    
    try:
        engine = DataQualityEngine()
        report = engine.assess(snap)
        
        parts = []
        if report.live_count > 0:
            parts.append(f"{report.live_count} live")
        if report.delayed_count > 0:
            parts.append(f"{report.delayed_count} delayed")
        if report.stale_count > 0:
            parts.append(f"{report.stale_count} stale")
        if report.unavailable_count > 0:
            parts.append(f"{report.unavailable_count} unavailable")
        
        if not parts:
            return "Quality assessment unavailable."
        
        summary = ", ".join(parts)
        
        if report.overall_quality == "GOOD":
            return f"Most primary fields live. {summary}."
        elif report.overall_quality == "PARTIAL":
            return f"Some fields delayed/stale. {summary}."
        else:
            return f"Limited live data. {summary}."
    except Exception:
        return "Quality assessment unavailable."


def render_verdict_header(v: Verdict, snap: MarketSnapshot) -> None:
    """Render the primary verdict section with clear visual hierarchy.
    
    Layout:
    - Main verdict with color coding
    - Evidence strength line
    - Context line (persistence, regime, expiry)
    - Score and data quality with tooltip
    """
    color_map = {
        "BULLISH": "green", "BEARISH": "red",
        "MIXED": "orange", "NONE": "gray",
    }
    color = color_map.get(v.direction, "gray")
    
    if color == "green":
        bg = "rgba(0,180,80,0.15)"
    elif color == "red":
        bg = "rgba(220,50,50,0.15)"
    elif color == "orange":
        bg = "rgba(220,160,0,0.15)"
    else:
        bg = "rgba(128,128,128,0.10)"
    
    # Build context fragments
    ctx_parts = []
    if v.persistence:
        ctx_parts.append(v.persistence)
    if v.market_regime:
        ctx_parts.append(v.market_regime)
    if v.expiry_context:
        ctx_parts.append(v.expiry_context.lower().replace("_", " "))
    ctx_str = " · ".join(ctx_parts)
    
    # Build strength fragment
    strength_str = ""
    if v.trend_strength > 0:
        strength_label = trend_strength_label(v.trend_strength)
        strength_str = f"Evidence: {v.trend_strength}/100 ({strength_label})"
    
    # Data quality with tooltip
    quality_explanation = data_quality_tooltip(snap)
    quality_tooltip = f"Data Quality: {v.data_quality} ⓘ<br><small>{quality_explanation}</small>"
    
    html = f"""
    <div style="padding:1.2rem;border-radius:10px;background:{bg};border-left:5px solid {color};margin-bottom:0.5rem;">
        <span style="font-size:1.4em;font-weight:bold">{v.emoji} {v.display_label}</span>
        <br>
        <span style="font-size:0.95em;color:#333">{strength_str}</span>
        <br>
        <span style="font-size:0.85em;color:#555">{ctx_str}</span>
        <br>
        <span style="font-size:0.8em;color:#666">
            Score: {v.raw_score:+d} · 
            <span title="{quality_explanation}">{quality_tooltip}</span>
        </span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _field_display_value(fm: FieldMeta) -> tuple[str, str, str]:
    """Return (display_value, status_label, color) for a FieldMeta.

    Color rules:
    - Positive numeric value → green
    - Negative numeric value → red
    - Zero or unavailable → gray

    Display includes freshness indicator for live/stale data.
    """
    if not isinstance(fm, FieldMeta):
        return "UNAVAILABLE", "UNAVAILABLE", "gray"

    val = fm.value
    status = fm.status
    quality = fm.quality

    if val is None:
        return "UNAVAILABLE", status, "gray"

    # Build freshness indicator
    freshness_indicator = ""
    if status == "STALE" or quality == "POOR":
        freshness_indicator = " [STALE]"
    elif status == "LIVE" and fm.freshness_seconds is not None:
        if fm.freshness_seconds < 10:
            freshness_indicator = " [LIVE]"
        elif fm.freshness_seconds < 60:
            freshness_indicator = f" [LIVE — {fm.freshness_seconds:.0f}s]"
        else:
            freshness_indicator = f" [DELAYED — {fm.freshness_seconds:.0f}s]"
    elif fm.freshness_seconds is not None and fm.freshness_seconds > 0:
        freshness_indicator = f" [{fm.freshness_seconds:.0f}s]"

    if isinstance(val, float):
        if abs(val) >= 1000:
            display = f"{val:,.0f}"
        else:
            display = f"{val:,.2f}"
    elif isinstance(val, int):
        display = f"{val:,}"
    else:
        display = str(val)

    if isinstance(val, (int, float)):
        if val > 0:
            color = "green"
        elif val < 0:
            color = "red"
        else:
            color = "gray"
    else:
        color = "gray"

    return display + freshness_indicator, status, color


def colored_value_html(value_str: str, color: str) -> str:
    """Return an HTML span with the given color."""
    return f"<span style='color:{color}; font-weight:bold;'>{value_str}</span>"


def colored_metric(label: str, value_str: str, color: str, status: str = ""):
    """Render a single metric with a colored value using HTML."""
    value_html = colored_value_html(value_str, color)
    status_html = f"<div style='font-size:0.75rem; color:#666; margin-top:2px;'>{status}</div>" if status else ""
    html = (
        f"<div style='margin-bottom:0.75rem;'>"
        f"<div style='font-size:0.85rem; color:#444;'>{label}</div>"
        f"<div style='font-size:1.25rem; font-weight:bold;'>{value_html}</div>"
        f"{status_html}"
        f"</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def data_source_banner(snap: MarketSnapshot):
    """Display data source, timestamp, and quality from MarketSnapshot."""
    status = snap.data_status
    source = snap.source
    ts = snap.snapshot_timestamp
    ts_str = ts.strftime("%H:%M:%S IST") if ts else "N/A"

    if status == "UNAVAILABLE":
        st.error("LIVE DATA UNAVAILABLE")
    elif status == "LIVE":
        st.success(f"LIVE — Source: {source}")
    elif status == "DELAYED":
        st.warning(f"DELAYED — Source: {source}")

    dq = snap.compute_data_quality()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption(f"**Source:** {source}")
    with col2:
        st.caption(f"**Last updated:** {ts_str}")
    with col3:
        st.caption(f"**Data Quality:** {dq['emoji']} {dq['level']}")


def verdict_panel(v: Verdict):
    """Render the final verdict with expandable explanation."""
    color_map = {
        "BULLISH": "green", "BEARISH": "red",
        "MIXED": "orange", "NONE": "gray",
    }
    color = color_map.get(v.direction, "gray")

    if color == "green":
        bg = "rgba(0,180,80,0.15)"
    elif color == "red":
        bg = "rgba(220,50,50,0.15)"
    elif color == "orange":
        bg = "rgba(220,160,0,0.15)"
    else:
        bg = "rgba(128,128,128,0.10)"

    conflict_str = " | CONFLICT" if v.conflict else ""
    persistence_str = f" | {v.persistence}" if v.persistence else ""
    expiry_str = f" | {v.expiry_context}" if v.expiry_context else ""
    regime_str = f" | {v.market_regime}" if v.market_regime else ""
    strength_str = ""
    if v.trend_strength > 0:
        strength_label = trend_strength_label(v.trend_strength)
        strength_str = f" | Strength: {v.trend_strength}/100 ({strength_label})"
    html = (
        f"<div style='padding:1rem;border-radius:8px;background:{bg};border-left:5px solid {color}'>"
        f"<span style='font-size:1.1em;font-weight:bold'>{v.emoji} {v.display_label}</span><br>"
        f"<span style='font-size:0.85em;color:#666'>Score: {v.raw_score:+d} | Quality: {v.data_quality}{conflict_str}{persistence_str}{expiry_str}{regime_str}{strength_str}</span>"
        f"</div>"
    )
    st.markdown(html, unsafe_allow_html=True)

    with st.expander("Why?", expanded=False):
        _render_verdict_explanation(v)


def _render_verdict_explanation(v: Verdict) -> None:
    """Render detailed explanation of verdict including trend strength, reversal risk, and drivers."""
    # Trend strength explanation
    if v.trend_strength > 0:
        st.markdown("**Trend Strength:**")
        strength_label = trend_strength_label(v.trend_strength)
        if v.trend_strength >= 70:
            st.success(f"Strong trend ({v.trend_strength}/100 — {strength_label}). High directional persistence.")
        elif v.trend_strength >= 40:
            st.info(f"Moderate trend ({v.trend_strength}/100 — {strength_label}). Some evidence supports direction but with notable limitations.")
        else:
            st.warning(f"Weak trend ({v.trend_strength}/100 — {strength_label}). Limited evidence; reversal risk is elevated.")

    # Reversal risk
    st.markdown("**Reversal Risk:**")
    risk_factors = []
    if v.persistence == "REVERSING":
        risk_factors.append("Recent verdict history shows direction change.")
    if v.persistence == "WEAKENING":
        risk_factors.append("Trend is losing momentum.")
    if v.market_regime == "CHOPPY":
        risk_factors.append("Market is range-bound/choppy; breakout failure risk is high.")
    if v.market_regime == "TRANSITIONAL":
        risk_factors.append("Market is transitioning between regimes.")
    if v.conflict:
        risk_factors.append("Major conflict between high-priority components.")
    if v.expiry_context == "EXPIRY_DAY":
        risk_factors.append("Expiry-day effects may distort signals.")
    macro = v.components.get("Macro")
    if macro and macro.score == -1:
        risk_factors.append("Macro environment is deteriorating.")
    if v.trend_strength < 40:
        risk_factors.append("Low trend strength indicates weak conviction.")

    if risk_factors:
        for risk in risk_factors:
            st.markdown(f"- ⚠️ {risk}")
    else:
        st.success("No major reversal signals detected. Trend continuation is more likely.")

    # Component drivers
    st.markdown("**Key Drivers:**")
    shown = 0
    for name, comp in v.components.items():
        if comp.label == "Insufficient Data":
            continue
        if not comp.evidence and comp.score == 0:
            continue
        emoji = "🟢" if comp.score > 0 else "🔴" if comp.score < 0 else "🟡"
        st.markdown(f"{emoji} **{name}**: {comp.label}")
        if comp.evidence:
            for e in comp.evidence[:4]:
                st.markdown(f"  - {e}")
        shown += 1
        if shown >= 6:
            break

    # Expiry context
    if v.expiry_context:
        st.markdown("**Market Context:**")
        if v.expiry_context == "EXPIRY_DAY":
            st.warning("Expiry day: options/futures signals may be temporary. Do not overinterpret single-day derivatives moves.")
        elif v.expiry_context == "POST_EXPIRY":
            st.info("Post-expiry: positioning may be resetting. Watch for migration to next expiry.")
        elif v.expiry_context == "NORMAL":
            st.caption("Normal trading day — no expiry-specific distortion expected.")

    # Original evidence
    if v.reasons:
        st.markdown("**Evidence Summary:**")
        for reason in v.reasons[:5]:
            st.markdown(f"- {reason}")

    if v.conflict:
        st.warning("**Major conflict detected:** High-priority components disagree. Directional score overridden.")

    st.markdown("**Note:** Score reflects direction of measured components, not probability of market direction.")


def component_table(components: dict):
    """Render component scores as a table."""
    import pandas as pd
    rows = []
    for name, comp in components.items():
        primary = " *" if comp.is_primary else ""
        rows.append({
            "Component": f"{name}{primary}",
            "Score": f"{comp.score:+d}",
            "Label": comp.label,
            "Reason": comp.reason,
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("* = High-priority component (used for conflict detection)")


def evidence_detail(components: dict):
    """Show expandable evidence for each component."""
    for name, comp in components.items():
        with st.expander(f"{name} — {comp.label}", expanded=False):
            for e in comp.evidence:
                st.markdown(f"- {e}")


def contribution_panel(verdict: Verdict):
    """Compact explanation of how each component contributed to the final verdict."""
    if not verdict or not verdict.components:
        return

    with st.expander("Why this conclusion?", expanded=False):
        for name, comp in verdict.components.items():
            score = comp.score
            if score == 1:
                emoji = "🟢"
                contrib = "Bullish contribution"
            elif score == -1:
                emoji = "🔴"
                contrib = "Bearish contribution"
            else:
                if comp.label == "Insufficient Data":
                    emoji = "⚪"
                    contrib = "Insufficient evidence"
                else:
                    emoji = "🟡"
                    contrib = "Mixed / neutral"

            st.markdown(f"**{emoji} {name}**")
            for e in comp.evidence:
                st.markdown(f"  {e}")
            st.markdown(f"**→ {contrib}**")
            st.markdown("")
