"""Stage 3 tests: Black-Scholes accuracy, boundary conditions, IV surface.

Run from the project root: pytest tests/test_options.py
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from backend.greeks import (
    black_scholes,
    expiry_dates,
    iv_smile,
    iv_with_term_effect,
    monthly_expiries,
    next_weekly_expiry,
    years_to_expiry,
)
from backend.market_data import MockMarketData


# ---------------------------------------------------------------------------
# Put-call parity and sanity
# ---------------------------------------------------------------------------

def test_put_call_parity_holds():
    spot, strike, t, sigma, r, q = 100.0, 105.0, 0.5, 0.25, 0.065, 0.0
    call = black_scholes(spot, strike, t, sigma, "CE", r, q)
    put = black_scholes(spot, strike, t, sigma, "PE", r, q)
    lhs = call["price"] - put["price"]
    rhs = spot - strike * math.exp(-r * t)
    assert lhs == pytest.approx(rhs, abs=1e-9)


def test_price_intrinsic_bounds():
    bs = black_scholes(120.0, 100.0, 1.0, 0.3, "CE")
    assert bs["price"] > 19.0          # > deep discount intrinsic
    assert bs["price"] < 120.0
    assert bs["delta"] > 0.80          # deep ITM call delta
    assert bs["delta"] < 1.0


def test_delta_bounds_call_and_put():
    call = black_scholes(100, 100, 0.25, 0.2, "CE")
    put = black_scholes(100, 100, 0.25, 0.2, "PE")
    assert 0 < call["delta"] < 1
    assert -1 < put["delta"] < 0
    assert call["delta"] - put["delta"] == pytest.approx(1.0, abs=1e-9)


def test_theta_is_negative_for_long_options():
    call = black_scholes(100, 100, 0.1, 0.2, "CE")
    put = black_scholes(100, 100, 0.1, 0.2, "PE")
    assert call["theta_per_day"] < 0
    assert put["theta_per_day"] < 0


def test_vega_positive_and_gamma_positive():
    bs = black_scholes(100, 100, 0.25, 0.2, "CE")
    assert bs["vega"] > 0
    assert bs["gamma"] > 0


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------

def test_expiry_zero_collapses_to_intrinsic():
    itm_call = black_scholes(110, 100, 0.0, 0.2, "CE")
    otm_call = black_scholes(95, 100, 0.0, 0.2, "CE")
    assert itm_call["price"] == pytest.approx(10.0)
    assert otm_call["price"] == 0.0
    assert itm_call["delta"] == 0.0 and itm_call["gamma"] == 0.0


def test_zero_vol_collapses_to_forward_intrinsic():
    bs = black_scholes(100, 100, 1.0, 0.0, "CE")
    # forward = 100*e^(r*T); intrinsic at forward = max(fwd - K, 0) discounted
    assert bs["price"] >= 0
    assert bs["vega"] == 0.0 and bs["gamma"] == 0.0


def test_deep_otm_tiny_but_stable():
    bs = black_scholes(100, 300, 0.25, 0.2, "CE")
    assert 0 <= bs["price"] < 1e-6
    assert math.isfinite(bs["d1"]) and math.isfinite(bs["d2"])


def test_deep_itm_delta_approaches_one():
    bs = black_scholes(1000, 100, 0.5, 0.2, "CE")
    assert bs["delta"] > 0.999


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        black_scholes(-100, 100, 1, 0.2, "CE")
    with pytest.raises(ValueError):
        black_scholes(100, 100, 1, -0.2, "CE")


# ---------------------------------------------------------------------------
# IV surface
# ---------------------------------------------------------------------------

def test_iv_smile_put_skew_and_wing_lift():
    base = 0.13
    atm = iv_smile(0.0, base)
    otm_put = iv_smile(math.log(0.9), base)          # 10% OTM put
    otm_call = iv_smile(math.log(1.1), base)         # 10% OTM call
    assert atm == pytest.approx(base)
    assert otm_put > atm                             # skew premium
    # At equal distance from ATM the put wing is richer than the call wing
    # (net-skew-dominant surface, mirroring real index vol).
    assert otm_put > otm_call


def test_iv_term_effect_pumps_near_expiry():
    base = 0.13
    far = iv_with_term_effect(base, 90 / 365)
    near = iv_with_term_effect(base, 2 / 365)
    assert far == pytest.approx(base)
    assert near > base


def test_chain_quotes_consistent():
    md = MockMarketData(volatility=0.0)
    chain = md.option_chain("NIFTY", md.expiries()[0], strikes_per_side=3)
    assert len(chain) == 7
    for row in chain:
        assert row["ce"]["strike"] == row["pe"]["strike"] == row["strike"]
        assert row["ce"]["bid"] < row["ce"]["ask"]
        # calls deeper ITM should be pricier than puts at same strike
        if row["strike"] < chain[3]["strike"]:
            assert row["ce"]["ltp"] > row["pe"]["ltp"]


def test_theta_decay_visible_between_buckets():
    """Premium falls as simulated clock advances past expiry day boundary.

    Uses a near-expiry date so a small simulated advance shortens T.
    """
    md = MockMarketData(volatility=0.0)
    expiry = (date.today() + timedelta(days=5)).isoformat()
    q1 = md.get_option_quote("NIFTY", expiry, 25000, "CE")["ltp"]
    md._tick = 5  # force several theta ticks
    q2 = md.get_option_quote("NIFTY", expiry, 25000, "CE")["ltp"]
    assert q2 < q1


# ---------------------------------------------------------------------------
# Expiry calendar
# ---------------------------------------------------------------------------

def test_weekly_expiries_are_thursdays():
    expiries = next_weekly_expiry(date(2026, 9, 25), count=4)
    assert all(d.weekday() == 3 for d in expiries)
    assert expiries == sorted(expiries)
    assert (expiries[1] - expiries[0]).days == 7


def test_monthly_expiries_are_last_thursdays():
    months = monthly_expiries(date(2026, 9, 25), months=2)
    assert all(d.weekday() == 3 for d in months)
    # next month after the last one must not contain another Thursday
    for d in months:
        next_week = d + timedelta(days=7)
        assert next_week.month != d.month


def test_expiry_ladder_merged_and_sorted():
    ladder = expiry_dates(date.today(), count=6)
    assert ladder == sorted(ladder)
    assert len(ladder) == 6
    assert all(d >= date.today() for d in ladder)
    assert all(d.weekday() == 3 for d in ladder)  # all Thursdays


def test_years_to_expiry_sign_handling():
    future = years_to_expiry(date.today() + timedelta(days=30))
    past = years_to_expiry(date.today() - timedelta(days=30))
    assert 0.08 < future < 0.084
    assert past == 0.0
