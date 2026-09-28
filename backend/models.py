"""Paper-Trader — normalized market data models (Stage 4).

Every provider returns these shapes regardless of its wire format, so
routes, the trading engine, and the frontend consume one stable schema.
All models are plain dataclasses with ``to_dict()`` for JSON responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DictAccessMixin:
    """Dict-style read access (``q["ltp"]``) so legacy consumers — notably
    the trading engine — work with model instances unchanged."""

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass
class Quote(DictAccessMixin):
    """Spot/LTP quote for an underlying."""

    symbol: str
    ltp: float
    change_pct: float = 0.0
    source: str = "mock"
    status: str = "simulated"
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class OptionQuote(DictAccessMixin):
    """Single option contract quote with computed Greeks."""

    underlying: str
    expiry: str
    strike: float
    option_type: str            # "CE" | "PE"
    ltp: float
    bid: float = 0.0
    ask: float = 0.0
    iv: float = 0.0             # annualised fraction, e.g. 0.135
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0           # per 1 IV point
    theta: float = 0.0          # per calendar day
    rho: float = 0.0
    oi: Optional[int] = None
    oi_change: Optional[int] = None
    volume: Optional[int] = None
    spot: float = 0.0
    t_years: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ChainRow(DictAccessMixin):
    """One strike: call side, put side, distance from spot."""

    strike: float
    ce: OptionQuote
    pe: OptionQuote
    atm_distance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strike": self.strike,
            "ce": self.ce.to_dict(),
            "pe": self.pe.to_dict(),
            "atm_distance": self.atm_distance,
        }


@dataclass
class OptionChain(DictAccessMixin):
    """Normalized option chain for one underlying + expiry."""

    underlying: str
    expiry: str
    spot: float
    atm_strike: Optional[float]
    rows: list[ChainRow]
    expiries: list[str] = field(default_factory=list)
    source: str = "mock"
    status: str = "simulated"
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.underlying,
            "expiry": self.expiry,
            "spot": self.spot,
            "atm_strike": self.atm_strike,
            "rows": [r.to_dict() for r in self.rows],
            "expiries": self.expiries,
            "source": self.source,
            "status": self.status,
            "timestamp": self.timestamp,
        }


@dataclass
class FutureQuote(DictAccessMixin):
    """Futures contract quote (one expiry)."""

    symbol: str
    expiry: str
    price: float
    basis: float = 0.0
    source: str = "mock"
    status: str = "simulated"
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ExpiryList(DictAccessMixin):
    """Expiry ladder + futures curve for an underlying."""

    symbol: str
    expiries: list[str]
    futures: list[FutureQuote]
    source: str = "mock"
    status: str = "simulated"
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "expiries": self.expiries,
            "futures": [f.to_dict() for f in self.futures],
            "source": self.source,
            "status": self.status,
            "timestamp": self.timestamp,
        }
