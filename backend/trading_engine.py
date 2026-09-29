"""Paper-Trader — trading engine (Stage 3).

Order lifecycle, margin, and risk rules for EQ/FUT/OPT:

- **Lot validation** — F&O quantities must be whole lots.
- **Margin model** — futures and short options hold margin (cash untouched
  on open, P&L settled on close); long options and EQ pay up front like
  Stage 2 equity buys.
- **Stop-loss** — standalone SL orders trigger on LTP breach and fill at
  the trigger price (market conversion).
- **Bracket orders** — entry + target + stoploss legs under a parent order;
  any exit leg fill closes the position and cancels sibling legs.

Pending orders are processed lazily: routes call :func:`process_pending_orders`
before reading state, so triggers fire on the first request after a breach.
"""

from __future__ import annotations

from typing import Any

from backend import db
from backend.market_data import MockMarketData
from backend.market_data_source import ProviderError
from backend.nse_data import contract_multiplier, margin_pct, SHORT_OPTION_MARGIN_PCT

VALID_ORDER_TYPES = ("MARKET", "LIMIT", "SL", "BRACKET")
VALID_INSTRUMENT_TYPES = ("FUT", "CE", "PE")


class OrderError(Exception):
    """Validation failure → HTTP 400."""

class NotFoundError(Exception):
    """Unknown instrument/order → HTTP 404."""


# ---------------------------------------------------------------------------
# Pricing helper
# ---------------------------------------------------------------------------

def ltp(md: MockMarketData, symbol: str, instrument_type: str,
        expiry: str = "", strike: float = 0.0, option_type: str = "") -> float:
    """Last traded price for any instrument key."""
    if instrument_type == "FUT":
        return md.get_future_price(symbol, expiry)
    if instrument_type in ("CE", "PE"):
        return md.get_option_quote(symbol, expiry, strike, instrument_type)["ltp"]
    raise ValueError(f"bad instrument_type: {instrument_type}. Only FUT, CE, PE supported.")


def option_side_of(order_side: str) -> str:
    """Map a CE/PE order's side field onto option_type (they are the same)."""
    return "CE" if order_side == "CE" else "PE"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_request(conn, md: MockMarketData, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise + validate an order payload. Raises OrderError/NotFoundError."""
    symbol = str(payload.get("symbol", "")).upper()
    side = str(payload.get("side", "")).upper()
    instrument_type = str(payload.get("instrument_type", "EQ")).upper()
    order_type = str(payload.get("order_type", "MARKET")).upper()
    quantity = payload.get("quantity")
    price = payload.get("price")
    trigger_price = payload.get("trigger_price")
    expiry = str(payload.get("expiry", "") or "")
    strike = float(payload.get("strike") or 0.0)

    if side not in ("BUY", "SELL"):
        raise OrderError("side must be BUY or SELL")
    if instrument_type not in VALID_INSTRUMENT_TYPES:
        raise OrderError(f"instrument_type must be one of {VALID_INSTRUMENT_TYPES}. Equity (EQ) trading is not supported.")
    if order_type not in VALID_ORDER_TYPES:
        raise OrderError(f"order_type must be one of {VALID_ORDER_TYPES}")

    inst = db.get_instrument(conn, symbol)
    if inst is None:
        raise NotFoundError(f"Unknown symbol: {symbol}")

    # Futures don't carry a strike — normalise to 0 so the position key
    # doesn't fragment into multiple rows for the same contract.
    if instrument_type == "FUT":
        strike = 0.0

    if instrument_type in ("CE", "PE") and not (expiry and strike > 0):
        raise OrderError("Options need expiry (YYYY-MM-DD) and strike")
    if instrument_type == "FUT" and not expiry:
        # Default futures to the near-month contract.
        expiries = md.expiries(1)
        if not expiries:
            raise OrderError("No expiries available")
        expiry = expiries[0]

    # --- lot validation (F&O only) ------------------------------------------
    lot = int(inst["lot_size"])
    if quantity is None or float(quantity) <= 0:
        raise OrderError("quantity must be positive")
    if float(quantity) % lot != 0:
        raise OrderError(
            f"{symbol} trades in lots of {lot}: quantity {quantity:g} is not a multiple"
        )

    quantity = float(quantity)

    # --- order-type-specific fields ------------------------------------------
    if order_type == "LIMIT" and not price:
        raise OrderError("LIMIT orders require a price")
    if order_type == "SL":
        if not trigger_price:
            raise OrderError("SL orders require a trigger_price")

    if order_type == "BRACKET":
        target = payload.get("target_price")
        stoploss = payload.get("stoploss_price")
        if not target or not stoploss:
            raise OrderError("BRACKET orders require target_price and stoploss_price")
        entry_ref = float(price) if (order_type == "BRACKET" and price) else \
            ltp(md, symbol, instrument_type, expiry, strike,
                option_side_of(instrument_type))
        if side == "BUY":
            if not (float(target) > entry_ref > float(stoploss)):
                raise OrderError(
                    f"For a long bracket: target ({target:g}) > entry (~{entry_ref:g}) > "
                    f"stoploss ({stoploss:g})"
                )
        else:
            if not (float(target) < entry_ref < float(stoploss)):
                raise OrderError(
                    f"For a short bracket: target ({target:g}) < entry (~{entry_ref:g}) < "
                    f"stoploss ({stoploss:g})"
                )

    if order_type == "SL" and not price:
        # SL-M style: fill at trigger when breached.
        price = trigger_price

    return {
        "symbol": symbol,
        "instrument_type": instrument_type,
        "expiry": expiry,
        "strike": strike,
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "price": float(price) if price else None,
        "trigger_price": float(trigger_price) if trigger_price else None,
        "target_price": float(payload["target_price"]) if payload.get("target_price") else None,
        "stoploss_price": float(payload["stoploss_price"]) if payload.get("stoploss_price") else None,
        "reduce_only": bool(payload.get("reduce_only", False)),
    }


# ---------------------------------------------------------------------------
# Margin / funds
# ---------------------------------------------------------------------------

def order_margin(md: MockMarketData, req: dict[str, Any]) -> float:
    """Margin/cash required to OPEN the described position."""
    qty = req["quantity"]
    mult = contract_multiplier(req["symbol"])
    if req["instrument_type"] == "FUT":
        px = req["price"] or ltp(md, req["symbol"], "FUT", req["expiry"])
        return px * qty * mult * margin_pct(req["symbol"])
    # Options
    if req["side"] == "BUY":
        px = req["price"] or ltp(md, req["symbol"], req["instrument_type"],
                                 req["expiry"], req["strike"])
        return px * qty
    # short option: % of spot notional
    if req["instrument_type"] in ("CE", "PE"):
        # Get first available expiry for the underlying future
        expiries = md.expiries(1, req["symbol"])
        if expiries:
            spot = ltp(md, req["symbol"], "FUT", expiries[0])
        else:
            spot = md.get_spot(req["symbol"])
    else:
        spot = 0
    return spot * qty * mult * SHORT_OPTION_MARGIN_PCT


def margin_held(conn, md: MockMarketData) -> float:
    """Margin currently locked by open F&O positions (long options: 0 —
    their premium already left the cash balance)."""
    total = 0.0
    for p in db.get_all_positions(conn):
        mult = contract_multiplier(p["symbol"])
        if p["instrument_type"] == "FUT":
            px = ltp(md, p["symbol"], "FUT", p["expiry"])
            total += px * p["quantity"] * mult * margin_pct(p["symbol"])
        elif p["instrument_type"] in ("CE", "PE") and p["side"] == "SHORT":
            expiries = md.expiries(1, p["symbol"])
            if expiries:
                spot = ltp(md, p["symbol"], "FUT", expiries[0])
            else:
                spot = md.get_spot(p["symbol"])
            total += spot * p["quantity"] * mult * SHORT_OPTION_MARGIN_PCT
    return total


def ensure_funds(conn, md: MockMarketData, req: dict[str, Any]) -> None:
    account = db.get_account(conn)
    required = order_margin(md, req)
    available = account["cash"] - margin_held(conn, md)
    if required > available + 1e-6:
        raise OrderError(
            f"Insufficient margin: need ₹{required:,.2f}, available ₹{max(available, 0):,.2f}"
        )


# ---------------------------------------------------------------------------
# Position application
# ---------------------------------------------------------------------------

def _open_position(conn, md: MockMarketData, req: dict[str, Any], fill_price: float) -> None:
    """Open or add to a position (BUY→LONG row, SELL→SHORT row)."""
    side = "LONG" if req["side"] == "BUY" else "SHORT"
    existing = db.get_position(
        conn, req["symbol"], req["instrument_type"], req["expiry"],
        req["strike"], side,
    )
    qty = req["quantity"]
    if existing:
        total_qty = existing["quantity"] + qty
        avg = (existing["avg_price"] * existing["quantity"] + fill_price * qty) / total_qty
        db.upsert_position(conn, req["symbol"], req["instrument_type"], req["expiry"],
                           req["strike"], side, total_qty, avg)
    else:
        db.upsert_position(conn, req["symbol"], req["instrument_type"], req["expiry"],
                           req["strike"], side, qty, fill_price)


def _close_position(conn, md: MockMarketData, req: dict[str, Any],
                    fill_price: float) -> float:
    """Reduce/close the opposing position. Returns realized P&L.

    Raises OrderError if there is nothing (enough) to close.
    """
    close_side = "LONG" if req["side"] == "SELL" else "SHORT"
    mult = contract_multiplier(req["symbol"])
    pos = db.get_position(conn, req["symbol"], req["instrument_type"],
                          req["expiry"], req["strike"], close_side)
    qty = req["quantity"]

    if pos is None:
        if req.get("reduce_only"):
            raise OrderError(
                f"Nothing to close — no {close_side} position in "
                f"{req['symbol']} {req['instrument_type']} "
                f"{req.get('expiry', '')} {req.get('strike', '')}".strip())
        # Opening a short F&O position.
        _open_position(conn, md, req, fill_price)
        return 0.0

    if pos["quantity"] + 1e-9 < qty:
        if req.get("reduce_only"):
            # Close button: clamp to what is actually held instead of
            # erroring — the position may have shrunk since the page loaded.
            qty = pos["quantity"]
            req = {**req, "quantity": qty}
        else:
            raise OrderError(
                f"Trying to close {qty:g} but holding {pos['quantity']:g} "
                f"{req['symbol']} {close_side}"
            )

    if close_side == "LONG":
        realized = (fill_price - pos["avg_price"]) * qty * mult
    else:
        realized = (pos["avg_price"] - fill_price) * qty * mult

    remaining = pos["quantity"] - qty
    if remaining <= 1e-9:
        db.delete_position(conn, pos["id"])
    else:
        db.upsert_position(conn, req["symbol"], req["instrument_type"],
                           req["expiry"], req["strike"], close_side,
                           remaining, pos["avg_price"])
        if realized:
            db.update_position_realized(conn, pos["id"], realized)

    # Cash settlement: instruments paid up front (long options) credit
    # full proceeds; margin instruments (FUT, short options) credit the
    # realized P&L only — their margin releases via the shrinking position.
    account = db.get_account(conn)
    if req["instrument_type"] in ("CE", "PE") and close_side == "LONG":
        credit = fill_price * qty * mult
    else:
        credit = realized
    db.update_account_cash(conn, account["cash"] + credit)
    return realized


def apply_fill(conn, md: MockMarketData, req: dict[str, Any],
               fill_price: float, leg_role: str = "ENTRY",
               parent_id: int | None = None,
               order_row_id: int | None = None) -> int:
    """Record the order row and apply cash/position effects. Returns order id.

    ``order_row_id`` fills an existing parked row (e.g. a triggered SL order)
    in place instead of inserting a duplicate.
    """
    side = req["side"]
    is_exit = False
    close_side = "LONG" if side == "SELL" else "SHORT"
    is_exit = db.get_position(conn, req["symbol"], req["instrument_type"],
                              req["expiry"], req["strike"], close_side) is not None

    # Margin/funds gate only for genuine opens — exits release, not consume.
    if leg_role == "ENTRY" and not is_exit:
        if req.get("reduce_only"):
            # Close-button semantics: nothing on the opposing side to exit,
            # so this order would OPEN a fresh position. Refuse — never flip.
            close_side_name = "LONG" if side == "SELL" else "SHORT"
            raise OrderError(
                f"Nothing to close — no {close_side_name} position in "
                f"{req['symbol']} {req['instrument_type']} "
                f"{req.get('expiry', '')} {req.get('strike', '')}".strip())
        ensure_funds(conn, md, req)

    def _record() -> int:
        if order_row_id is not None:
            db.set_order_status(conn, order_row_id, "FILLED", fill_price)
            return order_row_id
        new_id = db.record_order(
            conn, req["symbol"], req["instrument_type"], side, req["quantity"],
            req["order_type"], expiry=req["expiry"], strike=req["strike"],
            price=req["price"], trigger_price=req["trigger_price"],
            status="FILLED", leg_role=leg_role, parent_id=parent_id,
        )
        db.set_order_status(conn, new_id, "FILLED", fill_price)
        return new_id

    if is_exit:
        order_id = _record()
        _close_position(conn, md, req, fill_price)
    else:
        # Cash effects: long-option buys pay up front; margin instruments
        # (FUT, short opts) leave cash alone and hold margin implicitly.
        if req["instrument_type"] in ("CE", "PE") and side == "BUY":
            account = db.get_account(conn)
            premium = fill_price * req["quantity"]
            if premium > account["cash"] - margin_held(conn, md) + 1e-6:
                raise OrderError(
                    f"Insufficient funds for premium: need ₹{premium:,.2f}"
                )
            db.update_account_cash(conn, account["cash"] - premium)

        _open_position(conn, md, req, fill_price)
        order_id = _record()

    return order_id


# ---------------------------------------------------------------------------
# Bracket orders
# ---------------------------------------------------------------------------

def place_bracket(conn, md: MockMarketData, req: dict[str, Any]) -> dict[str, Any]:
    """Create parent + entry + target + stoploss legs.

    MARKET entries fill immediately; LIMIT entries wait for a touch. Exit
    legs go live as soon as the entry fills (immediately for MARKET).
    """
    parent_id = db.record_order(
        conn, req["symbol"], req["instrument_type"], req["side"], req["quantity"],
        "BRACKET", expiry=req["expiry"], strike=req["strike"],
        price=req["price"], trigger_price=req["trigger_price"],
        status="OPEN", leg_role="ENTRY",
    )

    exit_side = "SELL" if req["side"] == "BUY" else "BUY"
    entry_leg_id: int | None = None

    if req["order_type"] == "BRACKET" and req["price"]:
        # LIMIT entry — record as a working leg; processed by the trigger loop.
        entry_leg_id = db.record_order(
            conn, req["symbol"], req["instrument_type"], req["side"], req["quantity"],
            "LIMIT", expiry=req["expiry"], strike=req["strike"], price=req["price"],
            status="OPEN", leg_role="ENTRY", parent_id=parent_id,
        )
        legs = {"entry": entry_leg_id, "target": None, "stoploss": None}
        return {"parent_id": parent_id, **legs, "status": "WORKING"}

    # MARKET entry: fill now.
    fill_price = ltp(md, req["symbol"], req["instrument_type"],
                     req["expiry"], req["strike"])
    req_market = {**req, "order_type": "MARKET", "price": None,
                  "trigger_price": None}
    entry_leg_id = apply_fill(conn, md, req_market, fill_price,
                              leg_role="ENTRY", parent_id=parent_id)
    db.set_order_status(conn, parent_id, "FILLED", fill_price)

    target_id = db.record_order(
        conn, req["symbol"], req["instrument_type"], exit_side, req["quantity"],
        "LIMIT", expiry=req["expiry"], strike=req["strike"],
        price=req["target_price"], status="OPEN", leg_role="TARGET",
        parent_id=parent_id,
    )
    sl_id = db.record_order(
        conn, req["symbol"], req["instrument_type"], exit_side, req["quantity"],
        "SL", expiry=req["expiry"], strike=req["strike"],
        price=req["stoploss_price"], trigger_price=req["stoploss_price"],
        status="OPEN", leg_role="STOPLOSS", parent_id=parent_id,
    )
    return {
        "parent_id": parent_id,
        "entry": entry_leg_id,
        "target": target_id,
        "stoploss": sl_id,
        "status": "FILLED",
    }


def cancel_order(conn, order_id: int) -> dict[str, Any]:
    """Cancel an order and every working member of its bracket family.

    Works from any family member: the parent, an entry leg, or an exit leg.
    A filled parent with live legs cancels the legs; a fully-filled family
    raises OrderError (nothing left to cancel).
    """
    order = db.get_order(conn, order_id)
    if order is None:
        raise NotFoundError(f"No order {order_id}")

    root_id = order["parent_id"] or order_id
    root = db.get_order(conn, root_id)

    family: list[dict[str, Any]] = []
    if root is not None:
        family.append(root)
        family.extend(db.get_child_orders(conn, root_id))
    else:
        family.append(order)

    working = [o for o in family if o["status"] in ("OPEN", "PENDING")]
    if not working:
        raise OrderError(f"Order {order_id} is {order['status']}, cannot cancel")

    cancelled = []
    for o in working:
        db.set_order_status(conn, o["id"], "CANCELLED")
        cancelled.append(o["id"])
    return {"cancelled": cancelled}


# ---------------------------------------------------------------------------
# Pending-order processing (SL triggers, bracket legs, LIMIT entries)
# ---------------------------------------------------------------------------

def _leg_trigger_hit(md: MockMarketData, order: dict[str, Any]) -> float | None:
    """Return the fill price if this working order should trigger, else None.

    Working orders: LIMIT entries/targets fill on a favourable touch;
    SL legs and standalone SL orders fill when the trigger is breached.
    """
    try:
        price = ltp(md, order["symbol"], order["instrument_type"],
                    order["expiry"], order["strike"])
    except (KeyError, ValueError):
        return None

    side, otype = order["side"], order["order_type"]
    if otype == "LIMIT":
        if side == "BUY" and price <= (order["price"] or 0):
            return min(price, order["price"])
        if side == "SELL" and price >= (order["price"] or 0):
            return max(price, order["price"])
        return None
    if otype == "SL":
        trig = order["trigger_price"] or order["price"]
        if side == "BUY" and price >= trig:
            return trig
        if side == "SELL" and price <= trig:
            return trig
    return None


def process_pending_orders(conn, md: MockMarketData) -> list[dict[str, Any]]:
    """Check all working orders; fill triggers, cascade bracket closes.

    Returns a list of fill events for logging/UX.
    """
    events: list[dict[str, Any]] = []
    for order in db.get_working_orders(conn):
        fill = _leg_trigger_hit(md, order)
        if fill is None:
            continue

        parent_id = order["parent_id"]
        if order["leg_role"] == "ENTRY" and parent_id is None:
            # Standalone SL order — fill the parked row in place.
            req = _req_from_order(order)
            try:
                apply_fill(conn, md, req, fill, leg_role="ENTRY",
                           order_row_id=order["id"])
                events.append({"order_id": order["id"], "filled_at": fill,
                               "role": "SL"})
            except OrderError:
                db.set_order_status(conn, order["id"], "REJECTED")
                events.append({"order_id": order["id"], "rejected": True})
            continue

        if order["leg_role"] == "ENTRY":
            # LIMIT bracket entry filled → activate exit legs.
            db.set_order_status(conn, order["id"], "FILLED", fill)
            req = _req_from_order(order)
            try:
                ensure_funds(conn, md, req)
                _open_position(conn, md, req, fill)
                _pay_entry_cost(conn, req, fill)
            except OrderError:
                _cancel_bracket(conn, parent_id)
                events.append({"order_id": order["id"], "rejected": True})
                continue
            db.set_order_status(conn, parent_id, "FILLED", fill)
            events.append({"order_id": order["id"], "filled_at": fill,
                           "role": "ENTRY"})
            continue

        # Exit leg (TARGET or STOPLOSS) — close the position, cancel siblings.
        req = _req_from_order(order)
        try:
            realized = _close_position(conn, md, req, fill)
        except OrderError:
            # Position already closed by the sibling leg moments earlier.
            db.set_order_status(conn, order["id"], "CANCELLED")
            continue
        db.set_order_status(conn, order["id"], "FILLED", fill)
        _cancel_bracket(conn, parent_id, except_order=order["id"])
        events.append({"order_id": order["id"], "filled_at": fill,
                       "role": order["leg_role"], "realized_pnl": realized})
    return events


def _req_from_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": order["symbol"],
        "instrument_type": order["instrument_type"],
        "expiry": order["expiry"],
        "strike": order["strike"],
        "side": order["side"],
        "quantity": order["quantity"],
        "order_type": order["order_type"],
        "price": order["price"],
        "trigger_price": order["trigger_price"],
    }


def _pay_entry_cost(conn, req: dict[str, Any], fill: float) -> None:
    """Cash effects for a LIMIT entry that just filled (mirrors apply_fill)."""
    if req["instrument_type"] in ("CE", "PE") and req["side"] == "BUY":
        account = db.get_account(conn)
        db.update_account_cash(conn, account["cash"] - fill * req["quantity"])


def _cancel_bracket(conn, parent_id: int | None, except_order: int | None = None) -> None:
    """Cancel remaining working legs of a bracket (after one leg filled)."""
    if parent_id is None:
        return
    for child in db.get_child_orders(conn, parent_id):
        if child["id"] != except_order and child["status"] == "OPEN":
            db.set_order_status(conn, child["id"], "CANCELLED")


# ---------------------------------------------------------------------------
# Greeks aggregation
# ---------------------------------------------------------------------------

def portfolio_greeks(conn, md: MockMarketData) -> dict[str, float]:
    """Net portfolio Greeks: delta/gamma/vega/theta summed over positions.

    - FUT: delta = quantity × multiplier (1.0 per unit), other Greeks 0.
    - Options: quote Greeks × quantity, sign-flipped for SHORT.
    """
    totals = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for p in db.get_all_positions(conn):
        mult = contract_multiplier(p["symbol"])
        sign = 1.0 if p["side"] == "LONG" else -1.0
        if p["instrument_type"] == "FUT":
            totals["delta"] += sign * p["quantity"] * mult
            continue
        if p["instrument_type"] in ("CE", "PE"):
            q = md.get_option_quote(p["symbol"], p["expiry"], p["strike"],
                                    p["instrument_type"])
            qty = p["quantity"] * mult
            totals["delta"] += sign * q["delta"] * qty
            totals["gamma"] += sign * q["gamma"] * qty
            totals["vega"] += sign * q["vega"] * qty
            totals["theta"] += sign * q["theta"] * qty
    return {k: round(v, 4) for k, v in totals.items()}
