"""Phase D tests: Theta & Expected Move Analysis.

Tests for expected move calculation, theta decay, and trade-off assessment.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.expected_move_analyzer import ExpectedMoveAnalyzer, ExpectedMoveAnalysis
from providers.theta_decay_calculator import ThetaDecayCalculator


class TestExpectedMoveAnalyzer:
    """Test expected move and theta analysis."""

    def setup_method(self):
        self.analyzer = ExpectedMoveAnalyzer(config={})
        self.decay_calc = ThetaDecayCalculator()

    def test_expected_move_increases_with_vol(self):
        """Expected move increases with IV."""
        low_iv = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.10, option_type="CALL",
            current_premium=300, theta_daily=-0.3, delta=0.5, vega=50,
        )
        high_iv = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.30, option_type="CALL",
            current_premium=800, theta_daily=-0.8, delta=0.5, vega=150,
        )
        assert high_iv.expected_move_1std > low_iv.expected_move_1std

    def test_expected_move_increases_with_time(self):
        """Expected move increases with days to expiry."""
        short_dte = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=5,
            implied_vol=0.18, option_type="CALL",
            current_premium=200, theta_daily=-1.0, delta=0.5, vega=50,
        )
        long_dte = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.18, option_type="CALL",
            current_premium=550, theta_daily=-0.5, delta=0.5, vega=100,
        )
        assert long_dte.expected_move_1std > short_dte.expected_move_1std

    def test_atm_vs_otm_assessment(self):
        """ATM options have more favorable ratio than far OTM."""
        atm = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.18, option_type="CALL",
            current_premium=550, theta_daily=-0.5, delta=0.5, vega=100,
        )
        otm = self.analyzer.analyze_contract(
            strike=25000, spot=24600, days_to_expiry=30,
            implied_vol=0.18, option_type="CALL",
            current_premium=100, theta_daily=-0.2, delta=0.15, vega=30,
        )
        assert atm.pct_of_premium < otm.pct_of_premium * 2

    def test_theta_accelerates_near_expiry(self):
        """Theta accelerates near expiry."""
        schedule_30d = self.decay_calc.calculate_decay_schedule(
            original_premium=550, days_to_expiry=30,
            theta_daily_now=0.5, option_type="CALL",
        )
        schedule_2d = self.decay_calc.calculate_decay_schedule(
            original_premium=100, days_to_expiry=2,
            theta_daily_now=2.0, option_type="CALL",
        )
        assert schedule_2d[-1].theta_daily > schedule_2d[0].theta_daily

    def test_premium_required_zero_for_expired(self):
        """Expired option has EXPIRED assessment."""
        analysis = self.analyzer.analyze_contract(
            strike=24600, spot=24700, days_to_expiry=0,
            implied_vol=0.0, option_type="CALL",
            current_premium=100, theta_daily=0.0, delta=1.0, vega=0.0,
        )
        assert analysis.days_to_expiry == 0
        assert analysis.assessment == "EXPIRED"

    def test_favorable_assessment_for_high_expected_move(self):
        """FAVORABLE when expected move >> premium required."""
        analysis = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.20, option_type="CALL",
            current_premium=500, theta_daily=-0.2, delta=0.5, vega=50,
        )
        assert analysis.assessment == "FAVORABLE"

    def test_unfavorable_assessment_for_low_expected_move(self):
        """UNFAVORABLE when expected move << premium required."""
        analysis = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=1,
            implied_vol=0.05, option_type="CALL",
            current_premium=50, theta_daily=-70.0, delta=0.5, vega=10,
        )
        assert analysis.assessment == "UNFAVORABLE"

    def test_warnings_for_extreme_iv(self):
        """Warnings generated for extreme IV."""
        low_iv = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.05, option_type="CALL",
            current_premium=100, theta_daily=-0.5, delta=0.5, vega=30,
        )
        high_iv = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=30,
            implied_vol=0.60, option_type="CALL",
            current_premium=800, theta_daily=-1.0, delta=0.5, vega=200,
        )
        assert len(low_iv.warnings) > 0
        assert len(high_iv.warnings) > 0

    def test_warnings_for_near_expiry(self):
        """Warnings for options near expiry."""
        analysis = self.analyzer.analyze_contract(
            strike=24600, spot=24600, days_to_expiry=1,
            implied_vol=0.18, option_type="CALL",
            current_premium=50, theta_daily=-5.0, delta=0.5, vega=10,
        )
        assert any("expiry" in w.lower() for w in analysis.warnings)
