"""Weekly verdict engine. Consumes only MarketSnapshot.

Five components, each +1/0/-1. Conflict detection uses high-priority
components only (Capital Flows, Macro, Earnings).
"""

from datetime import datetime, timezone

from models.snapshot import MarketSnapshot
from models.verdict import Verdict, ComponentResult
from config import (
    WEEKLY_PRIMARY, WEEKLY_THRESHOLDS,
    MACRO_CRUDE_SUPPORTIVE, MACRO_CRUDE_HEADWIND,
    MACRO_INR_STRONG, MACRO_INR_WEAK,
    MACRO_YIELD_SUPPORTIVE, MACRO_YIELD_HEADWIND,
    EARNINGS_STRONG, EARNINGS_WEAK,
    AD_STRONG, AD_WEAK,
    SECTOR_STRONG_PARTICIPATION, SECTOR_WEAK_PARTICIPATION,
    PCR_SUPPORTIVE, PCR_RESISTIVE,
)


def _score_capital_flows(snap: MarketSnapshot) -> ComponentResult:
    """Evaluate FII and DII multi-period trends."""
    evidence = []
    bullish = 0
    bearish = 0

    fii_5d = snap.get("fii_flow_5d")
    fii_20d = snap.get("fii_flow_20d")
    dii_5d = snap.get("dii_flow_5d")
    dii_20d = snap.get("dii_flow_20d")

    any_missing = any(v is None for v in [fii_5d, fii_20d, dii_5d, dii_20d])
    if any_missing:
        missing_fields = [n for n, v in [("fii_5d", fii_5d), ("fii_20d", fii_20d),
                                          ("dii_5d", dii_5d), ("dii_20d", dii_20d)] if v is None]
        evidence.append(f"Missing capital flow data: {', '.join(missing_fields)}")
        return ComponentResult("Capital Flows", 0, "Insufficient Data",
                               "Capital flow data incomplete", evidence, is_primary=True)

    evidence.append(f"FII 5-day: ₹{fii_5d:+,.1f} Cr")
    evidence.append(f"FII 20-day: ₹{fii_20d:+,.1f} Cr")
    evidence.append(f"DII 5-day: ₹{dii_5d:+,.1f} Cr")
    evidence.append(f"DII 20-day: ₹{dii_20d:+,.1f} Cr")

    if fii_5d > 0 and fii_20d > 0:
        bullish += 1
        evidence.append("FII flows positive across timeframes")
    elif fii_5d < 0 and fii_20d < 0:
        bearish += 1
        evidence.append("FII flows negative across timeframes")
    else:
        evidence.append("FII flows mixed across timeframes")

    if dii_5d > 0 and dii_20d > 0:
        bullish += 1
        evidence.append("DII flows positive across timeframes")
    elif dii_5d < 0 and dii_20d < 0:
        bearish += 1
        evidence.append("DII flows negative across timeframes")
    else:
        evidence.append("DII flows mixed across timeframes")

    if bullish > bearish:
        return ComponentResult("Capital Flows", 1, "Improving", "Net positive flows", evidence, is_primary=True)
    elif bearish > bullish:
        return ComponentResult("Capital Flows", -1, "Deteriorating", "Net negative flows", evidence, is_primary=True)
    return ComponentResult("Capital Flows", 0, "Mixed", "Balanced flows", evidence, is_primary=True)


def _score_macro(snap: MarketSnapshot) -> ComponentResult:
    """Evaluate macro conditions."""
    evidence = []
    bullish = 0
    bearish = 0

    brent = snap.get("crude_price")
    usdinr = snap.get("usd_inr")
    us10y = snap.get("us10y_yield")
    fed = snap.get("fed_rate")
    india_rate = snap.get("india_policy_rate")

    # Check which macro inputs are available
    available_inputs = []
    missing_inputs = []

    if brent is not None:
        available_inputs.append(("Brent crude", brent, "brent"))
    else:
        missing_inputs.append("crude_price")

    if usdinr is not None:
        available_inputs.append(("USD/INR", usdinr, "usdinr"))
    else:
        missing_inputs.append("usd_inr")

    if us10y is not None:
        available_inputs.append(("US 10Y", us10y, "us10y"))
    else:
        missing_inputs.append("us10y_yield")

    if fed is not None:
        available_inputs.append(("Fed rate", fed, "fed"))
    else:
        missing_inputs.append("fed_rate")

    if india_rate is not None:
        available_inputs.append(("India rate", india_rate, "india_rate"))
    else:
        missing_inputs.append("india_policy_rate")

    # Flag PARTIAL if > 2 macro inputs missing
    if len(missing_inputs) > 2:
        evidence.append(f"Macro data severely limited: {len(missing_inputs)}/5 inputs missing")
        return ComponentResult("Macro", 0, "Insufficient Data",
                               f"Macro data incomplete ({5 - len(missing_inputs)}/5 available)",
                               evidence, is_primary=True)

    if not available_inputs:
        evidence.append("Macro data unavailable")
        return ComponentResult("Macro", 0, "Insufficient Data",
                               "Macro data unavailable", evidence, is_primary=True)

    for label, val, key in available_inputs:
        evidence.append(f"{label}: {val:.2f}")

    # Score based on available inputs
    if brent is not None:
        if brent < MACRO_CRUDE_SUPPORTIVE:
            bullish += 1
            evidence.append("Crude below 80 — supportive")
        elif brent > MACRO_CRUDE_HEADWIND:
            bearish += 1
            evidence.append("Crude above 90 — headwind")
        else:
            evidence.append("Crude in neutral range")

    if usdinr is not None:
        if usdinr < MACRO_INR_STRONG:
            bullish += 1
            evidence.append("INR relatively strong")
        elif usdinr > MACRO_INR_WEAK:
            bearish += 1
            evidence.append("INR under pressure")
        else:
            evidence.append("INR in neutral range")

    if us10y is not None:
        if us10y < MACRO_YIELD_SUPPORTIVE:
            bullish += 1
            evidence.append("US yields falling — supportive")
        elif us10y > MACRO_YIELD_HEADWIND:
            bearish += 1
            evidence.append("US yields elevated — headwind")
        else:
            evidence.append("US yields in neutral range")

    if bullish > bearish:
        return ComponentResult("Macro", 1, "Improving", "Macro conditions supportive", evidence, is_primary=True)
    elif bearish > bullish:
        return ComponentResult("Macro", -1, "Deteriorating", "Macro conditions challenging", evidence, is_primary=True)
    return ComponentResult("Macro", 0, "Mixed", "Macro conditions balanced", evidence, is_primary=True)


def _score_earnings(snap: MarketSnapshot) -> ComponentResult:
    """Evaluate earnings conditions."""
    evidence = []
    growth = snap.get("earnings_growth")

    if growth is None:
        evidence.append("Earnings growth data unavailable")
        return ComponentResult("Earnings", 0, "Insufficient Data",
                               "Earnings data unavailable", evidence, is_primary=True)

    evidence.append(f"Nifty earnings growth: {growth:+.1f}%")

    if growth > EARNINGS_STRONG:
        return ComponentResult("Earnings", 1, "Improving", "Strong earnings growth", evidence, is_primary=True)
    elif growth < EARNINGS_WEAK:
        return ComponentResult("Earnings", -1, "Deteriorating", "Negative earnings growth", evidence, is_primary=True)
    return ComponentResult("Earnings", 0, "Mixed", "Moderate earnings growth", evidence, is_primary=True)


def _score_participation(snap: MarketSnapshot) -> ComponentResult:
    """Evaluate breadth and sector participation."""
    evidence = []
    bullish = 0
    bearish = 0

    ad = snap.get("advance_decline_ratio")
    sectors = snap.get("sector_performance", {})

    has_ad = ad is not None

    if not (has_ad or sectors):
        return ComponentResult("Participation", 0, "Insufficient Data",
                               "Participation data unavailable", evidence, is_primary=False)

    if has_ad:
        evidence.append(f"A/D ratio: {ad:.2f}")
        if ad > AD_STRONG:
            bullish += 1
            evidence.append("Breadth positive")
        elif ad < AD_WEAK:
            bearish += 1
            evidence.append("Breadth negative")
        else:
            evidence.append("Breadth moderate")

    if sectors:
        up = sum(1 for v in sectors.values() if isinstance(v, dict) and (v.get("pChange") or 0) > 0)
        total = len(sectors)
        evidence.append(f"Sectors up: {up}/{total}")
        if up > total * SECTOR_STRONG_PARTICIPATION:
            bullish += 1
        elif up < total * SECTOR_WEAK_PARTICIPATION:
            bearish += 1

    if bullish > bearish:
        return ComponentResult("Participation", 1, "Improving", "Participation broadening", evidence, is_primary=False)
    elif bearish > bullish:
        return ComponentResult("Participation", -1, "Deteriorating", "Participation narrowing", evidence, is_primary=False)
    return ComponentResult("Participation", 0, "Mixed", "Balanced participation", evidence, is_primary=False)


def _score_derivatives(snap: MarketSnapshot) -> ComponentResult:
    """Evaluate multi-week derivatives positioning."""
    evidence = []
    bullish = 0
    bearish = 0

    pcr = snap.get("pcr")
    oi_chg = snap.get("futures_oi_change")

    has_pcr = pcr is not None
    has_oi = oi_chg is not None

    if not (has_pcr or has_oi):
        return ComponentResult("Derivatives", 0, "Insufficient Data",
                               "Derivatives data unavailable", evidence, is_primary=False)

    if has_pcr:
        evidence.append(f"PCR: {pcr:.3f}")
        if pcr > PCR_SUPPORTIVE:
            bullish += 1
            evidence.append("PCR > 1.0 — put writing, supportive")
        elif pcr < PCR_RESISTIVE:
            bearish += 1
            evidence.append("PCR < 0.8 — call writing, resistance")
        else:
            evidence.append("Derivatives positioning mixed")
    else:
        evidence.append("PCR unavailable")

    if has_oi:
        evidence.append(f"Futures OI change: {'+'if oi_chg>=0 else ''}{oi_chg:,}")
        if oi_chg > 0:
            evidence.append("Futures OI increasing — new positions")
        elif oi_chg < 0:
            evidence.append("Futures OI decreasing — position unwinding")
    else:
        evidence.append("Futures OI change unavailable")

    if bullish > bearish:
        return ComponentResult("Derivatives", 1, "Improving", "Derivatives supportive", evidence, is_primary=False)
    elif bearish > bullish:
        return ComponentResult("Derivatives", -1, "Deteriorating", "Derivatives bearish", evidence, is_primary=False)
    return ComponentResult("Derivatives", 0, "Mixed", "Derivatives neutral", evidence, is_primary=False)


def _detect_major_conflict(components: dict[str, ComponentResult]) -> bool:
    """Detect if high-priority components materially disagree."""
    primary_scores = [
        c.score for c in components.values()
        if c.is_primary and c.name in WEEKLY_PRIMARY
    ]
    has_bull = any(s == 1 for s in primary_scores)
    has_bear = any(s == -1 for s in primary_scores)
    return has_bull and has_bear


def _score_to_direction_state(raw_score: int) -> tuple[str, str]:
    """Map raw score to (direction, state) using thresholds."""
    for (lo, hi), (direction, state) in WEEKLY_THRESHOLDS.items():
        if lo <= raw_score <= hi:
            return direction, state
    return "MIXED", "WAIT"


def compute_verdict(snap: MarketSnapshot) -> Verdict:
    """Compute weekly verdict from a MarketSnapshot.

    Hierarchy:
    1. INSUFFICIENT DATA (critical fields missing)
    2. MAJOR CONFLICT (primary components disagree)
    3. DIRECTIONAL SCORE
    """
    missing = snap.critical_fields_missing()
    if missing:
        return Verdict(
            direction="NONE",
            state="INSUFFICIENT_DATA",
            raw_score=0,
            conflict=False,
            data_quality="POOR",
            reasons=[f"Critical fields missing: {', '.join(missing)}"],
            timestamp=snap.snapshot_timestamp,
        )

    components = {
        "Capital Flows": _score_capital_flows(snap),
        "Macro": _score_macro(snap),
        "Earnings": _score_earnings(snap),
        "Participation": _score_participation(snap),
        "Derivatives": _score_derivatives(snap),
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
    )
