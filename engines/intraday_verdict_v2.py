"""Extended intraday verdict engine with factor context.

Reuses existing component scoring from intraday_verdict.py.
Adds:
- factor_states context
- factor_contributions mapping
- timeframe_conflict detection
- factor_evidence generation
"""

from __future__ import annotations

from typing import Dict, List, Optional

from models.snapshot import MarketSnapshot
from models.verdict import Verdict, ComponentResult
from models.factor_state import FactorState, FactorDirection
from config import INTRADAY_PRIMARY, INTRADAY_THRESHOLDS
from engines.intraday_verdict import (
    _score_momentum,
    _score_volume,
    _score_futures,
    _score_options,
    _score_participation,
    _detect_major_conflict,
    _score_to_direction_state,
)


def _score_factors(snap: MarketSnapshot) -> ComponentResult:
    """Aggregate factor states into a single factor component score."""
    factor_states = snap.factor_states or {}
    contributions = _assess_factor_contributions(factor_states)
    scores = [c for c in contributions.values() if c != 0]
    if not scores:
        return ComponentResult("Factors", 0, "Neutral", "No strong factor signals", is_primary=False)
    avg = sum(scores) / len(scores)
    if avg > 0:
        label = "Bullish"
    elif avg < 0:
        label = "Bearish"
    else:
        label = "Mixed"
    return ComponentResult("Factors", round(avg), label, f"Factor bias: {label}", is_primary=False)


def compute_verdict(snap: MarketSnapshot) -> Verdict:
    """Compute intraday verdict with factor context."""
    missing = snap.critical_fields_missing()
    if missing:
        return Verdict(
            direction="NONE",
            state="INSUFFICIENT_DATA",
            raw_score=0,
            conflict=False,
            data_quality="POOR",
            components={},
            reasons=[f"Critical fields missing: {', '.join(missing)}"],
            timestamp=snap.snapshot_timestamp,
        )

    components = {
        "Momentum": _score_momentum(snap),
        "Volume": _score_volume(snap),
        "Futures": _score_futures(snap),
        "Options": _score_options(snap),
        "Participation": _score_participation(snap),
        "Factors": _score_factors(snap),
    }

    raw_score = sum(c.score for c in components.values())
    conflict = _detect_major_conflict(components)

    if conflict:
        direction, state = "MIXED", "WAIT"
        reasons = ["Major conflict: primary components disagree"]
    else:
        direction, state = _score_to_direction_state(raw_score)
        reasons = [f"Raw score {raw_score:+d}"]

    for name, comp in components.items():
        if comp.score == 1:
            reasons.append(f"✓ {name}: {comp.label}")
        elif comp.score == -1:
            reasons.append(f"✗ {name}: {comp.label}")
        else:
            reasons.append(f"– {name}: {comp.label}")

    factor_states = snap.factor_states or {}
    factor_contributions = _assess_factor_contributions(factor_states)
    timeframe_conflicts = _detect_timeframe_conflicts(direction, factor_states)
    factor_evidence = _generate_factor_evidence(factor_states, factor_contributions)

    reasons.extend(timeframe_conflicts)

    dq = snap.compute_data_quality()

    return Verdict(
        direction=direction,
        state=state,
        raw_score=raw_score,
        conflict=conflict,
        data_quality=dq["level"],
        components=components,
        reasons=reasons,
        timestamp=snap.snapshot_timestamp,
        factor_states=factor_states,
        factor_contributions=factor_contributions,
        timeframe_conflicts=timeframe_conflicts,
        factor_evidence=factor_evidence,
    )


def _assess_factor_contributions(factor_states: Dict[str, List[FactorState]]) -> Dict[str, int]:
    """Convert factor directions to NIFTY-perspective contributions."""
    contributions = {}

    for factor_name, states in factor_states.items():
        state_5d = next((s for s in states if s.timeframe == "5d"), None)

        if not state_5d or state_5d.direction == FactorDirection.INSUFFICIENT_DATA:
            contributions[factor_name] = 0
            continue

        if state_5d.is_reversing:
            contributions[factor_name] = 0
            continue

        if state_5d.nifty_relevance == "POSITIVE":
            contributions[factor_name] = (
                +1 if state_5d.direction == FactorDirection.BULLISH else
                -1 if state_5d.direction == FactorDirection.BEARISH else 0
            )
        elif state_5d.nifty_relevance == "NEGATIVE":
            contributions[factor_name] = (
                +1 if state_5d.direction == FactorDirection.BEARISH else
                -1 if state_5d.direction == FactorDirection.BULLISH else 0
            )
        else:
            contributions[factor_name] = 0

    return contributions


def _detect_timeframe_conflicts(
    nifty_direction: str,
    factor_states: Dict[str, List[FactorState]],
) -> List[str]:
    """Detect multi-timeframe conflicts."""
    conflicts = []

    reversing_factors = [
        f for f, states in factor_states.items()
        if any(s.is_reversing for s in states if s.timeframe == "5d")
    ]
    if reversing_factors and nifty_direction == "BULLISH":
        conflicts.append(
            f"Caution: {', '.join(reversing_factors)} reversing while NIFTY bullish — short-term pullback risk"
        )

    intraday_states = {
        f: next((s for s in states if s.timeframe == "intraday"), None)
        for f, states in factor_states.items()
    }
    weekly_states = {
        f: next((s for s in states if s.timeframe == "20d"), None)
        for f, states in factor_states.items()
    }

    for factor in intraday_states:
        intraday = intraday_states.get(factor)
        weekly = weekly_states.get(factor)

        if not intraday or not weekly:
            continue

        intraday_dir = (
            intraday.direction.value
            if intraday.direction != FactorDirection.INSUFFICIENT_DATA
            else None
        )
        weekly_dir = (
            weekly.direction.value
            if weekly.direction != FactorDirection.INSUFFICIENT_DATA
            else None
        )

        if intraday_dir and weekly_dir and intraday_dir != weekly_dir:
            conflicts.append(
                f"{factor}: intraday {intraday_dir} but weekly {weekly_dir} — view the weekly trend as primary"
            )

    return conflicts


def _generate_factor_evidence(
    factor_states: Dict[str, List[FactorState]],
    factor_contributions: Dict[str, int],
) -> List[str]:
    """Generate human-readable factor evidence."""
    evidence = []

    bullish_factors = [f for f, c in factor_contributions.items() if c == +1]
    if bullish_factors:
        evidence.append(f"Bullish factors: {', '.join(bullish_factors)}")

    bearish_factors = [f for f, c in factor_contributions.items() if c == -1]
    if bearish_factors:
        evidence.append(f"Bearish factors: {', '.join(bearish_factors)}")

    for factor, states in factor_states.items():
        for state in states:
            if state.is_reversing and state.timeframe in ["5d", "20d"]:
                evidence.append(
                    f"{factor} reversing ({state.timeframe}): {state.reversal_strength} reversal detected"
                )

    for factor, states in factor_states.items():
        for state in states:
            if state.history_quality == "INSUFFICIENT" and state.timeframe == "intraday":
                evidence.append(
                    f"⚠️ {factor} (intraday) has limited data; 5-day/20-day more reliable"
                )

    return evidence
