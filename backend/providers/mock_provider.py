"""Paper-Trader — mock provider (Stage 4).

Wraps the Stage 3 ``MockMarketData`` (deterministic Black-Scholes simulated
surface) behind the provider contract. Always available, always healthy —
the terminal fallback when live providers fail.
"""

from __future__ import annotations

from backend.market_data import BASE_EQUITY_SPOT, MockMarketData
from backend.market_data_source import MarketDataSource, ProviderMiss
from backend.models import (
    ChainRow,
    ExpiryList,
    FutureQuote,
    OptionChain,
    OptionQuote,
)
from backend.nse_data import INDEX_SPOT


class MockMarketDataProvider(MarketDataSource):
    name = "mock"

    def __init__(self, md: MockMarketData | None = None,
                 allow_fake_stocks: bool = True) -> None:
        self._md = md if md is not None else MockMarketData()
        self._allow_fake_stocks = allow_fake_stocks

    # Expose the wrapped engine (tests tick its theta clock / shift spots).
    @property
    def md(self) -> MockMarketData:
        return self._md

    def get_spot(self, symbol: str) -> float:
        if not self._allow_fake_stocks:
            if symbol in BASE_EQUITY_SPOT and symbol not in INDEX_SPOT:
                raise ProviderMiss(f"mock: no stock spot for {symbol}")
        return self._md.get_spot(symbol)

    def get_quotes(self) -> dict[str, float]:
        return self._md.get_quotes()

    def get_future_price(self, symbol: str, expiry: str) -> float:
        return self._md.get_future_price(symbol, expiry)

    def future_curve(self, symbol: str) -> list[dict]:
        return self._md.future_curve(symbol)

    def get_option_quote(self, underlying: str, expiry: str, strike: float,
                         option_type: str) -> OptionQuote:
        data = self._md.get_option_quote(underlying, expiry, strike, option_type)
        return OptionQuote(
            underlying=data["underlying"],
            expiry=data["expiry"],
            strike=data["strike"],
            option_type=data["option_type"],
            ltp=data["ltp"],
            bid=data["bid"],
            ask=data["ask"],
            iv=data["iv"],
            delta=data["delta"],
            gamma=data["gamma"],
            vega=data["vega"],
            theta=data["theta"],
            rho=data["rho"],
            spot=data["spot"],
            t_years=data["t_years"],
        )

    def option_chain(self, underlying: str, expiry: str,
                     strike_step: int | None = None,
                     strikes_per_side: int = 10) -> list[ChainRow]:
        rows = self._md.option_chain(underlying, expiry,
                                     strike_step=strike_step,
                                     strikes_per_side=strikes_per_side)
        return [
            ChainRow(
                strike=row["strike"],
                ce=self.get_option_quote(underlying, expiry, row["strike"], "CE"),
                pe=self.get_option_quote(underlying, expiry, row["strike"], "PE"),
                atm_distance=row["atm_distance"],
            )
            for row in rows
        ]

    def expiries(self, count: int = 6, symbol: str | None = None) -> list[str]:
        return self._md.expiries(count)

    def get_candles(self, symbol: str, interval: str,
                    start: str, end: str) -> list[dict]:
        """Deterministic simulated candles (status "simulated").

        Any underlying the mock quotes can also chart; unknown symbols
        raise ProviderMiss so the route reports unavailable instead of
        inventing data.
        """
        try:
            spot = self._md.get_spot(symbol)  # validates the symbol
        except KeyError as exc:
            raise ProviderMiss(f"mock: no data for {symbol}") from exc
        from backend.providers.candles import build_mock_candles

        return build_mock_candles(symbol, interval, start, end, base_spot=spot)
