"""History UI components for verdict changes and field freshness."""

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime, timezone
from typing import Optional

from models.verdict import Verdict
from providers.history_manager import history_manager


def compute_expiry_context(snap) -> str:
    """Classify current market context based on futures expiry.

    Returns one of: NORMAL, EXPIRY_DAY, POST_EXPIRY, or ""
    when futures_expiry is unavailable.
    """
    if not snap:
        return ""

    expiry_value = snap.get("futures_expiry")
    if not expiry_value:
        return ""

    try:
        if hasattr(expiry_value, "value"):
            expiry_value = expiry_value.value
        if not expiry_value:
            return ""

        today = datetime.now(timezone.utc).date()
        expiry_str = str(expiry_value).strip().upper()

        # Try common NSE expiry formats: DDMMMYY, YYYY-MM-DD, etc.
        parsed = None
        for fmt in ("%d%b%y", "%d%b%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(expiry_str, fmt).date()
                break
            except ValueError:
                continue

        if not parsed:
            return ""

        if today == parsed:
            return "EXPIRY_DAY"
        elif today > parsed:
            return "POST_EXPIRY"
        else:
            return "NORMAL"
    except Exception:
        return ""


def compute_market_regime(snap, history_limit: int = 20) -> str:
    """Determine whether market appears TRENDING, CHOPPY, TRANSITIONAL, or INSUFFICIENT_DATA.

    Uses current snapshot plus recent snapshot history.
    Analyzes price/VWAP relationship consistency as a proxy for trend stability.
    """
    if not snap:
        return "INSUFFICIENT_DATA"

    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        history = history_manager.get_snapshots(today)
    except Exception:
        history = []

    # Combine current observation with history
    observations = []
    current_fields = {}
    try:
        current_fields = {
            fname: getattr(snap, fname)
            for fname in dir(snap)
            if hasattr(getattr(snap, fname, object()), "value")
        }
    except Exception:
        pass

    if current_fields.get("nifty_spot") is not None and current_fields.get("vwap") is not None:
        observations.append({
            "price": current_fields["nifty_spot"].value,
            "vwap": current_fields["vwap"].value,
        })

    for entry in history:
        fields = entry.get("fields", {})
        price = fields.get("nifty_spot", {}).get("value")
        vwap = fields.get("vwap", {}).get("value")
        if price is not None and vwap is not None:
            observations.append({"price": price, "vwap": vwap})

    observations = observations[:history_limit]
    if len(observations) < 3:
        return "INSUFFICIENT_DATA"

    # Classify each observation as bullish (> VWAP), bearish (< VWAP), or neutral
    states = []
    for obs in observations:
        if obs["price"] > obs["vwap"]:
            states.append(1)
        elif obs["price"] < obs["vwap"]:
            states.append(-1)
        else:
            states.append(0)

    # Count transitions (direction changes)
    transitions = 0
    for i in range(1, len(states)):
        if states[i] != states[i - 1] and states[i] != 0 and states[i - 1] != 0:
            transitions += 1

    transition_ratio = transitions / (len(states) - 1) if len(states) > 1 else 0

    # Determine regime
    bullish_count = sum(1 for s in states if s == 1)
    bearish_count = sum(1 for s in states if s == -1)
    dominant = max(bullish_count, bearish_count)
    dominant_ratio = dominant / len(states) if states else 0

    if transition_ratio >= 0.5:
        return "CHOPPY"
    elif dominant_ratio >= 0.7 and transition_ratio <= 0.2:
        return "TRENDING"
    elif transition_ratio >= 0.3:
        return "TRANSITIONAL"
    return "TRENDING"


def compute_persistence(current: Verdict, lookback: int = 5) -> str:
    """Determine trend persistence from recent verdict history.

    Considers last `lookback` verdict observations to determine whether
    the current trend is STRENGTHENING, STABLE, WEAKENING, REVERSING, or
    INSUFFICIENT_HISTORY.
    """
    if not current or current.direction in ("NONE", "MIXED"):
        return ""

    try:
        recent = history_manager.get_verdict_changes(days=1, limit=lookback)
    except Exception:
        return ""

    if not recent:
        return "INSUFFICIENT_HISTORY"

    # Get most recent previous verdict with same or different direction
    prev = recent[0]
    prev_dir = prev.get("direction", "")
    prev_score = prev.get("raw_score", 0)

    if prev_dir != current.direction:
        return "REVERSING"

    # Same direction — compare magnitude across recent window
    # Use the most recent score for the same direction if it changed
    same_direction_scores = [current.raw_score]
    for entry in recent:
        if entry.get("direction") == current.direction:
            same_direction_scores.append(entry.get("raw_score", 0))
        else:
            break  # sequence broken by opposite direction

    if len(same_direction_scores) >= 2:
        recent_change = same_direction_scores[0] - same_direction_scores[-1]
    else:
        recent_change = current.raw_score - prev_score

    if current.direction == "BEARISH":
        recent_change = -recent_change

    if recent_change > 0:
        return "STRENGTHENING"
    elif recent_change < 0:
        return "WEAKENING"
    return "STABLE"


def compute_trend_strength(verdict: Verdict) -> int:
    """Compute trend strength score 0-100.

    Higher = stronger trend, lower reversal risk.
    Lower = weaker trend, higher reversal risk.
    """
    if not verdict or verdict.direction in ("NONE", "MIXED"):
        return 0

    if verdict.state == "INSUFFICIENT_DATA":
        return 0

    # Base: |raw_score| / max_possible * 100
    max_score = 5  # 5 components, each max ±1
    base = abs(verdict.raw_score) / max_score * 100

    modifiers = []

    # Conflict significantly reduces confidence
    if verdict.conflict:
        modifiers.append(-30)

    # Persistence
    if verdict.persistence == "STRENGTHENING":
        modifiers.append(+10)
    elif verdict.persistence == "WEAKENING":
        modifiers.append(-10)
    elif verdict.persistence == "REVERSING":
        modifiers.append(-25)
    elif verdict.persistence == "STABLE":
        modifiers.append(+5)

    # Market regime
    if verdict.market_regime == "TRENDING":
        modifiers.append(+10)
    elif verdict.market_regime == "CHOPPY":
        modifiers.append(-15)
    elif verdict.market_regime == "TRANSITIONAL":
        modifiers.append(-5)

    # Data quality
    if verdict.data_quality == "Good":
        modifiers.append(+5)
    elif verdict.data_quality == "Poor":
        modifiers.append(-15)

    # Component consensus
    if verdict.components:
        non_zero_scores = [c.score for c in verdict.components.values() if c.score != 0]
        if non_zero_scores:
            if all(s == non_zero_scores[0] for s in non_zero_scores):
                modifiers.append(+10)  # Unanimous direction
            else:
                modifiers.append(-5)  # Mixed signals

    # Macro factor alignment
    macro_comp = verdict.components.get("Macro")
    if macro_comp:
        if macro_comp.score == 1:
            modifiers.append(+5)
        elif macro_comp.score == -1:
            modifiers.append(-10)
        elif macro_comp.score == 0 and macro_comp.label == "Deteriorating":
            modifiers.append(-5)

    score = base + sum(modifiers)
    return max(0, min(100, int(round(score))))


def trend_strength_label(score: int) -> str:
    """Human-readable label for trend strength score."""
    if score >= 80:
        return "Very Strong"
    elif score >= 60:
        return "Strong"
    elif score >= 40:
        return "Moderate"
    elif score >= 20:
        return "Weak"
    else:
        return "Very Weak"


def verdict_history_panel(limit: int = 10) -> None:
    """Display last N verdict changes as a table.

    Shows timestamp, direction, state, score, and delta from previous score.
    """
    try:
        recent_verdicts = history_manager.get_verdict_changes(days=1, limit=limit)
    except Exception as e:
        st.caption(f"Verdict history unavailable: {e}")
        return

    if not recent_verdicts:
        st.caption("No verdict changes recorded yet.")
        return

    rows = []
    prev_score = None
    for v in recent_verdicts:
        score = v.get("raw_score", 0)
        delta = score - prev_score if prev_score is not None else 0
        prev_score = score

        ts = v.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = ts[:8] if ts else "?"

        direction = v.get("direction", "?")
        state = v.get("state", "?")

        rows.append({
            "Time": time_str,
            "Direction": direction,
            "State": state,
            "Score": f"{score:+d}",
            "Delta": f"{delta:+d}" if delta != 0 else "—",
        })

    df = pd.DataFrame(rows)

    # Color coding — use apply with axis=1 for row-wise styling
    def _color_row(row):
        direction = row["Direction"]
        if direction == "BULLISH":
            return ["color: green"] * len(row)
        elif direction == "BEARISH":
            return ["color: red"] * len(row)
        elif direction == "MIXED":
            return ["color: orange"] * len(row)
        return ["color: gray"] * len(row)

    styled = df.style.apply(_color_row, axis=1)
    st.dataframe(styled, width='stretch', hide_index=True)


def what_changed_panel(current_verdict: Optional[Verdict]) -> None:
    """Show what changed since the last verdict, if different."""
    if current_verdict is None:
        return

    try:
        recent = history_manager.get_verdict_changes(days=1, limit=2)
    except Exception:
        return

    if len(recent) < 2:
        return

    previous = recent[1]  # Second most recent
    prev_score = previous.get("raw_score", 0)
    curr_score = current_verdict.raw_score

    if prev_score == curr_score:
        return  # No change

    prev_direction = previous.get("direction", "?")
    prev_state = previous.get("state", "?")
    curr_direction = current_verdict.direction
    curr_state = current_verdict.state

    # Determine magnitude of overall change
    score_delta = abs(curr_score - prev_score)
    if score_delta >= 3:
        magnitude = "Major"
    elif score_delta >= 2:
        magnitude = "Moderate"
    else:
        magnitude = "Minor"

    st.markdown("### Verdict Changed")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**Previous:** {prev_direction} {prev_state} ({prev_score:+d})")
    with col2:
        st.markdown(f"**Current:** {curr_direction} {curr_state} ({curr_score:+d})")
    with col3:
        st.markdown(f"**Magnitude:** {magnitude} ({score_delta:+d})")

    # Show component changes with magnitude
    prev_components = previous.get("components", {})
    changes = []
    major_changes = []
    moderate_changes = []

    for name, comp in current_verdict.components.items():
        prev_comp = prev_components.get(name, {})
        prev_comp_score = prev_comp.get("score", 0)
        if comp.score != prev_comp_score:
            delta = comp.score - prev_comp_score
            abs_delta = abs(delta)
            if abs_delta >= 2:
                magnitude_label = "Major"
                major_changes.append(name)
            elif abs_delta == 1:
                magnitude_label = "Moderate"
                moderate_changes.append(name)
            else:
                magnitude_label = "Minor"

            arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            changes.append(
                f"- **{name}**: {prev_comp_score:+d} → {comp.score:+d} ({arrow}) "
                f"{comp.label} [{magnitude_label}]"
            )

    if changes:
        st.markdown("**Component Changes:**")
        for change in changes:
            st.markdown(change)

        # Synthesize what changed
        if major_changes:
            st.warning(
                f"Material deterioration in: {', '.join(major_changes)}"
            )
        elif moderate_changes:
            st.info(
                f"Notable shifts in: {', '.join(moderate_changes)}"
            )

    st.markdown(f"**Evidence:**")
    for reason in current_verdict.reasons[:5]:
        st.markdown(f"- {reason}")
