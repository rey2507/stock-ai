"""Factor direction provider.

Computes directional state for all tracked factors and exposes them
through a MarketSnapshot-compatible interface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from models.snapshot import MarketSnapshot
from models.factor_state import FactorSnapshot, FactorState, FactorDirection
from providers.base import BaseProvider
from providers.factor_direction_engine import FactorDirectionEngine
from providers.factor_data_sources import get_current_factor_value, get_historical_factor_values
from providers.history_manager import history_manager
from providers.cache import cache
from config import FACTOR_NIFTY_INTERPRETATIONS, FACTOR_THRESHOLDS

log = logging.getLogger(__name__)

FACTORS = [
    "crude", "usdinr", "us10y",
    "fii", "dii",
    "rbi_rate", "inflation", "gdp", "pmi",
]

TIMEFRAMES = ["intraday", "5d", "20d"]


class FactorDirectionProvider(BaseProvider):
    """Computes factor direction states for all tracked factors."""

    def __init__(self):
        self.engine = FactorDirectionEngine(
            interpretations=FACTOR_NIFTY_INTERPRETATIONS,
            thresholds=FACTOR_THRESHOLDS,
        )

    @property
    def name(self) -> str:
        return "FactorDirection"

    def fetch(self) -> MarketSnapshot:
        try:
            factor_snapshot = self._compute_all_factors()
            history_manager.save_factor_snapshot(factor_snapshot)
            return self._to_market_snapshot(factor_snapshot)
        except Exception as e:
            log.error(f"FactorDirectionProvider error: {e}")
            return MarketSnapshot(
                source="FactorDirection",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )

    def _compute_all_factors(self) -> FactorSnapshot:
        timestamp = datetime.now()
        factors: dict[str, list[FactorState]] = {}

        for factor_name in FACTORS:
            states: list[FactorState] = []
            current_value, source, observed_at = get_current_factor_value(factor_name)
            historical_values = get_historical_factor_values(factor_name, days=20)

            for timeframe in TIMEFRAMES:
                if timeframe == "intraday" and current_value is None:
                    states.append(self.engine._insufficient_data_state(
                        factor_name, timeframe, source or "UNKNOWN", observed_at or timestamp,
                        "No current value available"
                    ))
                    continue

                if timeframe == "intraday":
                    # Intraday: just current vs previous if available
                    intraday_hist = [(observed_at or timestamp, current_value)]
                    if len(historical_values) >= 1:
                        intraday_hist = [historical_values[-1], (observed_at or timestamp, current_value)]
                    state = self.engine.compute_factor_state(
                        factor_name=factor_name,
                        timeframe=timeframe,
                        current_value=current_value,
                        historical_values=intraday_hist,
                        source=source or "UNKNOWN",
                        observed_at=observed_at or timestamp,
                    )
                else:
                    window_days = {"5d": 5, "20d": 20}[timeframe]
                    cutoff = timestamp - timedelta(days=window_days)
                    window_hist = [(dt, val) for dt, val in historical_values if dt >= cutoff]
                    if not window_hist:
                        window_hist = historical_values[-window_days:] if len(historical_values) >= window_days else historical_values
                    state = self.engine.compute_factor_state(
                        factor_name=factor_name,
                        timeframe=timeframe,
                        current_value=current_value,
                        historical_values=window_hist,
                        source=source or "UNKNOWN",
                        observed_at=observed_at or timestamp,
                    )
                states.append(state)
            factors[factor_name] = states

        return FactorSnapshot(timestamp=timestamp, factors=factors)

    def _to_market_snapshot(self, factor_snapshot: FactorSnapshot) -> MarketSnapshot:
        """Convert FactorSnapshot into a MarketSnapshot with factor_states populated."""
        return MarketSnapshot(
            snapshot_timestamp=factor_snapshot.timestamp,
            source="FactorDirection",
            data_status="LIVE",
            missing_fields=[],
            factor_states={k: v[:] for k, v in factor_snapshot.factors.items()},
        )
