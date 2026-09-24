"""Shared UI helpers. Consumes only MarketSnapshot and Verdict."""

import math
import streamlit as st
import pandas as pd
from models.snapshot import MarketSnapshot, FieldMeta
from models.verdict import Verdict
from providers.data_quality import DataQualityEngine
from utils.history_ui import trend_strength_label


def _direction_state(change_pct) -> str:
    """Return deterministic direction state from change percentage."""
    if change_pct is None:
        return "Flat"
    if change_pct > 0:
        return "Rising"
    if change_pct < 0:
        return "Falling"
    return "Flat"


def _change_points(current_value, change_pct) -> str:
    """Compute change in points from current value and percentage change."""
    if current_value is None or change_pct is None:
        return "--"
    if change_pct == 0:
        return "+0.00 pts"
    prev = current_value / (1 + change_pct / 100)
    points = current_value - prev
    sign = "+" if points >= 0 else ""
    return f"{sign}{points:,.2f} pts"


def _render_index_card(label: str, spot_fm: FieldMeta, change_pct_fm: FieldMeta, nifty_direction: str) -> None:
    """Render a single related-index card with value, change %, points, status, and NIFTY confirmation."""
    spot_val = spot_fm.value if isinstance(spot_fm, FieldMeta) else None
    change_pct = change_pct_fm.value if isinstance(change_pct_fm, FieldMeta) else None
    status = change_pct_fm.status if isinstance(change_pct_fm, FieldMeta) else "UNAVAILABLE"

    if spot_val is None and change_pct is None:
        st.caption(f"**{label}**\n--\n--\n--\nUNAVAILABLE")
        return

    spot_display = f"{spot_val:,.2f}" if spot_val is not None else "--"
    pct_display = f"{change_pct:+.2f}%" if change_pct is not None else "--"
    points_display = _change_points(spot_val, change_pct)
    direction = _direction_state(change_pct)
    status_label = status if status else "UNAVAILABLE"

    confirmation = ""
    if nifty_direction and change_pct is not None:
        idx_dir = "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat")
        if idx_dir == nifty_direction:
            confirmation = "✓ Confirming NIFTY"
        elif idx_dir == "flat" or nifty_direction == "flat":
            confirmation = "↔ Neutral"
        else:
            confirmation = "↔ Diverging from NIFTY"

    color = "green" if (change_pct or 0) > 0 else ("red" if (change_pct or 0) < 0 else "gray")

    st.markdown(
        f"<div style='padding:0.5rem;border-radius:6px;border:1px solid #ddd;margin-bottom:0.5rem;'>"
        f"<div style='font-size:0.8rem;color:#666;'>{label}</div>"
        f"<div style='font-size:1.1rem;font-weight:bold;'>{spot_display}</div>"
        f"<div style='font-size:0.85rem;color:{color};'>{pct_display} · {points_display}</div>"
        f"<div style='font-size:0.75rem;color:#888;'>{direction} · {status_label}</div>"
        f"{'<div style=\"font-size:0.75rem;color:#888;margin-top:2px;\">' + confirmation + '</div>' if confirmation else ''}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_related_indices_section(snap: MarketSnapshot) -> None:
    """Render Related Indices section with cards and confirmation summary."""
    nifty_direction = None
    if snap.is_field_available("nifty_change_pct"):
        nifty_change = snap.nifty_change_pct.value
        if nifty_change is not None:
            nifty_direction = "up" if nifty_change > 0 else ("down" if nifty_change < 0 else "flat")

    domestic_indices = []
    for field_name, label in [
        ("sensex_spot", "Sensex"),
        ("banknifty_spot", "Bank Nifty"),
    ]:
        spot_fm = getattr(snap, field_name, None)
        change_fm = getattr(snap, field_name.replace("_spot", "_change_pct"), None)
        if isinstance(spot_fm, FieldMeta) and isinstance(change_fm, FieldMeta):
            if spot_fm.value is not None or change_fm.value is not None:
                domestic_indices.append((label, spot_fm, change_fm))

    gift_spot = getattr(snap, "giftnifty_spot", None)
    gift_change = getattr(snap, "giftnifty_change_pct", None)
    gift_available = isinstance(gift_spot, FieldMeta) and isinstance(gift_change, FieldMeta) and (gift_spot.value is not None or gift_change.value is not None)

    if domestic_indices or gift_available:
        st.subheader("Related Indices")

        if domestic_indices:
            st.markdown("**Domestic Indices**")
            cols = st.columns(min(len(domestic_indices), 3))
            for idx, (label, spot_fm, change_fm) in enumerate(domestic_indices):
                with cols[idx % 3]:
                    _render_index_card(label, spot_fm, change_fm, nifty_direction)

        if gift_available:
            st.markdown("**Overnight / Pre-market Context**")
            _render_index_card("GIFT Nifty", gift_spot, gift_change, None)

        if domestic_indices:
            st.markdown("**Related Indices Confirmation**")
            directions = []
            for _, spot_fm, change_fm in domestic_indices:
                change_pct = change_fm.value if isinstance(change_fm, FieldMeta) else None
                if change_pct is not None:
                    directions.append("up" if change_pct > 0 else ("down" if change_pct < 0 else "flat"))

            if not directions:
                st.caption("INSUFFICIENT DATA")
            elif not nifty_direction or nifty_direction == "flat":
                st.caption("INSUFFICIENT DATA")
            else:
                confirming = sum(1 for d in directions if d == nifty_direction)
                diverging = sum(1 for d in directions if d != nifty_direction and d != "flat")
                neutral = sum(1 for d in directions if d == "flat")

                if confirming == len(directions):
                    state = "CONFIRMING"
                    emoji = "🟢"
                elif diverging == len(directions):
                    state = "DIVERGING"
                    emoji = "🔴"
                elif confirming > diverging:
                    state = "MIXED"
                    emoji = "🟡"
                elif diverging > confirming:
                    state = "MIXED"
                    emoji = "🟡"
                else:
                    state = "MIXED"
                    emoji = "🟡"

                if state == "CONFIRMING":
                    explanation = "All available domestic indices are moving in the same direction as NIFTY."
                elif state == "DIVERGING":
                    explanation = "All available domestic indices are moving opposite to NIFTY."
                else:
                    up_names = [label for label, _, cf in domestic_indices if cf.value is not None and cf.value > 0]
                    down_names = [label for label, _, cf in domestic_indices if cf.value is not None and cf.value < 0]
                    parts = []
                    if up_names:
                        parts.append(f"{', '.join(up_names)} positive")
                    if down_names:
                        parts.append(f"{', '.join(down_names)} negative")
                    explanation = f"Cross-index confirmation is mixed: {'; '.join(parts)}."

                st.caption(f"**{emoji} {state}**\n{explanation}")


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
    - Trend Score as prominent metric out of 100
    - Evidence strength as prominent metric
    - Regime + persistence as context
    - Data quality with tooltip
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
    
    # Primary score is trend_strength out of 100
    strength_label = trend_strength_label(v.trend_strength) if v.trend_strength > 0 else "UNKNOWN"
    strength_str = f"Evidence: {v.trend_strength}/100 ({strength_label})"
    
    # Related indices summary with actual values
    related_summary = []
    related_component = v.components.get("Related Indices")
    if related_component and related_component.score != 0:
        related_summary.append(f"Related Indices: {related_component.label}")
    
    # Show actual related index values from snapshot if available
    if snap:
        related_values = []
        sensex_change = getattr(snap, "sensex_change_pct", None)
        banknifty_change = getattr(snap, "banknifty_change_pct", None)
        giftnifty_change = getattr(snap, "giftnifty_change_pct", None)
        
        if isinstance(sensex_change, FieldMeta) and sensex_change.value is not None:
            related_values.append(f"Sensex: {sensex_change.value:+.2f}%")
        if isinstance(banknifty_change, FieldMeta) and banknifty_change.value is not None:
            related_values.append(f"Bank Nifty: {banknifty_change.value:+.2f}%")
        if isinstance(giftnifty_change, FieldMeta) and giftnifty_change.value is not None:
            related_values.append(f"GIFT Nifty: {giftnifty_change.value:+.2f}%")
        
        if related_values:
            related_summary.append(" | ".join(related_values))
    
    # Build factor bias fragment
    factor_str = ""
    if v.factor_contributions:
        bullish_factors = [f for f, c in v.factor_contributions.items() if c == 1]
        bearish_factors = [f for f, c in v.factor_contributions.items() if c == -1]
        parts = []
        if bullish_factors:
            parts.append(f"🟢 {len(bullish_factors)} bullish: {', '.join(bullish_factors)}")
        if bearish_factors:
            parts.append(f"🔴 {len(bearish_factors)} bearish: {', '.join(bearish_factors)}")
        factor_str = " | ".join(parts)
    
    # Data quality with tooltip
    quality_explanation = data_quality_tooltip(snap)
    quality_tooltip = f"Data Quality: {v.data_quality} ⓘ<br><small>{quality_explanation}</small>"
    
    score_color = "green" if v.raw_score > 0 else ("red" if v.raw_score < 0 else "orange")
    strength_color = "green" if v.trend_strength >= 70 else ("orange" if v.trend_strength >= 40 else "red")
    
    related_str = " | ".join(related_summary) if related_summary else ""
    
    html = f"""
    <div style="padding:1.2rem;border-radius:10px;background:{bg};border-left:5px solid {color};margin-bottom:0.5rem;">
        <span style="font-size:1.4em;font-weight:bold">{v.emoji} {v.display_label}</span>
        <br>
        <span style="font-size:0.95em;color:#333; font-weight:bold">Trend Score: {v.trend_strength}/100</span> &nbsp;|&nbsp;
        <span style="font-size:0.85em;color:#555">{strength_str}</span> &nbsp;|&nbsp;
        <span style="font-size:0.85em;color:#555">{ctx_str}</span>
        <br>
        <span style="font-size:0.85em;color:#333">{factor_str}</span>
        <br>
        <span style="font-size:0.85em;color:#555">{related_str}</span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        colored_metric("Trend Score", f"{v.trend_strength}/100", strength_color)
    with col2:
        colored_metric("Evidence", v.data_quality or "UNKNOWN", "gray")
    with col3:
        st.markdown(f"<div style='margin-bottom:0.75rem;'><div style='font-size:0.85rem; color:#444;'>Data Quality</div><div style='font-size:1.25rem; font-weight:bold;'><span title='{quality_explanation}'>{v.data_quality} ⓘ</span></div></div>", unsafe_allow_html=True)


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

    status_emoji = {
        "LIVE": "🟢",
        "DELAYED": "🟡",
        "STALE": "🟠",
        "UNAVAILABLE": "⚪",
    }.get(status, "❓")

    dq = snap.compute_data_quality()
    dq_text = f"{dq['emoji']} {dq['level']}" if dq else "UNAVAILABLE"

    sources = [s.strip() for s in source.replace("+", "\n").split("\n") if s.strip()]
    source_names = " · ".join(sources) if sources else "N/A"

    if status == "UNAVAILABLE":
        st.error("LIVE DATA UNAVAILABLE")
    else:
        st.markdown(f"{status_emoji} **{status}** · Data Quality: {dq_text}")
    st.markdown(f"**Sources:** {source_names}")
    st.markdown(f"**Last updated:** {ts_str}")


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
    
    factor_summary = ""
    if v.factor_contributions:
        bullish = sum(1 for c in v.factor_contributions.values() if c == 1)
        bearish = sum(1 for c in v.factor_contributions.values() if c == -1)
        factor_summary = f" | Factors: {bullish}🟢 {bearish}🔴"
    
    html = (
        f"<div style='padding:1rem;border-radius:8px;background:{bg};border-left:5px solid {color}'>"
        f"<span style='font-size:1.1em;font-weight:bold'>{v.emoji} {v.display_label}</span><br>"
        f"<span style='font-size:0.9em;font-weight:bold;color:#222'>Trend Score: {v.trend_strength}/100</span> &nbsp;"
        f"<span style='font-size:0.85em;color:#666'>| Quality: {v.data_quality}{conflict_str}{persistence_str}{expiry_str}{regime_str}{strength_str}{factor_summary}</span>"
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

    # Factor intelligence
    if v.factor_contributions or v.timeframe_conflicts or v.factor_evidence:
        st.markdown("**Factor Intelligence:**")
        
        if v.factor_contributions:
            bullish = [(f, c) for f, c in v.factor_contributions.items() if c == 1]
            bearish = [(f, c) for f, c in v.factor_contributions.items() if c == -1]
            neutral = [(f, c) for f, c in v.factor_contributions.items() if c == 0]
            
            if bullish:
                st.success(f"Bullish factors ({len(bullish)}): {', '.join(f for f, _ in bullish)}")
            if bearish:
                st.error(f"Bearish factors ({len(bearish)}): {', '.join(f for f, _ in bearish)}")
            if neutral:
                st.caption(f"Neutral/insufficient ({len(neutral)}): {', '.join(f for f, _ in neutral)}")
        
        if v.timeframe_conflicts:
            st.warning("**Factor Conflicts:**")
            for conflict in v.timeframe_conflicts[:3]:
                st.markdown(f"- ⚠️ {conflict}")
        
        if v.factor_evidence:
            st.caption("Factor evidence:")
            for evidence in v.factor_evidence[:5]:
                st.caption(f"- {evidence}")

    # Related indices
    related_component = v.components.get("Related Indices")
    if related_component and related_component.score != 0:
        st.markdown("**Related Indices:**")
        emoji = "🟢" if related_component.score > 0 else "🔴"
        st.markdown(f"{emoji} **{related_component.label}**: {related_component.reason}")
        if related_component.evidence:
            for e in related_component.evidence:
                st.caption(f"- {e}")

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

    with st.expander("Component Contribution", expanded=False):
        rows = []
        for name, comp in verdict.components.items():
            score = comp.score
            if score == 1:
                emoji = "🟢"
                contrib = "Improving"
            elif score == -1:
                emoji = "🔴"
                contrib = "Deteriorating"
            else:
                if comp.label == "Insufficient Data":
                    emoji = "⚪"
                    contrib = "Insufficient"
                else:
                    emoji = "🟡"
                    contrib = "Neutral"
            rows.append({
                "Component": name,
                "Score": f"{score:+d}" if score != 0 else "--",
                "State": emoji,
                "Interpretation": contrib,
            })
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


def render_conclusion_bar(state: str, evidence: str, regime: str, persistence: str, summary: str, risk: str, trend_score: int | None = None) -> None:
    """Render the fixed-top conclusion bar for any page."""
    state_upper = (state or "").upper()
    if "BULLISH" in state_upper:
        badge = "🟢"
    elif "BEARISH" in state_upper:
        badge = "🔴"
    elif "CAUTION" in state_upper or "WEAK" in state_upper:
        badge = "🟡"
    else:
        badge = "⚪"

    st.markdown(f"### {badge} {state or 'UNKNOWN'}")
    col1, col2 = st.columns([1, 3])
    with col1:
        if trend_score is not None:
            display_score = trend_score if trend_score > 0 else 0
            score_color = "green" if display_score >= 70 else ("orange" if display_score >= 40 else "red")
            st.markdown(f"**Trend Score**")
            st.markdown(f"<span style='font-size:1.5em; font-weight:bold; color:{score_color}'>{display_score}/100</span>", unsafe_allow_html=True)
    with col2:
        meta_items = []
        if evidence:
            meta_items.append(("Evidence", evidence))
        if regime:
            meta_items.append(("Regime", regime))
        if persistence:
            meta_items.append(("Persistence", persistence))
        if meta_items:
            meta_cols = st.columns(len(meta_items))
            for col, (label, value) in zip(meta_cols, meta_items):
                with col:
                    st.caption(label)
                    st.markdown(f"**{value}**")
    st.divider()

    if summary:
        summary_items = [s.strip() for s in summary.replace(" – ", "\n").replace(" ✗ ", "\n").replace(" ✓ ", "\n").split("\n") if s.strip()]
        if summary_items:
            st.markdown("**Summary:**")
            for item in summary_items:
                st.markdown(f"- {item}")
    if risk:
        risk_items = [r.strip() for r in risk.replace(";", "\n").split("\n") if r.strip()]
        if risk_items:
            st.markdown("⚠️ **Main Risk:**")
            for item in risk_items:
                st.markdown(f"- {item}")


def render_what_changed(changes: list[tuple[str, str, str]]) -> None:
    """Render the 'What Changed' section.

    changes: list of (arrow_text, metric_text, timestamp_text)
    """
    if not changes:
        return
    st.markdown("### What Changed")
    for arrow, metric, ts in changes[:4]:
        st.markdown(f"{arrow} {metric} <span style='color:#666;font-size:0.85rem;'>{ts}</span>", unsafe_allow_html=True)


def render_evidence_group(title: str, bullets: list[str], expanded: bool = False) -> None:
    """Render an expandable evidence group."""
    with st.expander(title, expanded=expanded):
        for b in bullets:
            st.markdown(f"- {b}")


def render_data_status(name: str, status: str, timestamp: str = "") -> None:
    """Render a single data freshness indicator."""
    icon = {
        "LIVE": "🟢",
        "DELAYED": "🟡",
        "STALE": "🟠",
        "UNAVAILABLE": "⚪",
    }.get(status, "❓")
    ts = f" ({timestamp})" if timestamp else ""
    st.write(f"{icon} {name}: {status}{ts}")


def render_diagnostics(rows: list[dict]) -> None:
    """Render collapsed diagnostics table.

    rows: list of dicts with keys: name, status, timestamp, detail
    """
    if not rows:
        return
    with st.expander("[▶ Data Freshness & Diagnostics]", expanded=False):
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
