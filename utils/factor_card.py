"""Factor Monitor UI components."""

from __future__ import annotations

import streamlit as st
from models.factor_state import FactorState, FactorDirection, Acceleration, Persistence


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


def render_factor_card(state: FactorState) -> None:
    """Render a single factor state card."""
    color = _direction_color(state.direction)
    emoji = _direction_emoji(state.direction)
    direction_text = state.direction.value

    st.markdown(f"### {emoji} {state.factor_name.upper()}")

    value_str = f"{state.current_value:,.2f}" if isinstance(state.current_value, (int, float)) else "N/A"
    change_str = f"{state.change_pct:+.2f}%" if isinstance(state.change_pct, (int, float)) else "N/A"

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Current", value_str)
    with col2:
        st.metric("Change", change_str, f"{direction_text} ({state.confidence})")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption(f"**Acceleration:** {state.acceleration.value}")
    with col2:
        st.caption(f"**Persistence:** {state.persistence.value}")
    with col3:
        if state.is_reversing:
            st.caption(f"🔁 **REVERSING** ({state.reversal_strength or 'N/A'})")

    if state.nifty_interpretation:
        relevance_color = "green" if state.nifty_relevance == "POSITIVE" else (
            "red" if state.nifty_relevance == "NEGATIVE" else "gray"
        )
        st.markdown(
            f"**NIFTY impact:** <span style='color:{relevance_color}'>{state.nifty_relevance}</span>",
            unsafe_allow_html=True,
        )
        st.write(state.nifty_interpretation)

    with st.expander("Evidence & Details"):
        st.caption(f"Source: {state.source} (age: {state.data_age_seconds}s)")
        st.caption(f"History quality: {state.history_quality} ({state.data_points_in_window} points)")
        for e in state.evidence:
            st.write(f"• {e}")


def render_factor_monitor(factor_snapshot: FactorSnapshot | None) -> None:
    """Render the full Factor Monitor page."""
    if not factor_snapshot or not factor_snapshot.factors:
        st.warning("Factor data unavailable.")
        return

    st.caption(f"Last updated: {factor_snapshot.timestamp.strftime('%H:%M:%S') if factor_snapshot.timestamp else 'N/A'}")

    timeframe = st.radio("Timeframe", ["intraday", "5d", "20d"], horizontal=True, label_visibility="collapsed")

    for factor_name, states in factor_snapshot.factors.items():
        state = next((s for s in states if s.timeframe == timeframe), None)
        if not state:
            continue
        render_factor_card(state)
        st.divider()


def get_latest_factor_snapshot() -> FactorSnapshot | None:
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
