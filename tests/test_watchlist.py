"""Stage 3 tests: watchlist CRUD, quotes, portfolio Greeks aggregation.

Run from the project root: pytest tests/test_watchlist.py
"""

from __future__ import annotations

import pytest

from tests.conftest import client


def test_default_watchlist_seeded():
    body = client.get("/api/watchlist").json()
    symbols = [q["symbol"] for q in body["watchlist"]]
    assert "NIFTY" in symbols and "RELIANCE" in symbols
    quote = body["watchlist"][0]
    assert set(quote) >= {"symbol", "name", "kind", "lot_size", "ltp", "change_pct"}


def test_add_and_remove_symbol():
    r = client.post("/api/watchlist", json={"symbol": "TCS"})
    assert r.status_code == 200
    assert r.json()["added"] is True
    assert "TCS" in r.json()["symbols"]

    # Adding again is idempotent.
    r2 = client.post("/api/watchlist", json={"symbol": "TCS"})
    assert r2.json()["added"] is False

    quotes = client.get("/api/watchlist").json()["watchlist"]
    assert "TCS" in [q["symbol"] for q in quotes]

    r3 = client.delete("/api/watchlist/TCS")
    assert r3.status_code == 200
    assert "TCS" not in r3.json()["symbols"]

    # Removing again 404s.
    assert client.delete("/api/watchlist/TCS").status_code == 404


def test_add_unknown_symbol_404():
    assert client.post("/api/watchlist", json={"symbol": "NOPE"}).status_code == 404


def test_watchlist_quotes_carry_spot_data():
    quotes = client.get("/api/watchlist").json()["watchlist"]
    nifty = next(q for q in quotes if q["symbol"] == "NIFTY")
    assert nifty["kind"] == "index"
    assert nifty["ltp"] > 10_000
    assert nifty["lot_size"] == 65


def test_option_buy_shows_in_portfolio_greeks():
    """Buying a call adds positive delta and negative theta to the book."""
    chain = client.get("/api/option-chain/NIFTY").json()
    atm = chain["atm_strike"]
    before = client.get("/api/account").json()["greeks"]
    r = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "CE", "side": "BUY",
        "quantity": 65, "strike": atm, "expiry": chain["expiry"],
    })
    assert r.status_code == 200, r.text
    after = client.get("/api/account").json()["greeks"]
    assert after["delta"] > before["delta"] + 10    # ATM call ~0.5 × 65
    assert after["theta"] < before["theta"]          # long options bleed theta
    assert after["vega"] > before["vega"]


def test_futures_and_options_greeks_sum():
    """A long future (Δ≈+65) plus a put (Δ negative) nets correctly."""
    client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "FUT",
        "side": "BUY", "quantity": 65,
    })
    chain = client.get("/api/option-chain/NIFTY").json()
    atm = chain["atm_strike"]
    client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "PE", "side": "BUY",
        "quantity": 65, "strike": atm, "expiry": chain["expiry"],
    })
    greeks = client.get("/api/account").json()["greeks"]
    # FUT Δ=+65; ATM put Δ≈-0.45..-0.55 × 65 → net Δ in (25, 45).
    assert 20 < greeks["delta"] < 48
    assert greeks["theta"] < 0
