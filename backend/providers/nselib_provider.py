"""Paper-Trader — nselib live provider (Stage 4).

Adapts the ``nselib`` package (NSE India data over plain HTTP) to the
``MarketDataSource`` contract. Written against the **installed** nselib 2.5.1
surface, whose real entry points live in submodules:

- ``nselib.indices.live_index_performances()`` — one call returns *all*
  index rows (last, percentChange); the provider caches the table.
- ``nselib.derivatives.expiry_dates_option_index()`` — dict keyed by
  underlying → ``['29-Sep-2026', ...]`` (%d-%b-%Y strings).
- ``nselib.derivatives.nse_live_option_chain(symbol, expiry_date, oi_mode)``
  — NOTE: internally parses ``expiry_date`` as **%d-%m-%Y**, unlike the
  expiry listing's %d-%b-%Y (nselib inconsistency); this provider converts.

Coverage is deliberately partial and honest:

- **Live**: index spots (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY) and their
  option chains with real OI/IV/volume.
- **ProviderMiss** (no cooldown, per-symbol): stock spots — nselib has no
  clean live stock quote, so the manager falls through to the next provider
  *without* punishing the provider.
- **Derived**: futures prices (cost-of-carry from spot) — the manager
  labels them ``derived``.
- **Greeks**: computed locally with our Black-Scholes from the chain's IV
  (smile fallback when NSE publishes 0/blank).
"""

from __future__ import annotations

from datetime import date, datetime

from backend import greeks
from backend.market_data import BASE_IV, _strike_step
from backend.market_data_source import MarketDataSource, ProviderError, ProviderMiss
from backend.models import ChainRow, OptionQuote
from backend.nse_data import INDEX_SPOT

try:  # pragma: no cover - exercised via fake module in tests
    from nselib import capital_market as _cm
    from nselib import derivatives as _deriv
    from nselib import indices as _indices
except ImportError:  # pragma: no cover
    _cm = _deriv = _indices = None

CARRY_RATE = 0.065
INDEX_NAME_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MID SELECT",
}
INDEX_TABLE_TTL = 5.0


def available() -> bool:
    return _deriv is not None


def _parse_any_date(raw) -> str:
    """Normalize nselib date strings to ISO (tolerates several formats)."""
    if raw is None:
        raise ProviderError("empty date")
    text = str(raw).strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ProviderError(f"unparseable date: {text!r}")


def _iso_to_nse_chain(iso: str) -> str:
    """nse_live_option_chain parses expiry_date as %d-%m-%Y (see module doc)."""
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d-%m-%Y")


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


class NselibProvider(MarketDataSource):
    name = "nselib"

    def __init__(self, derivatives_module=None, indices_module=None,
                 capital_market_module=None) -> None:
        self._deriv = derivatives_module if derivatives_module is not None else _deriv
        self._indices = indices_module if indices_module is not None else _indices
        self._cm = capital_market_module if capital_market_module is not None else _cm
        if self._deriv is None:
            raise ProviderError("nselib is not installed")
        self._index_table: tuple[float, dict[str, tuple[float, float]]] | None = None
        # (fetched_at, {symbol: (last, pct_change)})

    # ---------------------------------------------------------------- spots
    def _index_performances(self) -> dict[str, tuple[float, float]]:
        """{symbol: (last, percentChange)} for the F&O indices, cached 5s."""
        import time

        now = time.monotonic()
        if self._index_table and now - self._index_table[0] < INDEX_TABLE_TTL:
            return self._index_table[1]
        try:
            df = self._indices.live_index_performances()
        except Exception as exc:
            raise ProviderError(f"index performances failed: {exc}") from exc
        if df is None or df.empty or "index" not in df.columns:
            raise ProviderError("index performances returned no rows")

        table: dict[str, tuple[float, float]] = {}
        for _, row in df.iterrows():
            name = str(row.get("index", "")).strip().upper()
            last = _num(row.get("last"))
            pct = _num(row.get("percentChange"))
            for symbol, nse_name in INDEX_NAME_MAP.items():
                if name == nse_name.upper():
                    table[symbol] = (last, pct)
        if not table:
            raise ProviderError("no F&O index rows in performances table")
        self._index_table = (now, table)
        return table

    def get_spot(self, symbol: str) -> float:
        if symbol in INDEX_NAME_MAP:
            table = self._index_performances()
            if symbol not in table:
                raise ProviderMiss(f"no live row for {symbol}")
            return round(table[symbol][0], 2)
        # Stocks: no clean live quote in nselib → per-symbol miss (no cooldown).
        raise ProviderMiss(f"live stock spot unsupported for {symbol}")

    def get_quotes(self) -> dict[str, float]:
        table = self._index_performances()
        return {s: round(last, 2) for s, (last, _pct) in table.items()}

    def quote_change_pct(self, symbol: str) -> float | None:
        """Live percentChange for indices (None when unknown)."""
        if symbol in INDEX_NAME_MAP:
            table = self._index_performances()
            if symbol in table:
                return round(table[symbol][1], 2)
        return None

    # -------------------------------------------------------------- expiries
    def expiries(self, count: int = 6, symbol: str | None = None) -> list[str]:
        try:
            mapping = self._deriv.expiry_dates_option_index()
        except Exception as exc:
            raise ProviderError(f"expiry fetch failed: {exc}") from exc
        if not isinstance(mapping, dict) or not mapping:
            raise ProviderError("empty expiry mapping")

        today = date.today()
        if symbol and symbol in mapping:
            raw = mapping[symbol]
        else:
            # Union of the index expiries (symbol-agnostic callers).
            seen: set[str] = set()
            for dates in mapping.values():
                seen.update(dates)
            raw = sorted(seen)

        dates = sorted({_parse_any_date(d) for d in raw})
        upcoming = [d for d in dates if date.fromisoformat(d) >= today]
        if not upcoming:
            raise ProviderError("no upcoming expiries")
        return upcoming[:count]

    # -------------------------------------------------------------- futures
    def get_future_price(self, symbol: str, expiry: str) -> float:
        # Derived: spot grown at the carry rate until expiry (the manager
        # labels these "derived" rather than "live").
        spot = self.get_spot(symbol)
        t = greeks.years_to_expiry(date.fromisoformat(expiry))
        return round(spot * (1 + CARRY_RATE * t), 2)

    def future_curve(self, symbol: str) -> list[dict]:
        spot = self.get_spot(symbol)
        out = []
        for expiry in self.expiries(3, symbol=symbol):
            price = self.get_future_price(symbol, expiry)
            out.append({"expiry": expiry, "price": price,
                        "basis": round(price - spot, 2)})
        return out

    # -------------------------------------------------------------- options
    def _chain_frame(self, underlying: str, expiry: str):
        try:
            return self._deriv.nse_live_option_chain(
                symbol=underlying, expiry_date=_iso_to_nse_chain(expiry),
                oi_mode="full",
            )
        except Exception as exc:
            raise ProviderError(f"chain fetch failed for {underlying}: {exc}") from exc

    def get_option_quote(self, underlying: str, expiry: str, strike: float,
                         option_type: str) -> OptionQuote:
        rows = self.option_chain(underlying, expiry, strike_step=None,
                                 strikes_per_side=10 ** 6)
        for row in rows:
            if row.strike == float(strike):
                return row.ce if option_type == "CE" else row.pe
        raise ProviderError(
            f"no {option_type} data at strike {strike} for {underlying}")

    def option_chain(self, underlying: str, expiry: str,
                     strike_step: int | None = None,
                     strikes_per_side: int = 10) -> list[ChainRow]:
        df = self._chain_frame(underlying, expiry)
        if df is None or df.empty:
            raise ProviderError("empty chain frame")

        strike_col = "Strike_Price"
        if strike_col not in df.columns:
            raise ProviderError("chain frame missing Strike_Price column")

        spot = self.get_spot(underlying)  # ProviderMiss → chain falls to mock
        strikes = sorted({_num(v) for v in df[strike_col]} - {0.0})
        if not strikes:
            raise ProviderError("no strikes in chain")

        if strike_step is None:
            strike_step = _strike_step(spot, underlying in INDEX_SPOT)
        atm = min(strikes, key=lambda k: abs(k - spot))
        lo = atm - strikes_per_side * strike_step
        hi = atm + strikes_per_side * strike_step
        window = [k for k in strikes if lo <= k <= hi]

        rows = []
        for strike in window:
            match = df[df[strike_col].apply(lambda v: _num(v) == strike)]
            if match.empty:
                continue
            row = match.iloc[0]
            ce = self._quote_from_row(row, underlying, expiry, spot, strike, "CE")
            pe = self._quote_from_row(row, underlying, expiry, spot, strike, "PE")
            if ce is not None and pe is not None:
                rows.append(ChainRow(strike=strike, ce=ce, pe=pe,
                                     atm_distance=round(strike - spot, 2)))
        if not rows:
            raise ProviderError("chain window produced no rows")
        return rows

    def _quote_from_row(self, row, underlying, expiry, spot, strike,
                        option_type) -> OptionQuote | None:
        prefix = "CALLS" if option_type == "CE" else "PUTS"

        ltp = _num(_first_of(row, f"{prefix}_LTP"))
        iv_pct = _num(_first_of(row, f"{prefix}_IV"))
        iv = iv_pct / 100.0 if iv_pct > 0 else self._fallback_iv(
            underlying, strike, spot)
        t = greeks.years_to_expiry(date.fromisoformat(expiry))
        sigma = min(max(iv, 0.01), 2.0)
        bs = greeks.black_scholes(spot, strike, t, sigma, option_type)

        def oi_value(*names):
            raw = _first_of(row, *names)
            if raw is None:
                return None
            value = _num(raw, -1)
            return int(value) if value >= 0 else None

        bid = _num(_first_of(row, f"{prefix}_Bid_Price"))
        ask = _num(_first_of(row, f"{prefix}_Ask_Price"))
        if bid <= 0 or ask <= 0 or ask < bid:
            bid, ask = round(max(ltp - 0.05, 0.05), 2), round(ltp + 0.05, 2)

        return OptionQuote(
            underlying=underlying,
            expiry=expiry,
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
            oi=oi_value(f"{prefix}_OI"),
            oi_change=oi_value(f"{prefix}_Chng_in_OI"),
            volume=oi_value(f"{prefix}_Volume"),
            spot=spot,
            t_years=round(t, 6),
        )

    def _fallback_iv(self, underlying: str, strike: float, spot: float) -> float:
        moneyness = greeks.math.log(strike / spot) if spot > 0 else 0.0
        return greeks.iv_smile(moneyness, BASE_IV.get(underlying, 0.20))

    # ------------------------------------------------------------- candles
    def get_candles(self, symbol: str, interval: str,
                    start: str, end: str) -> list[dict]:
        """Daily (1D) candles only — nselib publishes no intraday history.

        - Stocks: ``capital_market.price_volume_data`` (OHLCV + delivery).
        - Indices: ``capital_market.index_data`` (indexed to the F&O
          symbol via ``INDEX_NAME_MAP``).
        Intraday intervals raise ProviderMiss (no cooldown) so the manager
        falls through to the mock's deterministic series.
        """
        from backend.providers.candles import detect_column_map, normalize_candles

        if interval != "1D":
            raise ProviderMiss(f"nselib: no {interval} candles (daily only)")
        if self._cm is None:
            raise ProviderMiss("nselib: capital_market module unavailable")

        from_date = datetime.strptime(start, "%Y-%m-%d").strftime("%d-%m-%Y")
        to_date = datetime.strptime(end, "%Y-%m-%d").strftime("%d-%m-%Y")

        try:
            if symbol in INDEX_NAME_MAP:
                df = self._cm.index_data(
                    index=INDEX_NAME_MAP[symbol],
                    from_date=from_date, to_date=to_date,
                )
            else:
                df = self._cm.price_volume_data(symbol=symbol, from_date=from_date,
                                                to_date=to_date)
        except Exception as exc:
            raise ProviderError(f"candle fetch failed for {symbol}: {exc}") from exc

        if df is None or getattr(df, "empty", True):
            raise ProviderError(f"no candle rows for {symbol} {start}..{end}")

        column_map = detect_column_map(df.columns)
        missing = {"timestamp", "open", "high", "low", "close"} - set(column_map)
        if missing:
            raise ProviderError(f"candle frame missing columns: {sorted(missing)}")
        return normalize_candles(df.to_dict("records"), column_map)

    # -------------------------------------------------------------- health
    def health_check(self) -> bool:
        try:
            self._index_performances()
            return True
        except ProviderError:
            return False


def _first_of(row, *names):
    for name in names:
        if name in row.index:
            value = row[name]
            if value is not None and str(value).strip() not in ("", "nan"):
                return value
    return None
