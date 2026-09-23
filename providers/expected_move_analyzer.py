"""Expected move analysis based on implied volatility and Greeks.

Analyzes theta decay vs expected market move to provide trade-off insights.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ExpectedMoveAnalysis:
    """Analysis of expected move vs theta decay for a single contract."""
    strike: float
    spot: float
    days_to_expiry: int
    implied_vol: float
    option_type: str
    expected_move_1std: float
    expected_move_2std: float
    theta_daily: float
    theta_total: float
    theta_pct_of_premium: float
    current_premium: float
    delta: float
    premium_required_to_breakeven: float
    pct_of_premium: float
    assessment: str
    confidence: str
    evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class ExpectedMoveAnalyzer:
    """Analyze expected move vs theta decay."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    def analyze_contract(
        self,
        strike: float,
        spot: float,
        days_to_expiry: int,
        implied_vol: float,
        option_type: str,
        current_premium: float,
        theta_daily: float,
        delta: float,
        vega: float,
    ) -> ExpectedMoveAnalysis:
        """Analyze expected move vs decay for a single contract."""
        if days_to_expiry <= 0:
            return self._handle_expired(strike, spot, option_type)

        expected_move_1std = self._calculate_expected_move(spot, implied_vol, days_to_expiry, 1)
        expected_move_2std = self._calculate_expected_move(spot, implied_vol, days_to_expiry, 2)

        theta_total = theta_daily * days_to_expiry
        theta_pct = (abs(theta_total) / current_premium * 100) if current_premium > 0 else 0.0

        # 3. Calculate IV impact
        # Vega tells us: if IV drops 1%, premium loses vega_per_1pct
        # We report this separately; premium required focuses on theta
        iv_impact = vega * (0.30 * 100) * (-1)  # Negative (loss)

        # 4. Premium required to break even
        # Focus on theta: the minimum move to overcome time decay
        premium_required = abs(theta_total)
        premium_required_pct = (premium_required / current_premium * 100) if current_premium > 0 else 0.0

        assessment, confidence = self._assess_suitability(
            spot=spot,
            strike=strike,
            days_to_expiry=days_to_expiry,
            expected_move_1std=expected_move_1std,
            premium_required=premium_required,
            current_premium=current_premium,
            implied_vol=implied_vol,
            theta_pct=theta_pct,
            delta=delta,
            option_type=option_type,
        )

        evidence = self._generate_evidence(
            spot=spot,
            strike=strike,
            expected_move_1std=expected_move_1std,
            expected_move_2std=expected_move_2std,
            theta_daily=theta_daily,
            theta_total=theta_total,
            premium_required=premium_required,
            current_premium=current_premium,
            implied_vol=implied_vol,
            assessment=assessment,
        )

        warnings = self._generate_warnings(
            days_to_expiry=days_to_expiry,
            theta_daily=theta_daily,
            current_premium=current_premium,
            implied_vol=implied_vol,
            premium_required_pct=premium_required_pct,
        )

        return ExpectedMoveAnalysis(
            strike=strike,
            spot=spot,
            days_to_expiry=days_to_expiry,
            implied_vol=implied_vol,
            option_type=option_type,
            expected_move_1std=expected_move_1std,
            expected_move_2std=expected_move_2std,
            theta_daily=theta_daily,
            theta_total=theta_total,
            theta_pct_of_premium=theta_pct,
            current_premium=current_premium,
            delta=delta,
            premium_required_to_breakeven=premium_required,
            pct_of_premium=premium_required_pct,
            assessment=assessment,
            confidence=confidence,
            evidence=evidence,
            warnings=warnings,
        )

    def _calculate_expected_move(
        self, spot: float, iv: float, days_to_expiry: int, std_dev: int = 1
    ) -> float:
        """Calculate expected move from IV."""
        time_fraction = days_to_expiry / 365.0
        move_pct = iv * np.sqrt(time_fraction) * std_dev
        return spot * move_pct

    def _assess_suitability(
        self,
        spot: float,
        strike: float,
        days_to_expiry: int,
        expected_move_1std: float,
        premium_required: float,
        current_premium: float,
        implied_vol: float,
        theta_pct: float,
        delta: float,
        option_type: str,
    ) -> tuple[str, str]:
        """Assess whether this contract is favorable to trade."""
        if days_to_expiry <= 0:
            return "EXPIRED", "HIGH"
        if current_premium <= 0:
            return "INSUFFICIENT_DATA", "LOW"

        breakeven_move = premium_required
        move_vs_expected = breakeven_move / expected_move_1std if expected_move_1std > 0 else 0
        distance_to_strike = abs(spot - strike)
        moneyness = distance_to_strike / spot

        if days_to_expiry < 2:
            confidence = "LOW"
        elif days_to_expiry < 7:
            confidence = "MEDIUM"
        else:
            confidence = "HIGH"

        if move_vs_expected < 0.5:
            assessment = "FAVORABLE"
        elif move_vs_expected < 1.0:
            assessment = "NEUTRAL"
        elif move_vs_expected < 1.5:
            assessment = "UNFAVORABLE"
        else:
            assessment = "HIGHLY_UNFAVORABLE"

        if moneyness > 0.05:
            if assessment == "FAVORABLE":
                assessment = "NEUTRAL"
            elif assessment == "NEUTRAL":
                assessment = "UNFAVORABLE"

        return assessment, confidence

    def _generate_evidence(
        self,
        spot: float,
        strike: float,
        expected_move_1std: float,
        expected_move_2std: float,
        theta_daily: float,
        theta_total: float,
        premium_required: float,
        current_premium: float,
        implied_vol: float,
        assessment: str,
    ) -> List[str]:
        """Generate human-readable evidence."""
        evidence = []
        evidence.append(
            f"Expected move (1 STD): ±{expected_move_1std:.0f} points ({expected_move_1std / spot * 100:.2f}% of spot)"
        )
        evidence.append(
            f"Expected move (2 STD): ±{expected_move_2std:.0f} points"
        )
        evidence.append(
            f"Theta decay: ₹{abs(theta_daily):.2f}/day, ₹{abs(theta_total):.0f} total until expiry"
        )
        evidence.append(
            f"Premium required to break even: ₹{premium_required:.0f} ({premium_required / current_premium * 100:.1f}% of current premium)"
        )
        if implied_vol < 0.10:
            evidence.append("⚠️ Very low implied volatility: option underpriced")
        elif implied_vol > 0.30:
            evidence.append("⚠️ Very high implied volatility: option expensive")
        if assessment == "FAVORABLE":
            evidence.append("✅ Expected move well exceeds premium required → profitable if move happens")
        elif assessment == "NEUTRAL":
            evidence.append("→ Break-even requires moderate move; fair risk-reward")
        elif assessment == "UNFAVORABLE":
            evidence.append("⚠️ Break-even requires large move; unfavorable odds")
        else:
            evidence.append("🔴 Break-even requires unrealistic move; avoid")
        return evidence

    def _generate_warnings(
        self,
        days_to_expiry: int,
        theta_daily: float,
        current_premium: float,
        implied_vol: float,
        premium_required_pct: float,
    ) -> List[str]:
        """Generate risk warnings."""
        warnings = []
        if days_to_expiry <= 2:
            warnings.append("⚠️ Option near expiry: theta accelerating, Greeks unstable")
        if days_to_expiry <= 1:
            warnings.append("🔴 Option expiring very soon: extreme gamma risk")
        if current_premium > 0 and abs(theta_daily) > current_premium * 0.10:
            warnings.append("High theta: losing >10% of premium daily to time decay")
        if implied_vol < 0.08:
            warnings.append("Very low IV: premium will decompress; buyer loses")
        if implied_vol > 0.40:
            warnings.append("Extreme IV: premium will collapse; buyer exposed")
        if premium_required_pct > 50:
            warnings.append("Premium required exceeds 50% of current premium: difficult odds")
        return warnings

    def _handle_expired(
        self, strike: float, spot: float, option_type: str
    ) -> ExpectedMoveAnalysis:
        """Handle expired or expiring-today option."""
        intrinsic = max(spot - strike, 0) if option_type == "CALL" else max(strike - spot, 0)
        return ExpectedMoveAnalysis(
            strike=strike,
            spot=spot,
            days_to_expiry=0,
            implied_vol=0.0,
            option_type=option_type,
            expected_move_1std=0.0,
            expected_move_2std=0.0,
            theta_daily=0.0,
            theta_total=0.0,
            theta_pct_of_premium=0.0,
            current_premium=intrinsic,
            delta=1.0 if intrinsic > 0 else 0.0,
            premium_required_to_breakeven=0.0,
            pct_of_premium=0.0,
            assessment="EXPIRED",
            confidence="HIGH",
            evidence=[f"Option expired; intrinsic value = {intrinsic:.0f}"],
            warnings=["Option has expired or is expiring today"],
        )
