"""Phase E tests: Option Suitability Scoring & Recommendation Engine.

Tests for 5-dimension suitability scoring, ranking, and recommendations.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from providers.suitability_calculator import SuitabilityCalculator, SuitabilityScore
from providers.contract_ranker import ContractRanker
from providers.expected_move_analyzer import ExpectedMoveAnalysis
from providers.greeks_calculator import GreeksResult


class TestSuitabilityCalculator:
    """Test suitability scoring."""

    def setup_method(self):
        self.calc = SuitabilityCalculator(config={})
        self.ranker = ContractRanker()

    def test_bullish_call_scores_high(self):
        """CALL in bullish market scores high."""
        score = self.calc.calculate_suitability(
            strike=24600, spot=24600, option_type="CALL",
            days_to_expiry=30, expiry_date="2026-10-23",
            current_premium=550, implied_vol=0.18,
            greeks_result=GreeksResult(
                strike=24600, spot=24600, expiry_date="2026-10-23", days_to_expiry=30,
                time_to_expiry=30/365, option_type="CALL", premium=550, implied_vol=0.18,
                delta=0.50, gamma=0.002, theta=-0.5, vega=100, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=550, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=24600, spot=24600, days_to_expiry=30, implied_vol=0.18,
                option_type="CALL", expected_move_1std=1000, expected_move_2std=2000,
                theta_daily=-0.5, theta_total=-15, theta_pct_of_premium=2.7,
                current_premium=550, delta=0.50,
                premium_required_to_breakeven=15, pct_of_premium=2.7,
                assessment="FAVORABLE", confidence="HIGH",
                evidence=[], warnings=[],
            ),
            market_view="BULLISH",
            holding_period_days=30,
            risk_tolerance="MEDIUM",
        )
        assert score.direction_fit >= 80
        assert score.recommendation in ["BUY", "ACCEPT"]

    def test_put_scores_low_in_bullish(self):
        """PUT in bullish market scores low."""
        score = self.calc.calculate_suitability(
            strike=24600, spot=24600, option_type="PUT",
            days_to_expiry=30, expiry_date="2026-10-23",
            current_premium=500, implied_vol=0.18,
            greeks_result=GreeksResult(
                strike=24600, spot=24600, expiry_date="2026-10-23", days_to_expiry=30,
                time_to_expiry=30/365, option_type="PUT", premium=500, implied_vol=0.18,
                delta=-0.50, gamma=0.002, theta=-0.5, vega=100, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=500, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=24600, spot=24600, days_to_expiry=30, implied_vol=0.18,
                option_type="PUT", expected_move_1std=1000, expected_move_2std=2000,
                theta_daily=-0.5, theta_total=-15, theta_pct_of_premium=3.0,
                current_premium=500, delta=-0.50,
                premium_required_to_breakeven=15, pct_of_premium=3.0,
                assessment="FAVORABLE", confidence="HIGH",
                evidence=[], warnings=[],
            ),
            market_view="BULLISH",
            holding_period_days=30,
            risk_tolerance="MEDIUM",
        )
        assert score.direction_fit < 50

    def test_atm_scores_better_than_far_otm(self):
        """ATM scores better than far OTM."""
        atm = self.calc.calculate_suitability(
            strike=24600, spot=24600, option_type="CALL",
            days_to_expiry=30, expiry_date="2026-10-23",
            current_premium=550, implied_vol=0.18,
            greeks_result=GreeksResult(
                strike=24600, spot=24600, expiry_date="2026-10-23", days_to_expiry=30,
                time_to_expiry=30/365, option_type="CALL", premium=550, implied_vol=0.18,
                delta=0.50, gamma=0.002, theta=-0.5, vega=100, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=550, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=24600, spot=24600, days_to_expiry=30, implied_vol=0.18,
                option_type="CALL", expected_move_1std=1000, expected_move_2std=2000,
                theta_daily=-0.5, theta_total=-15, theta_pct_of_premium=2.7,
                current_premium=550, delta=0.50,
                premium_required_to_breakeven=15, pct_of_premium=2.7,
                assessment="FAVORABLE", confidence="HIGH",
                evidence=[], warnings=[],
            ),
            market_view="BULLISH",
        )

        far_otm = self.calc.calculate_suitability(
            strike=25000, spot=24600, option_type="CALL",
            days_to_expiry=30, expiry_date="2026-10-23",
            current_premium=100, implied_vol=0.18,
            greeks_result=GreeksResult(
                strike=25000, spot=24600, expiry_date="2026-10-23", days_to_expiry=30,
                time_to_expiry=30/365, option_type="CALL", premium=100, implied_vol=0.18,
                delta=0.15, gamma=0.001, theta=-0.2, vega=30, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=100, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=25000, spot=24600, days_to_expiry=30, implied_vol=0.18,
                option_type="CALL", expected_move_1std=1000, expected_move_2std=2000,
                theta_daily=-0.2, theta_total=-6, theta_pct_of_premium=6.0,
                current_premium=100, delta=0.15,
                premium_required_to_breakeven=6, pct_of_premium=6.0,
                assessment="FAVORABLE", confidence="HIGH",
                evidence=[], warnings=[],
            ),
            market_view="BULLISH",
        )

        assert atm.overall_score > far_otm.overall_score

    def test_recommendation_matches_score(self):
        """Recommendation maps correctly to score."""
        high_score = self.calc.calculate_suitability(
            strike=24600, spot=24600, option_type="CALL",
            days_to_expiry=30, expiry_date="2026-10-23",
            current_premium=550, implied_vol=0.18,
            greeks_result=GreeksResult(
                strike=24600, spot=24600, expiry_date="2026-10-23", days_to_expiry=30,
                time_to_expiry=30/365, option_type="CALL", premium=550, implied_vol=0.18,
                delta=0.50, gamma=0.002, theta=-0.5, vega=100, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=550, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=24600, spot=24600, days_to_expiry=30, implied_vol=0.18,
                option_type="CALL", expected_move_1std=1000, expected_move_2std=2000,
                theta_daily=-0.5, theta_total=-15, theta_pct_of_premium=2.7,
                current_premium=550, delta=0.50,
                premium_required_to_breakeven=15, pct_of_premium=2.7,
                assessment="FAVORABLE", confidence="HIGH",
                evidence=[], warnings=[],
            ),
            market_view="BULLISH",
        )

        low_score = self.calc.calculate_suitability(
            strike=25500, spot=24600, option_type="CALL",
            days_to_expiry=1, expiry_date="2026-09-24",
            current_premium=30, implied_vol=0.05,
            greeks_result=GreeksResult(
                strike=25500, spot=24600, expiry_date="2026-09-24", days_to_expiry=1,
                time_to_expiry=1/365, option_type="CALL", premium=30, implied_vol=0.05,
                delta=0.05, gamma=0.01, theta=-100.0, vega=5, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=30, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=25500, spot=24600, days_to_expiry=1, implied_vol=0.05,
                option_type="CALL", expected_move_1std=64, expected_move_2std=128,
                theta_daily=-100.0, theta_total=-100, theta_pct_of_premium=333.0,
                current_premium=30, delta=0.05,
                premium_required_to_breakeven=100, pct_of_premium=333.0,
                assessment="HIGHLY_UNFAVORABLE", confidence="LOW",
                evidence=[], warnings=[],
            ),
            market_view="NEUTRAL",
        )

        assert high_score.recommendation in ["BUY", "ACCEPT"]
        assert low_score.recommendation in ["AVOID", "DO_NOT_TRADE"]

    def test_ranking_orders_by_score(self):
        """Ranking sorts by overall score."""
        scores = [
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=80, theta_efficiency=80, time_alignment=80, risk_management=80, liquidity=80, overall_score=85, recommendation="BUY", confidence="HIGH", evidence=[], warnings=[]),
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=60, theta_efficiency=60, time_alignment=60, risk_management=60, liquidity=60, overall_score=55, recommendation="CAUTION", confidence="MEDIUM", evidence=[], warnings=[]),
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=70, theta_efficiency=70, time_alignment=70, risk_management=70, liquidity=70, overall_score=75, recommendation="ACCEPT", confidence="HIGH", evidence=[], warnings=[]),
        ]

        ranked = self.ranker.rank_contracts(scores)
        assert ranked[0].overall_score == 85
        assert ranked[1].overall_score == 75
        assert ranked[2].overall_score == 55

    def test_grouping_by_recommendation(self):
        """Grouping contracts by recommendation."""
        scores = [
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=80, theta_efficiency=80, time_alignment=80, risk_management=80, liquidity=80, overall_score=85, recommendation="BUY", confidence="HIGH", evidence=[], warnings=[]),
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=60, theta_efficiency=60, time_alignment=60, risk_management=60, liquidity=60, overall_score=70, recommendation="ACCEPT", confidence="HIGH", evidence=[], warnings=[]),
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=50, theta_efficiency=50, time_alignment=50, risk_management=50, liquidity=50, overall_score=50, recommendation="CAUTION", confidence="MEDIUM", evidence=[], warnings=[]),
            SuitabilityScore(strike=24600, spot=24600, option_type="CALL", days_to_expiry=30, expiry_date="2026-10-23", direction_fit=30, theta_efficiency=30, time_alignment=30, risk_management=30, liquidity=30, overall_score=30, recommendation="AVOID", confidence="LOW", evidence=[], warnings=[]),
        ]

        grouped = self.ranker.group_by_recommendation(scores)
        assert len(grouped["BUY"]) == 1
        assert len(grouped["ACCEPT"]) == 1
        assert len(grouped["CAUTION"]) == 1
        assert len(grouped["AVOID"]) == 1

    def test_profit_1std_positive_for_bullish_call(self):
        """1-STD profit is positive for bullish CALL with large expected move."""
        score = self.calc.calculate_suitability(
            strike=24600, spot=24600, option_type="CALL",
            days_to_expiry=30, expiry_date="2026-10-23",
            current_premium=550, implied_vol=0.30,
            greeks_result=GreeksResult(
                strike=24600, spot=24600, expiry_date="2026-10-23", days_to_expiry=30,
                time_to_expiry=30/365, option_type="CALL", premium=550, implied_vol=0.30,
                delta=0.50, gamma=0.002, theta=-0.5, vega=100, rho=0.0,
                risk_free_rate=0.065, dividend_yield=0.0,
                model_price=550, price_error=0, price_error_pct=0, model_fit="GOOD",
                warnings=[],
            ),
            expected_move_analysis=ExpectedMoveAnalysis(
                strike=24600, spot=24600, days_to_expiry=30, implied_vol=0.30,
                option_type="CALL", expected_move_1std=1500, expected_move_2std=3000,
                theta_daily=-0.5, theta_total=-15, theta_pct_of_premium=2.7,
                current_premium=550, delta=0.50,
                premium_required_to_breakeven=15, pct_of_premium=2.7,
                assessment="FAVORABLE", confidence="HIGH",
                evidence=[], warnings=[],
            ),
            market_view="BULLISH",
        )
        assert score.scenario_profit_1std > 0
