"""Paper-Trader — market data abstraction (Stage 4).

``MarketDataSource`` is the single contract every provider implements —
mock or live. It covers the *full* surface the trading engine needs (spots,
futures, option quotes, chains, expiries), not just display data, so the
whole app marks against one coherent source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from backend.models import ChainRow, ExpiryList, FutureQuote, OptionChain, OptionQuote


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations must be safe to call from request handlers (the manager
    serialises access and caches), must raise ``ProviderError`` on failure,
    and must return the normalized models from ``backend.models``.
    """

    name: str = "abstract"

    # ---------------------------------------------------------------- spots
    @abstractmethod
    def get_spot(self, symbol: str) -> float:
        """Latest underlying price (₹). Raises ProviderError if unknown."""

    def get_price(self, symbol: str) -> float:
        """Stage 2 compatibility alias for :meth:`get_spot`."""
        return self.get_spot(symbol)

    @abstractmethod
    def get_quotes(self) -> dict[str, float]:
        """Latest spot for every supported symbol."""

    # -------------------------------------------------------------- futures
    @abstractmethod
    def get_future_price(self, symbol: str, expiry: str) -> float:
        """Futures price for (symbol, expiry)."""

    @abstractmethod
    def future_curve(self, symbol: str) -> list[dict]:
        """[{expiry, price, basis}, ...] ascending by expiry."""

    # -------------------------------------------------------------- options
    @abstractmethod
    def get_option_quote(self, underlying: str, expiry: str, strike: float,
                         option_type: str) -> OptionQuote:
        """Single option contract quote with Greeks."""

    @abstractmethod
    def option_chain(self, underlying: str, expiry: str,
                     strike_step: int | None = None,
                     strikes_per_side: int = 10) -> list[ChainRow]:
        """Strike grid around ATM."""

    # ------------------------------------------------------------- candles
    @abstractmethod
    def expiries(self, count: int = 6) -> list[str]:
        """Upcoming expiry dates (YYYY-MM-DD), ascending."""

    def get_candles(self, symbol: str, interval: str,
                    start: str, end: str) -> list[dict]:
        """Historical OHLCV candles for (symbol, interval, start, end).

        Default: this provider does not cover candles — raising
        ``ProviderMiss`` makes the manager fall through to the next provider
        without cooling down. Implementations return rows of
        ``{timestamp, open, high, low, close, volume?, oi?}`` with ISO
        timestamps (YYYY-MM-DD or full datetimes) and ``None`` volume/oi
        when the source doesn't publish them.
        """
        raise ProviderMiss(f"{self.name}: candles not covered")

    # -------------------------------------------------------------- health
    def health_check(self) -> bool:
        """Cheap probe; default healthy. Live providers override."""
        return True


class ProviderError(Exception):
    """Provider failure — the manager falls through AND cools the provider
    down (network down, schema drift, empty responses)."""


class ProviderMiss(Exception):
    """Per-symbol data absence — the manager falls through to the next
    provider WITHOUT cooling the provider down (e.g. nselib serves indices
    live but has no stock spot)."""


# Supported candle intervals (validated before touching providers).
CANDLE_INTERVALS = ("1m", "5m", "15m", "1h", "1D")
