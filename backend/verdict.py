"""Paper-Trader — Technical verdict & price-action summary (Stage 17).

Pure functions over normalized candle dicts (the same shape every provider
returns via backend.providers.candles). Computes the same headline
indicators as web/js/indicators.js server-side, plus ATR-14 and the 20-day
range position, then aggregates a small set of named checks into a
Bullish / Bearish / Neutral verdict with a confidence score.

This is descriptive technical reading ONLY: it reports what standard
indicators currently say. It does not predict the market and nothing here
is guaranteed. No trading logic touches this module.
"""

from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Indicator primitives (mirror web/js/indicators.js math)
# ---------------------------------------------------------------------------

def sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """EMA seeded with the SMA of the first `period` values (like the JS)."""
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema(values: list[float], period: int) -> Optional[float]:
    series = ema_series(values, period)
    return series[-1] if series else None


def rsi(values: list[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI; matches web/js/indicators.js."""
    if len(values) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(values: list[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Optional[dict[str, float]]:
    fast_line = ema(values, fast)
    slow_line = ema(values, slow)
    if fast_line is None or slow_line is None:
        return None
    macd_val = fast_line - slow_line
    # Build the MACD series to seed the signal EMA like the frontend.
    series: list[float] = []
    f = ema_series(values, fast)
    s = ema_series(values, slow)
    start = next((i for i in range(len(values))
                  if f[i] is not None and s[i] is not None), None)
    if start is None:
        return {"macd": macd_val, "signal": macd_val, "histogram": 0.0}
    for i in range(start, len(values)):
        series.append(f[i] - s[i])
    sig = ema(series, signal)
    signal_val = sig if sig is not None else macd_val
    return {"macd": macd_val, "signal": signal_val,
            "histogram": macd_val - signal_val}


def atr(candles: list[dict[str, Any]], period: int = 14) -> Optional[float]:
    """Wilder-smoothed Average True Range."""
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    prev_close = float(candles[0]["close"])
    for c in candles[1:]:
        h, low = float(c["high"]), float(c["low"])
        trs.append(max(h - low, abs(h - prev_close), abs(low - prev_close)))
        prev_close = float(c["close"])
    if len(trs) < period:
        return None
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------

def _check(name: str, signal: str, detail: str) -> dict[str, str]:
    return {"name": name, "signal": signal, "detail": detail}


def _trend_check(closes: list[float]) -> dict[str, str]:
    if len(closes) < 10:
        return _check("Trend slope", "unclear", "not enough history")
    n = len(closes)
    half = n // 2
    first = sum(closes[:half]) / half
    second = sum(closes[half:]) / (n - half)
    if first == 0:
        return _check("Trend slope", "unclear", "degenerate prices")
    chg = (second / first - 1) * 100
    if chg > 1.0:
        return _check("Trend slope", "bullish",
                      f"second half of window +{chg:.1f}% vs first")
    if chg < -1.0:
        return _check("Trend slope", "bearish",
                      f"second half of window {chg:.1f}% vs first")
    return _check("Trend slope", "neutral", f"window change {chg:+.1f}%")


def compute_verdict(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate technical checks into a descriptive verdict.

    Returns {verdict, confidence, checks[], price_action{...}, disclaimer}.
    """
    closes = [float(c["close"]) for c in candles if c.get("close")]
    highs = [float(c["high"]) for c in candles if c.get("high")]
    lows = [float(c["low"]) for c in candles if c.get("low")]
    checks: list[dict[str, str]] = []

    if len(closes) < 30:
        return {
            "verdict": "unclear", "confidence": 0,
            "checks": [_check("History", "unclear",
                              f"only {len(closes)} candles — need 30+ for a "
                              "technical read")],
            "price_action": {},
            "disclaimer": DISCLAIMER,
        }

    last = closes[-1]
    prev = closes[-2]

    # 1. Price vs SMA50 (primary trend proxy).
    s50 = sma(closes, 50)
    if s50:
        diff = (last / s50 - 1) * 100
        sig = "bullish" if last > s50 else "bearish"
        checks.append(_check(
            "Price vs SMA 50", sig,
            f"close {'above' if last > s50 else 'below'} SMA50 by {abs(diff):.1f}%"))

    # 2. EMA 12 vs EMA 26 (momentum cross).
    e12, e26 = ema(closes, 12), ema(closes, 26)
    if e12 and e26:
        sig = "bullish" if e12 > e26 else "bearish"
        checks.append(_check(
            "EMA 12/26", sig,
            f"EMA12 {'above' if e12 > e26 else 'below'} EMA26 "
            f"({'bullish cross' if e12 > e26 else 'bearish cross'} state)"))

    # 3. RSI-14 zone.
    r = rsi(closes, 14)
    if r is not None:
        if r >= 70:
            checks.append(_check("RSI 14", "bearish",
                                 f"{r:.0f} — overbought zone"))
        elif r <= 30:
            checks.append(_check("RSI 14", "bullish",
                                 f"{r:.0f} — oversold zone"))
        elif r > 55:
            checks.append(_check("RSI 14", "bullish", f"{r:.0f} — bullish side"))
        elif r < 45:
            checks.append(_check("RSI 14", "bearish", f"{r:.0f} — bearish side"))
        else:
            checks.append(_check("RSI 14", "neutral", f"{r:.0f} — mid-zone"))

    # 4. MACD histogram sign.
    m = macd(closes)
    if m:
        sig = "bullish" if m["histogram"] > 0 else "bearish"
        checks.append(_check(
            "MACD 12/26/9", sig,
            f"histogram {m['histogram']:+.2f} "
            f"({'above' if m['histogram'] > 0 else 'below'} signal)"))

    # 5. 20-day range position.
    if len(closes) >= 20:
        hi20, lo20 = max(highs[-20:]), min(lows[-20:])
        rng = hi20 - lo20
        pos = (last - lo20) / rng if rng > 0 else 0.5
        pct = pos * 100
        if pct >= 80:
            sig, note = "bullish", "top of the 20-day range"
        elif pct <= 20:
            sig, note = "bearish", "bottom of the 20-day range"
        else:
            sig, note = "neutral", f"{pct:.0f}% up the 20-day range"
        checks.append(_check("20-day position", sig, f"trading at {note}"))

    # 6. Trend slope over the window.
    checks.append(_trend_check(closes))

    # 7. Last-session direction (context only).
    if prev:
        day_chg = (last / prev - 1) * 100
        sig = ("bullish" if day_chg > 0.15 else
               "bearish" if day_chg < -0.15 else "neutral")
        checks.append(_check("Last session", sig, f"{day_chg:+.2f}% close-to-close"))

    # Aggregate: score bullish +1 / bearish -1; confidence = conviction.
    score = sum({"bullish": 1, "bearish": -1}.get(c["signal"], 0) for c in checks)
    decisive = sum(1 for c in checks if c["signal"] in ("bullish", "bearish"))
    if score > 0:
        verdict = "bullish"
    elif score < 0:
        verdict = "bearish"
    else:
        verdict = "neutral"
    confidence = round(min(abs(score) / max(decisive, 1), 1.0) * 100)

    a = atr(candles)
    price_action: dict[str, Any] = {
        "last_close": round(last, 2),
        "prev_close": round(prev, 2) if prev else None,
        "day_change_pct": round((last / prev - 1) * 100, 2) if prev else None,
        "session_high": round(highs[-1], 2) if highs else None,
        "session_low": round(lows[-1], 2) if lows else None,
        "high_20d": round(max(highs[-20:]), 2) if len(highs) >= 20 else None,
        "low_20d": round(min(lows[-20:]), 2) if len(lows) >= 20 else None,
        "atr_14": round(a, 2) if a else None,
        "range_position_pct": (
            round((last - min(lows[-20:])) / (max(highs[-20:]) - min(lows[-20:])) * 100)
            if len(highs) >= 20 and max(highs[-20:]) > min(lows[-20:]) else None),
    }

    return {
        "verdict": verdict,
        "confidence": confidence,
        "checks": checks,
        "price_action": price_action,
        "disclaimer": DISCLAIMER,
    }


DISCLAIMER = ("Descriptive technical reading from standard indicators — "
              "not a prediction and not guaranteed. Trade decisions are yours.")


def one_liner(verdict: dict[str, Any]) -> str:
    """Plain-language price-action sentence for the UI."""
    pa = verdict.get("price_action") or {}
    pos = pa.get("range_position_pct")
    day = pa.get("day_change_pct")
    parts = []
    if day is not None:
        parts.append(f"{'up' if day >= 0 else 'down'} {abs(day):.2f}% on the day")
    if pos is not None:
        zone = ("near the 20-day high" if pos >= 80 else
                "near the 20-day low" if pos <= 20 else
                "mid-range")
        parts.append(f"trading {zone}")
    if pa.get("atr_14") and pa.get("last_close"):
        atr_pct = pa["atr_14"] / pa["last_close"] * 100
        parts.append(f"ATR {atr_pct:.1f}% of price")
    if not parts:
        return ""
    v = verdict.get("verdict", "unclear")
    label = {"bullish": "Indicators lean bullish",
             "bearish": "Indicators lean bearish",
             "neutral": "Indicators are mixed",
             "unclear": "Not enough history for a read"}[v]
    return f"{label}: " + ", ".join(parts) + "."
