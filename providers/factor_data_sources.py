"""Factor data source helpers.

Fetches historical factor values from Yahoo Finance or local storage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import requests

from providers.cache import cache
from providers.history_manager import history_manager

log = logging.getLogger(__name__)

YAHOO_TICKERS = {
    "crude": "BZ=F",
    "usdinr": "USDINR=X",
    "us10y": "^TNX",
}

FII_DII_HISTORY_DAYS = 30


def get_current_factor_value(factor_name: str) -> Tuple[Optional[float], str, datetime]:
    """Get current factor value from existing providers or cache."""
    now = datetime.now()

    if factor_name in ("crude", "usdinr", "us10y"):
        return _get_yahoo_current(factor_name, now)
    if factor_name in ("fii", "dii"):
        return _get_flows_current(factor_name, now)
    if factor_name == "rbi_rate":
        return _get_macro_cached("india_policy_rate", now)
    if factor_name == "inflation":
        return _get_macro_cached("inflation", now)
    if factor_name == "gdp":
        return _get_macro_cached("gdp_growth", now)
    if factor_name == "pmi":
        return _get_macro_cached("pmi", now)
    return None, "UNKNOWN", now


def get_historical_factor_values(factor_name: str, days: int = 20) -> List[Tuple[datetime, float]]:
    """Get historical factor values for trend analysis."""
    cache_key = f"factor_history_{factor_name}_{days}d"
    cached = cache.get(cache_key)
    if cached:
        return cached.value

    values: List[Tuple[datetime, float]] = []

    if factor_name in ("crude", "usdinr", "us10y"):
        values = _get_yahoo_history(factor_name, days)
    elif factor_name in ("fii", "dii"):
        values = _get_flows_history(factor_name, days)
    elif factor_name in ("rbi_rate", "inflation", "gdp", "pmi"):
        values = _get_macro_history(factor_name, days)

    if values:
        cache.put(cache_key, values, source="FactorHistory", freshness_window=86400)
    return values


def _get_yahoo_current(factor_name: str, now: datetime) -> Tuple[Optional[float], str, datetime]:
    ticker = YAHOO_TICKERS.get(factor_name)
    if not ticker:
        return None, "UNKNOWN", now
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if not hist.empty:
            value = float(hist["Close"].iloc[-1])
            return value, "YahooFinance", now
    except Exception as e:
        log.warning(f"Yahoo current fetch failed for {factor_name}: {e}")
    return None, "UNAVAILABLE", now


def _get_yahoo_history(factor_name: str, days: int) -> List[Tuple[datetime, float]]:
    ticker = YAHOO_TICKERS.get(factor_name)
    if not ticker:
        return []
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=f"{max(days + 5, 10)}d")
        if hist.empty:
            return []
        values = []
        for dt, row in hist.iterrows():
            values.append((dt.to_pydatetime().replace(tzinfo=datetime.now().tzinfo), float(row["Close"])))
        return values[-days:]
    except Exception as e:
        log.warning(f"Yahoo history fetch failed for {factor_name}: {e}")
    return []


def _get_flows_current(factor_name: str, now: datetime) -> Tuple[Optional[float], str, datetime]:
    """Get latest FII/DII value from stored snapshots or cache."""
    cache_key = f"{factor_name}_flow_current"
    cached = cache.get(cache_key)
    if cached:
        return cached.value, f"Cached ({cached.source})", now
    return None, "UNAVAILABLE", now


def _get_flows_history(factor_name: str, days: int) -> List[Tuple[datetime, float]]:
    """Get FII/DII history from stored snapshots."""
    values: List[Tuple[datetime, float]] = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = history_manager.SNAPSHOTS_DIR / f"{date}.json"
        if not filepath.exists():
            continue
        try:
            import json
            with open(filepath) as f:
                entries = json.load(f)
            if entries:
                last = entries[-1]
                fields = last.get("fields", {})
                key = f"{factor_name}_flow_1d"
                if key in fields and fields[key].get("value") is not None:
                    ts = datetime.fromisoformat(last["timestamp"]) if last.get("timestamp") else datetime.now()
                    values.append((ts, float(fields[key]["value"])))
        except Exception:
            continue
    return values


def _get_macro_cached(key: str, now: datetime) -> Tuple[Optional[float], str, datetime]:
    macro_cache = history_manager.load_macro_cache()
    value = macro_cache.get(key)
    if value is not None:
        return float(value), "Cached", now
    return None, "UNAVAILABLE", now


def _get_macro_history(factor_name: str, days: int) -> List[Tuple[datetime, float]]:
    """Get macro factor history from macro_cache timestamps if available."""
    macro_cache = history_manager.load_macro_cache()
    key_map = {
        "rbi_rate": "india_policy_rate",
        "inflation": "inflation",
        "gdp": "gdp_growth",
        "pmi": "pmi",
    }
    key = key_map.get(factor_name)
    if not key:
        return []
    value = macro_cache.get(key)
    ts_str = macro_cache.get(f"{key}_ts", "")
    if value is not None and ts_str:
        try:
            ts = datetime.fromisoformat(ts_str)
            return [(ts, float(value))]
        except Exception:
            pass
    return []
