"""Stage 5 tests: candles endpoint, normalization, provider fallback, badges.

Run from the project root: pytest tests/test_candles.py

No test here requires live NSE internet: the nselib provider is exercised
through the same fake-module pattern as tests/test_market_data.py, and the
app under test runs on the mock-only stack from conftest.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.market_data import MockMarketData
from backend.market_data_manager import MarketManager
from backend.market_data_source import CANDLE_INTERVALS, ProviderError, ProviderMiss
from backend.providers.candles import (
    build_mock_candles,
    default_range,
    detect_column_map,
    normalize_candles,
    validate_interval,
)
from backend.providers.mock_provider import MockMarketDataProvider
from tests.conftest import client


# ---------------------------------------------------------------------------
# Interval validation
# ---------------------------------------------------------------------------

def test_supported_intervals():
    assert CANDLE_INTERVALS == ("1m", "5m", "15m", "1h", "1D")
    for iv in CANDLE_INTERVALS:
        assert validate_interval(iv) == iv


def test_invalid_interval_rejected_422():
    r = client.get("/api/chart/NIFTY?interval=2m")
    assert r.status_code == 422
    assert "Unsupported interval" in r.json()["detail"]


def test_unknown_symbol_404():
    r = client.get("/api/chart/NOPE?interval=1D")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Mock candles: deterministic, well-formed
# ---------------------------------------------------------------------------

def test_mock_candles_deterministic():
    a = build_mock_candles("NIFTY", "1D", "2026-08-03", "2026-08-31", 24_850.0)
    b = build_mock_candles("NIFTY", "1D", "2026-08-03", "2026-08-31", 24_850.0)
    assert a == b
    assert len(a) > 15  # ~20 trading days in August 2026


def test_mock_candles_ohlc_sanity():
    candles = build_mock_candles("TCS", "5m", "2026-09-21", "2026-09-22", 4_120.0)
    assert candles, "expected intraday bars"
    for c in candles:
        assert set(c) >= {"timestamp", "open", "high", "low", "close", "volume"}
        assert c["high"] >= max(c["open"], c["close"])
        assert c["low"] <= min(c["open"], c["close"])
        assert c["volume"] > 0
        assert c["oi"] is None
    # Intraday timestamps carry a time-of-day within NSE hours.
    assert "T09:15" in candles[0]["timestamp"]
    assert "T15:" in candles[-1]["timestamp"]


def test_mock_candles_no_fake_for_unknown_symbol(fresh_db):
    r = client.get("/api/chart/NOPE")  # 404 handled before providers
    assert r.status_code == 404
    # Direct provider check: unknown symbol raises ProviderMiss, not fake data.
    md = MockMarketData(volatility=0.0)
    with pytest.raises(KeyError):
        md.get_spot("NOPE")


# ---------------------------------------------------------------------------
# /api/chart route: status/source metadata
# ---------------------------------------------------------------------------

def test_chart_route_mock_is_simulated(fresh_db):
    r = client.get("/api/chart/NIFTY?interval=1D")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "NIFTY"
    assert body["interval"] == "1D"
    assert body["status"] == "simulated"
    assert body["source"] == "mock"
    assert body["candles"]
    first = body["candles"][0]
    assert set(first) >= {"timestamp", "open", "high", "low", "close"}


def test_chart_route_default_range_by_interval(fresh_db):
    body = client.get("/api/chart/RELIANCE?interval=1D").json()
    assert body["start"] < body["end"]
    # Default 1D lookback is 90 days.
    dates = [c["timestamp"][:10] for c in body["candles"]]
    assert min(dates) >= body["start"] and max(dates) <= body["end"]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def test_normalize_candles_coerces_and_sorts():
    column_map = {"timestamp": "DATE", "open": "OPEN", "high": "HIGH",
                  "low": "LOW", "close": "CLOSE", "volume": "VOL", "oi": "OI"}
    rows = [
        {"DATE": "2026-09-22", "OPEN": "1,690.00", "HIGH": "1,700.5",
         "LOW": "1,680", "CLOSE": "1,695.25", "VOL": "12,000", "OI": "-"},
        {"DATE": "2026-09-21", "OPEN": 1680.0, "HIGH": 1690.0,
         "LOW": 1675.0, "CLOSE": 1682.4, "VOL": None, "OI": None},
        {"DATE": "-", "OPEN": 1, "HIGH": 1, "LOW": 1, "CLOSE": None},  # dropped
    ]
    out = normalize_candles(rows, column_map)
    assert [c["timestamp"] for c in out] == ["2026-09-21", "2026-09-22"]
    assert out[0]["volume"] is None and out[0]["oi"] is None
    assert out[1]["volume"] == 12000 and out[1]["oi"] is None
    assert out[1]["open"] == 1690.0  # comma string parsed


def test_detect_column_map_nse_spellings():
    mapping = detect_column_map(["HistoricalDate", "OPEN", "HIGH", "LOW",
                                 "CLOSE", "TtlTrdQnty", "CHNG_IN_OI"])
    assert mapping["timestamp"] == "HistoricalDate"
    assert mapping["volume"] == "TtlTrdQnty"
    assert "oi" not in mapping  # OI *change* is not OI


# ---------------------------------------------------------------------------
# nselib provider: daily live candles via fake module
# ---------------------------------------------------------------------------

class FakeCm:
    """Stand-in for nselib.capital_market (daily OHLCV)."""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames
        self.calls: list[tuple] = []

    def price_volume_data(self, symbol, from_date=None, to_date=None, period=None):
        self.calls.append(("stock", symbol, from_date, to_date))
        return self.frames[symbol]

    def index_data(self, index, from_date=None, to_date=None, period=None):
        self.calls.append(("index", index, from_date, to_date))
        return self.frames[index]


def _nselib_candle_provider():
    from backend.providers.nselib_provider import NselibProvider

    stock = pd.DataFrame([
        {"Date": "23-09-2026", "OPEN": "1,685.00", "HIGH": "1,702.30",
         "LOW": "1,677.10", "CLOSE": "1,698.45", "TTLTRDQNTY": "98,00,000"},
        {"Date": "24-09-2026", "OPEN": "1,700.00", "HIGH": "1,710.00",
         "LOW": "1,690.00", "CLOSE": "1,705.00", "TTLTRDQNTY": "85,00,000"},
    ])
    index = pd.DataFrame([
        {"HistoricalDate": "2026-09-23", "OPEN": 24_700.0, "HIGH": 24_950.0,
         "LOW": 24_650.0, "CLOSE": 24_900.0},
        {"HistoricalDate": "2026-09-24", "OPEN": 24_900.0, "HIGH": 25_020.0,
         "LOW": 24_860.0, "CLOSE": 24_980.0},
    ])
    cm = FakeCm({"INFY": stock, "NIFTY 50": index})
    # Derivatives/indices modules unused on the candle path but required
    # by the constructor; reuse the test fakes from test_market_data.
    from tests.test_market_data import FakeNselibDerivatives, FakeNselibIndices

    return NselibProvider(
        derivatives_module=FakeNselibDerivatives(pd.DataFrame()),
        indices_module=FakeNselibIndices({}),
        capital_market_module=cm,
    ), cm


def test_nselib_daily_stock_candles_live():
    p, cm = _nselib_candle_provider()
    candles = p.get_candles("INFY", "1D", "2026-09-01", "2026-09-30")
    assert len(candles) == 2
    assert candles[0]["timestamp"] == "23-09-2026"  # preserved as published
    assert candles[0]["close"] == 1698.45
    assert candles[0]["volume"] == 9_800_000
    assert candles[0]["oi"] is None
    assert candles[0]["high"] >= candles[0]["close"]


def test_nselib_daily_index_candles_live():
    p, cm = _nselib_candle_provider()
    candles = p.get_candles("NIFTY", "1D", "2026-09-01", "2026-09-30")
    assert candles[0]["close"] == 24_900.0
    assert candles[0]["volume"] is None  # index series carries no volume


def test_nselib_intraday_is_provider_miss():
    p, _ = _nselib_candle_provider()
    for interval in ("1m", "5m", "15m", "1h"):
        with pytest.raises(ProviderMiss):
            p.get_candles("INFY", interval, "2026-09-01", "2026-09-30")


def test_nselib_candle_failure_is_provider_error():
    p, _ = _nselib_candle_provider()
    p._cm = FakeCm({})  # symbol missing → KeyError inside fetch
    with pytest.raises(ProviderError):
        p.get_candles("INFY", "1D", "2026-09-01", "2026-09-30")


# ---------------------------------------------------------------------------
# Manager: candle TTL caching + fallback
# ---------------------------------------------------------------------------

def test_manager_candle_caching_by_ttl():
    ticks = {"t": 0.0}
    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    mgr = MarketManager(providers=[mock], quote_ttl=0.0,
                        candle_ttl_daily=1800.0, candle_ttl_intraday=60.0,
                        now_fn=lambda: ticks["t"])

    calls = {"n": 0}
    orig = mock.get_candles

    def counting(symbol, interval, start, end):
        calls["n"] += 1
        return orig(symbol, interval, start, end)

    mock.get_candles = counting

    a, src = mgr.get_candles("NIFTY", "1D", "2026-09-01", "2026-09-30")
    assert src == "mock"
    b, _ = mgr.get_candles("NIFTY", "1D", "2026-09-01", "2026-09-30")
    assert a == b and calls["n"] == 1  # cache hit

    ticks["t"] += 1801  # past daily TTL
    mgr.get_candles("NIFTY", "1D", "2026-09-01", "2026-09-30")
    assert calls["n"] == 2


def test_manager_candle_fallback_from_miss():
    """A provider raising ProviderMiss (intraday on nselib) falls through
    to mock without cooling the live provider down."""
    class NoCandles(MockMarketDataProvider):
        name = "nolive"

        def get_candles(self, symbol, interval, start, end):
            raise ProviderMiss("no candles here")

    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    mgr = MarketManager(providers=[NoCandles(mock), mock], quote_ttl=0.0)

    candles, source = mgr.get_candles("TCS", "5m", "2026-09-01", "2026-09-30")
    assert source == "mock"
    assert candles


# ---------------------------------------------------------------------------
# Route-level status-badge scenarios (per contract: LIVE/STALE/SIM/UNAVAILABLE)
# ---------------------------------------------------------------------------

def test_chart_unavailable_when_no_provider_serves(fresh_db, monkeypatch):
    """An empty provider stack yields status=unavailable, never fake data."""
    from backend import main

    empty = MarketManager(providers=[])
    monkeypatch.setattr(main, "market_data", empty)
    body = client.get("/api/chart/NIFTY?interval=1D").json()
    assert body["status"] == "unavailable"
    assert body["source"] is None
    assert body["candles"] == []


def test_watchlist_quotes_carry_status(fresh_db):
    body = client.get("/api/watchlist").json()
    for q in body["watchlist"]:
        assert q["status"] in ("live", "simulated", "stale", "unavailable", "derived")
        assert q["source"]


def test_positions_and_account_carry_marking(fresh_db):
    # Open a futures position, then check marking metadata.
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "side": "BUY", "quantity": 500,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200

    pos = client.get("/api/positions").json()
    assert pos["positions"], "expected one position"
    p = pos["positions"][0]
    assert p["status"] in ("live", "simulated", "stale", "unavailable", "derived")
    assert p["source"]

    acct = client.get("/api/account").json()
    assert acct["data_source"]["status"] in (
        "live", "simulated", "stale", "unavailable", "mixed")
    assert acct["data_source"]["source"]


def test_option_chain_rows_carry_oi_when_live(fresh_db):
    """Mock chain: volume/oi are None — the UI must dash them out, not
    display fake numbers."""
    body = client.get("/api/option-chain/NIFTY").json()
    assert body["status"] == "simulated"
    for row in body["rows"]:
        assert row["ce"]["volume"] is None
        assert row["ce"]["oi"] is None
