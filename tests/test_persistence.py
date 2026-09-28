"""Stage 8: Data persistence tests.

Verify that orders, positions, and account state survive database restart.
"""

from __future__ import annotations

import pytest

from backend import db as dbm
from backend.market_data import BASE_EQUITY_SPOT as BASE
from tests.conftest import client


def test_order_and_position_persist_after_db_restart(fresh_db):
    r = client.post("/api/orders", json={
        "symbol": "NIFTY", "side": "BUY", "quantity": 65,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200
    order_id = r.json()["order_id"]

    orders = client.get("/api/orders").json()["orders"]
    assert any(o["id"] == order_id for o in orders)

    positions = client.get("/api/positions").json()["positions"]
    assert len(positions) == 1
    assert positions[0]["symbol"] == "NIFTY"

    acct = client.get("/api/account").json()
    assert acct["cash"] == 1_000_000  # futures use margin, not cash

    dbm.init_db()

    orders = client.get("/api/orders").json()["orders"]
    assert any(o["id"] == order_id for o in orders)

    positions = client.get("/api/positions").json()["positions"]
    assert len(positions) == 1
    assert positions[0]["symbol"] == "NIFTY"

    acct = client.get("/api/account").json()
    assert acct["cash"] == 1_000_000


def test_position_close_pnl_persists(fresh_db):
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "side": "BUY", "quantity": 500,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200

    r2 = client.post("/api/orders", json={
        "symbol": "RELIANCE", "side": "SELL", "quantity": 500,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r2.status_code == 200

    positions = client.get("/api/positions").json()["positions"]
    assert len(positions) == 0

    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000, abs=1.0)

    dbm.init_db()

    positions = client.get("/api/positions").json()["positions"]
    assert len(positions) == 0

    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000, abs=1.0)


def test_multiple_orders_persist_in_order(fresh_db):
    # Use RELIANCE FUT with lot size 500
    reliance_price = BASE["RELIANCE"]
    qty = 500
    order_ids = []

    for i in range(3):
        r = client.post("/api/orders", json={
            "symbol": "RELIANCE", "side": "BUY", "quantity": qty,
            "instrument_type": "FUT", "order_type": "MARKET",
        })
        assert r.status_code == 200
        order_ids.append(r.json()["order_id"])

        acct = client.get("/api/account").json()
        # Cash unchanged for futures (margin only)
        assert acct["cash"] == pytest.approx(1_000_000)

    dbm.init_db()

    orders = client.get("/api/orders").json()["orders"]
    returned_ids = [o["id"] for o in orders]
    for oid in order_ids:
        assert oid in returned_ids

    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000)


def test_bracket_order_family_persists(fresh_db):
    r = client.post("/api/orders", json={
        "symbol": "SBIN", "side": "BUY", "quantity": 750,
        "instrument_type": "FUT", "order_type": "BRACKET",
        "target_price": BASE["SBIN"] * 1.02,
        "stoploss_price": BASE["SBIN"] * 0.99,
    })
    assert r.status_code == 200
    parent_id = r.json()["parent_id"]

    orders = client.get("/api/orders").json()["orders"]
    family = [o for o in orders if o["id"] == parent_id or o["parent_id"] == parent_id]
    assert len(family) >= 4
    leg_roles = {o["leg_role"] for o in family}
    assert {"ENTRY", "TARGET", "STOPLOSS"}.issubset(leg_roles)

    dbm.init_db()

    orders = client.get("/api/orders").json()["orders"]
    family = [o for o in orders if o["id"] == parent_id or o["parent_id"] == parent_id]
    assert len(family) >= 4
    leg_roles = {o["leg_role"] for o in family}
    assert {"ENTRY", "TARGET", "STOPLOSS"}.issubset(leg_roles)
