"""Theta decay schedule modeling.

Models how theta accelerates as expiry approaches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ThetaDecayDay:
    """Theta decay for a single day."""
    day: int
    theta_daily: float
    cumulative_decay: float
    premium_estimate: float
    pct_of_original: float


class ThetaDecayCalculator:
    """Model theta decay over time."""

    def calculate_decay_schedule(
        self,
        original_premium: float,
        days_to_expiry: int,
        theta_daily_now: float,
        option_type: str,
    ) -> List[ThetaDecayDay]:
        """Calculate estimated theta decay day-by-day."""
        schedule = []
        cumulative_decay = 0.0
        remaining_premium = original_premium

        for day in range(1, days_to_expiry + 1):
            remaining_days = days_to_expiry - day + 1
            if remaining_days <= 0:
                theta_estimate = 0.0
            else:
                time_factor = np.sqrt((days_to_expiry + 1) / (remaining_days + 1))
                theta_estimate = abs(theta_daily_now) * time_factor

            cumulative_decay += theta_estimate
            remaining_premium = max(0.0, original_premium - cumulative_decay)
            pct_of_original = (remaining_premium / original_premium * 100) if original_premium > 0 else 0.0

            schedule.append(
                ThetaDecayDay(
                    day=day,
                    theta_daily=theta_estimate,
                    cumulative_decay=cumulative_decay,
                    premium_estimate=remaining_premium,
                    pct_of_original=pct_of_original,
                )
            )

        return schedule
