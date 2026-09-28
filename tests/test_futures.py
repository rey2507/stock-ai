"""Stage 3 tests: futures lot sizes, margin math, futures curve.

Run from the project root: pytest tests/test_futures.py
"""

from __future__ import annotations

import pytest

from tests.conftest import client
from backend.market_data import BASE_EQUITY_SPOT as BASE
from backend.nse_data import (
    INDEX_META,
    INDEX_SPOT,
    STOCK_META,
    contract_multiplier,
    margin_pct,
)


# ---------------------------------------------------------------------------
# Static metadata
# ---------------------------------------------------------------------------

def test_index_lot_sizes_match_circulars():
    # NSE circular FAOP70616 — effective from Dec 30, 2025 / Jan 2026 series.
    assert INDEX_META["NIFTY"].lot_size == 65
    assert INDEX_META["BANKNIFTY"].lot_size == 30
    assert INDEX_META["FINNIFTY"].lot_size == 60
    assert INDEX_META["MIDCPNIFTY"].lot_size == 120


def test_stock_lot_sizes_are_positive_and_multipliers_unity():
    for symbol, meta in STOCK_META.items():
        assert meta.lot_size > 0
        assert contract_multiplier(symbol) == 1.0
        assert 0.05 <= margin_pct(symbol) <= 0.5


def test_index_spot_metadata_present():
    for symbol, spot in INDEX_SPOT.items():
        assert spot > 1000


# ---------------------------------------------------------------------------
# Futures curve behaviour
# ---------------------------------------------------------------------------

def test_futures_curve_is_contango():
    from backend.market_data import MockMarketData
    md = MockMarketData(volatility=0.0)
    curve = md.future_curve("RELIANCE")
    assert len(curve) >= 3
    prices = [c["price"] for c in curve]
    assert prices == sorted(prices), "far months should carry (contango)"
    assert curve[0]["basis"] > 0  # carry basis above spot


def test_expiries_endpoint_returns_curve():
    body = client.get("/api/expiries/NIFTY").json()
    assert body["symbol"] == "NIFTY"
    assert len(body["expiries"]) == 6
    assert len(body["futures"]) == 6
    assert all(set(f) >= {"expiry", "price", "basis"} for f in body["futures"])


# ---------------------------------------------------------------------------
# Margin mechanics via the API
# ---------------------------------------------------------------------------

def test_nifty_fut_margin_is_pct_of_notional():
    spot = INDEX_SPOT["NIFTY"]
    r = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "FUT",
        "side": "BUY", "quantity": 65,
    })
    assert r.status_code == 200, r.text
    fill = r.json()["fill_price"]
    notional = fill * 65
    margin = client.get("/api/account").json()["used_margin"]
    assert margin == pytest.approx(notional * margin_pct("NIFTY"), rel=1e-6)
    # Sanity: 12% of ~₹16.2L notional ≈ ₹1.9L, well under the ₹10L capital.
    assert 0.08 * notional < margin < 0.20 * notional


def test_insufficient_margin_rejected():
    # BANKNIFTY ~₹51,200 × 30 units × 8 lots ≈ ₹1.23 crore notional →
    # margin at 13% (≈₹16L) far exceeds the ₹10L capital.
    r = client.post("/api/orders", json={
        "symbol": "BANKNIFTY", "instrument_type": "FUT",
        "side": "BUY", "quantity": 240,  # 8 lots
    })
    assert r.status_code == 400
    assert "Insufficient margin" in r.json()["detail"]


def test_short_futures_margin_then_profit_on_fall(fresh_db):
    entry = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "SELL", "quantity": 500,
    }).json()["fill_price"]

    md = fresh_db
    md._base["RELIANCE"] = BASE["RELIANCE"] * 0.95  # 5% drop

    acct = client.get("/api/account").json()
    expected_pnl = entry * 0.05 * 500
    assert acct["unrealized_pnl"] == pytest.approx(expected_pnl, abs=5.0)
    assert acct["total_pnl"] == pytest.approx(expected_pnl, abs=5.0)

    # Close: realized lands in cash.
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "BUY", "quantity": 500,
    })
    assert r.status_code == 200, r.text
    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000 + expected_pnl, abs=5.0)
    assert acct["total_pnl"] == pytest.approx(expected_pnl, abs=5.0)


def test_long_futures_marks_to_market():
    r = client.post("/api/orders", json={
        "symbol": "TCS", "instrument_type": "FUT",
        "side": "BUY", "quantity": 225,
    })
    assert r.status_code == 200
    positions = client.get("/api/positions").json()["positions"]
    assert positions[0]["instrument_type"] == "FUT"
    assert positions[0]["quantity"] == 225
    assert positions[0]["current_price"] >= BASE["TCS"]  # carry basis
    assert positions[0]["unrealized_pnl"] == pytest.approx(0.0, abs=100.0)
