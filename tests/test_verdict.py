"""Unit tests for verdict engine and indicator calculations.

Run: python -m pytest tests/ -v
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures import (
    fixture_strong_bullish_intraday,
    fixture_strong_bearish_intraday,
    fixture_neutral_intraday,
    fixture_major_conflict_intraday,
    fixture_bullish_bias_intraday,
    fixture_missing_critical_data,
    fixture_provider_failure,
    fixture_source_metadata,
)
from engines.intraday_verdict import compute_verdict as intraday_verdict
from engines.weekly_verdict import compute_verdict as weekly_verdict
from models.verdict import Verdict, ComponentResult


# =============================================================================
# A. APPLICATION TESTS
# =============================================================================

class TestApplicationLaunch:
    """Verify core modules can be imported without errors."""

    def test_import_all_modules(self):
        """All core modules import cleanly."""
        import models.snapshot
        import models.verdict
        import providers.base
        import providers.registry
        import engines.intraday_verdict
        import engines.weekly_verdict
        import config


# =============================================================================
# B. DATA CONTRACT TESTS
# =============================================================================

class TestDataContract:
    """Verify providers follow the canonical contract."""

    def test_unavailable_snapshot_fields(self):
        """Provider failure produces all-invalid fields."""
        from models.snapshot import MarketSnapshot
        snap = MarketSnapshot(
            source="FailedProvider",
            data_status="UNAVAILABLE",
            missing_fields=["ALL"],
        )
        assert snap.is_field_available("nifty_spot") is False
        assert snap.is_field_available("futures_price") is False

    def test_no_fallback_substitution(self):
        """Missing nifty_spot must NOT silently use futures_price."""
        from models.snapshot import MarketSnapshot, FieldMeta
        snap = MarketSnapshot(
            source="Test",
            data_status="HISTORICAL",
            nifty_spot=FieldMeta(value=None, status="UNAVAILABLE", quality="INVALID"),
            futures_price=FieldMeta(value=25000.0, status="HISTORICAL", quality="GOOD"),
        )
        assert snap.get("nifty_spot") is None
        assert snap.get("futures_price") == 25000.0
        assert "nifty_spot" in snap.critical_fields_missing()


# =============================================================================
# C. INTRADAY CALCULATION TESTS
# =============================================================================

class TestIntradayCalculations:
    """Test indicator calculations with known fixtures."""

    def test_pcr_calculation(self):
        """PCR = put_oi / call_oi."""
        from models.snapshot import MarketSnapshot, FieldMeta
        snap = MarketSnapshot(
            call_oi=FieldMeta(value=8_000_000, quality="GOOD", status="HISTORICAL"),
            put_oi=FieldMeta(value=10_000_000, quality="GOOD", status="HISTORICAL"),
        )
        pcr = snap.get("put_oi") / max(snap.get("call_oi"), 1)
        assert abs(pcr - 1.25) < 0.01

    def test_advance_decline_ratio(self):
        """A/D ratio = advances / declines."""
        from models.snapshot import MarketSnapshot, FieldMeta
        snap = MarketSnapshot(
            advances=FieldMeta(value=30, quality="GOOD", status="HISTORICAL"),
            declines=FieldMeta(value=20, quality="GOOD", status="HISTORICAL"),
        )
        ad = snap.get("advances") / max(snap.get("declines"), 1)
        assert abs(ad - 1.5) < 0.01

    def test_relative_volume(self):
        """Relative volume = current / average."""
        current = 150_000_000
        avg = 100_000_000
        assert abs(current / avg - 1.5) < 0.01

    def test_vwap_relationship(self):
        """Price above VWAP = bullish signal."""
        from models.snapshot import MarketSnapshot, FieldMeta
        snap = MarketSnapshot(
            nifty_spot=FieldMeta(value=25000.0, quality="GOOD", status="HISTORICAL"),
            vwap=FieldMeta(value=24800.0, quality="GOOD", status="HISTORICAL"),
        )
        assert snap.get("nifty_spot") > snap.get("vwap")

    def test_futures_oi_classification(self):
        """Price up + OI up = bullish positioning."""
        from models.snapshot import MarketSnapshot, FieldMeta
        snap = MarketSnapshot(
            futures_change_pct=FieldMeta(value=0.5, quality="GOOD", status="HISTORICAL"),
            futures_oi_change=FieldMeta(value=500_000, quality="GOOD", status="HISTORICAL"),
        )
        price_up = snap.get("futures_change_pct") > 0
        oi_up = snap.get("futures_oi_change") > 0
        assert price_up and oi_up


# =============================================================================
# D. VERDICT TEST FIXTURES
# =============================================================================

class TestVerdictFixtures:
    """Deterministic test cases from the spec."""

    def test_1_strong_bullish(self):
        """All bullish -> BULLISH SETUP (+5)."""
        snap = fixture_strong_bullish_intraday()
        v = intraday_verdict(snap)
        assert v.direction == "BULLISH"
        assert v.state == "SETUP"
        assert v.raw_score == 5
        assert v.conflict is False

    def test_2_strong_bearish(self):
        """All bearish -> BEARISH SETUP (-5)."""
        snap = fixture_strong_bearish_intraday()
        v = intraday_verdict(snap)
        assert v.direction == "BEARISH"
        assert v.state == "SETUP"
        assert v.raw_score == -5
        assert v.conflict is False

    def test_3_neutral(self):
        """All neutral -> MIXED / WAIT (0)."""
        snap = fixture_neutral_intraday()
        v = intraday_verdict(snap)
        assert v.direction == "MIXED"
        assert v.state == "WAIT"
        assert v.raw_score == 0
        assert v.conflict is False

    def test_4_major_conflict(self):
        """Momentum +1, Futures -1, Participation -1 -> conflict -> MIXED / WAIT."""
        snap = fixture_major_conflict_intraday()
        v = intraday_verdict(snap)
        assert v.direction == "MIXED"
        assert v.state == "WAIT"
        assert v.conflict is True

    def test_5_bullish_bias(self):
        """Score +2, no conflict -> BULLISH BIAS."""
        snap = fixture_bullish_bias_intraday()
        v = intraday_verdict(snap)
        assert v.direction == "BULLISH"
        assert v.state == "BIAS"
        assert v.raw_score == 2
        assert v.conflict is False

    def test_6_missing_critical_data(self):
        """Nifty price missing -> INSUFFICIENT DATA."""
        snap = fixture_missing_critical_data()
        v = intraday_verdict(snap)
        assert v.direction == "NONE"
        assert v.state == "INSUFFICIENT_DATA"

    def test_7_provider_failure(self):
        """All fields unavailable -> INSUFFICIENT DATA."""
        snap = fixture_provider_failure()
        v = intraday_verdict(snap)
        assert v.direction == "NONE"
        assert v.state == "INSUFFICIENT_DATA"

    def test_8_source_metadata_preserved(self):
        """Source metadata preserved through verdict."""
        snap = fixture_source_metadata()
        assert snap.source == "TestProvider"
        assert snap.data_status == "LIVE"
        v = intraday_verdict(snap)
        assert v.data_quality in ("Good", "Partial", "Poor")

    def test_verdict_structured_output(self):
        """Verdict has all required fields."""
        snap = fixture_strong_bullish_intraday()
        v = intraday_verdict(snap)
        assert hasattr(v, "direction")
        assert hasattr(v, "state")
        assert hasattr(v, "raw_score")
        assert hasattr(v, "conflict")
        assert hasattr(v, "data_quality")
        assert hasattr(v, "components")
        assert hasattr(v, "reasons")
        assert hasattr(v, "timestamp")
        assert hasattr(v, "emoji")
        assert hasattr(v, "display_label")
        assert len(v.components) == 5

    def test_component_scores_in_range(self):
        """All component scores are +1, 0, or -1."""
        snap = fixture_strong_bullish_intraday()
        v = intraday_verdict(snap)
        for name, comp in v.components.items():
            assert comp.score in (-1, 0, 1), f"{name} score {comp.score} out of range"


class TestVerdictContext:
    """Test verdict context fields: expiry, persistence, regime."""

    def test_expiry_context_normal(self):
        """Today is not expiry day → NORMAL."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot, FieldMeta

        snap = MarketSnapshot(
            futures_expiry=FieldMeta(value="31DEC26", status="LIVE", quality="GOOD"),
        )
        ctx = compute_expiry_context(snap)
        assert ctx == "NORMAL"

    def test_expiry_context_expiry_day(self):
        """Today matches expiry date → EXPIRY_DAY."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot, FieldMeta
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%d%b%y").upper()
        snap = MarketSnapshot(
            futures_expiry=FieldMeta(value=today, status="LIVE", quality="GOOD"),
        )
        ctx = compute_expiry_context(snap)
        assert ctx == "EXPIRY_DAY"

    def test_expiry_context_post_expiry(self):
        """Past expiry → POST_EXPIRY."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot, FieldMeta

        snap = MarketSnapshot(
            futures_expiry=FieldMeta(value="01JAN20", status="LIVE", quality="GOOD"),
        )
        ctx = compute_expiry_context(snap)
        assert ctx == "POST_EXPIRY"

    def test_expiry_context_unavailable(self):
        """Missing expiry → empty string."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot

        snap = MarketSnapshot()
        ctx = compute_expiry_context(snap)
        assert ctx == ""

    def test_persistence_strengthening(self):
        """Score improved while direction stayed the same → STRENGTHENING."""
        from utils.history_ui import compute_persistence
        from providers.history_manager import history_manager
        from pathlib import Path
        import shutil

        # Use temp history
        tmp = Path(tempfile.mkdtemp())
        import providers.history_manager as hm
        old_verdicts = hm.VERDICTS_DIR
        hm.VERDICTS_DIR = tmp
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            v1 = Verdict(direction="BULLISH", state="BIAS", raw_score=2, conflict=False, data_quality="Good", timestamp=now)
            v2 = Verdict(direction="BULLISH", state="SETUP", raw_score=4, conflict=False, data_quality="Good", timestamp=now)
            history_manager.save_verdict(v1)
            history_manager.save_verdict(v2)

            current = Verdict(direction="BULLISH", state="SETUP", raw_score=5, conflict=False, data_quality="Good")
            assert compute_persistence(current) == "STRENGTHENING"
        finally:
            hm.VERDICTS_DIR = old_verdicts
            shutil.rmtree(tmp, ignore_errors=True)

    def test_persistence_insufficient_history(self):
        """No previous verdicts → INSUFFICIENT_HISTORY."""
        from utils.history_ui import compute_persistence
        from providers.history_manager import history_manager
        from pathlib import Path
        import shutil

        tmp = Path(tempfile.mkdtemp())
        import providers.history_manager as hm
        old_verdicts = hm.VERDICTS_DIR
        hm.VERDICTS_DIR = tmp
        try:
            current = Verdict(direction="BULLISH", state="BIAS", raw_score=2, conflict=False, data_quality="Good")
            assert compute_persistence(current) == "INSUFFICIENT_HISTORY"
        finally:
            hm.VERDICTS_DIR = old_verdicts
            shutil.rmtree(tmp, ignore_errors=True)

    def test_evidence_package_structure(self):
        """to_evidence_package returns expected keys."""
        snap = fixture_strong_bullish_intraday()
        v = intraday_verdict(snap)
        v.persistence = "STABLE"
        v.expiry_context = "NORMAL"
        v.market_regime = "TRENDING"
        pkg = v.to_evidence_package()
        assert "current_state" in pkg
        assert "persistence" in pkg
        assert "market_context" in pkg
        assert "components" in pkg
        assert "changes" in pkg
        assert pkg["persistence"]["value"] == "STABLE"
        assert pkg["market_context"]["expiry_context"] == "NORMAL"
        assert pkg["market_context"]["market_regime"] == "TRENDING"


class TestTrendStrength:
    """Test trend strength scoring 0-100."""

    def test_base_score_from_raw(self):
        """Base score reflects |raw_score| / 5 * 100."""
        from utils.history_ui import compute_trend_strength

        v = Verdict(direction="BULLISH", state="SETUP", raw_score=5, conflict=False, data_quality="Good")
        assert compute_trend_strength(v) == 100

        v = Verdict(direction="BULLISH", state="BIAS", raw_score=2, conflict=False, data_quality="Good")
        assert compute_trend_strength(v) == 45

        v = Verdict(direction="BEARISH", state="BIAS", raw_score=-3, conflict=False, data_quality="Good")
        assert compute_trend_strength(v) == 65

    def test_conflict_penalty(self):
        """Major conflict reduces trend strength."""
        from utils.history_ui import compute_trend_strength

        v = Verdict(direction="BULLISH", state="BIAS", raw_score=3, conflict=True, data_quality="Good")
        assert compute_trend_strength(v) == 35

    def test_persistence_modifiers(self):
        """Persistence affects trend strength."""
        from utils.history_ui import compute_trend_strength

        v_base = Verdict(direction="BULLISH", state="BIAS", raw_score=3, conflict=False, data_quality="Good")

        v_strengthening = Verdict(**{**v_base.__dict__, "persistence": "STRENGTHENING"})
        assert compute_trend_strength(v_strengthening) == 75

        v_weakening = Verdict(**{**v_base.__dict__, "persistence": "WEAKENING"})
        assert compute_trend_strength(v_weakening) == 55

        v_reversing = Verdict(**{**v_base.__dict__, "persistence": "REVERSING"})
        assert compute_trend_strength(v_reversing) == 40

    def test_regime_modifiers(self):
        """Market regime affects trend strength."""
        from utils.history_ui import compute_trend_strength

        v_base = Verdict(direction="BULLISH", state="BIAS", raw_score=3, conflict=False, data_quality="Good")

        v_trending = Verdict(**{**v_base.__dict__, "market_regime": "TRENDING"})
        assert compute_trend_strength(v_trending) == 75

        v_choppy = Verdict(**{**v_base.__dict__, "market_regime": "CHOPPY"})
        assert compute_trend_strength(v_choppy) == 50

        v_transitional = Verdict(**{**v_base.__dict__, "market_regime": "TRANSITIONAL"})
        assert compute_trend_strength(v_transitional) == 60

    def test_data_quality_modifiers(self):
        """Data quality affects trend strength."""
        from utils.history_ui import compute_trend_strength

        v_base = Verdict(direction="BULLISH", state="BIAS", raw_score=3, conflict=False, data_quality="Good")

        v_good = Verdict(**{**v_base.__dict__, "data_quality": "Good"})
        assert compute_trend_strength(v_good) == 65

        v_partial = Verdict(**{**v_base.__dict__, "data_quality": "Partial"})
        assert compute_trend_strength(v_partial) == 60

        v_poor = Verdict(**{**v_base.__dict__, "data_quality": "Poor"})
        assert compute_trend_strength(v_poor) == 45

    def test_consensus_modifier(self):
        """Component consensus affects trend strength."""
        from utils.history_ui import compute_trend_strength

        v_unanimous = Verdict(
            direction="BULLISH",
            state="BIAS",
            raw_score=3,
            conflict=False,
            data_quality="Good",
            components={
                "Momentum": ComponentResult("Momentum", 1, "Bullish", "", []),
                "Futures": ComponentResult("Futures", 1, "Bullish", "", []),
                "Options": ComponentResult("Options", 1, "Bullish", "", []),
            },
        )
        # Base 60 + 5 quality + 10 consensus = 75
        assert compute_trend_strength(v_unanimous) == 75

        v_mixed = Verdict(
            direction="BULLISH",
            state="BIAS",
            raw_score=1,
            conflict=False,
            data_quality="Good",
            components={
                "Momentum": ComponentResult("Momentum", 1, "Bullish", "", []),
                "Futures": ComponentResult("Futures", -1, "Bearish", "", []),
                "Options": ComponentResult("Options", 0, "Mixed", "", []),
            },
        )
        # Base 20 + 5 quality - 5 mixed = 20
        assert compute_trend_strength(v_mixed) == 20

    def test_macro_modifier(self):
        """Macro component affects trend strength."""
        from utils.history_ui import compute_trend_strength

        v_base = Verdict(direction="BULLISH", state="BIAS", raw_score=3, conflict=False, data_quality="Good")

        # Bullish macro → +5, plus consensus +10
        v_bullish_macro = Verdict(
            direction="BULLISH",
            state="BIAS",
            raw_score=3,
            conflict=False,
            data_quality="Good",
            components={
                "Macro": ComponentResult("Macro", 1, "Expanding", "", []),
            },
        )
        # Base 60 + 5 quality + 5 macro + 10 consensus = 80
        assert compute_trend_strength(v_bullish_macro) == 80

        # Bearish macro → -10, plus consensus +10
        v_bearish_macro = Verdict(
            direction="BULLISH",
            state="BIAS",
            raw_score=3,
            conflict=False,
            data_quality="Good",
            components={
                "Macro": ComponentResult("Macro", -1, "Deteriorating", "", []),
            },
        )
        # Base 60 + 5 quality - 10 macro + 10 consensus = 65
        assert compute_trend_strength(v_bearish_macro) == 65

        # Deteriorating label with 0 score → -5, no consensus bonus (score=0 excluded)
        v_deteriorating = Verdict(
            direction="BULLISH",
            state="BIAS",
            raw_score=3,
            conflict=False,
            data_quality="Good",
            components={
                "Macro": ComponentResult("Macro", 0, "Deteriorating", "", []),
            },
        )
        # Base 60 + 5 quality - 5 deteriorating = 60
        assert compute_trend_strength(v_deteriorating) == 60

    def test_clamping(self):
        """Score is clamped to 0-100."""
        from utils.history_ui import compute_trend_strength

        v = Verdict(
            direction="BULLISH",
            state="SETUP",
            raw_score=1,
            conflict=False,
            data_quality="Good",
            persistence="STRENGTHENING",
            market_regime="TRENDING",
            components={
                "M": ComponentResult("M", 1, "Bullish", "", []),
            },
        )
        assert compute_trend_strength(v) <= 100

        v = Verdict(
            direction="BEARISH",
            state="BIAS",
            raw_score=-1,
            conflict=True,
            data_quality="Poor",
            persistence="REVERSING",
            market_regime="CHOPPY",
            components={
                "M": ComponentResult("M", -1, "Bearish", "", []),
                "F": ComponentResult("F", 1, "Bullish", "", []),
            },
        )
        assert compute_trend_strength(v) >= 0

    def test_mixed_direction_zero(self):
        """MIXED direction returns 0."""
        from utils.history_ui import compute_trend_strength

        v = Verdict(direction="MIXED", state="WAIT", raw_score=0, conflict=False, data_quality="Good")
        assert compute_trend_strength(v) == 0

    def test_insufficient_data_zero(self):
        """INSUFFICIENT_DATA state returns 0."""
        from utils.history_ui import compute_trend_strength

        v = Verdict(direction="NONE", state="INSUFFICIENT_DATA", raw_score=0, conflict=False, data_quality="Good")
        assert compute_trend_strength(v) == 0

    def test_trend_strength_label(self):
        """Label maps correctly to score ranges."""
        from utils.history_ui import trend_strength_label

        assert trend_strength_label(95) == "Very Strong"
        assert trend_strength_label(70) == "Strong"
        assert trend_strength_label(50) == "Moderate"
        assert trend_strength_label(25) == "Weak"
        assert trend_strength_label(5) == "Very Weak"
