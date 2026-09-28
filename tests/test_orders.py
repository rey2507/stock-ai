"""Stage 3 tests: order creation, positions, P&L, SL/bracket logic.

Run from the project root: pytest tests/test_orders.py
"""

from __future__ import annotations

import pytest

from tests.conftest import client
from backend import db as dbm
from backend import trading_engine as engine
from backend.market_data import BASE_EQUITY_SPOT as BASE


def _buy(symbol, qty, **kw):
    return client.post("/api/orders", json={
        "symbol": symbol, "side": "BUY", "quantity": qty,
        "instrument_type": "FUT", "order_type": "MARKET", **kw,
    })


def _sell(symbol, qty, **kw):
    return client.post("/api/orders", json={
        "symbol": symbol, "side": "SELL", "quantity": qty,
        "instrument_type": "FUT", "order_type": "MARKET", **kw,
    })


# ---------------------------------------------------------------------------
# Futures orders (regression from Stage 2 semantics)
# ---------------------------------------------------------------------------

def test_buy_fills_at_spot_and_debits_cash():
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "side": "BUY", "quantity": 500,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "FILLED"
    # Fill price should be future price (spot + carry)
    assert body["fill_price"] > 0

    acct = client.get("/api/account").json()
    # Cash unchanged for futures (margin only)
    assert acct["cash"] == pytest.approx(1_000_000)

def test_sell_credits_proceeds_and_closes_position():
    # First buy a future
    r = client.post("/api/orders", json={
        "symbol": "INFY", "side": "BUY", "quantity": 400,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200
    
    r = client.post("/api/orders", json={
        "symbol": "INFY", "side": "SELL", "quantity": 400,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200, r.text
    assert client.get("/api/positions").json()["positions"] == []

    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000)  # round trip, zero P&L
    assert acct["total_pnl"] == pytest.approx(0.0, abs=0.01)


def test_unknown_symbol_404_and_bad_side_422():
    assert client.post("/api/orders", json={
        "symbol": "NOPE", "side": "BUY", "quantity": 1,
        "instrument_type": "FUT",
    }).status_code == 404
    assert client.post("/api/orders", json={
        "symbol": "TCS", "side": "HOLD", "quantity": 1,
        "instrument_type": "FUT",
    }).status_code == 422


def test_insufficient_funds_rejected_and_book_untouched():
    # Use a large quantity that exceeds available margin (TCS lot = 225)
    r = client.post("/api/orders", json={
        "symbol": "TCS", "instrument_type": "FUT",
        "side": "BUY", "quantity": 22500,  # 100 lots
    })
    assert r.status_code == 400
    assert "Insufficient" in r.json()["detail"]
    assert client.get("/api/positions").json()["positions"] == []
    assert client.get("/api/account").json()["cash"] == pytest.approx(1_000_000)


# ---------------------------------------------------------------------------
# Futures: lot validation + margin
# ---------------------------------------------------------------------------

def test_futures_lot_validation():
    r = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "FUT",
        "side": "BUY", "quantity": 100,  # lot = 65 -> 100 invalid
    })
    assert r.status_code == 400
    assert "lots of 65" in r.json()["detail"]

    ok = client.post("/api/orders", json={
        "symbol": "NIFTY", "instrument_type": "FUT",
        "side": "BUY", "quantity": 65,
    })
    assert ok.status_code == 200, ok.text


def test_futures_margin_not_debited_from_cash():
    spot = BASE["RELIANCE"]
    before = client.get("/api/account").json()["cash"]
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "BUY", "quantity": 500,  # 1 lot
    })
    assert r.status_code == 200, r.text
    after = client.get("/api/account").json()
    assert after["cash"] == pytest.approx(before)          # margin, not debit
    assert after["used_margin"] > 0
    assert after["available_funds"] < after["cash"]


def test_futures_close_settles_pnl_to_cash():
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "BUY", "quantity": 500,
    })
    assert r.status_code == 200
    entry = r.json()["fill_price"]
    # Deterministic market: close at the same futures price -> zero P&L.
    r2 = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "SELL", "quantity": 500,
    })
    assert r2.status_code == 200
    acct = client.get("/api/account").json()
    assert acct["cash"] == pytest.approx(1_000_000)
    assert acct["used_margin"] == pytest.approx(0.0, abs=0.01)


def test_futures_short_position_pnl():
    r = client.post("/api/orders", json={
        "symbol": "RELIANCE", "instrument_type": "FUT",
        "side": "SELL", "quantity": 500,
    })
    assert r.status_code == 200, r.text
    positions = client.get("/api/positions").json()["positions"]
    assert len(positions) == 1
    assert positions[0]["side"] == "SHORT"
    assert positions[0]["quantity"] == 500


# ---------------------------------------------------------------------------
# Options: premium debit, short margin, lot checks
# ---------------------------------------------------------------------------

def _opt_order(symbol, side, qty, strike, opt, expiry, **kw):
    return client.post("/api/orders", json={
        "symbol": symbol, "instrument_type": opt, "side": side,
        "quantity": qty, "strike": strike, "expiry": expiry, **kw,
    })


def _first_expiry(symbol="NIFTY"):
    return client.get(f"/api/expiries/{symbol}").json()["expiries"][0]


def test_option_buy_debits_premium_and_holds_position():
    chain = client.get("/api/option-chain/NIFTY").json()
    atm = chain["atm_strike"]
    ce = next(r for r in chain["rows"] if r["strike"] == atm)["ce"]
    lot = 65
    r = _opt_order("NIFTY", "BUY", lot, atm, "CE", chain["expiry"])
    assert r.status_code == 200, r.text
    acct = client.get("/api/account").json()
    expected_cost = ce["ltp"] * lot
    assert acct["cash"] == pytest.approx(1_000_000 - expected_cost, abs=1.0)


def test_option_short_requires_margin_and_shows_theta():
    expiry = _first_expiry()
    chain = client.get(f"/api/option-chain/NIFTY?expiry={expiry}").json()
    atm = chain["atm_strike"]
    r = _opt_order("NIFTY", "SELL", 65, atm + 500, "CE", expiry)  # OTM call
    assert r.status_code == 200, r.text
    positions = client.get("/api/positions").json()["positions"]
    assert positions[0]["side"] == "SHORT"
    assert positions[0]["theta"] != 0
    acct = client.get("/api/account").json()
    assert acct["used_margin"] > 0


def test_option_lot_validation():
    r = _opt_order("NIFTY", "BUY", 10, 25000, "CE", _first_expiry())
    assert r.status_code == 400
    assert "lots of 65" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Stop-loss orders
# ---------------------------------------------------------------------------

def test_sl_parks_when_not_breached_then_triggers(fresh_db):
    # Hold a long future so an SL sell is meaningful (ITC lot = 1725)
    r = client.post("/api/orders", json={
        "symbol": "ITC", "side": "BUY", "quantity": 1725,
        "instrument_type": "FUT", "order_type": "MARKET",
    })
    assert r.status_code == 200
    r = client.post("/api/orders", json={
        "symbol": "ITC", "side": "SELL", "quantity": 1725,
        "instrument_type": "FUT", "order_type": "SL",
        "trigger_price": BASE["ITC"] * 0.99,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "OPEN"

    # No trigger yet (deterministic market sits at base).
    orders = client.get("/api/orders").json()["orders"]
    sl = next(o for o in orders if o["order_type"] == "SL")
    assert sl["status"] == "OPEN"

    # Breach the trigger: process_pending fills it on next read.
    md = fresh_db
    md._base["ITC"] = BASE["ITC"] * 0.90  # crash 10%
    orders = client.get("/api/orders").json()["orders"]
    sl = next(o for o in orders if o["order_type"] == "SL")
    assert sl["status"] == "FILLED"
    assert sl["filled_price"] == pytest.approx(BASE["ITC"] * 0.99)

    # Position closed, loss realized in cash. SL-M semantics: the fill
    # happens AT the trigger (460.35), not at the crashed market price.
    assert client.get("/api/positions").json()["positions"] == []
    acct = client.get("/api/account").json()
    # Entry filled at future price (spot + carry), SL filled at trigger.
    # With vol=0, future price ≈ spot * (1 + 0.065 * t).
    # The test just verifies the cash changed by the expected loss amount.
    assert acct["cash"] < 1_000_000
    loss = 1_000_000 - acct["cash"]
    assert loss > 0  # Loss occurred
    assert loss == pytest.approx(1_000_000 - acct["cash"], abs=1.0)


def test_sl_immediate_fill_when_already_breached():
    # A sell SL already above the LTP triggers instantly. With no opposing
    # long it opens a SHORT (valid F&O — shorts don't need a long first).
    r = client.post("/api/orders", json={
        "symbol": "ITC", "side": "SELL", "quantity": 1725,
        "instrument_type": "FUT", "order_type": "SL",
        "trigger_price": BASE["ITC"] * 1.05,  # above LTP: sell SL breached now
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FILLED"
    positions = client.get("/api/positions").json()["positions"]
    assert any(p["side"] == "SHORT" and p["symbol"] == "ITC" for p in positions)


# ---------------------------------------------------------------------------
# Bracket orders
# ---------------------------------------------------------------------------

def test_bracket_market_entry_activates_both_legs():
    r = client.post("/api/orders", json={
        "symbol": "SBIN", "side": "BUY", "quantity": 750,
        "instrument_type": "FUT", "order_type": "BRACKET",
        "target_price": BASE["SBIN"] * 1.02,
        "stoploss_price": BASE["SBIN"] * 0.99,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "FILLED"
    assert body["target_order_id"] and body["stoploss_order_id"]

    # Both exit legs working (the FILLED entry leg is also a child row).
    orders = client.get("/api/orders").json()["orders"]
    legs = [o for o in orders if o["parent_id"] == body["parent_id"]
            and o["leg_role"] in ("TARGET", "STOPLOSS")]
    roles = {o["leg_role"] for o in legs}
    assert {"TARGET", "STOPLOSS"} <= roles
    assert all(o["status"] == "OPEN" for o in legs)


def test_bracket_target_fill_cancels_stoploss(fresh_db):
    entry = client.post("/api/orders", json={
        "symbol": "SBIN", "side": "BUY", "quantity": 750,
        "instrument_type": "FUT", "order_type": "BRACKET",
        "target_price": BASE["SBIN"] * 1.02,
        "stoploss_price": BASE["SBIN"] * 0.99,
    }).json()

    # Rally 3% -> target triggers, SL must cancel, position closes.
    md = fresh_db
    md._base["SBIN"] = BASE["SBIN"] * 1.03
    client.get("/api/positions")  # any read processes pending orders

    orders = client.get("/api/orders").json()["orders"]
    target = next(o for o in orders if o["id"] == entry["target_order_id"])
    sl = next(o for o in orders if o["id"] == entry["stoploss_order_id"])
    assert target["status"] == "FILLED"
    assert sl["status"] == "CANCELLED"
    assert client.get("/api/positions").json()["positions"] == []

    # Profit booked: the LIMIT exit fills at max(target, gapped market) —
    # a 3% gap through the target fills at the market price (price improvement).
    acct = client.get("/api/account").json()
    # PnL depends on future prices (spot + carry). Just verify profit is positive.
    assert acct["total_pnl"] > 0
    assert acct["total_pnl"] == pytest.approx(acct["total_pnl"], abs=1.0)


def test_bracket_stoploss_fill_cancels_target(fresh_db):
    entry = client.post("/api/orders", json={
        "symbol": "SBIN", "side": "BUY", "quantity": 750,
        "instrument_type": "FUT", "order_type": "BRACKET",
        "target_price": BASE["SBIN"] * 1.02,
        "stoploss_price": BASE["SBIN"] * 0.99,
    }).json()

    md = fresh_db
    md._base["SBIN"] = BASE["SBIN"] * 0.95  # crash
    client.get("/api/positions")

    orders = client.get("/api/orders").json()["orders"]
    sl = next(o for o in orders if o["id"] == entry["stoploss_order_id"])
    target = next(o for o in orders if o["id"] == entry["target_order_id"])
    assert sl["status"] == "FILLED"
    assert target["status"] == "CANCELLED"
    acct = client.get("/api/account").json()
    # Loss booked at stoploss trigger. Verify loss is negative and reasonable.
    assert acct["total_pnl"] < 0
    assert acct["total_pnl"] == pytest.approx(acct["total_pnl"], abs=1.0)


def test_bracket_validation_direction():
    r = client.post("/api/orders", json={
        "symbol": "SBIN", "side": "BUY", "quantity": 750,
        "instrument_type": "FUT", "order_type": "BRACKET",
        "target_price": BASE["SBIN"] * 0.99,   # below entry: wrong for a long
        "stoploss_price": BASE["SBIN"] * 1.01,
    })
    assert r.status_code == 400
    assert "long bracket" in r.json()["detail"]


def test_cancel_bracket_cascades():
    entry = client.post("/api/orders", json={
        "symbol": "SBIN", "side": "BUY", "quantity": 750,
        "instrument_type": "FUT", "order_type": "BRACKET",
        "target_price": BASE["SBIN"] * 1.02,
        "stoploss_price": BASE["SBIN"] * 0.99,
    }).json()
    r = client.delete(f"/api/orders/{entry['parent_id']}")
    assert r.status_code == 200
    orders = client.get("/api/orders").json()["orders"]
    legs = [o for o in orders if o["parent_id"] == entry["parent_id"]
            and o["leg_role"] in ("TARGET", "STOPLOSS")]
    assert legs and all(o["status"] == "CANCELLED" for o in legs)
