"""Paper-Trader — market data (Stage 3).

``MockMarketData`` now covers the full F&O surface:

- equity cash quotes for the Stage 2 underlyings (unchanged behaviour),
- index spots from ``nse_data.INDEX_SPOT``,
- futures per (symbol, expiry) with a basis curve (near month tight,
  far months wider, contango drift),
- options per (underlying, expiry, strike, CE/PE) priced with Black-Scholes
  on a deterministic IV smile; premium decays with a simulated theta clock.

The theta clock advances every ``THETA_TICK_SECONDS`` so option premiums
visibly decay in the UI. All prices are derived from a deterministic seed,
so repeated calls within one bucket return the same value.

The :class:`MarketDataSource` interface is preserved from Stage 2, so the
Stage 2 equity-cash routes keep working unchanged.
"""

from __future__ import annotations

import math
import random
import time
from datetime import date
from typing import Dict, Optional

from backend.greeks import (
    black_scholes,
    expiry_dates,
    iv_smile,
    iv_with_term_effect,
    years_to_expiry,
)
from backend.market_data_source import MarketDataSource
from backend.nse_data import INDEX_SPOT, fno_symbols, is_fno

THETA_TICK_SECONDS = 45.0
QUOTES_PER_BUCKET = 2.0  # re-roll micro-volatility every 2s

BASE_EQUITY_SPOT: Dict[str, float] = {
    "RELIANCE": 2_850.0,
    "TCS": 4_120.0,
    "INFY": 1_680.0,
    "HDFCBANK": 1_540.0,
    "ICICIBANK": 1_210.0,
    "SBIN": 790.0,
    "ITC": 465.0,
    "TATAMOTORS": 990.0,
    "AXISBANK": 1_090.0,
    "LT": 3_640.0,
}

# Base annualised ATM IV per underlying (indices carry skew; stocks pump).
BASE_IV: Dict[str, float] = {
    "NIFTY": 0.13, "BANKNIFTY": 0.15, "FINNIFTY": 0.14, "MIDCPNIFTY": 0.17,
    "RELIANCE": 0.22, "TCS": 0.20, "INFY": 0.21, "HDFCBANK": 0.21,
    "ICICIBANK": 0.22, "SBIN": 0.24, "ITC": 0.18, "TATAMOTORS": 0.28,
    "AXISBANK": 0.23, "LT": 0.21,
}


class MockMarketData(MarketDataSource):
    """Deterministic-by-bucket mock F&O market data provider.

    Implements the ``MarketDataSource`` contract directly (Stage 4), so it
    can serve as a provider on its own or wrapped by MockMarketDataProvider.
    """

    name = "mock-inner"

    def __init__(self, volatility: float = 0.002, base_spot: Dict[str, float] | None = None,
                 now_fn=None) -> None:
        self._vol = volatility
        self._base = dict(BASE_EQUITY_SPOT)
        if base_spot:
            self._base.update(base_spot)
        self._now = now_fn or time.time
        self._tick = 0
        self._last_theta_tick = self._now()

    # ------------------------------------------------------------------ time
    def _bucket(self) -> int:
        return int(self._now() // QUOTES_PER_BUCKET)

    def advance_theta_clock(self) -> bool:
        """Advance the simulated theta clock if a tick elapsed.

        Returns True when a new tick began (callers may persist cumulative
        theta or simply let pricing re-derive from T).
        """
        now = self._now()
        if now - self._last_theta_tick >= THETA_TICK_SECONDS:
            self._tick += 1
            self._last_theta_tick = now
            return True
        return False

    def sim_time_offset_days(self) -> float:
        """Simulated extra calendar days elapsed (theta acceleration)."""
        return self._tick * (THETA_TICK_SECONDS / 86400.0) * 240.0  # 240× time compression

    def _effective_years(self, t_years: float) -> float:
        """Time-to-expiry reduced by the simulated theta clock."""
        return max(t_years - self._tick * (THETA_TICK_SECONDS / 86400.0), 0.0)

    # --------------------------------------------------------------- spots
    def get_spot(self, symbol: str) -> float:
        if symbol in INDEX_SPOT:
            base = INDEX_SPOT[symbol]
        elif symbol in self._base:
            base = self._base[symbol]
        elif symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            base = INDEX_SPOT[symbol]
        else:
            raise KeyError(f"Unknown underlying: {symbol}")
        bucket = self._bucket()
        rng = random.Random(f"spot:{symbol}:{bucket}")
        return round(base * (1 + rng.uniform(-self._vol, self._vol)), 2)

    # Legacy Stage 2 interface ------------------------------------------------
    def get_price(self, symbol: str) -> float:
        """Cash price for an underlying (kept for Stage 2 routes/tests)."""
        return self.get_spot(symbol)

    def get_quotes(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for symbol in sorted(set(INDEX_SPOT) | set(self._base)):
            try:
                out[symbol] = self.get_spot(symbol)
            except KeyError:
                continue
        return out

    # -------------------------------------------------------------- futures
    def get_future_price(self, symbol: str, expiry: str) -> float:
        """Futures price with a basis curve: near month ≈ spot + carry,
        far months wider contango and a fatter simulated spread."""
        spot = self.get_spot(symbol)
        t = years_to_expiry(_parse_date(expiry))
        carry = spot * 0.065 * t                    # cost-of-carry at 6.5%
        bucket = self._bucket()
        rng = random.Random(f"fut:{symbol}:{expiry}:{bucket}")
        wiggle = spot * rng.uniform(-self._vol * 1.5, self._vol * 1.5)
        return round(spot + carry + wiggle, 2)

    def future_curve(self, symbol: str) -> list[dict]:
        return [
            {"expiry": e, "price": self.get_future_price(symbol, e),
             "basis": round(self.get_future_price(symbol, e) - self.get_spot(symbol), 2)}
            for e in [d.isoformat() for d in expiry_dates()]
        ]

    # -------------------------------------------------------------- options
    def _iv(self, underlying: str, strike: float, expiry: str, option_type: str,
            t_years: float | None = None) -> float:
        spot = self.get_spot(underlying)
        base = BASE_IV.get(underlying, 0.20)
        moneyness = math.log(strike / spot)
        iv = iv_smile(moneyness, base)
        t = t_years if t_years is not None else years_to_expiry(_parse_date(expiry))
        return iv_with_term_effect(iv, t)

    def get_option_quote(self, underlying: str, expiry: str, strike: float,
                         option_type: str) -> dict:
        """Full quote dict: ltp, bid/ask, iv, and all Greeks."""
        spot = self.get_spot(underlying)
        t = self._effective_years(years_to_expiry(_parse_date(expiry)))
        sigma = self._iv(underlying, strike, expiry, option_type, t)
        bs = black_scholes(spot, strike, t, sigma, option_type)
        ltp = max(bs["price"], 0.05)
        spread = max(0.05, ltp * 0.002)
        return {
            "underlying": underlying,
            "expiry": expiry,
            "strike": strike,
            "option_type": option_type,
            "ltp": round(ltp, 2),
            "bid": round(ltp - spread / 2, 2),
            "ask": round(ltp + spread / 2, 2),
            "iv": round(sigma, 4),
            "delta": round(bs["delta"], 4),
            "gamma": round(bs["gamma"], 6),
            "vega": round(bs["vega"], 4),
            "theta": round(bs["theta_per_day"], 4),
            "rho": round(bs["rho"], 4),
            "spot": spot,
            "t_years": round(t, 6),
        }

    def option_chain(self, underlying: str, expiry: str,
                     strike_step: int | None = None,
                     strikes_per_side: int | None = None) -> list[dict]:
        """Strike grid around ATM: calls and puts per strike with realistic
        OI/Volume patterns and IV smile/skew."""
        spot = self.get_spot(underlying)
        is_index = underlying in INDEX_SPOT
        if strike_step is None:
            strike_step = _strike_step(spot, is_index)

        # Default strikes per side - more for indices
        if strikes_per_side is None:
            strikes_per_side = 30 if is_index else 10
        # For indices, enforce minimum only when user didn't explicitly specify
        # (we can't easily detect explicit vs None, so we allow any value)

        # Generate strikes with slight randomness in OI/Volume patterns
        bucket = self._bucket()
        rng = random.Random(f"chain:{underlying}:{expiry}:{bucket}")

        # IV smile parameters - more realistic for NIFTY
        base_iv = BASE_IV.get(underlying, 0.15)
        if is_index:
            skew = rng.uniform(-0.4, -0.2)  # Put skew
            smile = rng.uniform(0.08, 0.15)  # Smile curvature
        else:
            skew = rng.uniform(-0.2, 0.1)   # Stocks: milder skew
            smile = rng.uniform(0.05, 0.12)

        # ATM strike
        atm = round(spot / strike_step) * strike_step
        strikes = [atm + i * strike_step
                   for i in range(-strikes_per_side, strikes_per_side + 1)]

        rows = []
        for strike in strikes:
            moneyness = strike / spot
            log_moneyness = math.log(strike / spot)

            # IV with skew and smile - more realistic
            iv = base_iv + skew * log_moneyness + smile * log_moneyness ** 2
            iv = max(0.05, min(iv, 0.5))  # clamp

            # Time to expiry
            t = self._effective_years(years_to_expiry(_parse_date(expiry)))

            # Generate OI/Volume with realistic patterns
            # Peak near ATM, exponential decay away from ATM
            atm_dist = abs(strike - atm) / strike_step
            oi_base = rng.uniform(1000, 50000)
            vol_base = rng.uniform(100, 5000)

            # OI/Volume decay - exponential with noise
            oi_decay = math.exp(-atm_dist * 0.15) * rng.uniform(0.3, 1.8)
            vol_decay = math.exp(-atm_dist * 0.2) * rng.uniform(0.2, 1.5)

            ce_oi = max(0, int(oi_base * oi_decay * rng.uniform(0.5, 2.0)))
            pe_oi = max(0, int(oi_base * oi_decay * rng.uniform(0.5, 2.0)))
            ce_vol = max(0, int(vol_base * vol_decay * rng.uniform(0.3, 2.5)))
            pe_vol = max(0, int(vol_base * vol_decay * rng.uniform(0.3, 2.5)))

            # Add extra liquidity near ATM
            if atm_dist <= 2:
                ce_oi = int(ce_oi * rng.uniform(3, 8))
                pe_oi = int(pe_oi * rng.uniform(3, 8))
                ce_vol = int(ce_vol * rng.uniform(2, 5))
                pe_vol = int(pe_vol * rng.uniform(2, 5))

            # Get quotes with this IV
            t_years = self._effective_years(years_to_expiry(_parse_date(expiry)))
            ce_bs = black_scholes(spot, strike, t_years, iv, "CE")
            pe_bs = black_scholes(spot, strike, t_years, iv, "PE")

            ltp_ce = max(ce_bs["price"], 0.05)
            ltp_pe = max(pe_bs["price"], 0.05)

            # Dynamic spread based on liquidity and moneyness
            liquidity_factor = max(0.3, 1.0 - atm_dist * 0.05)
            spread_ce = max(0.05, ltp_ce * 0.0015 * (2 - liquidity_factor))
            spread_pe = max(0.05, ltp_pe * 0.0015 * (2 - liquidity_factor))

            ce = {
                "underlying": underlying,
                "expiry": expiry,
                "strike": strike,
                "option_type": "CE",
                "ltp": round(ltp_ce, 2),
                "bid": round(ltp_ce - spread_ce / 2, 2),
                "ask": round(ltp_ce + spread_ce / 2, 2),
                "iv": round(iv, 4),
                "delta": round(ce_bs["delta"], 4),
                "gamma": round(ce_bs["gamma"], 6),
                "vega": round(ce_bs["vega"], 4),
                "theta": round(ce_bs["theta_per_day"], 4),
                "rho": round(ce_bs["rho"], 4),
                "volume": ce_vol,
                "oi": ce_oi,
                "spot": spot,
                "t_years": round(t, 6),
            }

            pe = {
                "underlying": underlying,
                "expiry": expiry,
                "strike": strike,
                "option_type": "PE",
                "ltp": round(ltp_pe, 2),
                "bid": round(ltp_pe - spread_pe / 2, 2),
                "ask": round(ltp_pe + spread_pe / 2, 2),
                "iv": round(iv, 4),
                "delta": round(pe_bs["delta"], 4),
                "gamma": round(pe_bs["gamma"], 6),
                "vega": round(pe_bs["vega"], 4),
                "theta": round(pe_bs["theta_per_day"], 4),
                "rho": round(pe_bs["rho"], 4),
                "volume": pe_vol,
                "oi": pe_oi,
                "spot": spot,
                "t_years": round(t, 6),
            }

            rows.append({
                "strike": strike,
                "ce": ce,
                "pe": pe,
                "atm_distance": strike - spot,
            })
        return rows

    def expiries(self, count: int = 6, symbol: str | None = None) -> list[str]:
        return [d.isoformat() for d in expiry_dates(count=count)]


# ---------------------------------------------------------------- helpers

def _strike_step(spot: float, is_index: bool) -> int:
    if is_index:
        if spot > 40000:
            return 500
        if spot > 20000:
            return 100
        return 100
    if spot > 3000:
        return 40
    if spot > 1000:
        return 20
    if spot > 500:
        return 10
    return 5


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


# Singleton used by routes; swap for a live provider in Stage 4+.
market_data = MockMarketData()
