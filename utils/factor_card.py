"""Factor Monitor UI components."""

from __future__ import annotations

import logging
import streamlit as st
from models.factor_state import FactorState, FactorDirection, Acceleration, Persistence
from utils.ui import render_conclusion_bar, render_evidence_group, render_diagnostics

log = logging.getLogger(__name__)


def _direction_color(direction: FactorDirection) -> str:
    if direction == FactorDirection.BULLISH:
        return "green"
    if direction == FactorDirection.BEARISH:
        return "red"
    if direction in (FactorDirection.REVERSING, FactorDirection.MIXED):
        return "orange"
    return "gray"


def _direction_emoji(direction: FactorDirection) -> str:
    mapping = {
        FactorDirection.BULLISH: "🟢",
        FactorDirection.BEARISH: "🔴",
        FactorDirection.REVERSING: "🟡",
        FactorDirection.MIXED: "🟡",
        FactorDirection.NEUTRAL: "⚪",
        FactorDirection.INSUFFICIENT_DATA: "⚪",
    }
    return mapping.get(direction, "⚪")


@st.fragment(run_every=10)
def render_factor_monitor(factor_snapshot=None) -> None:
    """Render the full Factor Monitor page with table-first layout."""
    if factor_snapshot is None:
        try:
            factor_snapshot = get_latest_factor_snapshot()
        except Exception:
            factor_snapshot = None

    if not factor_snapshot or not factor_snapshot.factors:
        st.warning("Factor data unavailable.")
        return

    st.header("Factor Monitor")
    st.caption(f"**Last updated:** {factor_snapshot.timestamp.strftime('%H:%M:%S IST') if factor_snapshot.timestamp else 'N/A'}")

    timeframe = st.radio("Timeframe", ["intraday", "5d", "20d"], horizontal=True, label_visibility="collapsed")

    # Collect states for selected timeframe
    states = []
    for factor_name, state_list in factor_snapshot.factors.items():
        state = next((s for s in state_list if s.timeframe == timeframe), None)
        if state:
            states.append(state)

    if not states:
        st.caption("No factor data for selected timeframe.")
        return

    # ── 1. FACTOR CHANGES SUMMARY ───────────────────────────────
    accelerating = [s for s in states if s.acceleration == Acceleration.ACCELERATING]
    steady = [s for s in states if s.acceleration == Acceleration.STEADY]
    decelerating = [s for s in states if s.acceleration == Acceleration.DECELERATING]
    reversing = [s for s in states if s.is_reversing]

    summary_parts = []
    if accelerating:
        summary_parts.append(f"🔥 ACCELERATING ({len(accelerating)}): " + ", ".join(s.factor_name for s in accelerating[:3]))
    if steady:
        summary_parts.append(f"🚗 STEADY ({len(steady)}): " + ", ".join(s.factor_name for s in steady[:3]))
    if decelerating:
        summary_parts.append(f"⚠ DECELERATING ({len(decelerating)}): " + ", ".join(s.factor_name for s in decelerating[:3]))
    if reversing:
        summary_parts.append(f"↩ REVERSING ({len(reversing)}): " + ", ".join(s.factor_name for s in reversing[:3]))

    if summary_parts:
        st.markdown("### Factor Changes Summary")
        for part in summary_parts:
            st.markdown(part)
        st.markdown("---")

    # ── 2. FACTOR DETAIL TABLE ──────────────────────────────────
    st.markdown("### Factor Detail")
    rows = []
    for state in states:
        direction_text = state.direction.value
        accel_text = state.acceleration.value
        change_str = f"{state.change_pct:+.2f}%" if isinstance(state.change_pct, (int, float)) else "N/A"
        value_str = f"{state.current_value:,.2f}" if isinstance(state.current_value, (int, float)) else "N/A"
        data_quality = state.history_quality if state.history_quality != "INSUFFICIENT" else "INSUFFICIENT"
        data_age = f"{state.data_age_seconds:.0f}s" if state.data_age_seconds else "N/A"
        source = state.source or "UNKNOWN"
        evidence = " | ".join(state.evidence) if state.evidence else ""
        rows.append({
            "Factor": state.factor_name,
            "Value": value_str,
            "Change": change_str,
            "Direction": direction_text,
            "Trend": accel_text,
            "Confidence": state.confidence,
            "Relevance": state.nifty_relevance,
            "Source": source,
            "Data Age": data_age,
            "Quality": data_quality,
            "Evidence": evidence,
        })

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)

        def color_direction(val):
            if val in ("BULLISH",):
                return "color: green"
            elif val in ("BEARISH",):
                return "color: red"
            elif val in ("REVERSING", "MIXED"):
                return "color: orange"
            return "color: gray"

        def color_quality(val):
            if val == "SUFFICIENT":
                return "color: green"
            elif val == "MODERATE":
                return "color: orange"
            elif val == "INSUFFICIENT":
                return "color: red"
            return "color: gray"

        styled = df.style.map(color_direction, subset=["Direction", "Trend"]).map(color_quality, subset=["Quality"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── 3. FACTOR GROUPING BY STATE ─────────────────────────────
    st.markdown("### Factor Grouping")
    group_bullets = []
    if accelerating:
        group_bullets.append(f"🔥 **ACCELERATING:** " + ", ".join(s.factor_name for s in accelerating))
    if steady:
        group_bullets.append(f"🚗 **STEADY:** " + ", ".join(s.factor_name for s in steady))
    if decelerating:
        group_bullets.append(f"⚠ **DECELERATING:** " + ", ".join(s.factor_name for s in decelerating))
    if reversing:
        group_bullets.append(f"↩ **REVERSING:** " + ", ".join(s.factor_name for s in reversing))

    if group_bullets:
        for b in group_bullets:
            st.markdown(b)
    else:
        st.caption("No acceleration data available.")

    st.markdown("---")

    # ── 4. FACTOR DIVERGENCE MATRIX ─────────────────────────────
    divergences = [s for s in states if s.nifty_relevance in ("NEGATIVE", "UNCLEAR") or s.is_reversing]
    if divergences:
        st.markdown("### Factor Divergence")
        st.caption("Factors where current direction conflicts with NIFTY outlook or are reversing:")
        for state in divergences:
            st.markdown(f"- **{state.factor_name}**: {state.direction.value} — {state.nifty_interpretation}")

    st.markdown("---")

    # ── 5. DIAGNOSTICS ──────────────────────────────────────────
    diag_rows = []
    for state in states:
        diag_rows.append({
            "Factor": state.factor_name,
            "Source": state.source,
            "Status": "🟢" if state.data_age_seconds < 120 else "🟡" if state.data_age_seconds < 300 else "🟠",
            "Age": f"{state.data_age_seconds:.0f}s",
            "Quality": state.history_quality,
        })
    render_diagnostics(diag_rows)


def get_latest_factor_snapshot():
    """Get the most recent factor snapshot from today's history."""
    from datetime import date
    from providers.history_manager import history_manager
    entries = history_manager.get_factor_snapshots(date.today().isoformat())
    if not entries:
        return None
    latest = entries[-1]
    try:
        from models.factor_state import FactorSnapshot, FactorState, FactorDirection, Acceleration, Persistence
        from datetime import datetime as dt

        parsed_factors = {}
        for fname, states in latest.get("factors", {}).items():
            parsed_factors[fname] = [
                FactorState(
                    factor_name=s.get("factor_name", fname),
                    timeframe=s.get("timeframe", "intraday"),
                    current_value=s.get("current_value"),
                    direction=FactorDirection(s.get("direction", "INSUFFICIENT_DATA")),
                    confidence=s.get("confidence", "LOW"),
                    previous_value=s.get("previous_value"),
                    change_absolute=s.get("change_absolute"),
                    change_pct=s.get("change_pct"),
                    acceleration=Acceleration(s.get("acceleration", "UNKNOWN")),
                    persistence=Persistence(s.get("persistence", "UNKNOWN")),
                    days_in_current_direction=s.get("days_in_current_direction", 0),
                    is_reversing=s.get("is_reversing", False),
                    reversal_strength=s.get("reversal_strength"),
                    nifty_relevance=s.get("nifty_relevance", "UNCLEAR"),
                    nifty_interpretation=s.get("nifty_interpretation", ""),
                    source=s.get("source", "UNKNOWN"),
                    observed_at=dt.fromisoformat(s["observed_at"]) if s.get("observed_at") else None,
                    data_age_seconds=s.get("data_age_seconds", 0),
                    data_points_in_window=s.get("data_points_in_window", 0),
                    history_quality=s.get("history_quality", "INSUFFICIENT"),
                    evidence=s.get("evidence", []),
                )
                for s in states
            ]
        return FactorSnapshot(
            timestamp=dt.fromisoformat(latest["timestamp"]) if latest.get("timestamp") else dt.now(),
            factors=parsed_factors,
        )
    except Exception as e:
        log.warning(f"Failed to parse factor snapshot: {e}")
        return None
