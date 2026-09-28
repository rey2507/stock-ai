"""Stage 8: Account reconciliation tests.

Verify that account balances reconcile with actual trading activity.
"""

from __future__ import annotations

import pytest

from backend import db as dbm
from backend.market_data import BASE_EQUITY_SPOT as BASE
from backend.nse_data import margin_pct
from tests.conftest import client


def test_cash_reconciliation_after_trades(fresh_db):
    # For futures, cash is unchanged on open/close (margin only)
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "side": "BUY", "quantity": 500,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200

    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000)

    r2 = client.post("/api/orders", json={
        "symbol": "RELIANCE", "side": "SELL", "quantity": 500,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r2.status_code == 200

    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000)
    assert acct["total_pnl"] == pytest.approx(0.0, abs=0.01)


def test_margin_reconciliation_for_futures(fresh_db):
    r = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "FUT",
        "side": "BUY", "quantity": 65,
    })
    assert r.status_code == 200
    fill_price = r.json()["fill_price"]

    acct = client.get("/api/account").json()
    notional = fill_price * 65
    expected_margin = notional * margin_pct("NIFTY")
    assert acct["used_margin"] == pytest.approx(expected_margin, rel=1e-6)
    assert acct["available_funds"] == pytest.approx(acct["cash"] - expected_margin)

    r2 = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "FUT",
        "side": "SELL", "quantity": 65,
    })
    assert r2.status_code == 200

    acct = client.get("/api/account").json()
    assert acct["used_margin"] == pytest.approx(0.0, abs=0.01)
    assert acct["available_funds"] == pytest.approx(acct["cash"])


def test_unrealized_pnl_tracking(fresh_db):
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "BUY", "quantity": 500,
    })
    assert r.status_code == 200
    entry_price = r.json()["fill_price"]

    acct = client.get("/api/account").json()
    assert acct["unrealized_pnl"] == pytest.approx(0.0, abs=100.0)

    md = fresh_db
    md._base["RELIANCE"] = BASE["RELIANCE"] * 1.05
    acct = client.get("/api/account").json()
    expected_up = entry_price * 0.05 * 500
    assert acct["unrealized_pnl"] == pytest.approx(expected_up, abs=5.0)

    md._base["RELIANCE"] = BASE["RELIANCE"] * 0.90
    acct = client.get("/api/account").json()
    expected_down = entry_price * -0.10 * 500
    assert acct["unrealized_pnl"] == pytest.approx(expected_down, abs=5.0)

    r2 = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "SELL", "quantity": 500,
    })
    assert r2.status_code == 200
    acct = client.get("/api/account").json()
    assert acct["unrealized_pnl"] == pytest.approx(0.0, abs=0.01)


def test_no_fake_data_in_account_response(fresh_db):
    acct = client.get("/api/account").json()

    assert acct["starting_capital"] == 1_000_000.0
    assert acct["cash"] <= 1_000_000 + 0.01
    assert acct["available_funds"] == pytest.approx(acct["cash"] - acct["used_margin"])
    assert acct["equity"] >= acct["cash"] - 0.01
    assert acct["total_pnl"] == pytest.approx(acct["equity"] - acct["starting_capital"])
    assert acct["unrealized_pnl"] == pytest.approx(
        sum(p["unrealized_pnl"] for p in client.get("/api/positions").json()["positions"]),
        abs=0.01,
    )

    status = acct["data_source"]["status"]
    assert status in ("live", "simulated", "stale", "unavailable", "mixed", "none")


def test_portfolio_greeks_reconcile_with_positions(fresh_db):
    chain = client.get("/api/option-chain/NIFTY").json()
    atm = chain["atm_strike"]
    expiry = chain["expiry"]

    r = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "CE", "side": "BUY",
        "quantity": 65, "strike": atm, "expiry": expiry,
    })
    assert r.status_code == 200

    positions = client.get("/api/positions").json()["positions"]
    account = client.get("/api/account").json()

    pos_delta = sum(
        (1 if p["side"] == "LONG" else -1) * p["quantity"] * (p.get("delta") or 0)
        for p in positions
    )
    pos_theta = sum(
        (1 if p["side"] == "LONG" else -1) * p["quantity"] * (p.get("theta") or 0)
        for p in positions
    )

    assert account["greeks"]["delta"] == pytest.approx(pos_delta, abs=0.01)
    assert account["greeks"]["theta"] == pytest.approx(pos_theta, abs=0.01)
