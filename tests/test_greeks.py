"""Phase C tests: Greeks Calculation & Option Pricing Model.

Tests for Black-Scholes Greeks calculation, validation, and edge cases.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from providers.greeks_calculator import GreeksCalculator, GreeksResult


class TestGreeksCalculator:
    """Test Black-Scholes Greeks calculation."""

    def setup_method(self):
        self.calculator = GreeksCalculator(risk_free_rate=0.065, dividend_yield=0.0)

    def test_call_delta_atm(self):
        """ATM call delta near 0.5."""
        result = self.calculator.calculate_greeks(
            spot=24600.0,
            strike=24600.0,
            days_to_expiry=30,
            implied_vol=0.18,
            market_premium=576.0,
            option_type="CALL",
            expiry_date="2026-10-23",
        )
        assert 0.4 < result.delta < 0.6

    def test_put_delta_atm(self):
        """ATM put delta near -0.5."""
        result = self.calculator.calculate_greeks(
            spot=24600.0,
            strike=24600.0,
            days_to_expiry=30,
            implied_vol=0.18,
            market_premium=444.0,
            option_type="PUT",
            expiry_date="2026-10-23",
        )
        assert -0.6 < result.delta < -0.4

    def test_otm_call_delta_decreases(self):
        """OTM call has lower delta than ATM call."""
        atm = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        otm = self.calculator.calculate_greeks(
            spot=24600.0, strike=25200.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=335.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        assert otm.delta < atm.delta

    def test_gamma_highest_atm(self):
        """ATM has highest gamma; OTM and ITM have lower gamma."""
        atm = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        otm = self.calculator.calculate_greeks(
            spot=24600.0, strike=25200.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=335.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        itm = self.calculator.calculate_greeks(
            spot=24600.0, strike=24000.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=953.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        assert atm.gamma > otm.gamma
        assert atm.gamma > itm.gamma

    def test_theta_negative_for_long_call(self):
        """Theta negative for long call."""
        result = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        assert result.theta < 0

    def test_theta_accelerates_near_expiry(self):
        """|theta| increases as expiry approaches."""
        r30 = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        r1 = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=1,
            implied_vol=0.18, market_premium=90.0,
            option_type="CALL", expiry_date="2026-09-24",
        )
        assert abs(r1.theta) > abs(r30.theta)

    def test_vega_positive(self):
        """Vega positive for calls and puts."""
        call_r = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        put_r = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=444.0,
            option_type="PUT", expiry_date="2026-10-23",
        )
        assert call_r.vega > 0
        assert put_r.vega > 0

    def test_model_fit_good_for_atm_liquid(self):
        """Model fit is GOOD or ACCEPTABLE for reasonable ATM premium."""
        result = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        assert result.model_fit in ("GOOD", "ACCEPTABLE")

    def test_expiry_today_delta_binary(self):
        """Delta is 0 or 1 on expiry day."""
        otm = self.calculator.calculate_greeks(
            spot=24600.0, strike=24700.0, days_to_expiry=0,
            implied_vol=0.0, market_premium=0.0,
            option_type="CALL", expiry_date="2026-09-23",
        )
        itm = self.calculator.calculate_greeks(
            spot=24600.0, strike=24500.0, days_to_expiry=0,
            implied_vol=0.0, market_premium=100.0,
            option_type="CALL", expiry_date="2026-09-23",
        )
        assert otm.delta in (0.0, 1.0)
        assert itm.delta in (0.0, 1.0)

    def test_call_put_parity_delta(self):
        """delta_call - delta_put ≈ 1."""
        call_r = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        put_r = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=444.0,
            option_type="PUT", expiry_date="2026-10-23",
        )
        assert abs((call_r.delta - put_r.delta) - 1.0) < 0.1

    def test_greeks_result_has_required_fields(self):
        """GreeksResult has all required fields."""
        result = self.calculator.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        assert hasattr(result, "strike")
        assert hasattr(result, "spot")
        assert hasattr(result, "expiry_date")
        assert hasattr(result, "days_to_expiry")
        assert hasattr(result, "time_to_expiry")
        assert hasattr(result, "option_type")
        assert hasattr(result, "premium")
        assert hasattr(result, "implied_vol")
        assert hasattr(result, "delta")
        assert hasattr(result, "gamma")
        assert hasattr(result, "theta")
        assert hasattr(result, "vega")
        assert hasattr(result, "rho")
        assert hasattr(result, "risk_free_rate")
        assert hasattr(result, "dividend_yield")
        assert hasattr(result, "model_price")
        assert hasattr(result, "price_error")
        assert hasattr(result, "price_error_pct")
        assert hasattr(result, "model_fit")
        assert hasattr(result, "warnings")
        assert isinstance(result.warnings, list)

    def test_calculator_defaults(self):
        """Calculator uses default risk-free rate and dividend yield."""
        calc = GreeksCalculator()
        result = calc.calculate_greeks(
            spot=24600.0, strike=24600.0, days_to_expiry=30,
            implied_vol=0.18, market_premium=576.0,
            option_type="CALL", expiry_date="2026-10-23",
        )
        assert result.risk_free_rate == 0.065
        assert result.dividend_yield == 0.0
