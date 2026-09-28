"""Paper-Trader — Black-Scholes pricing and Greeks (Stage 3).

Pure-math module (stdlib only, no scipy): norm cdf via erf keeps the
dependency footprint small while matching scipy.stats accuracy to ~1e-9.

Conventions:
- Prices/Greeks are per unit (multiplier applied elsewhere, in the engine).
- ``T`` is in years (365-day convention); ``sigma`` is annualised IV.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal

Side = Literal["CE", "PE"]


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: Side = "CE",
    rate: float = 0.065,
    dividend_yield: float = 0.0,
) -> dict:
    """Return price + Greeks for a European call/put.

    Boundary handling: T→0 or sigma→0 collapses to the deterministic payoff
    with zero Greeks; for sigma>0, T>0 d1/d2 are always finite, so deep
    ITM/OTM tails stay stable.
    """
    if spot <= 0 or strike <= 0 or sigma < 0 or t_years < 0:
        raise ValueError("invalid Black-Scholes inputs")

    if t_years == 0 or sigma == 0:
        df_r = math.exp(-rate * t_years)
        df_q = math.exp(-dividend_yield * t_years)
        if option_type == "CE":
            price = max(spot * df_q - strike * df_r, 0.0)
        else:
            price = max(strike * df_r - spot * df_q, 0.0)
        return {
            "price": price,
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "theta_per_day": 0.0,
            "rho": 0.0,
            "d1": float("nan"),
            "d2": float("nan"),
        }

    srt = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * sigma ** 2) * t_years) / (sigma * srt)
    d2 = d1 - sigma * srt
    df_r = math.exp(-rate * t_years)
    df_q = math.exp(-dividend_yield * t_years)
    pdf_d1 = _norm_pdf(d1)

    if option_type == "CE":
        price = spot * df_q * _norm_cdf(d1) - strike * df_r * _norm_cdf(d2)
        delta = df_q * _norm_cdf(d1)
        theta_annual = (
            -spot * df_q * pdf_d1 * sigma / (2 * srt)
            - rate * strike * df_r * _norm_cdf(d2)
            + dividend_yield * spot * df_q * _norm_cdf(d1)
        )
        rho = strike * t_years * df_r * _norm_cdf(d2)
    else:
        price = strike * df_r * _norm_cdf(-d2) - spot * df_q * _norm_cdf(-d1)
        delta = df_q * (_norm_cdf(d1) - 1.0)
        theta_annual = (
            -spot * df_q * pdf_d1 * sigma / (2 * srt)
            + rate * strike * df_r * _norm_cdf(-d2)
            - dividend_yield * spot * df_q * _norm_cdf(-d1)
        )
        rho = -strike * t_years * df_r * _norm_cdf(-d2)

    gamma = df_q * pdf_d1 / (spot * sigma * srt)
    vega = spot * df_q * pdf_d1 * srt  # per 1.00 IV change

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega / 100.0,           # per 1 IV point (1%)
        "theta_per_day": theta_annual / 365.0,
        "rho": rho / 100.0,             # per 1% rate change
        "d1": d1,
        "d2": d2,
    }


# ---------------------------------------------------------------------------
# IV smile / skew (mock surface)
# ---------------------------------------------------------------------------

def iv_smile(
    moneyness: float,
    base_iv: float,
    skew: float = -0.35,
    smile: float = 0.12,
) -> float:
    """Annualised IV for a strike, as a function of log-moneyness ln(K/S).

    Models an index-style surface: OTM puts (moneyness < 0) carry a skew
    premium, far wings lift in a smile, ATM ≈ base_iv. Clamped to [5%, 120%].
    """
    iv = base_iv + skew * moneyness + smile * moneyness ** 2
    return min(1.20, max(0.05, iv))


def iv_with_term_effect(base_iv: float, t_years: float, floor_days: float = 7.0) -> float:
    """Raise IV as expiry approaches (event-risk floor), decaying to base.

    Below ``floor_days`` to expiry IV is pumped up linearly (max +5 points),
    mimicking pre-expiry demand; beyond ~60 days it sits at base.
    """
    days = max(t_years, 0.0) * 365.0
    if days >= 60:
        return base_iv
    if days >= floor_days:
        return base_iv
    pump = 0.05 * (1.0 - (days / floor_days))
    return min(1.20, base_iv + pump)


# ---------------------------------------------------------------------------
# Expiry calendar (NSE-style: weekly Thursday + last-Thursday monthly)
# ---------------------------------------------------------------------------

def next_weekly_expiry(after: date, weeks: int = 0, count: int = 4) -> list[date]:
    """Next ``count`` Thursdays from ``after`` (weekly NSE expiries)."""
    days_ahead = (3 - after.weekday()) % 7  # Thursday = weekday 3
    first = after + timedelta(days=days_ahead)
    if days_ahead == 0:
        first = after
    return [first + timedelta(weeks=weeks + i) for i in range(count)]


def monthly_expiries(after: date, months: int = 3) -> list[date]:
    """Last Thursday of the next ``months`` months (excluding the current one)."""
    out: list[date] = []
    year, month = after.year, after.month
    for _ in range(months):
        month += 1
        if month > 12:
            month = 1
            year += 1
        # last day of `month` = first day of the following month minus one
        if month == 12:
            last_day = date(year, 12, 31)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)
        while last_day.weekday() != 3:
            last_day -= timedelta(days=1)
        out.append(last_day)
    return out


def expiry_dates(today: date | None = None, count: int = 6) -> list[date]:
    """Stage 3 expiry ladder: next weekly Thursdays, monthly last-Thursdays
    merged, deduplicated, ascending, ``count`` entries."""
    today = today or date.today()
    weeks = next_weekly_expiry(today, count=count)
    months = monthly_expiries(today, months=3)
    merged = sorted(set(weeks) | set(months))
    return merged[:count]


def years_to_expiry(expiry: date, today: date | None = None) -> float:
    """Fractional years to 15:30 IST expiry.

    Expiry day itself keeps the intraday remainder (~5.75 trading hours);
    strictly past dates are worth 0.
    """
    today = today or date.today()
    days = (expiry - today).days
    if days < 0:
        return 0.0
    fraction = days + ((15.5 - 9.75) / 24.0 if days == 0 else 0.0)
    return fraction / 365.0
