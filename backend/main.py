"""Paper-Trader — Stage 4 backend.

JSON API (health, orders, positions, account, watchlist, instruments,
expiries, option-chain, market-status) + static frontend. SQLite
persistence, provider-based market data (nselib live → mock fallback),
and a bracket/stop-loss capable trading engine.
"""

import logging
import threading
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend import db
from backend import macro as macro_mod
from backend import overall_verdict as overall_verdict_mod
from backend import trading_engine as engine
from backend import verdict as verdict_mod
from backend.config import ANGEL
from backend.market_data import BASE_EQUITY_SPOT, INDEX_SPOT
from backend.market_data_manager import MarketManager, default_manager, warm_market_data
from backend.market_data_source import ProviderError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialise the database schema and warm market-data caches."""
    db.init_db()
    if ANGEL.complete:
        logging.getLogger("papertrader").info(
            "Angel One credentials loaded from .env (client %s)", ANGEL.client_code)
    else:
        logging.getLogger("papertrader").info(
            "Angel One credentials incomplete; missing: %s", ANGEL.missing())
    # Warm the live provider caches in a worker thread so the first page
    # load doesn't pay the multi-second NSE round-trips itself. Also opens
    # the Angel One session when configured, so failover is instant.
    import threading

    def _warm() -> None:
        warm_market_data(market_data)
        for provider in market_data.providers:
            if provider.name == "angel":
                try:
                    provider._ensure_session()
                    logging.getLogger("papertrader").info(
                        "Angel One session established (failover ready)")
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger("papertrader").warning(
                        "Angel One session warm-up failed: %s", exc)

    threading.Thread(target=_warm, daemon=True, name="md-warmup").start()

    def _spot_refresher() -> None:
        """Refresh every instrument's spot price every 30 seconds.

        Keeps the per-key quote cache warm so /api/positions, /api/account
        and the watchlist read a cached spot instead of paying live provider
        round-trips (yfinance alone is ~5s/call) on the request path.
        """
        while True:
            try:
                with db.db() as conn:
                    symbols = [i["symbol"] for i in db.get_instruments(conn)]
            except Exception:  # noqa: BLE001 — DB hiccup must not kill the loop
                symbols = []
            for symbol in symbols:
                try:
                    market_data.get_spot(symbol)
                except Exception:  # noqa: BLE001 — provider down/cooling: skip
                    pass
            time.sleep(60)

    threading.Thread(target=_spot_refresher, daemon=True,
                     name="md-spot-refresher").start()
    # Stage 16: background refresher for the macro snapshot (free sources,
    # cached ~5 min) so the Macro page never pays the fetch on first paint.
    macro_mod.start_macro_refresher()
    yield


app = FastAPI(title="Paper-Trader", version="0.4.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


@app.middleware("http")
async def cache_static(request: Request, call_next):
    """Long cache for hashed CSS/JS assets; never cache API responses."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith(("/css/", "/js/", "/index.html", "/")) and not path.startswith("/api"):
        # Always revalidate (ETag 304s keep this cheap) — stale JS breaks
        # the app badly since there's no build-hash fingerprinting.
        response.headers["Cache-Control"] = "no-cache"
    return response

# Production provider stack: nselib (when installed) → mock. Tests swap
# this for a mock-only manager via conftest.
market_data = default_manager()


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT", "SL", "BRACKET"] = "MARKET"
    instrument_type: Literal["EQ", "FUT", "CE", "PE"] = "EQ"
    expiry: str = ""
    strike: float = Field(default=0, ge=0)
    price: Optional[float] = Field(default=None, gt=0)
    trigger_price: Optional[float] = Field(default=None, gt=0)
    target_price: Optional[float] = Field(default=None, gt=0)
    stoploss_price: Optional[float] = Field(default=None, gt=0)
    # Close-button semantics: reject (instead of silently opening a new
    # position) when there is nothing on the opposing side to exit.
    reduce_only: bool = False


class WatchlistRequest(BaseModel):
    symbol: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

# Serializes every mutating trading operation (place/cancel/clear). Without
# it, concurrent orders each read the same position row and the last write
# wins — N rapid clicks produced N fills but only 2 lots of quantity.
_TRADING_LOCK = threading.Lock()


class OrderPreviewRequest(OrderRequest):
    """Same shape as an order; used only for the ticket's summary math."""


@app.post("/api/order-preview")
def order_preview(order: OrderPreviewRequest) -> dict:
    """Read-only: margin/available-after math for the order ticket summary.

    Uses the exact same engine.margin_held()/order_margin() the placement
    path uses, so the preview can never disagree with what placement will
    actually charge. No state is modified.
    """
    payload = order.model_dump()
    try:
        with db.db() as conn:
            req = engine.validate_request(conn, market_data, payload)
            account = db.get_account(conn)
            used_margin = engine.margin_held(conn, market_data)
            margin_required = 0.0
            if req.get("order_type") == "BRACKET":
                # Entry leg margin (approximation consistent with the engine).
                margin_required = engine.order_margin(market_data, req)
            else:
                close_side = "LONG" if req["side"] == "SELL" else "SHORT"
                is_exit = db.get_position(
                    conn, req["symbol"], req["instrument_type"], req["expiry"],
                    req["strike"], close_side) is not None
                if not is_exit:
                    margin_required = engine.order_margin(market_data, req)
        est_value = (req["price"] or 0) * req["quantity"]
        return {
            "margin_required": round(margin_required, 2),
            "available_funds": round(account["cash"] - used_margin, 2),
            "used_margin": round(used_margin, 2),
            "available_after": round(
                account["cash"] - used_margin - margin_required, 2),
            "est_value": round(est_value, 2),
        }
    except engine.OrderError as exc:
        # Validation problems (unknown symbol, bad lots, …) surface as a
        # message the ticket can show without blocking editing.
        return {
            "margin_required": 0.0,
            "available_funds": 0.0,
            "used_margin": 0.0,
            "available_after": 0.0,
            "est_value": 0.0,
            "error": str(exc),
        }
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {exc}")

@app.post("/api/orders")
def place_order(order: OrderRequest) -> dict:
    """Validate and place an EQ/FUT/OPT order (MARKET/LIMIT/SL/BRACKET)."""
    if order.order_type == "LIMIT" and not order.price:
        raise HTTPException(status_code=422, detail="LIMIT orders require a price")

    payload = order.model_dump()
    with _TRADING_LOCK, db.db() as conn:
        # Settle any triggered stop-losses / bracket legs first.
        engine.process_pending_orders(conn, market_data)
        try:
            req = engine.validate_request(conn, market_data, payload)

            if req["order_type"] == "BRACKET":
                result = engine.place_bracket(conn, market_data, req)
                return {
                    "status": result["status"],
                    "parent_id": result["parent_id"],
                    "entry_order_id": result.get("entry"),
                    "target_order_id": result.get("target"),
                    "stoploss_order_id": result.get("stoploss"),
                    "symbol": req["symbol"],
                    "instrument_type": req["instrument_type"],
                    "quantity": req["quantity"],
                }

            if req["order_type"] == "SL":
                # Standalone SL: market-style when already breached, else parked.
                fill = engine.ltp(market_data, req["symbol"], req["instrument_type"],
                                  req["expiry"], req["strike"])
                breached = (req["side"] == "BUY" and req["trigger_price"] is not None
                            and fill >= req["trigger_price"]) or \
                           (req["side"] == "SELL" and req["trigger_price"] is not None
                            and fill <= req["trigger_price"])
                if breached:
                    order_id = engine.apply_fill(
                        conn, market_data, req, req["trigger_price"]
                    )
                    return {
                        "order_id": order_id, "status": "FILLED",
                        "symbol": req["symbol"],
                        "instrument_type": req["instrument_type"],
                        "side": req["side"], "quantity": req["quantity"],
                        "fill_price": req["trigger_price"],
                    }
                order_id = db.record_order(
                    conn, req["symbol"], req["instrument_type"], req["side"],
                    req["quantity"], "SL", expiry=req["expiry"],
                    strike=req["strike"], price=req["price"],
                    trigger_price=req["trigger_price"], status="OPEN",
                )
                return {
                    "order_id": order_id, "status": "OPEN",
                    "symbol": req["symbol"],
                    "instrument_type": req["instrument_type"],
                    "side": req["side"], "quantity": req["quantity"],
                    "trigger_price": req["trigger_price"],
                }

            # MARKET / immediate-fill LIMIT.
            fill = req["price"] or engine.ltp(
                market_data, req["symbol"], req["instrument_type"],
                req["expiry"], req["strike"],
            )
            order_id = engine.apply_fill(conn, market_data, req, fill)
        except engine.OrderError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except engine.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ProviderError as exc:
            # No provider could price this instrument right now (all NSE
            # sources down/cooling). A clear 503 beats an opaque 500.
            raise HTTPException(
                status_code=503,
                detail=(f"Market data unavailable — cannot price {order.symbol} "
                        f"{order.instrument_type}. Try again shortly. ({exc})"))

    return {
        "order_id": order_id,
        "symbol": req["symbol"],
        "instrument_type": req["instrument_type"],
        "expiry": req["expiry"],
        "strike": req["strike"],
        "side": req["side"],
        "quantity": req["quantity"],
        "order_type": req["order_type"],
        "fill_price": fill,
        "status": "FILLED",
    }


@app.get("/api/orders")
def list_orders(limit: int = 100) -> dict:
    with db.db() as conn:
        engine.process_pending_orders(conn, market_data)
        orders = db.get_recent_orders(conn, limit=min(max(limit, 1), 500))
    # Filter out EQ orders (equity trading removed)
    orders = [o for o in orders if o.get("instrument_type") != "EQ"]
    return {"orders": orders}


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: int) -> dict:
    with _TRADING_LOCK, db.db() as conn:
        try:
            result = engine.cancel_order(conn, order_id)
        except engine.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except engine.OrderError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return result


@app.delete("/api/orders")
def clear_all_orders() -> dict:
    """Clear all orders from the database. Use with caution."""
    with _TRADING_LOCK, db.db() as conn:
        conn.execute("DELETE FROM orders")
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Positions / account
# ---------------------------------------------------------------------------

@app.get("/api/positions")
def list_positions() -> dict:
    with db.db() as conn:
        engine.process_pending_orders(conn, market_data)
        positions = db.get_all_positions(conn)
        try:
            greeks_total = engine.portfolio_greeks(conn, market_data)
        except ProviderError:
            # All providers down/cooling — show zero greeks rather than a 500.
            greeks_total = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}

    # Filter out EQ positions (equity trading removed)
    positions = [p for p in positions if p["instrument_type"] != "EQ"]

    enriched = []
    total_unrealized = 0.0
    for p in positions:
        itype = p["instrument_type"]
        mult = engine.contract_multiplier(p["symbol"])
        sign = 1.0 if p["side"] == "LONG" else -1.0

        current = None
        entry = p["avg_price"]
        greeks: dict = {}
        marking = {"source": "none", "status": "unavailable"}

        try:
            if itype == "FUT":
                current = market_data.get_future_price(p["symbol"], p["expiry"])
                marking = market_data.marking_meta(p["symbol"], "FUT")
            elif itype in ("CE", "PE"):
                q = market_data.get_option_quote(p["symbol"], p["expiry"],
                                                 p["strike"], itype)
                current, entry = q["ltp"], p["avg_price"]
                greeks = {"delta": q["delta"], "gamma": q["gamma"],
                          "vega": q["vega"], "theta": q["theta"], "iv": q["iv"]}
                marking = {"source": q.get("source", "mock"),
                           "status": q.get("status", "simulated")}
            else:
                current = market_data.get_spot(p["symbol"])
                marking = market_data.marking_meta(p["symbol"], itype)
        except ProviderError:
            pass

        if current is not None:
            if sign > 0:
                unrealized = (current - entry) * p["quantity"] * mult
            else:
                unrealized = (entry - current) * p["quantity"] * mult
            total_unrealized += unrealized
        else:
            unrealized = None

        enriched.append({
            **p,
            "current_price": current,
            "market_value": round(sign * current * p["quantity"] * mult, 2) if current is not None else None,
            "unrealized_pnl": round(unrealized, 2) if unrealized is not None else None,
            **greeks,
            **marking,
        })

    return {
        "positions": enriched,
        "total_unrealized_pnl": round(total_unrealized, 2),
        "greeks": greeks_total,
    }


@app.get("/api/account")
def get_account() -> dict:
    with db.db() as conn:
        engine.process_pending_orders(conn, market_data)
        account = db.get_account(conn)
        positions = db.get_all_positions(conn)
        try:
            used_margin = engine.margin_held(conn, market_data)
        except ProviderError:
            used_margin = 0.0
        try:
            greeks_total = engine.portfolio_greeks(conn, market_data)
        except ProviderError:
            greeks_total = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}

    prepaid_value = 0.0
    notional_value = 0.0
    margin_unrealized = 0.0
    prepaid_unrealized = 0.0
    leg_sources: set[str] = set()
    leg_statuses: set[str] = set()
    any_unpriced = False

    for p in positions:
        itype = p["instrument_type"]
        mult = engine.contract_multiplier(p["symbol"])
        sign = 1.0 if p["side"] == "LONG" else -1.0
        priced = True
        marking = {"source": "none", "status": "unavailable"}

        try:
            if itype == "FUT":
                current = market_data.get_future_price(p["symbol"], p["expiry"])
                marking = market_data.marking_meta(p["symbol"], "FUT")
                notional_value += sign * current * p["quantity"] * mult
                if sign > 0:
                    margin_unrealized += (current - p["avg_price"]) * p["quantity"] * mult
                else:
                    margin_unrealized += (p["avg_price"] - current) * p["quantity"] * mult
            elif itype in ("CE", "PE"):
                q = market_data.get_option_quote(p["symbol"], p["expiry"],
                                                 p["strike"], itype)
                marking = {"source": q.get("source", "mock"),
                           "status": q.get("status", "simulated")}
                if sign > 0:
                    prepaid_value += q["ltp"] * p["quantity"]
                    prepaid_unrealized += (q["ltp"] - p["avg_price"]) * p["quantity"]
                else:
                    notional_value -= q["ltp"] * p["quantity"]
                    margin_unrealized += (p["avg_price"] - q["ltp"]) * p["quantity"]
            else:
                current = market_data.get_spot(p["symbol"])
                marking = market_data.marking_meta(p["symbol"], itype)
                prepaid_value += current * p["quantity"]
                prepaid_unrealized += (current - p["avg_price"]) * p["quantity"]
        except ProviderError:
            priced = False
            any_unpriced = True

        if priced:
            leg_sources.add(marking["source"])
            leg_statuses.add(marking["status"])

    if any_unpriced:
        return {
            "starting_capital": account["starting_capital"],
            "cash": round(account["cash"], 2),
            "available_funds": round(account["cash"] - used_margin, 2),
            "used_margin": round(used_margin, 2),
            "portfolio_value": None,
            "unrealized_pnl": None,
            "equity": None,
            "total_pnl": None,
            "greeks": greeks_total,
            "data_source": {
                "source": "mixed" if leg_sources else "none",
                "status": "unavailable",
            },
        }

    unrealized_total = margin_unrealized + prepaid_unrealized
    equity = (account["cash"]
              + prepaid_value
              + margin_unrealized)

    if not leg_statuses:
        account_marking = {"source": "none", "status": "simulated"}
    elif leg_statuses == {"live"}:
        account_marking = {"source": "/".join(sorted(leg_sources)), "status": "live"}
    else:
        account_marking = {
            "source": "mixed" if len(leg_sources) > 1
            else (next(iter(leg_sources)) if leg_sources else "mock"),
            "status": "mixed" if len(leg_statuses) > 1 else next(iter(leg_statuses)),
        }

    return {
        "starting_capital": account["starting_capital"],
        "cash": round(account["cash"], 2),
        "available_funds": round(account["cash"] - used_margin, 2),
        "used_margin": round(used_margin, 2),
        "portfolio_value": round(prepaid_value + notional_value, 2),
        "unrealized_pnl": round(unrealized_total, 2),
        "equity": round(equity, 2),
        "total_pnl": round(equity - account["starting_capital"], 2),
        "greeks": greeks_total,
        "data_source": account_marking,
    }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

@app.get("/api/watchlist")
def get_watchlist() -> dict:
    with db.db() as conn:
        engine.process_pending_orders(conn, market_data)
        symbols = db.get_watchlist(conn)
        instruments = {i["symbol"]: i for i in db.get_instruments(conn)}
    quotes = []
    for symbol in symbols:
        inst = instruments.get(symbol)
        if inst is None:
            continue
        try:
            quote = market_data.get_quote(symbol)
            quotes.append({
                "symbol": symbol,
                "name": inst["name"],
                "kind": inst["kind"],
                "lot_size": inst["lot_size"],
                "ltp": quote.ltp,
                "change_pct": quote.change_pct,
                "source": quote.source,
                "status": quote.status,
            })
        except ProviderError:
            quotes.append({
                "symbol": symbol,
                "name": inst["name"],
                "kind": inst["kind"],
                "lot_size": inst["lot_size"],
                "ltp": None,
                "change_pct": None,
                "source": "none",
                "status": "unavailable",
            })
    return {"watchlist": quotes}


@app.post("/api/watchlist")
def add_watchlist(req: WatchlistRequest) -> dict:
    symbol = req.symbol.upper()
    with db.db() as conn:
        if db.get_instrument(conn, symbol) is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
        added = db.add_to_watchlist(conn, symbol)
        symbols = db.get_watchlist(conn)
    return {"added": added, "symbols": symbols}


@app.delete("/api/watchlist/{symbol}")
def remove_watchlist(symbol: str) -> dict:
    with db.db() as conn:
        removed = db.remove_from_watchlist(conn, symbol.upper())
        if not removed:
            raise HTTPException(status_code=404, detail=f"Not in watchlist: {symbol}")
        symbols = db.get_watchlist(conn)
    return {"removed": symbol.upper(), "symbols": symbols}


# ---------------------------------------------------------------------------
# Instruments / expiries / option chain
# ---------------------------------------------------------------------------

_INSTRUMENTS_CACHE_TTL = 30.0
_instruments_cache: dict[str, tuple[float, dict]] = {}


@app.get("/api/instrument/{symbol}")
def get_instrument(symbol: str) -> dict:
    """Lightweight instrument metadata (lot size, name, kind).

    Does not call market data providers — safe to use when the market-data
    stack is slow or temporarily unavailable.
    """
    symbol = symbol.upper()
    with db.db() as conn:
        inst = db.get_instrument(conn, symbol)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    return {
        "symbol": inst["symbol"],
        "name": inst["name"],
        "kind": inst["kind"],
        "lot_size": inst["lot_size"],
        "margin_pct": inst.get("margin_pct"),
    }


@app.get("/api/instruments")
def list_instruments(kind: Optional[Literal["index", "stock"]] = None) -> dict:
    """All tradable instruments with spots, cached as a whole for 30s.

    Building the response walks every instrument's spot; caching the final
    payload (keyed by the ``kind`` filter) turns repeat loads from ~9s of
    provider round-trips into ~0s.
    """
    cache_key = kind or ""
    now = time.monotonic()
    cached = _instruments_cache.get(cache_key)
    if cached is not None and now - cached[0] < _INSTRUMENTS_CACHE_TTL:
        return cached[1]

    with db.db() as conn:
        instruments = db.get_instruments(conn, kind)
    # Batch spot fetch: get_quotes() hits the provider once (cached),
    # instead of one provider round-trip per instrument.
    try:
        quotes = market_data.get_quotes()
    except ProviderError:
        quotes = {}
    out = []
    for i in instruments:
        spot = quotes.get(i["symbol"])
        if spot is None:
            try:
                spot = market_data.get_spot(i["symbol"])
            except ProviderError:
                spot = None
        base = INDEX_SPOT.get(i["symbol"]) or BASE_EQUITY_SPOT.get(i["symbol"])
        out.append({**i, "spot": spot,
                    "change_pct": round((spot - base) / base * 100, 2) if (spot and base) else None})
    payload = {"instruments": out}
    _instruments_cache[cache_key] = (time.monotonic(), payload)
    return payload


@app.get("/api/expiries/{symbol}")
def get_expiries(symbol: str) -> dict:
    symbol = symbol.upper()
    with db.db() as conn:
        if db.get_instrument(conn, symbol) is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    try:
        return market_data.expiries_full(symbol, count=6).to_dict()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {exc}")


@app.get("/api/option-chain/{symbol}")
def get_option_chain(symbol: str, expiry: Optional[str] = None,
                     strikes_per_side: Optional[int] = None) -> dict:
    symbol = symbol.upper()
    with db.db() as conn:
        if db.get_instrument(conn, symbol) is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    try:
        chain = market_data.option_chain_full(symbol, expiry, strikes_per_side)
        # Portfolio-level greeks for the chain footer (summed across visible rows).
        return chain.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {exc}")


@app.get("/api/chart/{symbol}")
def get_chart(symbol: str,
              interval: str = "1D",
              start: Optional[str] = None,
              end: Optional[str] = None) -> dict:
    """Normalized OHLCV candles for a symbol.

    Intervals: 1m/5m/15m/1h/1D. Omitted range defaults per interval.
    Unknown intervals → 422; unknown symbol → 404; when no provider covers
    candles the response is {status: "unavailable"} (never fake data with a
    live label).
    """
    from backend.market_data_source import CANDLE_INTERVALS
    from backend.providers.candles import default_range, validate_interval

    symbol = symbol.upper()
    try:
        interval = validate_interval(interval)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    with db.db() as conn:
        if db.get_instrument(conn, symbol) is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    start, end = start or None, end or None
    if not start or not end:
        d_start, d_end = default_range(interval)
        start = start or d_start
        end = end or d_end

    try:
        candles, source = market_data.get_candles(symbol, interval, start, end)
    except Exception as exc:
        return {
            "symbol": symbol, "interval": interval,
            "start": start, "end": end,
            "status": "unavailable", "source": None, "candles": [],
            "detail": f"Market data unavailable: {exc}",
        }

    if source == "mock":
        status = "simulated"
    elif source == "nselib" and interval != "1D":
        status = "derived"  # defensive: nselib serves daily only
    else:
        status = "live"
    return {
        "symbol": symbol, "interval": interval,
        "start": start, "end": end,
        "status": status, "source": source,
        "candles": candles,
    }


@app.get("/api/market-status")
def market_status() -> dict:
    """Provider stack diagnostics: order, health, cooldowns, last errors."""
    return {
        "providers": market_data.provider_status(),
        "healthy": market_data.health_check(),
        "primary": market_data.providers[0].name if market_data.providers else None,
        "broker": {"angel": ANGEL.status()},
    }


# ---------------------------------------------------------------------------
# Technical verdict (Stage 17) — descriptive reading of daily candles
# ---------------------------------------------------------------------------

_VERDICT_CACHE: dict[str, tuple[float, dict]] = {}
_VERDICT_TTL = 300.0  # daily closes — 5 min is plenty


@app.get("/api/verdict/{symbol}")
def get_verdict(symbol: str) -> dict:
    """Aggregated technical verdict + price-action stats for a symbol.

    Uses the same daily candle stack as the chart (providers unchanged).
    Descriptive only — the response carries an explicit disclaimer.
    """
    from backend.providers.candles import default_range
    import time as _time

    symbol = symbol.upper()
    with db.db() as conn:
        if db.get_instrument(conn, symbol) is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    cached = _VERDICT_CACHE.get(symbol)
    if cached and (_time.time() - cached[0]) < _VERDICT_TTL:
        return cached[1]

    start, end = default_range("1D")
    try:
        candles, source = market_data.get_candles(symbol, "1D", start, end)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbol": symbol, "status": "unavailable", "source": None,
            "detail": f"Market data unavailable: {exc}",
            "verdict": "unclear", "confidence": 0, "checks": [],
            "price_action": {},
            "disclaimer": verdict_mod.DISCLAIMER,
        }

    if not candles:
        return {
            "symbol": symbol, "status": "unavailable", "source": source,
            "detail": "No daily candles returned by any provider.",
            "verdict": "unclear", "confidence": 0, "checks": [],
            "price_action": {},
            "disclaimer": verdict_mod.DISCLAIMER,
        }

    result = verdict_mod.compute_verdict(candles)
    result.update({
        "symbol": symbol,
        "status": "live" if source != "mock" else "simulated",
        "source": source,
        "candles_used": len(candles),
        "one_liner": verdict_mod.one_liner(result),
    })
    _VERDICT_CACHE[symbol] = (_time.time(), result)
    return result


_OVERALL_CACHE: dict[str, tuple[float, dict]] = {}
_OVERALL_TTL = 300.0


@app.get("/api/overall-verdict/NIFTY")
def get_overall_verdict() -> dict:
    """One overall NIFTY 50 verdict combining the existing technical
    verdict (/api/verdict/NIFTY) with the existing macro factor tally
    (/api/macro). No new indicators, sources, or predictions — just the
    combination. Descriptive only.
    """
    import time as _time

    cached = _OVERALL_CACHE.get("NIFTY")
    if cached and (_time.time() - cached[0]) < _OVERALL_TTL:
        return cached[1]

    technical = get_verdict("NIFTY")
    macro_snapshot = macro_mod.get_macro_snapshot()
    result = overall_verdict_mod.combine(technical, macro_snapshot)
    result.update({
        "symbol": "NIFTY",
        "status": "live" if (technical.get("status") == "live"
                              and any(f.get("status") == "live"
                                      for f in macro_snapshot.get("factors", [])))
                    else "unavailable",
    })
    _OVERALL_CACHE["NIFTY"] = (_time.time(), result)
    return result


@app.get("/api/health")
def health() -> dict[str, str]:
    """Health check for the API."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Macro Factors (Stage 16) — descriptive free-data dashboard + journal
# ---------------------------------------------------------------------------


class JournalEntryRequest(BaseModel):
    trade_date: str = Field(min_length=8, max_length=10)
    expected: Literal["UP", "DOWN", "FLAT"]
    confidence: int = Field(ge=1, le=5)
    reasons: str = Field(default="", max_length=2000)


@app.get("/api/macro")
def macro_snapshot() -> dict:
    """Macro factors snapshot (cached ~5 min; every row carries source,
    timestamp and honest status — unavailable rows are never faked)."""
    return macro_mod.get_macro_snapshot()


@app.get("/api/macro/journal")
def macro_journal_get() -> dict:
    with db.db() as conn:
        try:
            macro_mod.resolve_open_journal_entries(conn)
        except Exception:  # noqa: BLE001 — resolution is best-effort
            pass
        entries = db.get_journal(conn)
        stats = db.journal_stats(conn)
    return {"entries": entries, "stats": stats,
            "note": "Outcome compares your stated expectation with the "
                    "actual next-session NIFTY 50 open. Descriptive only."}


@app.post("/api/macro/journal")
def macro_journal_post(entry: JournalEntryRequest) -> dict:
    try:
        datetime.strptime(entry.trade_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail="trade_date must be YYYY-MM-DD") from exc
    with _TRADING_LOCK, db.db() as conn:
        inserted = db.upsert_journal_entry(
            conn, entry.trade_date, entry.expected, entry.confidence,
            entry.reasons.strip())
    return {"ok": True, "inserted": inserted}


@app.delete("/api/macro/journal/{entry_id}")
def macro_journal_delete(entry_id: int) -> dict:
    with _TRADING_LOCK, db.db() as conn:
        if not db.delete_journal_entry(conn, entry_id):
            raise HTTPException(status_code=404, detail="Journal entry not found")
    return {"ok": True}


# Mounted last so /api/* routes take precedence over the static files.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
