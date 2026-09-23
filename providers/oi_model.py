"""OI Data Model — multi-temporal open interest observations.

Tracks OI at multiple timestamps for intraday OI change calculation.
Critical for Futures, Options, and Derivatives components.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from collections import deque


@dataclass
class OIObservation:
    """Single OI observation at a point in time."""
    timestamp: datetime
    oi: float
    price: Optional[float] = None  # LTP at observation time
    volume: Optional[float] = None


@dataclass
class OITimeseries:
    """Time series of OI observations for a single instrument.

    Stores observations at multiple resolutions:
    - Intraday: every 5 minutes (for OI change calculation)
    - Daily: end-of-day values
    - Weekly: end-of-week values
    """
    symbol: str
    token: str
    instrument_type: str  # FUTIDX | OPTIDX | OPTSTK

    # Intraday observations (last 8 hours = ~96 observations at 5min)
    intraday: deque = field(default_factory=lambda: deque(maxlen=100))

    # Daily observations (last 30 days)
    daily: deque = field(default_factory=lambda: deque(maxlen=30))

    # Weekly observations (last 12 weeks)
    weekly: deque = field(default_factory=lambda: deque(maxlen=12))

    @property
    def current_oi(self) -> Optional[float]:
        """Most recent OI value."""
        if self.intraday:
            return self.intraday[-1].oi
        if self.daily:
            return self.daily[-1].oi
        return None

    @property
    def current_price(self) -> Optional[float]:
        """Most recent price."""
        if self.intraday:
            return self.intraday[-1].price
        if self.daily:
            return self.daily[-1].price
        return None

    @property
    def oi_change_5min(self) -> Optional[float]:
        """OI change in last 5 minutes."""
        if len(self.intraday) < 2:
            return None
        return self.intraday[-1].oi - self.intraday[-2].oi

    @property
    def oi_change_intraday(self) -> Optional[float]:
        """OI change from market open (first observation today)."""
        if len(self.intraday) < 2:
            return None
        return self.intraday[-1].oi - self.intraday[0].oi

    @property
    def oi_change_1d(self) -> Optional[float]:
        """OI change from previous day close."""
        if len(self.daily) < 2:
            return None
        return self.daily[-1].oi - self.daily[-2].oi

    @property
    def oi_change_5d(self) -> Optional[float]:
        """OI change over last 5 trading days."""
        if len(self.daily) < 6:
            if self.daily:
                return self.daily[-1].oi - self.daily[0].oi
            return None
        return self.daily[-1].oi - self.daily[-6].oi

    @property
    def oi_change_20d(self) -> Optional[float]:
        """OI change over last 20 trading days."""
        if len(self.daily) < 20:
            if self.daily:
                return self.daily[-1].oi - self.daily[0].oi
            return None
        return self.daily[-1].oi - self.daily[-20].oi

    def add_intraday(self, oi: float, price: Optional[float] = None, volume: Optional[float] = None):
        """Add an intraday observation."""
        self.intraday.append(OIObservation(
            timestamp=datetime.now(timezone.utc),
            oi=oi,
            price=price,
            volume=volume,
        ))

    def add_daily(self, oi: float, price: Optional[float] = None):
        """Add a daily close observation."""
        self.daily.append(OIObservation(
            timestamp=datetime.now(timezone.utc),
            oi=oi,
            price=price,
        ))

    def add_weekly(self, oi: float, price: Optional[float] = None):
        """Add a weekly close observation."""
        self.weekly.append(OIObservation(
            timestamp=datetime.now(timezone.utc),
            oi=oi,
            price=price,
        ))


class OIAggregate:
    """Aggregated OI metrics for option chain analysis.

    Combines CE and PE OI data for:
    - PCR (by OI)
    - Max Pain calculation
    - OI buildup direction
    - Support/resistance from OI concentration
    """

    def __init__(self):
        self._ce_oi: dict[float, float] = {}  # strike -> CE OI
        self._pe_oi: dict[float, float] = {}  # strike -> PE OI
        self._ce_change: dict[float, float] = {}
        self._pe_change: dict[float, float] = {}

    def set_strike_oi(
        self,
        strike: float,
        ce_oi: Optional[float] = None,
        pe_oi: Optional[float] = None,
        ce_change: Optional[float] = None,
        pe_change: Optional[float] = None,
    ):
        """Set OI for a specific strike."""
        if ce_oi is not None:
            self._ce_oi[strike] = ce_oi
        if pe_oi is not None:
            self._pe_oi[strike] = pe_oi
        if ce_change is not None:
            self._ce_change[strike] = ce_change
        if pe_change is not None:
            self._pe_change[strike] = pe_change

    @property
    def total_call_oi(self) -> float:
        return sum(self._ce_oi.values())

    @property
    def total_put_oi(self) -> float:
        return sum(self._pe_oi.values())

    @property
    def pcr_by_oi(self) -> Optional[float]:
        """Put-Call Ratio by OI."""
        total_ce = self.total_call_oi
        if total_ce == 0:
            return None
        return round(self.total_put_oi / total_ce, 4)

    @property
    def total_call_change(self) -> float:
        return sum(self._ce_change.values())

    @property
    def total_put_change(self) -> float:
        return sum(self._pe_change.values())

    @property
    def pcr_change(self) -> Optional[float]:
        """PCR of OI changes."""
        total_ce_change = self.total_call_change
        if total_ce_change == 0:
            return None
        return round(self.total_put_change / total_ce_change, 4)

    @property
    def max_pain(self) -> Optional[float]:
        """Calculate Max Pain strike.

        Max Pain is the strike where total OI (CE + PE) is highest
        — the price where most options expire worthless.
        """
        all_strikes = set(self._ce_oi.keys()) | set(self._pe_oi.keys())
        if not all_strikes:
            return None

        min_pain = None
        min_pain_strike = None

        for test_strike in all_strikes:
            pain = 0.0
            for strike, oi in self._ce_oi.items():
                if strike < test_strike:
                    pain += (test_strike - strike) * oi
            for strike, oi in self._pe_oi.items():
                if strike > test_strike:
                    pain += (strike - test_strike) * oi

            if min_pain is None or pain < min_pain:
                min_pain = pain
                min_pain_strike = test_strike

        return min_pain_strike

    @property
    def support_levels(self) -> list[dict]:
        """Find support levels from PE OI concentration.

        High PE OI at a strike = support (put writers expect price to stay above).
        """
        if not self._pe_oi:
            return []
        sorted_strikes = sorted(self._pe_oi.items(), key=lambda x: x[1], reverse=True)
        return [
            {"strike": s, "oi": oi, "type": "SUPPORT"}
            for s, oi in sorted_strikes[:3]
        ]

    @property
    def resistance_levels(self) -> list[dict]:
        """Find resistance levels from CE OI concentration.

        High CE OI at a strike = resistance (call writers expect price to stay below).
        """
        if not self._ce_oi:
            return []
        sorted_strikes = sorted(self._ce_oi.items(), key=lambda x: x[1], reverse=True)
        return [
            {"strike": s, "oi": oi, "type": "RESISTANCE"}
            for s, oi in sorted_strikes[:3]
        ]

    def get_strike_view(self, strike: float) -> dict:
        """Get OI view for a specific strike."""
        return {
            "strike": strike,
            "ce_oi": self._ce_oi.get(strike),
            "pe_oi": self._pe_oi.get(strike),
            "ce_change": self._ce_change.get(strike),
            "pe_change": self._pe_change.get(strike),
        }
