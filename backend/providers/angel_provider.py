"""Paper-Trader — Angel One SmartAPI provider (broker-grade failover).

Priority in the production stack: nselib → **angel** → yfinance. When the
scraping provider (nselib) fails, cools down, or stalls, the manager falls
through to the broker's official API instead of degraded/stale data.

Credentials come from ``.env`` via ``backend.config.ANGEL`` (API key,
client code, PIN, TOTP secret). Without all four the provider is skipped
at construction — the stack degrades exactly as it does when a package is
missing.

Coverage (honest, verified against the Phase-0 probe in
``tools/smartapi_probe.py``):

- **Spots** — ``ltpData`` per symbol; index tokens are a verified static
  map, stocks resolve lazily via ``searchScrip`` (cached). Unknown or
  unresolvable symbols raise ``ProviderMiss`` (per-symbol, no cooldown).
- **Expiries** — ``searchScrip`` on NFO, expiry column parsed flexibly.
- **Option chains** — ``searchScrip`` strike ladder + ONE batched
  ``getMarketDataFULL`` call (no per-strike rate-limit pain); Greeks are
  computed locally with our Black-Scholes from the IV when published, or
  the IV-smile fallback (same policy as the nselib provider).
- **Futures** — real FUT contract LTP via ``searchScrip`` + ``ltpData``
  (not a carry model).
- **Candles** — ``getCandleData`` (1m/5m/15m/1h/1D), the probe-verified
  historical endpoint.

Rate limits (~1 rps quote endpoints) are enforced with a min-interval
throttle; the manager's TTL cache keeps the request count low anyway.
Sessions: single login on first use, one transparent re-login attempt when
an auth/expiry error is detected. Threads: all calls are lock-guarded and
safe to run from the manager's shared executor.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime

from backend import greeks
from backend.config import ANGEL
from backend.market_data import BASE_IV, _strike_step
from backend.market_data_source import MarketDataSource, ProviderError, ProviderMiss
from backend.models import ChainRow, OptionQuote
from backend.nse_data import INDEX_SPOT

try:  # pragma: no cover - exercised via injected fake api in tests
    from SmartApi import SmartConnect as _SmartConnect
    import pyotp as _pyotp
except ImportError:  # pragma: no cover
    _SmartConnect = _pyotp = None

# Index spot tokens (probe-verified for NIFTY/BANKNIFTY).
INDEX_TOKENS: dict[str, tuple[str, str]] = {
    "NIFTY": ("NSE", "99926000"),
    "BANKNIFTY": ("NSE", "99926009"),
    "FINNIFTY": ("NSE", "99926037"),
    "MIDCPNIFTY": ("NSE", "99926048"),
    "SENSEX": ("BSE", "99919000"),
}

_CANDLE_INTERVALS = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1h": "SIXTY_MINUTE",
    "1D": "DAY",
}

QUOTE_MIN_INTERVAL = 1.05  # Angel allows ~1 rps on quote endpoints

_AUTH_MARKERS = ("token", "session", "auth", "expired", "invalid login",
                 "logout", "401", "403")


def _parse_expiry(raw) -> str | None:
    """Normalize Angel expiry strings (25Dec2026, 25-Dec-2026, ISO) → ISO."""
    if raw is None:
        return None
    text = str(raw).strip()
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(raw, default: float = 0.0) -> float:
    if raw is None:
        return default
    text = str(raw).replace(",", "").strip()
    if text in ("", "-", "nan", "None"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


class AngelProvider(MarketDataSource):
    name = "angel"

    def __init__(self, api=None, creds=None) -> None:
        creds = creds or ANGEL
        if api is None:
            if _SmartConnect is None:
                raise ProviderError("SmartApi is not installed")
            if not creds.complete:
                raise ProviderError(
                    f"Angel One credentials incomplete; missing: {creds.missing()}")
            api = _SmartConnect(api_key=creds.api_key)
        self._api = api
        self._creds = creds
        self._session_lock = threading.Lock()
        self._logged_in = False
        self._rate_lock = threading.Lock()
        self._last_quote_ts = 0.0
        self._scrip_cache: dict[tuple[str, str], tuple[str, str] | None] = {}
        # (exchange, symbol) → (exchange, token) | None (resolution failed)
        self._nfo_cache: dict[str, tuple[float, list[dict]]] = {}
        # symbol → (timestamp, searchScrip rows); NFO scrip rows barely
        # change intraday, so caching them for 15 min keeps chain/expiry/
        # option-quote lookups from burning the ~1 rps searchScrip quota.
        self._NFO_TTL = 900.0

    # ------------------------------------------------------------ plumbing
    def _ensure_session(self) -> None:
        if self._logged_in:
            return
        with self._session_lock:
            if self._logged_in:
                return
            import pyotp  # installed alongside SmartApi

            try:
                try:
                    totp = pyotp.TOTP(self._creds.totp_secret).now()
                except Exception:
                    # Malformed secret — let the API reject it rather than
                    # crashing locally (keeps fakes/probes simple too).
                    totp = "000000"
                data = self._api.generateSession(
                    self._creds.client_code, self._creds.pin, totp)
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"angel login failed: {exc}") from exc
            if not (isinstance(data, dict) and data.get("status")):
                msg = (data or {}).get("message", "no payload") if isinstance(data, dict) else data
                raise ProviderError(f"angel login rejected: {msg}")
            self._logged_in = True

    def _maybe_relogin(self, message: str) -> bool:
        """One transparent re-login attempt on auth/expiry-looking errors."""
        lowered = str(message).lower()
        if not any(m in lowered for m in _AUTH_MARKERS):
            return False
        with self._session_lock:
            self._logged_in = False
        try:
            self._ensure_session()
            return True
        except ProviderError:
            return False

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = QUOTE_MIN_INTERVAL - (time.monotonic() - self._last_quote_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_quote_ts = time.monotonic()

    def _call(self, fn_name: str, *args, rate_limited: bool = False, **kwargs):
        """Invoke a SmartAPI method; re-login once on auth errors."""
        self._ensure_session()
        for attempt in (1, 2):
            if rate_limited:
                self._throttle()
            try:
                resp = getattr(self._api, fn_name)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if attempt == 1 and self._maybe_relogin(str(exc)):
                    continue
                raise ProviderError(f"angel {fn_name} failed: {exc}") from exc
            if isinstance(resp, dict) and resp.get("status") is False:
                msg = str(resp.get("message", "error"))
                if attempt == 1 and self._maybe_relogin(msg):
                    continue
                lowered = msg.lower()
                # Unknown/unresolvable instrument → per-symbol gap, not a
                # provider failure (same policy as the nselib provider).
                if any(m in lowered for m in (
                        "token not found", "no data", "not found",
                        "invalid token", "no record")):
                    raise ProviderMiss(f"angel {fn_name}: {msg}")
                raise ProviderError(f"angel {fn_name} error: {msg}")
            return resp
        raise ProviderError(f"angel {fn_name}: unreachable")

    def _resolve(self, symbol: str, exchange: str = "NSE") -> tuple[str, str]:
        """(exchange, token) for a symbol; static index map first, then a
        cached searchScrip lookup. Misses raise ProviderMiss."""
        if symbol in INDEX_TOKENS:
            return INDEX_TOKENS[symbol]
        key = (exchange, symbol)
        if key not in self._scrip_cache:
            try:
                resp = self._call("searchScrip", exchange, symbol)
            except ProviderError:
                resp = None
            rows = (resp or {}).get("data") or []
            token = None
            for row in rows:
                if str(row.get("tradingsymbol", "")).upper() == symbol:
                    token = (str(row.get("exchange") or exchange),
                             str(row.get("symboltoken", "")))
                    break
            self._scrip_cache[key] = token if token and token[1] else None
        resolved = self._scrip_cache[key]
        if resolved is None:
            raise ProviderMiss(f"angel: no token for {symbol} on {exchange}")
        return resolved

    def _search_nfo(self, symbol: str) -> list[dict]:
        """NFO scrip rows for a symbol, cached 15 min (rate-limit guard)."""
        now = time.monotonic()
        hit = self._nfo_cache.get(symbol)
        if hit is not None and now - hit[0] < self._NFO_TTL:
            return hit[1]
        try:
            resp = self._call("searchScrip", "NFO", symbol)
        except ProviderError as exc:
            raise ProviderError(f"angel NFO search failed: {exc}") from exc
        rows = (resp or {}).get("data") or []
        self._nfo_cache[symbol] = (now, rows)
        return rows

    def _quote_from_full(self, data: dict, underlying: str, expiry_iso: str,
                         strike: float, option_type: str, spot: float,
                         token: str) -> OptionQuote:
        """Normalize one getMarketDataFULL row into an OptionQuote."""
        ltp = _num(data.get("ltp"))

        iv_raw = _num(data.get("iv"))
        iv = iv_raw / 100.0 if iv_raw > 0 else self._fallback_iv(
            underlying, strike, spot)
        t = greeks.years_to_expiry(date.fromisoformat(expiry_iso))
        sigma = min(max(iv, 0.01), 2.0)
        bs = greeks.black_scholes(spot, strike, t, sigma, option_type)

        bid = ask = 0.0
        depth = (data.get("depth") or {})
        buy = depth.get("buy") or []
        sell = depth.get("sell") or []
        if buy and sell:
            bid, ask = _num(buy[0].get("price")), _num(sell[0].get("price"))
        if bid <= 0 or ask <= 0 or ask < bid:
            bid, ask = round(max(ltp - 0.05, 0.05), 2), round(ltp + 0.05, 2)

        oi = int(_num(data.get("oi"), -1))
        return OptionQuote(
            underlying=underlying,
            expiry=expiry_iso,
            strike=strike,
            option_type=option_type,
            ltp=round(ltp, 2) if ltp > 0 else round(bs["price"], 2),
            bid=bid,
            ask=ask,
            iv=round(sigma, 4),
            delta=round(bs["delta"], 4),
            gamma=round(bs["gamma"], 6),
            vega=round(bs["vega"], 4),
            theta=round(bs["theta_per_day"], 4),
            rho=round(bs["rho"], 4),
            oi=oi if oi >= 0 else None,
            oi_change=None,          # SmartAPI full quotes don't carry ΔOI
            volume=int(_num(data.get("volume"), -1)) or None,
            spot=spot,
            t_years=round(t, 6),
        )

    def _fallback_iv(self, underlying: str, strike: float, spot: float) -> float:
        moneyness = greeks.math.log(strike / spot) if spot > 0 else 0.0
        return greeks.iv_smile(moneyness, BASE_IV.get(underlying, 0.20))

    # ---------------------------------------------------------------- spots
    def get_spot(self, symbol: str) -> float:
        exchange, token = self._resolve(symbol)
        resp = self._call("ltpData", exchange, symbol, token, rate_limited=True)
        data = (resp or {}).get("data") or {}
        ltp = _num(data.get("ltp"), -1)
        if ltp <= 0:
            # Bad/unknown token etc — per-symbol miss, no cooldown.
            raise ProviderMiss(f"angel: no ltp for {symbol}")
        return round(ltp, 2)

    def get_quotes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        last_error: Exception | None = None
        for symbol in INDEX_TOKENS:
            try:
                out[symbol] = self.get_spot(symbol)
            except (ProviderMiss, ProviderError) as exc:
                last_error = exc
        if not out:
            raise ProviderError(f"angel: no spots served ({last_error})")
        return out

    def quote_change_pct(self, symbol: str) -> float | None:
        return None  # no cheap day-change endpoint; manager falls back to base

    # ------------------------------------------------------------- expiries
    def expiries(self, count: int = 6, symbol: str | None = None) -> list[str]:
        symbol = symbol or "NIFTY"
        rows = self._search_nfo(symbol)
        today = date.today()
        dates = sorted({
            iso for r in rows
            if (iso := _parse_expiry(r.get("expiry"))) and date.fromisoformat(iso) >= today
        })
        if not dates:
            raise ProviderMiss(f"angel: no upcoming expiries for {symbol}")
        return dates[:count]

    # -------------------------------------------------------------- futures
    def get_future_price(self, symbol: str, expiry: str) -> float:
        rows = self._search_nfo(symbol)
        for row in rows:
            if "FUT" not in str(row.get("tradingsymbol", "")).upper():
                continue
            if _parse_expiry(row.get("expiry")) != expiry:
                continue
            token = str(row.get("symboltoken", ""))
            tradingsym = str(row.get("tradingsymbol", ""))
            resp = self._call("ltpData", "NFO", tradingsym, token, rate_limited=True)
            ltp = _num((resp or {}).get("data", {}).get("ltp"), -1)
            if ltp > 0:
                return round(ltp, 2)
            break
        raise ProviderMiss(f"angel: no FUT row for {symbol} {expiry}")

    def future_curve(self, symbol: str) -> list[dict]:
        spot = self.get_spot(symbol)
        out = []
        for expiry in self.expiries(3, symbol=symbol):
            try:
                price = self.get_future_price(symbol, expiry)
            except (ProviderMiss, ProviderError):
                continue
            out.append({"expiry": expiry, "price": price,
                        "basis": round(price - spot, 2)})
        if not out:
            raise ProviderError(f"angel: empty future curve for {symbol}")
        return out

    # -------------------------------------------------------------- options
    def option_chain(self, underlying: str, expiry: str,
                     strike_step: int | None = None,
                     strikes_per_side: int = 10) -> list[ChainRow]:
        rows = self._search_nfo(underlying)
        if not rows:
            raise ProviderMiss(f"angel: no NFO rows for {underlying}")

        spot = self.get_spot(underlying)
        if strike_step is None:
            strike_step = _strike_step(spot, underlying in INDEX_SPOT)

        # Strike ladder for this expiry from the searchScrip rows.
        ladder: dict[float, dict[str, tuple[str, str]]] = {}
        # strike → {"CE": (token, tradingsym), "PE": (…)}
        for row in rows:
            if _parse_expiry(row.get("expiry")) != expiry:
                continue
            tradingsym = str(row.get("tradingsymbol", "")).upper()
            # SDK search rows don't carry option_type/strike fields — parse
            # them from the NFO trading symbol (e.g. NIFTY2510025100CE).
            if not tradingsym.endswith(("CE", "PE")):
                continue
            otype = tradingsym[-2:]
            digits = tradingsym[:-2]
            strike_part = digits[len(underlying.upper()):] if digits.upper().startswith(underlying.upper()) else digits
            if not strike_part.isdigit():
                continue
            strike = float(strike_part)
            ladder.setdefault(strike, {})[otype] = (
                str(row.get("symboltoken", "")), tradingsym)

        strikes = sorted(ladder)
        if not strikes:
            raise ProviderMiss(f"angel: no option rows for {underlying} {expiry}")

        atm = min(strikes, key=lambda k: abs(k - spot))
        lo, hi = atm - strikes_per_side * strike_step, atm + strikes_per_side * strike_step
        window = [k for k in strikes if lo <= k <= hi]

        tokens = {"NFO": [
            ladder[k][otype][0]
            for k in window for otype in ("CE", "PE") if otype in ladder[k]
        ]}
        resp = self._call("getMarketData", "FULL", tokens, rate_limited=True)
        fetched = ((resp or {}).get("data", {}) or {}).get("fetched") or []
        by_token = {str(q.get("symbolToken")): q for q in fetched}

        out = []
        for strike in window:
            sides = {}
            complete = True
            for otype in ("CE", "PE"):
                entry = ladder[strike].get(otype)
                if entry is None:
                    complete = False
                    break
                q = by_token.get(entry[0])
                if q is None:
                    complete = False
                    break
                sides[otype] = self._quote_from_full(
                    q, underlying, expiry, strike, otype, spot, entry[0])
            if complete:
                out.append(ChainRow(strike=strike, ce=sides["CE"], pe=sides["PE"],
                                    atm_distance=round(strike - spot, 2)))
        if not out:
            raise ProviderError(f"angel: chain window produced no rows for {underlying}")
        return out

    def get_option_quote(self, underlying: str, expiry: str, strike: float,
                         option_type: str) -> OptionQuote:
        chain = self.option_chain(underlying, expiry, strikes_per_side=10 ** 6)
        for row in chain:
            if row.strike == float(strike):
                return row.ce if option_type == "CE" else row.pe
        raise ProviderError(
            f"angel: no {option_type} data at strike {strike} for {underlying}")

    # -------------------------------------------------------------- candles
    def get_candles(self, symbol: str, interval: str,
                    start: str, end: str) -> list[dict]:
        yf_style = _CANDLE_INTERVALS.get(interval)
        if yf_style is None:
            raise ProviderMiss(f"angel: unsupported interval {interval}")
        _, token = self._resolve(symbol)
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": yf_style,
            "fromdate": f"{start} 09:15",
            "todate": f"{end} 15:30",
        }
        resp = self._call("getCandleData", params)
        rows = (resp or {}).get("data") or []
        if not rows:
            raise ProviderMiss(f"angel: no {interval} candles for {symbol}")

        out = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(str(r[0]))
                o, h, low, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            except (ValueError, TypeError, IndexError):
                continue
            if c <= 0:
                continue
            vol = r[5] if len(r) > 5 else None
            out.append({
                "timestamp": ts.isoformat(),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(low, 2),
                "close": round(c, 2),
                "volume": int(vol) if isinstance(vol, (int, float)) else None,
            })
        if not out:
            raise ProviderMiss(f"angel: no usable {interval} candles for {symbol}")
        return out

    # --------------------------------------------------------------- health
    def health_check(self) -> bool:
        return _SmartConnect is not None and ANGEL.complete
