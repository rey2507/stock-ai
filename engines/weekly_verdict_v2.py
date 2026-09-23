"""Extended weekly verdict engine with factor-driven macro component.

Reuses existing component scoring from weekly_verdict.py.
Changes:
- Macro component uses factor states (20-day) instead of static thresholds
- Adds factor context to final Verdict
"""

from __future__ import annotations

from typing import Dict, List, Optional

from models.snapshot import MarketSnapshot
from models.verdict import Verdict, ComponentResult
from models.factor_state import FactorState, FactorDirection
from config import (
    WEEKLY_PRIMARY, WEEKLY_THRESHOLDS,
    EARNINGS_STRONG, EARNINGS_WEAK,
    AD_STRONG, AD_WEAK,
    SECTOR_STRONG_PARTICIPATION, SECTOR_WEAK_PARTICIPATION,
    PCR_SUPPORTIVE, PCR_RESISTIVE,
)
from engines.weekly_verdict import (
    _score_capital_flows,
    _score_earnings,
    _score_participation,
    _score_derivatives,
    _detect_major_conflict,
    _score_to_direction_state,
)


def _score_macro_from_factors(snap: MarketSnapshot) -> ComponentResult:
    """Compute macro component from factor states instead of static thresholds."""
    factor_states = snap.factor_states or {}
    macro_factors = ["crude", "usdinr", "us10y", "inflation", "gdp", "pmi"]

    available_scores = []
    evidence = []

    for factor in macro_factors:
        if factor not in factor_states:
            continue

        state_20d = next(
            (s for s in factor_states[factor] if s.timeframe == "20d"),
            None,
        )

        if not state_20d or state_20d.direction == FactorDirection.INSUFFICIENT_DATA:
            continue

        contribution = _factor_to_macro_score(factor, state_20d)
        available_scores.append(contribution)

        if contribution == +1:
            evidence.append(
                f"✅ {factor}: {state_20d.direction.value} — {state_20d.nifty_interpretation}"
            )
        elif contribution == -1:
            evidence.append(
                f"⚠️ {factor}: {state_20d.direction.value} — {state_20d.nifty_interpretation}"
            )

    if available_scores:
        avg_score = sum(available_scores) / len(available_scores)
        macro_score = round(avg_score)
    else:
        macro_score = 0
        evidence.append("Insufficient macro factor data for assessment")

    label = {+1: "Bullish", -1: "Bearish"}.get(macro_score, "Neutral")

    return ComponentResult(
        name="Macro",
        score=macro_score,
        label=label,
        evidence=evidence,
        reason=f"Macro: {label} ({len(available_scores)} factors assessed)",
        is_primary=True,
    )


def _factor_to_macro_score(factor: str, state: FactorState) -> int:
    """Convert factor state to +1/0/-1 for macro component."""
    if state.is_reversing or state.direction == FactorDirection.INSUFFICIENT_DATA:
        return 0

    if state.nifty_relevance == "POSITIVE":
        return (
            +1
            if state.direction == FactorDirection.BULLISH
            else -1 if state.direction == FactorDirection.BEARISH else 0
        )
    if state.nifty_relevance == "NEGATIVE":
        return (
            +1
            if state.direction == FactorDirection.BEARISH
            else -1 if state.direction == FactorDirection.BULLISH else 0
        )
    return 0


def _score_related_indices(snap: MarketSnapshot) -> ComponentResult:
    """Score based on Sensex, Bank Nifty, and GIFT Nifty trends."""
    nifty_direction = None
    if snap.is_field_available("nifty_change_pct"):
        nifty_change = snap.nifty_change_pct.value
        if nifty_change is not None:
            nifty_direction = "up" if nifty_change > 0 else ("down" if nifty_change < 0 else "flat")

    related_signals = []
    evidence = []

    for field_name, label in [
        ("sensex_change_pct", "Sensex"),
        ("banknifty_change_pct", "Bank Nifty"),
        ("giftnifty_change_pct", "GIFT Nifty"),
    ]:
        if not snap.is_field_available(field_name):
            continue
        change = getattr(snap, field_name).value
        if change is None:
            continue
        direction = "up" if change > 0 else ("down" if change < 0 else "flat")
        related_signals.append(direction)
        evidence.append(f"{label}: {change:+.2f}%")

    if not related_signals:
        return ComponentResult("Related Indices", 0, "Insufficient Data", "No related index data", is_primary=False)

    if nifty_direction:
        confirming = sum(1 for s in related_signals if s == nifty_direction)
        if confirming == len(related_signals):
            score = 1
            label = "All confirming"
        elif confirming > 0:
            score = 0
            label = "Mixed confirmation"
        else:
            score = -1
            label = "All diverging"
    else:
        ups = sum(1 for s in related_signals if s == "up")
        downs = sum(1 for s in related_signals if s == "down")
        if ups > downs:
            score = 1
            label = "Broadly up"
        elif downs > ups:
            score = -1
            label = "Broadly down"
        else:
            score = 0
            label = "Mixed"

    return ComponentResult("Related Indices", score, label, "; ".join(evidence), evidence=evidence, is_primary=False)


def _score_factors(snap: MarketSnapshot) -> ComponentResult:
    """Aggregate all factor states into a single factor component score."""
    factor_states = snap.factor_states or {}
    contributions = {}
    for factor_name, states in factor_states.items():
        state_20d = next((s for s in states if s.timeframe == "20d"), None)
        if not state_20d or state_20d.direction == FactorDirection.INSUFFICIENT_DATA:
            continue
        if state_20d.is_reversing:
            continue
        if state_20d.nifty_relevance == "POSITIVE":
            contributions[factor_name] = (
                +1 if state_20d.direction == FactorDirection.BULLISH else
                -1 if state_20d.direction == FactorDirection.BEARISH else 0
            )
        elif state_20d.nifty_relevance == "NEGATIVE":
            contributions[factor_name] = (
                +1 if state_20d.direction == FactorDirection.BEARISH else
                -1 if state_20d.direction == FactorDirection.BULLISH else 0
            )
    scores = [c for c in contributions.values() if c != 0]
    if not scores:
        return ComponentResult("Factors", 0, "Neutral", "No strong factor signals", is_primary=False)
    avg = sum(scores) / len(scores)
    label = "Bullish" if avg > 0 else "Bearish" if avg < 0 else "Mixed"
    return ComponentResult("Factors", round(avg), label, f"Factor bias: {label}", is_primary=False)


def compute_verdict(snap: MarketSnapshot) -> Verdict:
    """Compute weekly verdict with factor-driven macro and all-factor component."""
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
        "Capital Flows": _score_capital_flows(snap),
        "Macro": _score_macro_from_factors(snap),
        "Earnings": _score_earnings(snap),
        "Participation": _score_participation(snap),
        "Derivatives": _score_derivatives(snap),
        "Related Indices": _score_related_indices(snap),
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
        factor_states=snap.factor_states or {},
    )
