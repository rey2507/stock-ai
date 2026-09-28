"""Paper-Trader — candle normalization + deterministic mock generation.

Shared helpers for the Stage 5 candle architecture:

- :func:`normalize_candles` coerces messy provider rows (any of the real
  NSE column spellings) into the normalized
  ``{timestamp, open, high, low, close, volume, oi}`` shape.
- :func:`build_mock_candles` generates deterministic OHLCV series for the
  mock provider (mean-reverting walk around the base spot, seeded per
  (symbol, interval) so tests are stable for a given range).
- :func:`default_range` maps an interval to a sensible default lookback.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

# Minutes per intraday bar; 1D handled separately (one bar per session).
INTERVAL_MINUTES: dict[str, int | None] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "1D": None,
}

# Default lookback per interval when the request omits start/end.
DEFAULT_LOOKBACK_DAYS: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 10,
    "1h": 20,
    "1D": 90,
}

# NSE session in local (IST) time.
SESSION_START = (9, 15)
SESSION_END = (15, 30)

# Volatility per bar by interval (fraction).
BAR_VOL: dict[str, float] = {
    "1m": 0.0005,
    "5m": 0.0012,
    "15m": 0.002,
    "1h": 0.004,
    "1D": 0.012,
}

MAX_CANDLES = 2000  # hard guard for absurd ranges


def validate_interval(interval: str) -> str:
    """Raise ValueError for unsupported intervals."""
    if interval not in INTERVAL_MINUTES:
        raise ValueError(
            f"Unsupported interval: {interval!r} (supported: {', '.join(INTERVAL_MINUTES)})"
        )
    return interval


def default_range(interval: str, today: date | None = None) -> tuple[str, str]:
    """(start, end) ISO dates covering the default lookback for ``interval``."""
    end = today or date.today()
    start = end - timedelta(days=DEFAULT_LOOKBACK_DAYS[interval])
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

# Column matchers, applied to lowercased alphanumerics-only column names.
_COL_MATCHERS: list[tuple[str, tuple[str, ...]]] = [
    ("timestamp", ("date", "historicaldate", "indexdate", "timestamp", "tradedate")),
    ("open", ("open", "openprice")),
    ("high", ("high", "highprice")),
    ("low", ("low", "lowprice")),
    ("close", ("close", "closeprice", "lastprice")),
    ("volume", ("volume", "ttltrdqnty", "tottrdqty", "totaltradedquantity")),
    ("oi", ("openinterest", "oi", "chnginoi")),
]


def _clean_column(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _num(raw, default: float | None = None) -> float | None:
    if raw is None:
        return default
    text = str(raw).replace(",", "").strip()
    if text in ("", "-", "nan", "None"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def normalize_candles(rows: list[dict], column_map: dict[str, str]) -> list[dict]:
    """Coerce provider rows into normalized candle dicts.

    ``column_map`` maps normalized field → provider column name. Rows missing
    values get ``None`` for volume/oi; a row without a timestamp or close is
    dropped entirely (a candle without price/time is useless, never faked).
    """
    out: list[dict] = []
    for row in rows:
        timestamp = row.get(column_map["timestamp"])
        close = _num(row.get(column_map["close"]))
        if timestamp is None or close is None:
            continue
        ts = str(timestamp).strip()
        open_ = _num(row.get(column_map["open"]), close)
        high = _num(row.get(column_map["high"]), close)
        low = _num(row.get(column_map["low"]), close)
        volume = _num(row.get(column_map.get("volume", "") or ""))
        oi = _num(row.get(column_map.get("oi", "") or ""))
        # Sanity: high/low must bracket open/close — repair rather than lie.
        high = max(high, open_, close)
        low = min(low or high, open_, close) if low is not None else min(open_, close)
        out.append({
            "timestamp": ts,
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": int(volume) if volume is not None else None,
            "oi": int(oi) if oi is not None else None,
        })
    out.sort(key=lambda c: c["timestamp"])
    return out


def detect_column_map(columns) -> dict[str, str]:
    """Best-effort mapping of provider dataframe columns to candle fields."""
    cleaned = {col: _clean_column(col) for col in columns}
    mapping: dict[str, str] = {}
    for field, candidates in _COL_MATCHERS:
        for col, clean in cleaned.items():
            if any(clean == c or clean.startswith(c) for c in candidates):
                if field == "oi" and "chng" in clean:
                    continue  # OI *change*, not OI
                mapping.setdefault(field, col)
                break
    return mapping


# ---------------------------------------------------------------------------
# Deterministic mock candles
# ---------------------------------------------------------------------------

def _session_buckets(day: date, minutes: int) -> list[str]:
    start = datetime(day.year, day.month, day.day, *SESSION_START)
    end = datetime(day.year, day.month, day.day, *SESSION_END)
    out = []
    ts = start
    while ts < end:
        out.append(ts.strftime("%Y-%m-%dT%H:%M:%S"))
        ts += timedelta(minutes=minutes)
    return out


def _trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri; holidays not modelled in mock
            days.append(d)
        d += timedelta(days=1)
    return days


def build_mock_candles(symbol: str, interval: str, start: str, end: str,
                       base_spot: float) -> list[dict]:
    """Deterministic OHLCV candles around ``base_spot``.

    Mean-reverting random walk seeded per (symbol, interval): identical
    (symbol, interval, start, end) requests always return identical data.
    """
    minutes = INTERVAL_MINUTES[interval]
    vol = BAR_VOL[interval]
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if end_d < start_d:
        return []

    rng = random.Random(f"candles:{symbol}:{interval}")
    timestamps: list[str] = []
    if minutes is None:
        timestamps = [d.isoformat() for d in _trading_days(start_d, end_d)]
    else:
        for day in _trading_days(start_d, end_d):
            timestamps.extend(_session_buckets(day, minutes))
    timestamps = timestamps[-MAX_CANDLES:]

    candles: list[dict] = []
    price = base_spot
    prev_close = base_spot
    for ts in timestamps:
        # Mean reversion pulls the walk back toward the base spot.
        drift = rng.uniform(-vol, vol) + 0.05 * (base_spot - price) / base_spot
        close = price * (1 + drift)
        open_ = prev_close
        high = max(open_, close) * (1 + rng.uniform(0, vol))
        low = min(open_, close) * (1 - rng.uniform(0, vol))
        volume = int(rng.uniform(0.5, 1.5) * (base_spot * 100))
        candles.append({
            "timestamp": ts,
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": volume,
            "oi": None,
        })
        prev_close = close
        price = close
    return candles
