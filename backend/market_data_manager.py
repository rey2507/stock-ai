"""Paper-Trader — market data manager (Stage 4).

Sits between routes/engine and providers:

- **Priority + fallback** — tries providers in order (live first, mock
  last); a provider that raises ``ProviderError``/``KeyError`` cools down
  for :data:`PROVIDER_COOLDOWN_SECONDS` before being retried.
- **TTL cache** — per-call cache keyed by (method, args); quotes/chains
  5s, expiries 1h. Also the practical NSE rate-limiter (~1 req/s).
- **Metadata** — helper methods return models carrying
  ``source``/``status``/``timestamp``; ``mock`` → ``"simulated"``,
  ``nselib`` spots/chains → ``"live"``, derived futures → ``"derived"``.
- **Engine-compatible** — duck-types the full ``MockMarketData`` surface
  the trading engine already uses, so ``trading_engine`` is untouched.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from backend.market_data_source import MarketDataSource, ProviderError, ProviderMiss
from backend.models import (
    ChainRow,
    ExpiryList,
    FutureQuote,
    OptionChain,
    OptionQuote,
)
from backend.nse_data import INDEX_SPOT

PROVIDER_COOLDOWN_SECONDS = 30.0
QUOTE_TTL_SECONDS = 5.0
EXPIRY_TTL_SECONDS = 3600.0
LIVE_CALL_TIMEOUT_SECONDS = 3.0
LIVE_CALL_TIMEOUT_HEAVY_SECONDS = 8.0   # nselib chain scrape needs 4-6s as fallback

# Heavy/bulky calls: Angel's batched API is faster and more reliable than
# the nselib HTML scrape, so those methods try Angel first. Cheap spot
# calls keep nselib first to protect Angel's ~1 rps quota.
ANGEL_FIRST_METHODS = {
    "option_chain", "get_option_quote", "expiries", "get_candles",
}
_HEAVY_METHODS = ANGEL_FIRST_METHODS
CANDLE_TTL_DAILY_SECONDS = 3600.0       # EOD histories are immutable
CANDLE_TTL_INTRADAY_SECONDS = 300.0     # today's bars still form

_PROVIDER_STATUS = {"mock": "simulated", "nselib": "live", "angel": "live"}

# Shared pool for live-provider calls so a hung NSE request can't block a
# route forever. Timed-out worker threads keep running in the background
# until their HTTP timeout fires — acceptable for a paper trader.
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout

_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="md-live")


class MarketManager:
    """Provider-selecting, caching facade over :class:`MarketDataSource`."""

    def __init__(
        self,
        providers: list[MarketDataSource] | None = None,
        quote_ttl: float = QUOTE_TTL_SECONDS,
        expiry_ttl: float = EXPIRY_TTL_SECONDS,
        candle_ttl_daily: float = CANDLE_TTL_DAILY_SECONDS,
        candle_ttl_intraday: float = CANDLE_TTL_INTRADAY_SECONDS,
        now_fn=time.monotonic,
    ) -> None:
        self.providers = providers or []
        self._quote_ttl = quote_ttl
        self._expiry_ttl = expiry_ttl
        self._candle_ttl_daily = candle_ttl_daily
        self._candle_ttl_intraday = candle_ttl_intraday
        self._now = now_fn
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._last_errors: dict[str, str] = {}
        # Single-flight: concurrent calls for the same key share one upstream
        # fetch instead of stacking duplicate NSE round-trips (the 3s UI poll
        # plus a user click used to fire the same slow scrape twice). Threads
        # waiting on an in-flight joiner must NOT occupy the shared pool, so
        # they block on an Event rather than a Future.
        self._inflight: dict[tuple, threading.Event] = {}
        self._inflight_result: dict[tuple, tuple[bool, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ provider
    @property
    def mock(self) -> Optional[Any]:
        """The wrapped mock engine, if a mock provider is in the stack."""
        from backend.providers.mock_provider import MockMarketDataProvider

        for p in self.providers:
            if isinstance(p, MockMarketDataProvider):
                return p.md
        return None

    def provider_status(self) -> list[dict[str, Any]]:
        """Runtime health snapshot for diagnostics."""
        out = []
        now = self._now()
        for p in self.providers:
            out.append({
                "name": p.name,
                "cooling_down": self._cooldown_until.get(p.name, 0) > now,
                "last_error": self._last_errors.get(p.name),
            })
        return out

    # -------------------------------------------------------------- plumbing
    def _fetch(self, method: str, *args, ttl: float, **kwargs) -> tuple[Any, str]:
        """Return (value, provider_name), using cache + fallback order.

        Concurrent callers with the same key coalesce into one upstream
        fetch (single-flight): the first thread does the work, the rest
        wait on an event and reuse its result — success or failure.
        """
        key = (method, args, tuple(sorted(kwargs.items())))
        now = self._now()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1], hit[2] if len(hit) > 2 else "cached"

        # Join an in-flight fetch for this key if one exists.
        with self._lock:
            event = self._inflight.get(key)
            if event is None:
                event = threading.Event()
                self._inflight[key] = event
                leader = True
            else:
                leader = False

        if not leader:
            # Wait for the leader (bounded by the live timeout + slack).
            event.wait(timeout=LIVE_CALL_TIMEOUT_SECONDS + 2.0)
            with self._lock:
                outcome = self._inflight_result.pop(key, None)
            if outcome is not None:
                ok, payload = outcome
                if ok:
                    return payload
                # Leader failed → re-raise the same error type/message.
                raise ProviderError(str(payload))
            # Leader died without publishing (shouldn't happen) — retry once
            # as leader ourselves.
            return self._fetch(method, *args, ttl=ttl, **kwargs)

        try:
            value, source = self._fetch_as_leader(method, key, now, *args, **kwargs)
            with self._lock:
                self._inflight_result[key] = (True, (value, source))
            return value, source
        except ProviderError as exc:
            with self._lock:
                self._inflight_result[key] = (False, str(exc))
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            event.set()

    def _providers_for(self, method: str) -> list[MarketDataSource]:
        """Provider order for a call: heavy chain/candle/expiry methods try
        Angel's batched API first; everything else keeps stack order."""
        if method in ANGEL_FIRST_METHODS:
            return sorted(self.providers,
                          key=lambda p: 0 if p.name == "angel" else 1)
        return self.providers

    def _fetch_as_leader(self, method: str, key: tuple, now: float,
                         *args, **kwargs) -> tuple[Any, str]:
        """Leader path: try providers in order, populate the cache."""
        errors: list[str] = []
        timeout = (LIVE_CALL_TIMEOUT_HEAVY_SECONDS if method in _HEAVY_METHODS
                   else LIVE_CALL_TIMEOUT_SECONDS)
        for provider in self._providers_for(method):
            if self._cooldown_until.get(provider.name, 0) > now:
                continue
            try:
                if provider.name == "mock":
                    value = getattr(provider, method)(*args, **kwargs)
                else:
                    future = _EXECUTOR.submit(
                        getattr(provider, method), *args, **kwargs)
                    value = future.result(timeout=timeout)
            except _FutureTimeout:
                error = f"{provider.name}: timed out after {timeout:g}s"
                self._cooldown_until[provider.name] = self._now() + PROVIDER_COOLDOWN_SECONDS
                self._last_errors[provider.name] = error
                errors.append(error)
                continue
            except ProviderMiss:
                # Per-symbol gap: fall through without punishing the provider.
                errors.append(f"{provider.name}: data not covered")
                continue
            except (ProviderError, KeyError) as exc:
                self._cooldown_until[provider.name] = self._now() + PROVIDER_COOLDOWN_SECONDS
                self._last_errors[provider.name] = str(exc)
                errors.append(f"{provider.name}: {exc}")
                continue
            self._cooldown_until.pop(provider.name, None)
            self._cache[key] = (self._now(), value, provider.name)
            return value, provider.name

        raise ProviderError("; ".join(errors) or "no providers configured")

    def _status_of(self, provider_name: str, method: str = "") -> str:
        if provider_name == "nselib" and method.startswith(("get_future", "future_")):
            return "derived"  # futures are carry-model, not scraped
        return _PROVIDER_STATUS.get(provider_name, "live")

    # ---------------------------------------------------------------- spots
    def get_spot(self, symbol: str) -> float:
        value, _ = self._fetch("get_spot", symbol, ttl=self._quote_ttl)
        return value

    def get_price(self, symbol: str) -> float:
        return self.get_spot(symbol)

    def get_quotes(self) -> dict[str, float]:
        value, _ = self._fetch("get_quotes", ttl=self._quote_ttl)
        return value

    def get_quote(self, symbol: str):
        from backend.models import Quote

        ltp, source = self._fetch("get_spot", symbol, ttl=self._quote_ttl)
        change = self._live_change_pct(symbol)
        if change is None:
            change = self._change_pct(symbol, ltp)
        return Quote(
            symbol=symbol,
            ltp=ltp,
            change_pct=change,
            source=source,
            status=self._status_of(source),
        )

    def _live_change_pct(self, symbol: str) -> float | None:
        """Ask live providers for a real percentChange (indices via nselib)."""
        for provider in self.providers:
            if self._cooldown_until.get(provider.name, 0) > self._now():
                continue
            getter = getattr(provider, "quote_change_pct", None)
            if getter is None:
                continue
            try:
                value = getter(symbol)
            except Exception:
                continue
            if value is not None:
                return value
        return None

    # -------------------------------------------------------------- futures
    def get_future_price(self, symbol: str, expiry: str) -> float:
        value, _ = self._fetch("get_future_price", symbol, expiry,
                               ttl=self._quote_ttl)
        return value

    def future_curve(self, symbol: str) -> list[dict]:
        value, _ = self._fetch("future_curve", symbol, ttl=self._quote_ttl)
        return value

    def expiries_full(self, symbol: str, count: int = 6) -> ExpiryList:
        expiries, source = self._fetch("expiries", count, ttl=self._expiry_ttl)
        spot = self.get_spot(symbol)
        curve = []
        for expiry in expiries:
            price, fut_source = self._fetch("get_future_price", symbol, expiry,
                                            ttl=self._quote_ttl)
            curve.append(FutureQuote(
                symbol=symbol, expiry=expiry, price=price,
                basis=round(price - spot, 2),
                source=fut_source,
                status=self._status_of(fut_source, "get_future_price"),
            ))
        return ExpiryList(
            symbol=symbol, expiries=expiries, futures=curve,
            source=source, status=self._status_of(source),
        )

    # -------------------------------------------------------------- options
    def get_option_quote(self, underlying: str, expiry: str, strike: float,
                         option_type: str) -> OptionQuote:
        value, source = self._fetch("get_option_quote", underlying, expiry,
                                    strike, option_type, ttl=self._quote_ttl)
        if source != "mock" and isinstance(value, OptionQuote):
            value.source, value.status = source, self._status_of(source)
        return value

    def option_chain(self, underlying: str, expiry: str,
                     strike_step: int | None = None,
                     strikes_per_side: int = 10) -> list[ChainRow]:
        value, _ = self._fetch("option_chain", underlying, expiry,
                               ttl=self._quote_ttl,
                               strike_step=strike_step,
                               strikes_per_side=strikes_per_side)
        return value

    def option_chain_full(self, underlying: str, expiry: str | None = None,
                          strikes_per_side: int | None = None) -> OptionChain:
        if strikes_per_side is None:
            strikes_per_side = 10
        expiries = self.expiries(6)
        chosen = expiry or (expiries[0] if expiries else "")
        if chosen not in expiries:
            raise ProviderError(f"Invalid expiry: {chosen}")

        rows, source = self._fetch("option_chain", underlying, chosen,
                                   ttl=self._quote_ttl,
                                   strikes_per_side=strikes_per_side)
        spot = self.get_spot(underlying)
        atm = min((r.strike for r in rows), key=lambda s: abs(s - spot)) if rows else None
        return OptionChain(
            underlying=underlying, expiry=chosen, spot=spot, atm_strike=atm,
            rows=rows, expiries=expiries,
            source=source, status=self._status_of(source),
        )

    # ------------------------------------------------------------- expiries
    def expiries(self, count: int = 6, symbol: str | None = None) -> list[str]:
        value, _ = self._fetch("expiries", count, ttl=self._expiry_ttl, symbol=symbol)
        return value

    # -------------------------------------------------------------- candles
    def get_candles(self, symbol: str, interval: str,
                    start: str, end: str) -> tuple[list[dict], str]:
        """OHLCV candles via the provider stack; returns (candles, source).

        TTL: 1h for daily (histories are immutable EOD data), 5m for
        intraday (today's bars still form). Falls through providers in
        priority order; mock always serves deterministic simulated data,
        so only an empty provider stack or unknown symbol raises.
        """
        ttl = self._candle_ttl_daily if interval == "1D" \
            else self._candle_ttl_intraday
        return self._fetch("get_candles", symbol, interval, start, end, ttl=ttl)

    # ---------------------------------------------------------------- theta
    def advance_theta_clock(self) -> bool:
        """Advance the mock theta clock (no-op when live providers serve)."""
        md = self.mock
        return bool(md and md.advance_theta_clock())

    # -------------------------------------------------------------- marking
    def marking_meta(self, symbol: str, instrument_type: str) -> dict[str, str]:
        """Data-source metadata for marking a position (EQ/FUT; option
        quotes already carry their own source/status)."""
        try:
            _, source = self._fetch("get_spot", symbol, ttl=self._quote_ttl)
        except ProviderError:
            source = "mock"
        if instrument_type == "FUT" and source == "nselib":
            return {"source": source, "status": "derived"}  # carry model
        return {"source": source, "status": self._status_of(source)}

    # --------------------------------------------------------------- health
    def health_check(self) -> bool:
        return any(p.health_check() for p in self.providers)

    # -------------------------------------------------------------- helpers
    def _change_pct(self, symbol: str, ltp: float) -> float:
        """Change vs our reference base (mock spot base / index spot)."""
        from backend.market_data import BASE_EQUITY_SPOT

        base = INDEX_SPOT.get(symbol) or BASE_EQUITY_SPOT.get(symbol)
        if not base:
            return 0.0
        return round((ltp - base) / base * 100, 2)


# Alias so existing imports/tests keep working: ``market_data.get_spot(...)``.
class _ManagerAsSource(MarketManager):
    """Compat shim: exposes the legacy ``get_option_quote`` dict contract.

    The trading engine reads quotes with ``quote["ltp"]``; OptionQuote
    models support ``__getitem__``, so the manager passes models straight
    through. No overrides needed — documented for clarity.
    """


def default_manager() -> MarketManager:
    """Production stack: nselib → angel → yfinance. NO mock.

    Real data or nothing: instruments a live provider can't price show as
    UNAVAILABLE rather than simulated values. Mock remains available for
    tests (conftest injects it explicitly). Angel One SmartAPI sits between
    the scrapers and the fallback: when nselib fails/cools down, broker
    data takes over (requires ANGEL_* credentials in .env; skipped cleanly
    when absent or SmartApi isn't installed).
    """
    providers: list[MarketDataSource] = []
    try:
        from backend.providers.nselib_provider import NselibProvider

        providers.append(NselibProvider())
    except ProviderError:
        pass  # nselib not installed — skip
    try:
        from backend.providers.angel_provider import AngelProvider

        providers.append(AngelProvider())
    except ProviderError:
        pass  # SmartApi not installed or credentials incomplete — skip
    try:
        from backend.providers.yfinance_provider import YfinanceProvider

        providers.append(YfinanceProvider())
    except ProviderError:
        pass  # yfinance not installed — skip
    return MarketManager(providers=providers)


def warm_market_data(manager: MarketManager, symbols: tuple[str, ...] = (
        "NIFTY", "BANKNIFTY")) -> None:
    """Pre-populate the caches at startup so the first UI paint isn't the
    one paying the multi-second NSE round-trips (index table + expiries)."""
    import logging

    log = logging.getLogger("papertrader")
    for symbol in symbols:
        try:
            manager.get_spot(symbol)
        except ProviderError as exc:
            log.warning("warm-up spot %s failed: %s", symbol, exc)
    try:
        manager.expiries(6)
    except ProviderError as exc:
        log.warning("warm-up expiries failed: %s", exc)
