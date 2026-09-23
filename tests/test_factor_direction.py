"""Tests for factor direction engine and provider."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from models.factor_state import FactorState, FactorDirection, Acceleration, Persistence
from providers.factor_direction_engine import FactorDirectionEngine
from config import FACTOR_NIFTY_INTERPRETATIONS, FACTOR_THRESHOLDS


class TestFactorDirectionEngine:
    """Tests for FactorDirectionEngine."""

    def setup_method(self):
        self.engine = FactorDirectionEngine(
            interpretations=FACTOR_NIFTY_INTERPRETATIONS,
            thresholds=FACTOR_THRESHOLDS,
        )

    def test_crude_rising_bullish(self):
        current = 100.50
        historical = [
            (datetime.now() - timedelta(days=i), 98.20 - i * 0.10)
            for i in range(20, 0, -1)
        ]

        state = self.engine.compute_factor_state(
            factor_name="crude",
            timeframe="20d",
            current_value=current,
            historical_values=historical,
            source="Yahoo Finance",
            observed_at=datetime.now(),
        )

        assert state.direction == FactorDirection.BULLISH
        assert state.change_pct > 0
        assert state.confidence in ("MEDIUM", "HIGH")
        assert state.nifty_relevance == "NEGATIVE"

    def test_usdinr_falling_bullish_for_nifty(self):
        current = 83.00
        historical = [
            (datetime.now() - timedelta(days=i), 85.00 - i * 0.10)
            for i in range(20, 0, -1)
        ]

        state = self.engine.compute_factor_state(
            factor_name="usdinr",
            timeframe="20d",
            current_value=current,
            historical_values=historical,
            source="Yahoo Finance",
            observed_at=datetime.now(),
        )

        assert state.direction == FactorDirection.BEARISH
        assert state.nifty_relevance == "POSITIVE"

    def test_insufficient_history(self):
        state = self.engine.compute_factor_state(
            factor_name="crude",
            timeframe="intraday",
            current_value=100.50,
            historical_values=[],
            source="Yahoo Finance",
            observed_at=datetime.now(),
        )

        assert state.direction == FactorDirection.INSUFFICIENT_DATA
        assert state.confidence == "LOW"
        assert state.history_quality == "INSUFFICIENT"

    def test_reversal_detection(self):
        historical = [
            (datetime.now() - timedelta(days=10), 100.0),
            (datetime.now() - timedelta(days=9), 99.5),
            (datetime.now() - timedelta(days=8), 99.0),
            (datetime.now() - timedelta(days=7), 98.5),
            (datetime.now() - timedelta(days=6), 99.0),
            (datetime.now() - timedelta(days=5), 99.5),
            (datetime.now() - timedelta(days=4), 100.0),
            (datetime.now() - timedelta(days=3), 100.5),
            (datetime.now() - timedelta(days=2), 101.0),
            (datetime.now() - timedelta(days=1), 101.5),
        ]

        state = self.engine.compute_factor_state(
            factor_name="crude",
            timeframe="20d",
            current_value=101.5,
            historical_values=historical,
            source="Yahoo Finance",
            observed_at=datetime.now(),
        )

        assert state.is_reversing is True
        assert "Reversal detected" in " ".join(state.evidence)

    def test_persistence_strong(self):
        now = datetime.now() - timedelta(seconds=10)
        historical = [
            (now - timedelta(days=6), 100.0),
            (now - timedelta(days=5), 100.5),
            (now - timedelta(days=4), 101.0),
            (now - timedelta(days=3), 101.5),
            (now - timedelta(days=2), 102.0),
            (now - timedelta(days=1), 102.5),
        ]
        current = 103.0

        state = self.engine.compute_factor_state(
            factor_name="crude",
            timeframe="5d",
            current_value=current,
            historical_values=historical,
            source="Yahoo Finance",
            observed_at=now,
        )

        assert state.persistence == Persistence.STRONG
        assert state.days_in_current_direction >= 5

    def test_acceleration_detection(self):
        historical = [
            (datetime.now() - timedelta(days=4), 100.0),
            (datetime.now() - timedelta(days=3), 100.5),
            (datetime.now() - timedelta(days=2), 101.0),
            (datetime.now() - timedelta(days=1), 102.0),
            (datetime.now(), 103.5),
        ]

        state = self.engine.compute_factor_state(
            factor_name="crude",
            timeframe="5d",
            current_value=103.5,
            historical_values=historical,
            source="Yahoo Finance",
            observed_at=datetime.now(),
        )

        assert state.acceleration == Acceleration.ACCELERATING

    def test_neutral_zone(self):
        historical = [
            (datetime.now() - timedelta(days=i), 100.0)
            for i in range(5, 0, -1)
        ]

        state = self.engine.compute_factor_state(
            factor_name="crude",
            timeframe="5d",
            current_value=100.0,
            historical_values=historical,
            source="Yahoo Finance",
            observed_at=datetime.now(),
        )

        assert state.direction == FactorDirection.NEUTRAL
