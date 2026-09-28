"""Paper-Trader — yfinance stock spot provider (Stage 10).

Fetches current prices for Indian equities via yfinance. Used as the
primary stock-spot provider in production so that equities like RELIANCE,
TCS, INFY carry real prices instead of simulated ones.

Only implements ``get_spot`` and ``get_quotes``; every other engine
surface raises ``ProviderMiss`` so the manager falls through for those
instruments.
"""

from __future__ import annotations

import logging

from backend.market_data_source import MarketDataSource, ProviderError, ProviderMiss

logger = logging.getLogger(__name__)

try:
    import yfinance as yf  # type: ignore

    _YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover — exercised via tests
    _YFINANCE_AVAILABLE = False

# Common Indian equities mapped to Yahoo Finance tickers.
_STOCK_TICKER_MAP: dict[str, str] = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "INFY": "INFY.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBIN": "SBIN.NS",
    "ITC": "ITC.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "LT": "LT.NS",
    "AXISBANK": "AXISBANK.NS",
}

# F&O indices on Yahoo Finance.
_INDEX_TICKER_MAP: dict[str, str] = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "^NSEMDCP50",
    "SENSEX": "^BSESN",
}

# yfinance interval strings + the max history each supports.
_CANDLE_INTERVALS: dict[str, str] = {
    "1m": "1m",    # last 7 days only
    "5m": "5m",    # last 60 days
    "15m": "15m",  # last 60 days
    "1h": "1h",    # last 730 days
    "1D": "1d",    # arbitrary
}


class YfinanceProvider(MarketDataSource):
    name = "yfinance"

    def __init__(self) -> None:
        if not _YFINANCE_AVAILABLE:
            raise ProviderError("yfinance is not installed")

    # ---- spots ----

    def get_spot(self, symbol: str) -> float:
        ticker = _STOCK_TICKER_MAP.get(symbol)
        if not ticker:
            raise ProviderMiss(f"yfinance: no ticker mapping for {symbol}")
        try:
            t = yf.Ticker(ticker)
            price = t.fast_info.get("lastPrice")  # type: ignore[union-attr]
            if price is None or price <= 0:
                raise ProviderMiss(f"yfinance: no price for {symbol}")
            return float(price)
        except ProviderMiss:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yfinance: {symbol} -> {exc}") from exc

    def get_quotes(self) -> dict[str, float]:
        quotes: dict[str, float] = {}
        for symbol in _STOCK_TICKER_MAP:
            try:
                quotes[symbol] = self.get_spot(symbol)
            except (ProviderMiss, ProviderError):
                pass
        return quotes

    # ---- everything else not covered ----

    def get_future_price(self, symbol: str, expiry: str) -> float:
        raise ProviderMiss("yfinance does not provide futures prices")

    def future_curve(self, symbol: str) -> list[dict]:
        raise ProviderMiss("yfinance does not provide futures curves")

    def get_option_quote(self, underlying, expiry, strike, option_type):
        raise ProviderMiss("yfinance does not provide option quotes")

    def option_chain(self, underlying, expiry, strike_step=None, strikes_per_side=10):
        raise ProviderMiss("yfinance does not provide option chains")

    def expiries(self, count: int = 6, symbol: str | None = None) -> list[str]:
        raise ProviderMiss("yfinance does not provide expiries")

    def get_candles(self, symbol, interval, start, end):
        raise ProviderMiss("yfinance does not provide historical candles")

    def get_candles(self, symbol: str, interval: str, start: str, end: str) -> list[dict]:
        """Real OHLCV candles from Yahoo Finance (intraday + daily).

        yfinance enforces its own lookback windows per interval (1m → 7d,
        5m/15m → 60d, 1h → 730d); ranges older than the window return no
        rows and the manager falls through (mock SIM candles / unavailable).
        """
        yf_interval = _CANDLE_INTERVALS.get(interval)
        if yf_interval is None:
            raise ProviderMiss(f"yfinance: unsupported interval {interval}")
        ticker = _STOCK_TICKER_MAP.get(symbol) or _INDEX_TICKER_MAP.get(symbol)
        if not ticker:
            raise ProviderMiss(f"yfinance: no ticker mapping for {symbol}")

        import pandas as _pd  # yfinance dependency, guaranteed present

        try:
            df = yf.download(
                ticker,
                start=start,
                end=end,
                interval=yf_interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yfinance candles {symbol} {interval}: {exc}") from exc

        if df is None or getattr(df, "empty", True):
            raise ProviderMiss(
                f"yfinance: no {interval} rows for {symbol} in {start}..{end}")

        # yfinance may return MultiIndex columns (ticker level) — flatten.
        if isinstance(df.columns, _pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        candles = []
        for ts, row in df.iterrows():
            try:
                o = float(row["Open"])
                h = float(row["High"])
                low = float(row["Low"])
                c = float(row["Close"])
            except (KeyError, TypeError, ValueError):
                continue
            if c <= 0:
                continue
            vol = row.get("Volume")
            candles.append({
                "timestamp": ts.isoformat(),
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(low, 2),
                "close": round(c, 2),
                "volume": int(vol) if vol == vol and vol is not None else None,
            })
        if not candles:
            raise ProviderMiss(f"yfinance: no usable {interval} rows for {symbol}")
        return candles

    def health_check(self) -> bool:
        return _YFINANCE_AVAILABLE
