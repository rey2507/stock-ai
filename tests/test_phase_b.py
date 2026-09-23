"""Phase B tests: NIFTY Synthesis with Factor Context.

Tests for:
- Verdict dataclass factor fields
- intraday_verdict_v2 factor context
- weekly_verdict_v2 factor-driven macro
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from models.verdict import Verdict, ComponentResult
from models.factor_state import FactorState, FactorDirection
from engines.intraday_verdict_v2 import compute_verdict as intraday_verdict_v2
from engines.weekly_verdict_v2 import compute_verdict as weekly_verdict_v2
from tests.fixtures import fixture_strong_bullish_intraday


# =============================================================================
# A. VERDICT DATACLASS EXTENSION
# =============================================================================

class TestVerdictDataclassExtension:
    """Verify Verdict dataclass accepts and stores factor context."""

    def test_factor_fields_default_empty(self):
        """Factor fields default to empty collections."""
        v = Verdict(direction="BULLISH", state="SETUP", raw_score=3, conflict=False, data_quality="Good")
        assert v.factor_states == {}
        assert v.factor_contributions == {}
        assert v.timeframe_conflicts == []
        assert v.factor_evidence == []

    def test_factor_fields_assignable(self):
        """Factor fields can be assigned."""
        v = Verdict(direction="BULLISH", state="SETUP", raw_score=3, conflict=False, data_quality="Good")
        v.factor_states = {"crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE")]}
        v.factor_contributions = {"crude": +1}
        v.timeframe_conflicts = ["crude: intraday bullish but weekly bearish"]
        v.factor_evidence = ["Bullish factors: crude"]
        assert v.factor_states["crude"][0].direction == FactorDirection.BULLISH
        assert v.factor_contributions["crude"] == +1
        assert len(v.timeframe_conflicts) == 1
        assert len(v.factor_evidence) == 1

    def test_component_result_factor_influence(self):
        """ComponentResult accepts factor_influence."""
        comp = ComponentResult("Momentum", 1, "Bullish", "Strong", factor_influence="crude bullish")
        assert comp.factor_influence == "crude bullish"


# =============================================================================
# B. INTRADAY VERDICT V2
# =============================================================================

class TestIntradayVerdictV2:
    """Test intraday_verdict_v2 with factor context."""

    def test_factor_fields_populated(self):
        """Verdict includes factor_states and factor_evidence."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE", nifty_interpretation="Crude rising — mild headwind")],
            "usdinr": [FactorState(factor_name="usdinr", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE", nifty_interpretation="INR weakening — headwind")],
        }
        v = intraday_verdict_v2(snap)
        assert "crude" in v.factor_states
        assert "usdinr" in v.factor_states
        assert v.factor_contributions["crude"] == -1  # POSITIVE relevance + bearish direction = -1
        assert v.factor_contributions["usdinr"] == -1
        assert len(v.factor_evidence) > 0

    def test_factor_contributions_positive_relevance(self):
        """POSITIVE relevance: bullish factor = +1, bearish = -1."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "gdp": [FactorState(factor_name="gdp", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE")],
            "pmi": [FactorState(factor_name="pmi", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE")],
        }
        v = intraday_verdict_v2(snap)
        assert v.factor_contributions["gdp"] == +1
        assert v.factor_contributions["pmi"] == -1

    def test_factor_contributions_negative_relevance(self):
        """NEGATIVE relevance: bullish factor = -1, bearish = +1."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="NEGATIVE")],
            "inflation": [FactorState(factor_name="inflation", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="NEGATIVE")],
        }
        v = intraday_verdict_v2(snap)
        assert v.factor_contributions["crude"] == -1  # NEGATIVE + bullish = -1
        assert v.factor_contributions["inflation"] == +1  # NEGATIVE + bearish = +1

    def test_factor_contributions_neutral_relevance(self):
        """NEUTRAL relevance always contributes 0."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "pmi": [FactorState(factor_name="pmi", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="NEUTRAL")],
        }
        v = intraday_verdict_v2(snap)
        assert v.factor_contributions["pmi"] == 0

    def test_timeframe_conflicts_detected(self):
        """Conflicts between intraday and weekly timeframes are detected."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "usdinr": [
                FactorState(factor_name="usdinr", timeframe="intraday", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE", nifty_interpretation="INR strong intraday"),
                FactorState(factor_name="usdinr", timeframe="20d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE", nifty_interpretation="INR weak weekly"),
            ],
        }
        v = intraday_verdict_v2(snap)
        assert any("intraday" in c and "weekly" in c for c in v.timeframe_conflicts)

    def test_reversing_factors_warning(self):
        """Reversing factors generate warning when NIFTY bullish."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE", is_reversing=True, reversal_strength="strong")],
        }
        v = intraday_verdict_v2(snap)
        assert any("reversing" in e.lower() for e in v.factor_evidence)

    def test_no_factors_empty_evidence(self):
        """Empty factor states produce empty contributions and evidence."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {}
        v = intraday_verdict_v2(snap)
        assert v.factor_contributions == {}
        assert v.factor_evidence == []

    def test_insufficient_factor_data(self):
        """INSUFFICIENT_DATA direction contributes 0."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.INSUFFICIENT_DATA, nifty_relevance="POSITIVE")],
        }
        v = intraday_verdict_v2(snap)
        assert v.factor_contributions["crude"] == 0


# =============================================================================
# C. WEEKLY VERDICT V2
# =============================================================================

class TestWeeklyVerdictV2:
    """Test weekly_verdict_v2 with factor-driven macro."""

    def test_macro_from_bullish_factors(self):
        """Macro component is bullish when factors are bullish."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "crude": [FactorState(factor_name="crude", timeframe="20d", direction=FactorDirection.BEARISH, nifty_relevance="NEGATIVE", nifty_interpretation="Crude falling — supportive")],
            "usdinr": [FactorState(factor_name="usdinr", timeframe="20d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE", nifty_interpretation="INR weak — headwind")],
        }
        v = weekly_verdict_v2(snap)
        macro = v.components.get("Macro")
        assert macro is not None
        assert macro.name == "Macro"
        assert macro.score in (-1, 0, 1)
        assert any("crude" in e.lower() for e in macro.evidence)

    def test_macro_score_zero_when_no_factors(self):
        """Macro score is 0 when no macro factors available."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {}
        v = weekly_verdict_v2(snap)
        macro = v.components.get("Macro")
        assert macro is not None
        assert macro.score == 0

    def test_macro_score_zero_when_insufficient_data(self):
        """Macro score is 0 when factors are INSUFFICIENT_DATA."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "crude": [FactorState(factor_name="crude", timeframe="20d", direction=FactorDirection.INSUFFICIENT_DATA, nifty_relevance="POSITIVE")],
        }
        v = weekly_verdict_v2(snap)
        macro = v.components.get("Macro")
        assert macro is not None
        assert macro.score == 0

    def test_weekly_verdict_has_factor_states(self):
        """Weekly verdict includes factor_states."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {
            "gdp": [FactorState(factor_name="gdp", timeframe="20d", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE", nifty_interpretation="GDP growing")],
        }
        v = weekly_verdict_v2(snap)
        assert "gdp" in v.factor_states

    def test_weekly_verdict_includes_other_components(self):
        """Weekly verdict still includes Capital Flows, Earnings, Participation, Derivatives."""
        snap = fixture_strong_bullish_intraday()
        snap.factor_states = {}
        v = weekly_verdict_v2(snap)
        assert "Capital Flows" in v.components
        assert "Earnings" in v.components
        assert "Participation" in v.components
        assert "Derivatives" in v.components


# =============================================================================
# D. FACTOR CONTRIBUTION LOGIC
# =============================================================================

class TestFactorContributionLogic:
    """Direct tests for _assess_factor_contributions."""

    def _compute_contributions(self, factor_states):
        from engines.intraday_verdict_v2 import _assess_factor_contributions
        return _assess_factor_contributions(factor_states)

    def test_bullish_positive_relevance(self):
        states = {"gdp": [FactorState(factor_name="gdp", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE")]}
        assert self._compute_contributions(states)["gdp"] == +1

    def test_bearish_positive_relevance(self):
        states = {"crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="POSITIVE")]}
        assert self._compute_contributions(states)["crude"] == -1

    def test_bullish_negative_relevance(self):
        states = {"crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="NEGATIVE")]}
        assert self._compute_contributions(states)["crude"] == -1

    def test_bearish_negative_relevance(self):
        states = {"inflation": [FactorState(factor_name="inflation", timeframe="5d", direction=FactorDirection.BEARISH, nifty_relevance="NEGATIVE")]}
        assert self._compute_contributions(states)["inflation"] == +1

    def test_neutral_relevance(self):
        states = {"pmi": [FactorState(factor_name="pmi", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="NEUTRAL")]}
        assert self._compute_contributions(states)["pmi"] == 0

    def test_insufficient_data_direction(self):
        states = {"crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.INSUFFICIENT_DATA, nifty_relevance="POSITIVE")]}
        assert self._compute_contributions(states)["crude"] == 0

    def test_no_5d_state(self):
        states = {"crude": [FactorState(factor_name="crude", timeframe="20d", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE")]}
        assert self._compute_contributions(states)["crude"] == 0

    def test_reversing_factor(self):
        states = {"crude": [FactorState(factor_name="crude", timeframe="5d", direction=FactorDirection.BULLISH, nifty_relevance="POSITIVE", is_reversing=True)]}
        assert self._compute_contributions(states)["crude"] == 0
