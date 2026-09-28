"""Stage 8: Verify API responses never contain fake/placeholder data."""

from __future__ import annotations

import pytest

from backend import main
from backend.market_data import BASE_EQUITY_SPOT as BASE
from backend.market_data_manager import MarketManager
from backend.providers.mock_provider import MockMarketDataProvider
from tests.conftest import client


def test_empty_positions_no_demo_data(fresh_db):
    resp = client.get("/api/positions").json()
    assert resp["positions"] == []
    assert resp["total_unrealized_pnl"] == 0.0
    assert resp["greeks"] == {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}


def test_empty_orders_no_sample_data(fresh_db):
    resp = client.get("/api/orders").json()
    assert resp["orders"] == []


def test_chart_unavailable_not_faked(fresh_db, monkeypatch):
    empty = MarketManager(providers=[])
    monkeypatch.setattr(main, "market_data", empty)

    body = client.get("/api/chart/NIFTY?interval=1D").json()
    assert body["status"] == "unavailable"
    assert body["source"] is None
    assert body["candles"] == []
    assert "detail" in body


def test_option_chain_strikes_reasonable(fresh_db):
    body = client.get("/api/option-chain/NIFTY").json()
    spot = body["spot"]
    strikes = [r["strike"] for r in body["rows"]]

    assert len(strikes) > 0
    for strike in strikes:
        assert strike > 0
        assert abs(strike - spot) < spot


def test_prices_from_provider_not_hardcoded(fresh_db):
    body1 = client.get("/api/watchlist").json()
    rel1 = next(q for q in body1["watchlist"] if q["symbol"] == "RELIANCE")
    price1 = rel1["ltp"]

    md = fresh_db
    new_base = 5000.0
    md._base["RELIANCE"] = new_base

    body2 = client.get("/api/watchlist").json()
    rel2 = next(q for q in body2["watchlist"] if q["symbol"] == "RELIANCE")
    price2 = rel2["ltp"]

    assert price2 != price1
    assert abs(price2 - new_base) < new_base * 0.01


def test_account_metrics_computed_not_constant(fresh_db):
    acct = client.get("/api/account").json()

    assert acct["available_funds"] == pytest.approx(acct["cash"] - acct["used_margin"])
    assert acct["equity"] >= acct["cash"] - 0.01
    assert acct["total_pnl"] == pytest.approx(acct["equity"] - acct["starting_capital"])
    assert acct["unrealized_pnl"] == pytest.approx(
        sum(p["unrealized_pnl"] for p in client.get("/api/positions").json()["positions"]),
        abs=0.01,
    )


def test_watchlist_source_labeled_honestly(fresh_db):
    body = client.get("/api/watchlist").json()
    for q in body["watchlist"]:
        assert q["status"] in ("live", "simulated", "stale", "unavailable", "derived")
        assert q["source"] is not None
