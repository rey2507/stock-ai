"""Factor direction computation engine.

Computes directional state, acceleration, persistence, and reversal
for each factor across timeframes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from models.factor_state import FactorState, FactorDirection, Acceleration, Persistence

log = logging.getLogger(__name__)


class FactorDirectionEngine:
    """Computes directional state for each factor across timeframes."""

    def __init__(self, interpretations: dict, thresholds: dict | None = None):
        self.interpretations = interpretations
        self.thresholds = thresholds or {}

    def compute_factor_state(
        self,
        factor_name: str,
        timeframe: str,
        current_value: Optional[float],
        historical_values: List[Tuple[datetime, float]],
        source: str,
        observed_at: Optional[datetime] = None,
    ) -> FactorState:
        if current_value is None:
            return self._insufficient_data_state(
                factor_name, timeframe, source, observed_at, "No current value available"
            )

        if observed_at is None:
            observed_at = datetime.now()

        window_days = self._get_window_days(timeframe)
        cutoff = observed_at - timedelta(days=window_days)
        window_values = [(dt, val) for dt, val in historical_values if dt >= cutoff]

        if len(window_values) < 2:
            return self._insufficient_data_state(
                factor_name, timeframe, source, observed_at,
                reason=f"Insufficient history: {len(window_values)} data points"
            )

        previous_value = window_values[-2][1]
        change_absolute = current_value - previous_value
        change_pct = (change_absolute / previous_value * 100) if previous_value != 0 else 0.0
        values_in_window = [v for _, v in window_values]

        direction, confidence = self._compute_direction(
            factor_name, current_value, values_in_window, change_pct, len(window_values)
        )

        acceleration = self._compute_acceleration(values_in_window)
        persistence, days_in_direction = self._compute_persistence(values_in_window)
        is_reversing, reversal_strength = self._detect_reversal(values_in_window, direction)
        nifty_interpretation, nifty_relevance = self._interpret_for_nifty(
            factor_name, direction, change_pct, persistence
        )
        history_quality = self._assess_history_quality(len(window_values), window_days)
        evidence = self._generate_evidence(
            factor_name, timeframe, current_value, previous_value, change_pct,
            direction, acceleration, persistence, is_reversing, len(window_values)
        )

        return FactorState(
            factor_name=factor_name,
            timeframe=timeframe,
            current_value=current_value,
            direction=direction,
            confidence=confidence,
            previous_value=previous_value,
            change_absolute=change_absolute,
            change_pct=change_pct,
            acceleration=acceleration,
            persistence=persistence,
            days_in_current_direction=days_in_direction,
            is_reversing=is_reversing,
            reversal_strength=reversal_strength,
            nifty_relevance=nifty_relevance,
            nifty_interpretation=nifty_interpretation,
            source=source,
            observed_at=observed_at,
            data_age_seconds=int((datetime.now() - observed_at).total_seconds()),
            data_points_in_window=len(window_values),
            history_quality=history_quality,
            evidence=evidence,
        )

    def _compute_direction(
        self,
        factor_name: str,
        current: float,
        values: List[float],
        change_pct: float,
        n_points: int,
    ) -> Tuple[FactorDirection, str]:
        threshold_pct = self.thresholds.get(f"{factor_name}_threshold_pct",
                                            self.thresholds.get("default_threshold_pct", 2.0))

        window_avg = sum(values) / len(values) if values else current

        if n_points < 5:
            if current > window_avg * (1 + threshold_pct / 100):
                return FactorDirection.BULLISH, "LOW"
            elif current < window_avg * (1 - threshold_pct / 100):
                return FactorDirection.BEARISH, "LOW"
            return FactorDirection.NEUTRAL, "LOW"

        if current > window_avg * (1 + threshold_pct / 100):
            return FactorDirection.BULLISH, "MEDIUM" if n_points < 15 else "HIGH"
        elif current < window_avg * (1 - threshold_pct / 100):
            return FactorDirection.BEARISH, "MEDIUM" if n_points < 15 else "HIGH"
        return FactorDirection.NEUTRAL, "MEDIUM"

    def _compute_acceleration(self, values: List[float]) -> Acceleration:
        if len(values) < 3:
            return Acceleration.UNKNOWN

        current_move = values[-1] - values[-2]
        prior_moves = [values[i] - values[i - 1] for i in range(1, len(values) - 1)]
        avg_prior = sum(prior_moves) / len(prior_moves) if prior_moves else 0.0

        if avg_prior == 0:
            return Acceleration.UNKNOWN

        ratio = abs(current_move) / abs(avg_prior)
        if ratio > 1.5:
            return Acceleration.ACCELERATING
        if ratio < 0.67:
            return Acceleration.DECELERATING
        return Acceleration.STEADY

    def _compute_persistence(self, values: List[float]) -> Tuple[Persistence, int]:
        if len(values) < 2:
            return Persistence.UNKNOWN, 0

        current_direction = 1 if values[-1] > values[-2] else -1
        days_in_direction = 1
        for i in range(len(values) - 1, 0, -1):
            move = values[i] - values[i - 1]
            if (move > 0 and current_direction == 1) or (move < 0 and current_direction == -1):
                days_in_direction += 1
            else:
                break

        if days_in_direction >= 5:
            return Persistence.STRONG, days_in_direction
        if days_in_direction >= 3:
            return Persistence.MODERATE, days_in_direction
        return Persistence.WEAK, days_in_direction

    def _detect_reversal(
        self, values: List[float], current_direction: FactorDirection
    ) -> Tuple[bool, Optional[str]]:
        if len(values) < 5:
            return False, None

        mid = len(values) // 2
        first_half_avg = sum(values[:mid]) / mid if mid else values[0]
        second_half_avg = sum(values[mid:]) / (len(values) - mid) if (len(values) - mid) else values[-1]

        historical_dir = "UP" if second_half_avg > first_half_avg else "DOWN"
        current_dir_str = "UP" if current_direction == FactorDirection.BULLISH else "DOWN"

        if historical_dir != current_dir_str:
            recent_moves = [
                1 if values[i] > values[i - 1] else -1
                for i in range(max(1, len(values) - 3), len(values))
            ]
            if len(recent_moves) >= 2 and abs(sum(recent_moves)) == len(recent_moves):
                return True, "STRONG"
            return True, "TEMPORARY"
        return False, None

    def _interpret_for_nifty(
        self, factor_name: str, direction: FactorDirection, change_pct: float,
        persistence: Persistence
    ) -> Tuple[str, str]:
        factor_interp = self.interpretations.get(factor_name)
        if not factor_interp:
            return "Unknown", "UNCLEAR"

        if direction == FactorDirection.BULLISH:
            return factor_interp.get("bullish_explanation", ""), factor_interp.get("bullish_for_nifty", "NEUTRAL")
        if direction == FactorDirection.BEARISH:
            return factor_interp.get("bearish_explanation", ""), factor_interp.get("bearish_for_nifty", "NEUTRAL")
        return "", "NEUTRAL"

    def _assess_history_quality(self, n_points: int, window_days: int) -> str:
        if n_points >= 15:
            return "SUFFICIENT"
        if n_points >= 8:
            return "MODERATE"
        if n_points >= 3:
            return "LIMITED"
        return "INSUFFICIENT"

    def _generate_evidence(
        self,
        factor_name: str,
        timeframe: str,
        current: float,
        previous: float,
        change_pct: float,
        direction: FactorDirection,
        acceleration: Acceleration,
        persistence: Persistence,
        is_reversing: bool,
        n_points: int,
    ) -> List[str]:
        evidence = []
        direction_str = direction.value if direction != FactorDirection.INSUFFICIENT_DATA else "Unknown"
        evidence.append(f"{direction_str}: {change_pct:+.2f}% change")

        if acceleration not in (Acceleration.UNKNOWN,):
            evidence.append(f"Acceleration: {acceleration.value}")
        if persistence not in (Persistence.UNKNOWN,):
            evidence.append(f"Persistence: {persistence.value}")
        if is_reversing:
            evidence.append("Reversal detected: direction changed from historical trend")
        if n_points < 5:
            evidence.append(f"Limited history: {n_points} data points only")
        return evidence

    def _get_window_days(self, timeframe: str) -> int:
        return {"intraday": 1, "5d": 5, "20d": 20}.get(timeframe, 20)

    def _insufficient_data_state(
        self,
        factor_name: str,
        timeframe: str,
        source: str,
        observed_at: Optional[datetime],
        reason: str,
    ) -> FactorState:
        return FactorState(
            factor_name=factor_name,
            timeframe=timeframe,
            direction=FactorDirection.INSUFFICIENT_DATA,
            confidence="LOW",
            nifty_relevance="UNCLEAR",
            nifty_interpretation=reason,
            source=source,
            observed_at=observed_at,
            history_quality="INSUFFICIENT",
            evidence=[reason],
        )
