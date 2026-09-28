"""Stage 4 tests: provider abstraction, fallback, caching, normalization.

Run from the project root: pytest tests/test_market_data.py
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backend import main
from backend.market_data import MockMarketData
from backend.market_data_manager import MarketManager
from backend.market_data_source import MarketDataSource, ProviderError
from backend.models import OptionQuote
from backend.providers.mock_provider import MockMarketDataProvider
from tests.conftest import client


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FlakyProvider(MarketDataSource):
    """Always fails — used to assert fall-through and cooldown."""

    name = "flaky"
    calls = 0

    def get_spot(self, symbol: str) -> float:
        self.calls += 1
        raise ProviderError("boom")

    def get_quotes(self) -> dict[str, float]:
        raise ProviderError("boom")

    def get_future_price(self, symbol: str, expiry: str) -> float:
        raise ProviderError("boom")

    def future_curve(self, symbol: str) -> list[dict]:
        raise ProviderError("boom")

    def get_option_quote(self, underlying, expiry, strike, option_type):
        raise ProviderError("boom")

    def option_chain(self, underlying, expiry, strike_step=None, strikes_per_side=10):
        raise ProviderError("boom")

    def expiries(self, count: int = 6) -> list[str]:
        raise ProviderError("boom")


class LiveProvider(FlakyProvider):
    """Fake 'live' provider: serves spots from a dict, fails after N calls."""

    name = "fake-live"

    def __init__(self, spots: dict[str, float], fail_after: int | None = None):
        self.spots = dict(spots)
        self.fail_after = fail_after
        self.calls = 0

    def get_spot(self, symbol: str) -> float:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise ProviderError("live feed down")
        if symbol not in self.spots:
            raise ProviderError(f"unknown {symbol}")
        return self.spots[symbol]

    def get_quotes(self) -> dict[str, float]:
        return dict(self.spots)

    def expiries(self, count: int = 6) -> list[str]:
        return ["2026-10-01", "2026-10-29"][:count]

    def get_future_price(self, symbol: str, expiry: str) -> float:
        return round(self.spots[symbol] * 1.01, 2)

    def future_curve(self, symbol: str) -> list[dict]:
        return [{"expiry": e, "price": self.spots[symbol] * 1.01, "basis": 0.0}
                for e in self.expiries(2)]

    def get_option_quote(self, underlying, expiry, strike, option_type):
        bs = __import__("backend.greeks", fromlist=["black_scholes"]).black_scholes(
            self.spots[underlying], strike, 0.05, 0.15, option_type)
        return OptionQuote(
            underlying=underlying, expiry=expiry, strike=strike,
            option_type=option_type, ltp=round(bs["price"], 2),
            iv=0.15, delta=round(bs["delta"], 4), spot=self.spots[underlying],
        )

    def option_chain(self, underlying, expiry, strike_step=None, strikes_per_side=2):
        spot = self.spots[underlying]
        step = strike_step or 100
        atm = round(spot / step) * step
        rows = []
        for k in range(atm - strikes_per_side * step, atm + strikes_per_side * step + 1, step):
            rows.append(_make_row(self, underlying, expiry, k))
        return rows


def _make_row(provider, underlying, expiry, strike):
    return __import__("backend.models", fromlist=["ChainRow"]).ChainRow(
        strike=strike,
        ce=provider.get_option_quote(underlying, expiry, strike, "CE"),
        pe=provider.get_option_quote(underlying, expiry, strike, "PE"),
    )


class FakeNselibIndices:
    """Stand-in for nselib.indices (live index performances table)."""

    def __init__(self, spots: dict[str, float]):
        name_map = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}
        self.spots = spots
        self._rows = [
            {"index": nse_name, "last": spots[symbol], "percentChange": 0.42}
            for symbol, nse_name in name_map.items() if symbol in spots
        ]

    def live_index_performances(self):
        return pd.DataFrame(self._rows)


class FakeNselibDerivatives:
    """Stand-in for nselib.derivatives (expiries + live option chain)."""

    def __init__(self, chain_df: pd.DataFrame):
        self.chain_df = chain_df

    def expiry_dates_option_index(self):
        # Real nselib returns %d-%b-%Y strings keyed by underlying.
        return {"NIFTY": ["02-10-2026", "30-10-2026", "27-11-2026"]}

    def nse_live_option_chain(self, symbol: str, expiry_date: str,
                              oi_mode: str = "full"):
        # Real nselib parses expiry_date as %d-%m-%Y; the provider converts.
        assert expiry_date == "02-10-2026", expiry_date  # format contract
        return self.chain_df


def _fake_chain_df() -> pd.DataFrame:
    """NSE-style option chain frame (real 2.5.1 column names) with messy
    but realistic formatting."""
    return pd.DataFrame([
        {
            "Strike_Price": 25000,
            "CALLS_LTP": "162.45",
            "CALLS_IV": "13.7",
            "CALLS_OI": "2,00,000",
            "CALLS_Chng_in_OI": "50,000",
            "CALLS_Volume": "1,00,000",
            "CALLS_Bid_Price": "162.20",
            "CALLS_Ask_Price": "162.70",
            "PUTS_LTP": "185.05",
            "PUTS_IV": "14.1",
            "PUTS_OI": "1,80,000",
            "PUTS_Chng_in_OI": "-45,000",
            "PUTS_Volume": "95,000",
            "PUTS_Bid_Price": "184.80",
            "PUTS_Ask_Price": "185.30",
        },
        {
            "Strike_Price": 25100,
            "CALLS_LTP": "-",          # missing values tolerated
            "CALLS_IV": "-",
            "CALLS_OI": "-",
            "PUTS_LTP": "240.20",
            "PUTS_IV": "14.4",
            "PUTS_OI": "1,50,000",
        },
    ])


# ---------------------------------------------------------------------------
# Manager: fallback, cooldown, cache, metadata
# ---------------------------------------------------------------------------

def _make_manager(*providers, quote_ttl=0.0, now=None):
    ticks = {"t": 0.0}

    def now_fn():
        return ticks["t"]

    mgr = MarketManager(providers=list(providers), quote_ttl=quote_ttl,
                        now_fn=now_fn)
    return mgr, ticks


def test_fallback_to_mock_when_live_fails():
    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    live = LiveProvider({"NIFTY": 25000.0}, fail_after=0)  # fails immediately
    mgr, _ = _make_manager(live, mock)

    spot = mgr.get_spot("NIFTY")
    assert spot > 20_000  # mock value served

    status = mgr.provider_status()
    flaky_state = next(p for p in status if p["name"] == "fake-live")
    assert flaky_state["cooling_down"] is True
    assert "live feed down" in flaky_state["last_error"]


def test_cooldown_prevents_hot_retry():
    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    live = LiveProvider({"NIFTY": 25000.0}, fail_after=0)
    mgr, ticks = _make_manager(live, mock)

    mgr.get_spot("NIFTY")          # live fails → cooldown, mock serves
    assert live.calls == 1

    mgr.get_spot("NIFTY")          # still cooling: live not retried
    assert live.calls == 1

    ticks["t"] += 31               # cooldown expired
    mgr.get_spot("NIFTY")
    assert live.calls == 2


def test_ttl_cache_serves_same_value():
    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    live = LiveProvider({"NIFTY": 25000.0})
    mgr, ticks = _make_manager(live, mock, quote_ttl=10.0)

    a = mgr.get_spot("NIFTY")
    live.spots["NIFTY"] = 26000.0  # provider value changes underneath
    b = mgr.get_spot("NIFTY")
    assert a == b == 25000.0       # cache hit

    ticks["t"] += 11               # TTL expired → re-fetch
    c = mgr.get_spot("NIFTY")
    assert c == 26000.0


def test_quote_metadata_carries_source():
    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    live = LiveProvider({"NIFTY": 25000.0})
    mgr, _ = _make_manager(live, mock)

    q_live = mgr.get_quote("NIFTY")
    assert q_live.source == "fake-live"
    assert q_live.status == "live"

    q_mock = mgr.get_quote("RELIANCE")  # live doesn't know it → mock serves
    assert q_mock.source == "mock"
    assert q_mock.status == "simulated"


def test_all_providers_down_raises():
    mgr, _ = _make_manager(FlakyProvider())
    with pytest.raises(ProviderError):
        mgr.get_spot("NIFTY")


def test_manager_engine_surface_mock_only():
    """Manager duck-types the engine surface using only the mock provider."""
    mock = MockMarketDataProvider(MockMarketData(volatility=0.0))
    mgr, _ = _make_manager(mock)
    expiries = mgr.expiries(2)
    assert len(expiries) == 2
    quote = mgr.get_option_quote("NIFTY", expiries[0], 25000, "CE")
    assert quote["ltp"] > 0            # legacy dict-style access on models
    assert quote.delta > 0
    chain = mgr.option_chain("NIFTY", expiries[0], strikes_per_side=2)
    assert len(chain) == 5


# ---------------------------------------------------------------------------
# Route metadata
# ---------------------------------------------------------------------------

def test_option_chain_route_has_metadata(fresh_db):
    body = client.get("/api/option-chain/NIFTY").json()
    assert body["source"] == "mock"
    assert body["status"] == "simulated"
    assert body["timestamp"].endswith("Z")
    assert body["rows"][0]["ce"]["delta"] is not None


def test_expiries_route_has_metadata(fresh_db):
    body = client.get("/api/expiries/NIFTY").json()
    assert body["source"] == "mock"
    assert body["status"] == "simulated"
    assert len(body["futures"]) == 6


def test_market_status_route(fresh_db):
    body = client.get("/api/market-status").json()
    assert body["primary"] == "mock"   # conftest uses mock-only stack
    assert body["healthy"] is True
    assert any(p["name"] == "mock" for p in body["providers"])


# ---------------------------------------------------------------------------
# nselib provider normalization (fake module)
# ---------------------------------------------------------------------------

def _nselib_provider():
    from backend.providers.nselib_provider import NselibProvider

    spots = {"NIFTY": 24850.0, "BANKNIFTY": 51200.0}
    return NselibProvider(
        derivatives_module=FakeNselibDerivatives(_fake_chain_df()),
        indices_module=FakeNselibIndices(spots),
    )


def test_nselib_available_flag():
    from backend.providers import nselib_provider

    assert nselib_provider.available() in (True, False)  # env-dependent


def test_nselib_spot_normalization():
    p = _nselib_provider()
    assert p.get_spot("NIFTY") == 24850.0


def test_nselib_expiry_parsing():
    p = _nselib_provider()
    expiries = p.expiries(3, symbol="NIFTY")
    assert expiries == ["2026-10-02", "2026-10-30", "2026-11-27"]


def test_nselib_option_quote_greeks_computed():
    p = _nselib_provider()
    q = p.get_option_quote("NIFTY", "2026-10-02", 25000.0, "CE")
    assert q.ltp == 162.45
    assert q.iv == pytest.approx(0.137)
    assert 0 < q.delta < 1
    assert q.oi == 200000 and q.oi_change == 50000 and q.volume == 100000


def test_nselib_missing_fields_fall_back():
    p = _nselib_provider()
    q = p.get_option_quote("NIFTY", "2026-10-02", 25100.0, "CE")
    # LTP '-' → BS-derived price; IV '-' → smile fallback
    assert q.ltp > 0
    assert 0 < q.iv < 2
    assert q.oi is None and q.volume is None


def test_nselib_chain_rows_sorted_window():
    p = _nselib_provider()
    rows = p.option_chain("NIFTY", "2026-10-02", strike_step=100,
                          strikes_per_side=1)
    strikes = [r.strike for r in rows]
    assert strikes == sorted(strikes)
    assert all(r.ce.ltp > 0 for r in rows)


def test_default_manager_stack():
    """Production stack: nselib → yfinance, NO mock (real data or UNAVAILABLE)."""
    from backend.market_data_manager import default_manager

    mgr = default_manager()
    names = [p.name for p in mgr.providers]
    assert names[0] == "nselib"
    assert "yfinance" in names
    assert "mock" not in names
