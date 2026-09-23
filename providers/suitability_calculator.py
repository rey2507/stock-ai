"""Option suitability scoring engine.

Calculates 5-dimension suitability scores for option contracts
and generates trading recommendations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from providers.expected_move_analyzer import ExpectedMoveAnalysis
from providers.greeks_calculator import GreeksResult

logger = logging.getLogger(__name__)


@dataclass
class SuitabilityScore:
    """Detailed suitability assessment for an option contract."""
    strike: float
    spot: float
    option_type: str
    days_to_expiry: int
    expiry_date: str
    direction_fit: float
    theta_efficiency: float
    time_alignment: float
    risk_management: float
    liquidity: float
    overall_score: float
    recommendation: str
    confidence: str
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    scenario_profit_1std: float = 0.0
    scenario_loss_max: float = 0.0
    scenario_breakeven_move: float = 0.0


class SuitabilityCalculator:
    """Calculate option suitability score."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def calculate_suitability(
        self,
        strike: float,
        spot: float,
        option_type: str,
        days_to_expiry: int,
        expiry_date: str,
        current_premium: float,
        implied_vol: float,
        greeks_result: GreeksResult,
        expected_move_analysis: ExpectedMoveAnalysis,
        market_view: Optional[str] = None,
        holding_period_days: Optional[int] = None,
        risk_tolerance: str = "MEDIUM",
        existing_position: bool = False,
    ) -> SuitabilityScore:
        direction_fit = self._calculate_direction_fit(strike, spot, option_type, market_view)
        theta_efficiency = self._calculate_theta_efficiency(expected_move_analysis, current_premium)
        time_alignment = self._calculate_time_alignment(days_to_expiry, holding_period_days)
        risk_management = self._calculate_risk_management(strike, spot, option_type, current_premium, greeks_result, risk_tolerance)
        liquidity = self._calculate_liquidity(implied_vol, days_to_expiry, strike, spot)

        overall_score = float(np.mean([direction_fit, theta_efficiency, time_alignment, risk_management, liquidity]))
        recommendation = self._score_to_recommendation(overall_score)
        confidence = self._assess_confidence(days_to_expiry, implied_vol)

        evidence = self._generate_evidence(strike, spot, option_type, days_to_expiry, direction_fit, theta_efficiency, time_alignment, risk_management, liquidity, expected_move_analysis, market_view)
        warnings = self._generate_warnings(strike, spot, days_to_expiry, implied_vol, greeks_result, expected_move_analysis)

        scenario_profit_1std = self._calculate_profit_1std(option_type, strike, spot, expected_move_analysis, current_premium)
        scenario_loss_max = self._calculate_max_loss(option_type, strike, current_premium)
        scenario_breakeven_move = expected_move_analysis.premium_required_to_breakeven

        return SuitabilityScore(
            strike=strike,
            spot=spot,
            option_type=option_type,
            days_to_expiry=days_to_expiry,
            expiry_date=expiry_date,
            direction_fit=direction_fit,
            theta_efficiency=theta_efficiency,
            time_alignment=time_alignment,
            risk_management=risk_management,
            liquidity=liquidity,
            overall_score=overall_score,
            recommendation=recommendation,
            confidence=confidence,
            evidence=evidence,
            warnings=warnings,
            scenario_profit_1std=scenario_profit_1std,
            scenario_loss_max=scenario_loss_max,
            scenario_breakeven_move=scenario_breakeven_move,
        )

    def _calculate_direction_fit(self, strike: float, spot: float, option_type: str, market_view: Optional[str]) -> float:
        distance_to_strike = abs(spot - strike)
        moneyness = distance_to_strike / spot if spot > 0 else 0

        if not market_view or market_view == "NEUTRAL":
            if moneyness < 0.01:
                return 50.0
            elif moneyness < 0.03:
                return 40.0
            return 20.0

        if market_view == "BULLISH":
            if option_type == "CALL":
                if moneyness < 0.01:
                    return 85.0
                elif moneyness < 0.03:
                    return 75.0
                return 60.0
            else:
                if moneyness < 0.02:
                    return 30.0
                return 10.0

        if market_view == "BEARISH":
            if option_type == "PUT":
                if moneyness < 0.01:
                    return 85.0
                elif moneyness < 0.03:
                    return 75.0
                return 60.0
            else:
                if moneyness < 0.02:
                    return 30.0
                return 10.0

        return 50.0

    def _calculate_theta_efficiency(self, analysis: ExpectedMoveAnalysis, current_premium: float) -> float:
        if current_premium <= 0:
            return 0.0
        premium_required = analysis.premium_required_to_breakeven
        if premium_required <= 0:
            return 90.0
        move_vs_required = analysis.expected_move_1std / premium_required
        if move_vs_required >= 2.0:
            return 90.0
        elif move_vs_required >= 1.5:
            return 75.0
        elif move_vs_required >= 1.0:
            return 50.0
        elif move_vs_required >= 0.5:
            return 25.0
        return 10.0

    def _calculate_time_alignment(self, days_to_expiry: int, holding_period_days: Optional[int]) -> float:
        if holding_period_days is None:
            if days_to_expiry >= 15:
                return 60.0
            elif days_to_expiry >= 5:
                return 70.0
            return 40.0
        if abs(days_to_expiry - holding_period_days) <= 2:
            return 100.0
        if days_to_expiry < holding_period_days * 0.5:
            return 20.0
        if days_to_expiry > holding_period_days * 3:
            return 60.0
        return 75.0

    def _calculate_risk_management(self, strike: float, spot: float, option_type: str, current_premium: float, greeks_result: GreeksResult, risk_tolerance: str) -> float:
        base_score = 70.0
        delta_adjustment = abs(greeks_result.delta) * 10
        if risk_tolerance == "LOW":
            return max(20.0, base_score - delta_adjustment)
        elif risk_tolerance == "HIGH":
            return min(100.0, base_score + delta_adjustment)
        return base_score

    def _calculate_liquidity(self, implied_vol: float, days_to_expiry: int, strike: float, spot: float) -> float:
        moneyness = abs(spot - strike) / spot if spot > 0 else 0
        if moneyness < 0.01:
            base_score = 90.0
        elif moneyness < 0.03:
            base_score = 75.0
        elif moneyness < 0.05:
            base_score = 60.0
        else:
            base_score = 40.0
        if days_to_expiry < 3:
            base_score -= 20.0
        elif days_to_expiry > 30:
            base_score -= 10.0
        if implied_vol < 0.08 or implied_vol > 0.40:
            base_score -= 15.0
        return max(0.0, min(100.0, base_score))

    def _score_to_recommendation(self, score: float) -> str:
        if score >= 80:
            return "BUY"
        if score >= 60:
            return "ACCEPT"
        if score >= 40:
            return "CAUTION"
        if score >= 20:
            return "AVOID"
        return "DO_NOT_TRADE"

    def _assess_confidence(self, days_to_expiry: int, implied_vol: float) -> str:
        if days_to_expiry < 2:
            return "LOW"
        if implied_vol < 0.08 or implied_vol > 0.40:
            return "MEDIUM"
        return "HIGH"

    def _generate_evidence(self, strike: float, spot: float, option_type: str, days_to_expiry: int, direction_fit: float, theta_efficiency: float, time_alignment: float, risk_management: float, liquidity: float, expected_move_analysis: ExpectedMoveAnalysis, market_view: Optional[str]) -> List[str]:
        evidence = []
        if direction_fit >= 80:
            evidence.append(f"✅ Excellent direction alignment ({direction_fit:.0f}/100)")
        elif direction_fit >= 50:
            evidence.append(f"→ Acceptable direction fit ({direction_fit:.0f}/100)")
        else:
            evidence.append(f"⚠️ Poor direction alignment ({direction_fit:.0f}/100)")
        if theta_efficiency >= 80:
            evidence.append(f"✅ Expected move well exceeds decay ({theta_efficiency:.0f}/100)")
        elif theta_efficiency >= 50:
            evidence.append(f"→ Move adequate for decay ({theta_efficiency:.0f}/100)")
        else:
            evidence.append(f"⚠️ Decay risk high ({theta_efficiency:.0f}/100)")
        if time_alignment >= 80:
            evidence.append(f"✅ Expiry matches holding period ({time_alignment:.0f}/100)")
        else:
            evidence.append(f"→ Expiry alignment OK ({time_alignment:.0f}/100)")
        if risk_management >= 80:
            evidence.append(f"✅ Risk well-defined and manageable ({risk_management:.0f}/100)")
        else:
            evidence.append(f"→ Risk acceptable ({risk_management:.0f}/100)")
        if liquidity >= 80:
            evidence.append(f"✅ High liquidity, easy exits ({liquidity:.0f}/100)")
        elif liquidity >= 60:
            evidence.append(f"→ Reasonable liquidity ({liquidity:.0f}/100)")
        else:
            evidence.append(f"⚠️ Liquidity concerns ({liquidity:.0f}/100)")
        return evidence

    def _generate_warnings(self, strike: float, spot: float, days_to_expiry: int, implied_vol: float, greeks_result: GreeksResult, expected_move_analysis: ExpectedMoveAnalysis) -> List[str]:
        warnings = []
        if days_to_expiry <= 1:
            warnings.append("🔴 Option expiring very soon: extreme gamma risk")
        elif days_to_expiry <= 3:
            warnings.append("⚠️ Option near expiry: theta accelerating")
        if implied_vol < 0.08:
            warnings.append("⚠️ Very low IV: premium collapse risk")
        elif implied_vol > 0.40:
            warnings.append("⚠️ Extreme IV: reversion likely, premium loss risk")
        if expected_move_analysis.theta_pct_of_premium > 30:
            warnings.append("⚠️ High theta: losing > 30% of premium daily")
        if greeks_result.model_fit == "POOR":
            warnings.append("⚠️ Model-market divergence: Greeks may be unreliable")
        return warnings

    def _calculate_profit_1std(self, option_type: str, strike: float, spot: float, expected_move_analysis: ExpectedMoveAnalysis, current_premium: float) -> float:
        move = expected_move_analysis.expected_move_1std
        if option_type == "CALL":
            new_spot = spot + move
            intrinsic = max(new_spot - strike, 0)
        else:
            new_spot = spot - move
            intrinsic = max(strike - new_spot, 0)
        return max(0.0, intrinsic - current_premium)

    def _calculate_max_loss(self, option_type: str, strike: float, current_premium: float) -> float:
        return current_premium
