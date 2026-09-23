"""Intraday verdict engine. Consumes only MarketSnapshot.

Five components, each +1/0/-1. Conflict detection uses high-priority
components only (Momentum, Futures, Participation).
"""

from datetime import datetime, timezone
from typing import Optional

from models.snapshot import MarketSnapshot
from models.verdict import Verdict, ComponentResult
from config import (
    INTRADAY_PRIMARY, INTRADAY_THRESHOLDS,
    MOMENTUM_VWAP_SLOPE_THRESHOLD, VOLUME_CONFIRMATION_RATIO,
    PCR_SUPPORTIVE, PCR_RESISTIVE,
    AD_STRONG, AD_WEAK,
    SECTOR_STRONG_PARTICIPATION, SECTOR_WEAK_PARTICIPATION,
)


def _score_momentum(snap: MarketSnapshot) -> ComponentResult:
    """Momentum: VWAP position, VWAP slope, RSI."""
    evidence = []
    bullish = 0
    bearish = 0

    price = snap.get("nifty_spot")
    vwap = snap.get("vwap")
    if price is not None and vwap is not None:
        if price > vwap:
            evidence.append(f"Price {price:,.2f} above VWAP {vwap:,.2f}")
            bullish += 1
        elif price < vwap:
            evidence.append(f"Price {price:,.2f} below VWAP {vwap:,.2f}")
            bearish += 1
        else:
            evidence.append(f"Price at VWAP ({vwap:,.2f})")
    else:
        evidence.append("VWAP or price data missing")

    slope = snap.get("vwap")
    # VWAP slope is not directly in snapshot — use RSI as proxy
    # Actually we have no slope field. Skip if not available.
    # The snapshot has vwap but not slope. We'll skip slope for now.
    # When live data provides slope, add it to MarketSnapshot.

    rsi = snap.get("rsi")
    if rsi is not None:
        if rsi > 50:
            evidence.append(f"RSI {rsi:.1f} > 50")
            bullish += 1
        elif rsi < 50:
            evidence.append(f"RSI {rsi:.1f} < 50")
            bearish += 1
        else:
            evidence.append(f"RSI {rsi:.1f} = 50")
    else:
        evidence.append("RSI data missing")

    if bullish > bearish:
        return ComponentResult("Momentum", 1, "Bullish", f"{bullish} bullish vs {bearish} bearish", evidence, is_primary=True)
    elif bearish > bullish:
        return ComponentResult("Momentum", -1, "Bearish", f"{bearish} bearish vs {bullish} bullish", evidence, is_primary=True)
    return ComponentResult("Momentum", 0, "Neutral", "Mixed signals", evidence, is_primary=True)


def _score_volume(snap: MarketSnapshot) -> ComponentResult:
    """Volume confirmation: relative volume + price direction."""
    evidence = []
    rel_vol = snap.get("relative_volume")
    price = snap.get("nifty_spot")
    vwap = snap.get("vwap")

    if rel_vol is None:
        return ComponentResult("Volume", 0, "Neutral", "Insufficient volume data", ["Relative volume missing"])

    above_avg = rel_vol > VOLUME_CONFIRMATION_RATIO
    evidence.append(f"Relative volume: {rel_vol:.2f}x average")

    if price is not None and vwap is not None:
        price_above = price > vwap
        direction = "up" if price_above else "down"
        if above_avg:
            score = 1 if price_above else -1
            label = "Bullish Confirmation" if price_above else "Bearish Confirmation"
            evidence.append(f"Price moving {direction} with high volume — {label.lower()}")
            return ComponentResult("Volume", score, label, f"High volume confirms {direction} move", evidence)
        else:
            evidence.append(f"Price moving {direction} with normal/low volume — weak")
            return ComponentResult("Volume", 0, "Weak", f"Move on normal volume", evidence)

    return ComponentResult("Volume", 0, "Neutral", "Insufficient data for volume assessment", evidence)


def _score_futures(snap: MarketSnapshot) -> ComponentResult:
    """Futures positioning: price change + OI change."""
    evidence = []
    price_chg = snap.get("futures_change_pct")
    oi_chg = snap.get("futures_oi_change")

    if price_chg is None and oi_chg is None:
        return ComponentResult("Futures", 0, "Insufficient Data",
                               "Futures price and OI change unavailable", evidence, is_primary=True)

    if price_chg is not None:
        evidence.append(f"Price change: {'+'if price_chg>=0 else ''}{price_chg:.2f}%")
    else:
        evidence.append("Price change unavailable")

    if oi_chg is not None:
        evidence.append(f"OI change: {'+'if oi_chg>=0 else ''}{oi_chg:,}")
    else:
        evidence.append("OI change unavailable")

    price_up = price_chg > 0 if price_chg is not None else False
    oi_up = oi_chg > 0 if oi_chg is not None else False

    if price_up and oi_up:
        label = "Bullish positioning signal"
        score = 1
    elif price_up and not oi_up:
        label = "Short-covering-type signal"
        score = 0
    elif not price_up and oi_up:
        label = "Bearish positioning signal"
        score = -1
    else:
        label = "Long-unwinding-type signal"
        score = 0

    evidence.append(f"Interpretation: {label}")

    return ComponentResult("Futures", score, label, f"Price {'↑' if price_up else '↓'} + OI {'↑' if oi_up else '↓'}", evidence, is_primary=True)


def _score_options(snap: MarketSnapshot) -> ComponentResult:
    """Options positioning: call/put OI changes, PCR, IV."""
    evidence = []
    bullish = 0
    bearish = 0

    call_chg = snap.get("call_oi_change")
    put_chg = snap.get("put_oi_change")
    pcr = snap.get("pcr")
    iv = snap.get("atm_iv")

    has_call = call_chg is not None
    has_put = put_chg is not None
    has_pcr = pcr is not None

    if not (has_call or has_put or has_pcr):
        return ComponentResult("Options", 0, "Insufficient Data",
                               "Options data unavailable", evidence, is_primary=False)

    if has_call:
        evidence.append(f"Call OI change: {'+'if call_chg>=0 else ''}{call_chg:,}")
    if has_put:
        evidence.append(f"Put OI change: {'+'if put_chg>=0 else ''}{put_chg:,}")

    if has_call and has_put:
        if put_chg > 0 and call_chg <= 0:
            evidence.append("Put OI rising, Call OI flat/falling — supportive")
            bullish += 1
        elif call_chg > 0 and put_chg <= 0:
            evidence.append("Call OI rising, Put OI flat/falling — resistance building")
            bearish += 1
        elif put_chg > call_chg:
            evidence.append("Put OI change > Call OI change — net supportive")
            bullish += 1
        elif call_chg > put_chg:
            evidence.append("Call OI change > Put OI change — net resistance")
            bearish += 1
        else:
            evidence.append("Call and Put OI changes balanced")
    elif has_put and not has_call:
        evidence.append("Call OI change unavailable — cannot assess relative positioning")
    elif has_call and not has_put:
        evidence.append("Put OI change unavailable — cannot assess relative positioning")

    if has_pcr:
        evidence.append(f"PCR: {pcr:.3f}")
        if pcr > PCR_SUPPORTIVE:
            bullish += 1
        elif pcr < PCR_RESISTIVE:
            bearish += 1
    else:
        evidence.append("PCR unavailable")

    if iv is not None:
        evidence.append(f"ATM IV: {iv:.2f}%")

    if bullish > bearish:
        return ComponentResult("Options", 1, "Bullish", f"{bullish} bullish vs {bearish} bearish", evidence, is_primary=False)
    elif bearish > bullish:
        return ComponentResult("Options", -1, "Bearish", f"{bearish} bearish vs {bullish} bullish", evidence, is_primary=False)
    return ComponentResult("Options", 0, "Mixed", "Balanced options signals", evidence, is_primary=False)


def _score_participation(snap: MarketSnapshot) -> ComponentResult:
    """Market participation: A/D ratio and sector breadth."""
    evidence = []
    bullish = 0
    bearish = 0

    ad = snap.get("advance_decline_ratio")
    advances = snap.get("advances")
    declines = snap.get("declines")
    sectors = snap.get("sector_performance", {})

    has_ad = ad is not None
    has_adv = advances is not None
    has_dec = declines is not None

    if not (has_ad or has_adv or has_dec or sectors):
        return ComponentResult("Participation", 0, "Insufficient Data",
                               "Participation data unavailable", evidence, is_primary=True)

    if has_ad:
        evidence.append(f"A/D ratio: {ad:.2f}")
        if ad > AD_STRONG:
            bullish += 1
            evidence.append("Strong A/D ratio")
        elif ad < AD_WEAK:
            bearish += 1
            evidence.append("Weak A/D ratio")
        else:
            evidence.append("Moderate A/D ratio")
    elif has_adv and has_dec:
        evidence.append(f"Advances: {advances}, Declines: {declines}")
        if advances > declines * 2:
            bullish += 1
            evidence.append("Strong advance/decline")
        elif declines > advances * 2:
            bearish += 1
            evidence.append("Weak advance/decline")
        else:
            evidence.append("Balanced advance/decline")
    else:
        evidence.append("A/D data unavailable")

    if sectors:
        up_sectors = sum(1 for v in sectors.values() if isinstance(v, dict) and (v.get("pChange") or 0) > 0)
        down_sectors = sum(1 for v in sectors.values() if isinstance(v, dict) and (v.get("pChange") or 0) < 0)
        total = len(sectors)
        evidence.append(f"Sectors up: {up_sectors}/{total}, down: {down_sectors}/{total}")
        if up_sectors > total * SECTOR_STRONG_PARTICIPATION:
            bullish += 1
            evidence.append("Broad sector participation")
        elif down_sectors > total * SECTOR_STRONG_PARTICIPATION:
            bearish += 1
            evidence.append("Broad sector weakness")
        else:
            evidence.append("Mixed sector participation")

    if bullish > bearish:
        return ComponentResult("Participation", 1, "Strong Participation", f"{bullish} bullish vs {bearish} bearish", evidence, is_primary=True)
    elif bearish > bullish:
        return ComponentResult("Participation", -1, "Weak Participation", f"{bearish} bearish vs {bullish} bullish", evidence, is_primary=True)
    return ComponentResult("Participation", 0, "Mixed Participation", "Balanced signals", evidence, is_primary=True)


def _detect_major_conflict(components: dict[str, ComponentResult]) -> bool:
    """Detect if high-priority components materially disagree.

    Major conflict exists when:
    1. At least one primary component is bullish (+1)
    2. At least one primary component is bearish (-1)
    """
    primary_scores = [
        c.score for c in components.values()
        if c.is_primary and c.name in INTRADAY_PRIMARY
    ]
    has_bull = any(s == 1 for s in primary_scores)
    has_bear = any(s == -1 for s in primary_scores)
    return has_bull and has_bear


def _score_to_direction_state(raw_score: int) -> tuple[str, str]:
    """Map raw score to (direction, state) using thresholds."""
    for (lo, hi), (direction, state) in INTRADAY_THRESHOLDS.items():
        if lo <= raw_score <= hi:
            return direction, state
    return "MIXED", "WAIT"


def compute_verdict(snap: MarketSnapshot) -> Verdict:
    """Compute intraday verdict from a MarketSnapshot.

    Hierarchy:
    1. INSUFFICIENT DATA (critical fields missing)
    2. MAJOR CONFLICT (primary components disagree)
    3. DIRECTIONAL SCORE
    """
    # Rule 1: Data check
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

    # Compute all components
    components = {
        "Momentum": _score_momentum(snap),
        "Volume": _score_volume(snap),
        "Futures": _score_futures(snap),
        "Options": _score_options(snap),
        "Participation": _score_participation(snap),
    }

    raw_score = sum(c.score for c in components.values())

    # Rule 2: Major conflict
    conflict = _detect_major_conflict(components)

    if conflict:
        direction, state = "MIXED", "WAIT"
        reasons = ["Major conflict: primary components disagree"]
    else:
        # Rule 3: Directional score
        direction, state = _score_to_direction_state(raw_score)
        reasons = [f"Raw score {raw_score:+d}"]

    # Build reasons from components
    for name, comp in components.items():
        if comp.score == 1:
            reasons.append(f"✓ {name}: {comp.label}")
        elif comp.score == -1:
            reasons.append(f"✗ {name}: {comp.label}")
        else:
            reasons.append(f"– {name}: {comp.label}")

    # Data quality
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
