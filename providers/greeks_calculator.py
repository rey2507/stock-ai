"""Black-Scholes Greeks calculator.

Calculates Delta, Gamma, Theta, Vega, Rho for European options.
Validates model price against market price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from scipy.stats import norm

logger = logging.getLogger(__name__)


@dataclass
class GreeksResult:
    """Greeks for a single option contract."""
    strike: float
    spot: float
    expiry_date: str
    days_to_expiry: int
    time_to_expiry: float
    option_type: str
    premium: float
    implied_vol: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    risk_free_rate: float
    dividend_yield: float
    model_price: float
    price_error: float
    price_error_pct: float
    model_fit: str
    warnings: List[str] = field(default_factory=list)


class GreeksCalculator:
    """Black-Scholes Greeks calculator."""

    def __init__(self, risk_free_rate: float = 0.065, dividend_yield: float = 0.0):
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield

    def calculate_greeks(
        self,
        spot: float,
        strike: float,
        days_to_expiry: int,
        implied_vol: float,
        market_premium: float,
        option_type: str,
        expiry_date: str,
    ) -> GreeksResult:
        if spot <= 0 or strike <= 0:
            raise ValueError("Spot and strike must be positive")
        if days_to_expiry < 0:
            raise ValueError("Days to expiry cannot be negative")
        if implied_vol < 0:
            raise ValueError("Volatility cannot be negative")
        if option_type not in ("CALL", "PUT"):
            raise ValueError("option_type must be CALL or PUT")

        if days_to_expiry == 0:
            return self._handle_expiry_today(
                spot, strike, market_premium, option_type, expiry_date
            )

        T = days_to_expiry / 365.0
        sigma = implied_vol
        S = spot
        K = strike
        r = self.risk_free_rate
        q = self.dividend_yield

        sqrt_T = np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + sigma**2 / 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        N_d1 = norm.cdf(d1)
        N_d2 = norm.cdf(d2)
        N_prime_d1 = norm.pdf(d1)

        if option_type == "CALL":
            delta = N_d1
            theta_annual = (
                -S * N_prime_d1 * sigma * np.exp(-q * T) / (2 * sqrt_T)
                - r * K * np.exp(-r * T) * N_d2
                + q * S * np.exp(-q * T) * N_d1
            )
            model_price = S * np.exp(-q * T) * N_d1 - K * np.exp(-r * T) * N_d2
        else:
            delta = N_d1 - 1.0
            theta_annual = (
                -S * N_prime_d1 * sigma * np.exp(-q * T) / (2 * sqrt_T)
                + r * K * np.exp(-r * T) * norm.cdf(-d2)
                - q * S * np.exp(-q * T) * norm.cdf(-d1)
            )
            model_price = (
                K * np.exp(-r * T) * norm.cdf(-d2)
                - S * np.exp(-q * T) * norm.cdf(-d1)
            )

        gamma = N_prime_d1 / (S * sigma * sqrt_T) if S > 0 and sigma > 0 and T > 0 else 0.0
        vega = S * N_prime_d1 * sqrt_T * np.exp(-q * T)
        rho = K * T * np.exp(-r * T) * (N_d2 if option_type == "CALL" else -norm.cdf(-d2))

        theta_daily = theta_annual / 365.0
        vega_per_1pct = vega * 0.01

        price_error = abs(market_premium - model_price)
        price_error_pct = (price_error / market_premium * 100) if market_premium > 0 else 0.0

        warnings = []
        if price_error_pct > 10:
            model_fit = "POOR"
            warnings.append(
                f"Model price diverges from market: model={model_price:.2f}, market={market_premium:.2f} ({price_error_pct:.1f}% error)"
            )
        elif price_error_pct > 5:
            model_fit = "ACCEPTABLE"
            warnings.append(
                f"Model price differs from market by {price_error_pct:.1f}% (model={model_price:.2f}, market={market_premium:.2f})"
            )
        else:
            model_fit = "GOOD"

        if days_to_expiry <= 1:
            warnings.append("Option near expiry: Greeks highly unstable")
        if implied_vol < 0.05:
            warnings.append("Very low implied volatility: model may be unreliable")
        if implied_vol > 0.50:
            warnings.append("Very high implied volatility: model may be unreliable")

        return GreeksResult(
            strike=strike,
            spot=spot,
            expiry_date=expiry_date,
            days_to_expiry=days_to_expiry,
            time_to_expiry=T,
            option_type=option_type,
            premium=market_premium,
            implied_vol=implied_vol,
            delta=delta,
            gamma=gamma,
            theta=theta_daily,
            vega=vega_per_1pct,
            rho=rho,
            risk_free_rate=r,
            dividend_yield=q,
            model_price=model_price,
            price_error=price_error,
            price_error_pct=price_error_pct,
            model_fit=model_fit,
            warnings=warnings,
        )

    def _handle_expiry_today(
        self, spot: float, strike: float, market_premium: float,
        option_type: str, expiry_date: str
    ) -> GreeksResult:
        intrinsic = max(spot - strike, 0) if option_type == "CALL" else max(strike - spot, 0)
        return GreeksResult(
            strike=strike,
            spot=spot,
            expiry_date=expiry_date,
            days_to_expiry=0,
            time_to_expiry=0.0,
            option_type=option_type,
            premium=market_premium,
            implied_vol=0.0,
            delta=1.0 if intrinsic > 0 else 0.0,
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            rho=0.0,
            risk_free_rate=self.risk_free_rate,
            dividend_yield=self.dividend_yield,
            model_price=intrinsic,
            price_error=abs(market_premium - intrinsic),
            price_error_pct=0.0 if intrinsic == 0 else abs(market_premium - intrinsic) / intrinsic * 100,
            model_fit="GOOD" if market_premium == intrinsic else "ACCEPTABLE",
            warnings=["Option expiring today: Greeks undefined"],
        )
