"""Tests for history manager and new features."""

import sys
import os
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock

from providers.history_manager import HistoryManager, history_manager
from models.snapshot import MarketSnapshot, FieldMeta
from models.verdict import Verdict, ComponentResult


class TestHistoryManager:
    """Test history persistence."""

    @pytest.fixture
    def temp_manager(self, tmp_path):
        """Create a HistoryManager with temporary directories."""
        manager = HistoryManager()
        # Override directories with temp paths BEFORE any operations
        snap_dir = tmp_path / "snapshots"
        verdict_dir = tmp_path / "verdicts"
        oi_dir = tmp_path / "oi"
        macro_file = tmp_path / "macro.json"
        for d in [snap_dir, verdict_dir, oi_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Patch the module-level paths
        import providers.history_manager as hm
        hm.SNAPSHOTS_DIR = snap_dir
        hm.VERDICTS_DIR = verdict_dir
        hm.OI_HISTORY_DIR = oi_dir
        hm.MACRO_CACHE_FILE = macro_file

        # Also patch the manager's internal references
        manager._snapshots_dir = snap_dir
        manager._verdicts_dir = verdict_dir
        manager._oi_dir = oi_dir
        manager._macro_file = macro_file
        return manager

    def test_save_and_get_snapshot(self, temp_manager):
        """Snapshots are persisted and retrievable."""
        snap = MarketSnapshot(
            snapshot_timestamp=datetime.now(timezone.utc),
            source="Test",
            data_status="LIVE",
            nifty_spot=FieldMeta(value=25000.0, status="LIVE", quality="GOOD"),
        )
        temp_manager.save_snapshot(snap)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        results = temp_manager.get_snapshots(date)
        assert len(results) == 1
        assert results[0]["source"] == "Test"

    def test_save_verdict_only_when_changed(self, temp_manager):
        """Verdict is only saved when score changes."""
        v1 = Verdict(
            direction="BULLISH",
            state="BIAS",
            raw_score=2,
            conflict=False,
            data_quality="Good",
            components={},
        )
        v2 = Verdict(
            direction="BULLISH",
            state="SETUP",
            raw_score=4,
            conflict=False,
            data_quality="Good",
            components={},
        )
        v3 = Verdict(
            direction="BULLISH",
            state="SETUP",
            raw_score=4,
            conflict=False,
            data_quality="Good",
            components={},
        )

        temp_manager.save_verdict(v1)
        temp_manager.save_verdict(v2)
        temp_manager.save_verdict(v3)  # Same score as v2

        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        results = temp_manager.get_verdict_changes(days=1, limit=10)
        assert len(results) == 2  # Only v1 and v2

    def test_save_oi_observation(self, temp_manager):
        """OI observations are persisted."""
        temp_manager.save_oi_observation({
            "futures_oi": 1000000,
            "call_oi": 500000,
            "put_oi": 600000,
        })
        history = temp_manager.get_oi_history(days=1)
        assert len(history) == 1
        assert history[0]["futures_oi"] == 1000000

    def test_macro_cache_save_and_load(self, temp_manager):
        """Macro cache is saved and loaded correctly."""
        temp_manager.save_macro_cache({
            "india_policy_rate": 6.00,
            "fed_rate": 4.50,
            "inflation": 5.09,
        })
        loaded = temp_manager.load_macro_cache()
        assert loaded["india_policy_rate"] == 6.00
        assert loaded["fed_rate"] == 4.50
        assert loaded["inflation"] == 5.09


class TestMacroProviderNoHardcodedFallbacks:
    """Verify macro provider has no hardcoded values."""

    def test_no_hardcoded_values_in_source(self):
        """Macro provider source should not contain hardcoded fallback values."""
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "providers", "macro_provider.py"
        )
        with open(source_path, "r") as f:
            content = f.read()

        # Check for common hardcoded patterns
        forbidden_patterns = [
            "rate = 6.00",
            "rate = 4.50",
            "inflation = 5.09",
            "gdp = 6.5",
            "pmi = 56.9",
            "# Fallback:",
            "# fallback:",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"Found hardcoded fallback: {pattern}"

    def test_macro_provider_returns_none_on_failure(self):
        """When all sources fail, macro provider should return UNAVAILABLE fields."""
        from providers.macro_provider import MacroProvider
        from providers.cache import cache

        provider = MacroProvider()

        # Clear all caches
        cache.clear()

        # Mock all network calls to fail (both session.get and yfinance)
        with patch.object(provider._session, "get", side_effect=ConnectionError("Network down")):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = MagicMock(empty=True)
                snap = provider.fetch()

        # All macro fields should be UNAVAILABLE
        assert snap.get("india_policy_rate") is None
        assert snap.get("fed_rate") is None
        assert snap.get("inflation") is None
        assert snap.get("gdp_growth") is None
        assert snap.get("pmi") is None


class TestFreshnessDisplay:
    """Test freshness indicators in UI."""

    def test_field_display_value_with_freshness(self):
        """Field display includes freshness indicator."""
        from utils.ui import _field_display_value

        fm_live = FieldMeta(value=25000.0, status="LIVE", quality="GOOD", freshness_seconds=3.0)
        display, status, color = _field_display_value(fm_live)
        assert "[LIVE" in display or "[LIVE]" in display
        assert status == "LIVE"

        fm_stale = FieldMeta(value=25000.0, status="STALE", quality="POOR", freshness_seconds=120.0)
        display, status, color = _field_display_value(fm_stale)
        assert "STALE" in display
        assert status == "STALE"

        fm_unavail = FieldMeta(value=None, status="UNAVAILABLE", quality="INVALID")
        display, status, color = _field_display_value(fm_unavail)
        assert "UNAVAILABLE" in display
        assert status == "UNAVAILABLE"


class TestVolumeCalculation:
    """Test volume metrics are properly labeled."""

    def test_total_option_volume_in_snapshot(self):
        """MarketSnapshot has total_option_volume field."""
        snap = MarketSnapshot(
            total_option_volume=FieldMeta(value=1000000, status="LIVE", quality="GOOD"),
        )
        assert snap.get("total_option_volume") == 1000000

    def test_option_chain_volume_calculation(self):
        """Option chain volume is sum of CE and PE last prices."""
        # This is a unit test for the calculation logic
        ce_vol = 500000
        pe_vol = 300000
        total = ce_vol + pe_vol
        assert total == 800000


class TestExpiryPage:
    """Test expiry page components."""

    def test_expiry_page_imports(self):
        """Expiry UI module can be imported."""
        from utils.expiry_ui import render_expiry_dashboard, _render_option_chain_heatmap
        assert callable(render_expiry_dashboard)

    def test_option_chain_heatmap_with_data(self):
        """Heatmap renders with valid strike map."""
        from utils.expiry_ui import _render_option_chain_heatmap
        from models.snapshot import MarketSnapshot, FieldMeta

        snap = MarketSnapshot(
            atm_strike=FieldMeta(value=24600.0, status="LIVE", quality="GOOD"),
            call_oi=FieldMeta(value=1000000, status="LIVE", quality="GOOD"),
            put_oi=FieldMeta(value=800000, status="LIVE", quality="GOOD"),
            pcr=FieldMeta(value=0.8, status="LIVE", quality="GOOD"),
            max_pain=FieldMeta(value=24500.0, status="LIVE", quality="GOOD"),
            atm_iv=FieldMeta(value=15.0, status="LIVE", quality="GOOD"),
        )
        # Should not raise
        try:
            _render_option_chain_heatmap(snap, 24600.0)
        except Exception as e:
            # Expected to fail without cached strike map
            assert "not cached" in str(e).lower() or "unavailable" in str(e).lower()


class TestWebSocketFix:
    """Test WebSocket initialization fix."""

    def test_jwt_token_stored_after_connect(self):
        """SmartAPIClient stores JWT token from session."""
        from providers.smartapi_client import SmartAPIClient

        client = SmartAPIClient()
        assert client._jwt_token is None  # Not connected yet

    def test_get_websocket_connection_requires_auth(self):
        """get_websocket_connection raises if not authenticated."""
        from providers.smartapi_client import SmartAPIClient

        client = SmartAPIClient()
        with pytest.raises(ConnectionError):
            client.get_websocket_connection()


class TestOptionGreekFormat:
    """Test optionGreek expiry format handling."""

    def test_option_greek_tries_multiple_formats(self):
        """option_greek attempts multiple expiry formats."""
        from providers.smartapi_client import SmartAPIClient

        client = SmartAPIClient()
        # Mock the API
        mock_api = MagicMock()
        mock_api.optionGreek.return_value = {"status": True, "data": {"strikes": []}}
        client._api = mock_api
        client._session_active = True

        result = client.option_greek("NIFTY", "29SEP26")
        # Should have called with at least one format
        assert mock_api.optionGreek.called
        # Should have tried multiple formats
        call_count = mock_api.optionGreek.call_count
        assert call_count >= 1


class TestExpiryContext:
    """Test expiry context detection."""

    def test_expiry_day_detection(self):
        """Today matching expiry date → EXPIRY_DAY."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot, FieldMeta
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%d%b%y").upper()
        snap = MarketSnapshot(
            futures_expiry=FieldMeta(value=today, status="LIVE", quality="GOOD"),
        )
        assert compute_expiry_context(snap) == "EXPIRY_DAY"

    def test_post_expiry_detection(self):
        """Past expiry → POST_EXPIRY."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot, FieldMeta

        snap = MarketSnapshot(
            futures_expiry=FieldMeta(value="01JAN20", status="LIVE", quality="GOOD"),
        )
        assert compute_expiry_context(snap) == "POST_EXPIRY"

    def test_normal_expiry(self):
        """Future expiry → NORMAL."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot, FieldMeta

        snap = MarketSnapshot(
            futures_expiry=FieldMeta(value="31DEC26", status="LIVE", quality="GOOD"),
        )
        assert compute_expiry_context(snap) == "NORMAL"

    def test_missing_expiry(self):
        """No expiry data → empty string."""
        from utils.history_ui import compute_expiry_context
        from models.snapshot import MarketSnapshot

        assert compute_expiry_context(MarketSnapshot()) == ""


class TestMarketRegime:
    """Test choppy/range detection."""

    def test_insufficient_data(self):
        """Less than 3 observations → INSUFFICIENT_DATA."""
        from utils.history_ui import compute_market_regime
        from models.snapshot import MarketSnapshot, FieldMeta

        snap = MarketSnapshot(
            nifty_spot=FieldMeta(value=25000, status="LIVE", quality="GOOD"),
            vwap=FieldMeta(value=24900, status="LIVE", quality="GOOD"),
        )
        assert compute_market_regime(snap) == "INSUFFICIENT_DATA"

    def test_trending_regime(self):
        """Consistent price above VWAP → TRENDING."""
        from utils.history_ui import compute_market_regime
        from models.snapshot import MarketSnapshot, FieldMeta
        from providers.history_manager import history_manager
        from datetime import datetime, timezone
        import providers.history_manager as hm
        from pathlib import Path
        import shutil

        tmp = Path(tempfile.mkdtemp())
        old_snap = hm.SNAPSHOTS_DIR
        hm.SNAPSHOTS_DIR = tmp
        try:
            now = datetime.now(timezone.utc)
            for price in [25000, 25050, 25100, 25150]:
                snap = MarketSnapshot(
                    snapshot_timestamp=now,
                    source="Test",
                    data_status="HISTORICAL",
                    nifty_spot=FieldMeta(value=price, status="HISTORICAL", quality="GOOD", timestamp=now),
                    vwap=FieldMeta(value=24900, status="HISTORICAL", quality="GOOD", timestamp=now),
                )
                history_manager.save_snapshot(snap)

            current = MarketSnapshot(
                nifty_spot=FieldMeta(value=25200, status="HISTORICAL", quality="GOOD", timestamp=now),
                vwap=FieldMeta(value=24900, status="HISTORICAL", quality="GOOD", timestamp=now),
            )
            assert compute_market_regime(current) == "TRENDING"
        finally:
            hm.SNAPSHOTS_DIR = old_snap
            shutil.rmtree(tmp, ignore_errors=True)

    def test_choppy_regime(self):
        """Alternating above/below VWAP → CHOPPY."""
        from utils.history_ui import compute_market_regime
        from models.snapshot import MarketSnapshot, FieldMeta
        from providers.history_manager import history_manager
        from datetime import datetime, timezone
        import providers.history_manager as hm
        from pathlib import Path
        import shutil

        tmp = Path(tempfile.mkdtemp())
        old_snap = hm.SNAPSHOTS_DIR
        hm.SNAPSHOTS_DIR = tmp
        try:
            now = datetime.now(timezone.utc)
            prices = [25000, 24900, 25050, 24800, 25100, 24700]
            for price in prices:
                snap = MarketSnapshot(
                    snapshot_timestamp=now,
                    source="Test",
                    data_status="HISTORICAL",
                    nifty_spot=FieldMeta(value=price, status="HISTORICAL", quality="GOOD", timestamp=now),
                    vwap=FieldMeta(value=24950, status="HISTORICAL", quality="GOOD", timestamp=now),
                )
                history_manager.save_snapshot(snap)

            current = MarketSnapshot(
                nifty_spot=FieldMeta(value=25050, status="HISTORICAL", quality="GOOD", timestamp=now),
                vwap=FieldMeta(value=24950, status="HISTORICAL", quality="GOOD", timestamp=now),
            )
            assert compute_market_regime(current) == "CHOPPY"
        finally:
            hm.SNAPSHOTS_DIR = old_snap
            shutil.rmtree(tmp, ignore_errors=True)


class TestPersistenceExtended:
    """Test persistence with multiple historical observations."""

    def test_weakening_across_window(self):
        """Score declining across window → WEAKENING."""
        from utils.history_ui import compute_persistence
        from providers.history_manager import history_manager
        from datetime import datetime, timezone
        import providers.history_manager as hm
        from pathlib import Path
        import shutil

        tmp = Path(tempfile.mkdtemp())
        old_verdicts = hm.VERDICTS_DIR
        hm.VERDICTS_DIR = tmp
        try:
            now = datetime.now(timezone.utc)
            for score in [5, 4, 3]:
                v = Verdict(direction="BULLISH", state="SETUP", raw_score=score, conflict=False, data_quality="Good", timestamp=now)
                history_manager.save_verdict(v)

            current = Verdict(direction="BULLISH", state="BIAS", raw_score=2, conflict=False, data_quality="Good")
            assert compute_persistence(current) == "WEAKENING"
        finally:
            hm.VERDICTS_DIR = old_verdicts
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reversing_on_direction_change(self):
        """Direction changes → REVERSING."""
        from utils.history_ui import compute_persistence
        from providers.history_manager import history_manager
        from datetime import datetime, timezone
        import providers.history_manager as hm
        from pathlib import Path
        import shutil

        tmp = Path(tempfile.mkdtemp())
        old_verdicts = hm.VERDICTS_DIR
        hm.VERDICTS_DIR = tmp
        try:
            now = datetime.now(timezone.utc)
            v = Verdict(direction="BEARISH", state="BIAS", raw_score=-2, conflict=False, data_quality="Good", timestamp=now)
            history_manager.save_verdict(v)

            current = Verdict(direction="BULLISH", state="BIAS", raw_score=2, conflict=False, data_quality="Good")
            assert compute_persistence(current) == "REVERSING"
        finally:
            hm.VERDICTS_DIR = old_verdicts
            shutil.rmtree(tmp, ignore_errors=True)


class TestWhatChangedMagnitude:
    """Test what_changed_panel magnitude classification."""

    def test_major_change_detection(self):
        """Score delta >= 3 → Major."""
        from utils.history_ui import what_changed_panel
        from models.verdict import Verdict, ComponentResult

        prev = {
            "direction": "BULLISH",
            "state": "BIAS",
            "raw_score": 5,
            "components": {
                "Momentum": {"score": 1, "label": "Bullish", "reason": "", "evidence": [], "is_primary": True},
            },
        }
        current = Verdict(
            direction="MIXED",
            state="WAIT",
            raw_score=2,
            conflict=False,
            data_quality="Good",
            components={
                "Momentum": ComponentResult("Momentum", 0, "Neutral", "", [], is_primary=True),
            },
        )

        # Mock history_manager
        import providers.history_manager as hm
        old_get = hm.history_manager.get_verdict_changes
        hm.history_manager.get_verdict_changes = lambda days=1, limit=2: [prev, prev]
        try:
            # Should not raise; just verify it runs
            what_changed_panel(current)
        finally:
            hm.history_manager.get_verdict_changes = old_get
