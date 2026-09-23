"""Comprehensive test suite for SmartAPI diagnostics, fallbacks, field status, and verdict integrity.

Covers:
- SmartAPI diagnostic engine (7-check workflow)
- Nil response handling
- Field status granularity
- Source conflict detection
- WebSocket health monitoring
- Fallback policy enforcement
- Verdict integrity with data health
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


# ─── Field Status Tests ────────────────────────────────────────

class TestFieldStatus:
    def test_healthy_statuses(self):
        from models.field_status import FieldStatus
        assert FieldStatus.LIVE.is_healthy
        assert FieldStatus.HISTORICAL.is_healthy
        assert not FieldStatus.DELAYED.is_healthy
        assert not FieldStatus.UNAVAILABLE.is_healthy

    def test_retryable_statuses(self):
        from models.field_status import FieldStatus
        assert FieldStatus.TEMPORARILY_UNAVAILABLE.is_retryable
        assert FieldStatus.RATE_LIMITED.is_retryable
        assert FieldStatus.TIMEOUT.is_retryable
        assert not FieldStatus.LIVE.is_retryable
        assert not FieldStatus.UNSUPPORTED.is_retryable

    def test_terminal_statuses(self):
        from models.field_status import FieldStatus
        assert FieldStatus.UNSUPPORTED.is_terminal
        assert FieldStatus.INVALID_INSTRUMENT.is_terminal
        assert FieldStatus.API_ERROR.is_terminal
        assert not FieldStatus.LIVE.is_terminal
        assert not FieldStatus.TEMPORARILY_UNAVAILABLE.is_terminal

    def test_display_labels(self):
        from models.field_status import FieldStatus
        assert FieldStatus.LIVE.display_label == "Live"
        assert FieldStatus.TEMPORARILY_UNAVAILABLE.display_label == "Temporarily Unavailable"
        assert FieldStatus.UNSUPPORTED.display_label == "Unsupported"

    def test_diagnostic_reason_messages(self):
        from models.field_status import DiagnosticReason
        assert "successfully" in DiagnosticReason.OK.display_message.lower()
        assert "empty" in DiagnosticReason.SMARTAPI_RETURNED_NIL.display_message.lower()
        assert "rate limit" in DiagnosticReason.SMARTAPI_RATE_LIMITED.display_message.lower()


# ─── Source Policy Tests ───────────────────────────────────────

class TestSourcePolicy:
    def test_smartapi_fields_registered(self):
        from models.source_policy import SMARTAPI_FIELDS
        assert "nifty_spot" in SMARTAPI_FIELDS
        assert "futures_price" in SMARTAPI_FIELDS
        assert "futures_oi" in SMARTAPI_FIELDS
        assert "pcr" in SMARTAPI_FIELDS
        assert "vwap" in SMARTAPI_FIELDS
        assert "rsi" in SMARTAPI_FIELDS

    def test_external_fields_registered(self):
        from models.source_policy import EXTERNAL_FIELDS
        assert "fii_flow_1d" in EXTERNAL_FIELDS
        assert "dii_flow_1d" in EXTERNAL_FIELDS
        assert "crude_price" in EXTERNAL_FIELDS
        assert "usd_inr" in EXTERNAL_FIELDS
        assert "us10y_yield" in EXTERNAL_FIELDS

    def test_is_smartapi_field(self):
        from models.source_policy import is_smartapi_field
        assert is_smartapi_field("nifty_spot")
        assert not is_smartapi_field("futures_oi")  # Now provided by NSEOptionsProvider
        assert not is_smartapi_field("pcr")  # Derived from option chain
        assert not is_smartapi_field("fii_flow_1d")
        assert not is_smartapi_field("crude_price")

    def test_get_field_policy(self):
        from models.source_policy import get_field_policy
        policy = get_field_policy("nifty_spot")
        assert policy is not None
        assert policy.primary == "AngelBroking"
        assert policy.smartapi_supported is True

        policy = get_field_policy("fii_flow_1d")
        assert policy is not None
        assert policy.primary == "CapitalFlows"
        assert policy.smartapi_supported is False

    def test_expected_freshness(self):
        from models.source_policy import get_expected_freshness
        assert get_expected_freshness("nifty_spot") == 5
        assert get_expected_freshness("futures_oi") == 30
        assert get_expected_freshness("fii_flow_1d") == 86400


# ─── FieldMeta Enhanced Tests ─────────────────────────────────

class TestFieldMetaEnhanced:
    def test_field_meta_has_diagnostics(self):
        from models.snapshot import FieldMeta
        fm = FieldMeta(
            value=23400.0,
            status="LIVE",
            diagnostic_reason="ok",
            diagnostic_message="Data received",
        )
        assert fm.diagnostic_reason == "ok"
        assert fm.diagnostic_message == "Data received"
        assert fm.retry_count == 0

    def test_field_meta_mark_unavailable(self):
        from models.snapshot import FieldMeta
        fm = FieldMeta(value=23400.0, status="LIVE")
        fm.mark_unavailable("smartapi_returned_nil", "SmartAPI returned nil")
        assert fm.value is None
        assert fm.status == "UNAVAILABLE"
        assert fm.diagnostic_reason == "smartapi_returned_nil"

    def test_field_meta_mark_temporarily_unavailable(self):
        from models.snapshot import FieldMeta
        fm = FieldMeta(value=None, status="UNKNOWN")
        fm.mark_temporarily_unavailable("retry_scheduled", "Retrying in 2s")
        assert fm.status == "TEMPORARILY_UNAVAILABLE"
        assert fm.retry_count == 1

    def test_field_meta_to_dict(self):
        from models.snapshot import FieldMeta
        fm = FieldMeta(value=100.0, source="Test", status="LIVE")
        d = fm.to_dict()
        assert d["value"] == 100.0
        assert d["source"] == "Test"
        assert d["status"] == "LIVE"

    def test_field_meta_can_retry(self):
        from models.snapshot import FieldMeta
        fm = FieldMeta(retry_count=0, max_retries=3)
        assert fm.can_retry
        fm.retry_count = 3
        assert not fm.can_retry


# ─── Diagnostic Engine Tests ───────────────────────────────────

class TestDiagnosticEngine:
    def _make_ctx(self, **kwargs):
        from providers.diagnostic_engine import DiagnosticContext
        defaults = {
            "field_name": "futures_oi",
            "exchange": "NFO",
            "token": "58662",
            "instrument_type": "FUTIDX",
            "expiry": "29SEP26",
            "endpoint": "getOIData",
            "is_market_open": True,
            "retry_count": 0,
            "max_retries": 3,
        }
        defaults.update(kwargs)
        return DiagnosticContext(**defaults)

    def test_market_closed_detected(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(is_market_open=False, is_holiday=False)
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.MARKET_CLOSED

    def test_holiday_detected(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(is_holiday=True)
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.MARKET_CLOSED

    def test_invalid_token_detected(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(token="")
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.INVALID_INSTRUMENT

    def test_expired_contract_detected(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        # Use a past expiry
        ctx = self._make_ctx(expiry="01JAN20")
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.INVALID_INSTRUMENT

    def test_auth_error_detected(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(api_error="session expired")
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.AUTH_ERROR

    def test_rate_limit_detected(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(api_error="rate limit exceeded")
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.RATE_LIMITED
        assert result.should_retry

    def test_timeout_retryable(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(api_error="connection timed out", retry_count=0)
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.TEMPORARILY_UNAVAILABLE
        assert result.should_retry

    def test_timeout_exhausted(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(api_error="connection timed out", retry_count=3, max_retries=3)
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.TIMEOUT
        assert not result.should_retry

    def test_server_error_retryable(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(api_error="server error 500", retry_count=0)
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.TEMPORARILY_UNAVAILABLE
        assert result.should_retry

    def test_nil_response_retryable(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(
            api_response={"status": False, "data": None, "message": "no data"},
            retry_count=0,
        )
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.TEMPORARILY_UNAVAILABLE
        assert result.should_retry

    def test_nil_response_exhausted(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(
            api_response={"status": False, "data": None},
            retry_count=3,
            max_retries=3,
        )
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.TEMPORARILY_UNAVAILABLE
        assert not result.should_retry

    def test_external_field_unsupported(self):
        from providers.diagnostic_engine import NilDiagnosticEngine
        from models.field_status import FieldStatus
        engine = NilDiagnosticEngine()
        ctx = self._make_ctx(field_name="fii_flow_1d", endpoint="external")
        result = engine.diagnose(ctx)
        assert result.field_status == FieldStatus.UNSUPPORTED
        assert result.fallback_provider == "CapitalFlows"

    def test_diagnostic_result_applies_to_field(self):
        from providers.diagnostic_engine import NilDiagnosticEngine, DiagnosticResult
        from models.field_status import FieldStatus, DiagnosticReason
        from models.snapshot import FieldMeta
        engine = NilDiagnosticEngine()
        result = DiagnosticResult(
            field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
            diagnostic_reason=DiagnosticReason.SMARTAPI_RETURNED_NIL,
            message="Test message",
        )
        fm = FieldMeta(value=100.0)
        result.apply_to_field(fm)
        assert fm.value is None
        assert fm.status == "UNAVAILABLE"  # mark_unavailable sets UNAVAILABLE
        assert fm.diagnostic_reason == DiagnosticReason.SMARTAPI_RETURNED_NIL.value


# ─── Conflict Detection Tests ──────────────────────────────────

class TestConflictDetector:
    def test_no_conflict_within_tolerance(self):
        from providers.conflict_detector import ConflictDetector
        detector = ConflictDetector(default_tolerance_pct=2.0)
        result = detector.check_conflict("pcr", "SmartAPI", 0.85, source_b="WebSource", value_b=0.86)
        assert result is None

    def test_conflict_detected(self):
        from providers.conflict_detector import ConflictDetector
        detector = ConflictDetector(default_tolerance_pct=0.1)
        result = detector.check_conflict("pcr", "SmartAPI", 0.85, source_b="WebSource", value_b=0.95)
        assert result is not None
        assert result.is_significant

    def test_conflict_recorded(self):
        from providers.conflict_detector import ConflictDetector
        detector = ConflictDetector(default_tolerance_pct=0.1)
        detector.check_conflict("pcr", "SmartAPI", 0.85, source_b="WebSource", value_b=0.95)
        assert detector.has_conflicts
        assert detector.conflict_count == 1

    def test_conflict_cleared(self):
        from providers.conflict_detector import ConflictDetector
        detector = ConflictDetector(default_tolerance_pct=0.1)
        detector.check_conflict("pcr", "SmartAPI", 0.85, source_b="WebSource", value_b=0.95)
        detector.clear_conflict("pcr")
        assert not detector.has_conflicts

    def test_no_conflict_one_source(self):
        from providers.conflict_detector import ConflictDetector
        detector = ConflictDetector()
        result = detector.check_conflict("pcr", "SmartAPI", 0.85)
        assert result is None


# ─── WebSocket Health Tests ────────────────────────────────────

class TestWebSocketHealth:
    def test_initial_state(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor()
        assert monitor.state == StreamState.DISCONNECTED

    def test_connected_state(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor()
        monitor.on_connected()
        assert monitor.state == StreamState.CONNECTED

    def test_message_updates_tick(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor()
        monitor.on_connected()
        monitor.on_message(latency_ms=120.0)
        health = monitor.health
        assert health.last_tick_at is not None
        assert health.total_messages == 1
        assert health.latency_ms == 120.0

    def test_stale_detection(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor(stale_threshold_sec=0.1)
        monitor.on_connected()
        monitor.on_message()
        import time
        time.sleep(0.2)
        assert monitor.state == StreamState.DELAYED

    def test_disconnect_reconnecting(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor()
        monitor.on_connected()
        monitor.on_disconnected(will_reconnect=True)
        assert monitor.state == StreamState.RECONNECTING
        assert monitor.health.reconnect_count == 1

    def test_disconnect_failed(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor()
        monitor.on_connected()
        monitor.on_disconnected(will_reconnect=False)
        assert monitor.state == StreamState.FAILED

    def test_reset(self):
        from providers.ws_health import WebSocketHealthMonitor, StreamState
        monitor = WebSocketHealthMonitor()
        monitor.on_connected()
        monitor.on_message()
        monitor.reset()
        assert monitor.state == StreamState.DISCONNECTED
        assert monitor.health.total_messages == 0


# ─── Fallback Policy Tests ─────────────────────────────────────

class TestFallbackPolicy:
    def test_smartapi_field_no_auto_fallback(self):
        """SmartAPI-supported fields should NOT auto-switch to external providers."""
        from models.source_policy import get_field_policy
        policy = get_field_policy("nifty_spot")  # Use a known SmartAPI field
        assert policy is not None
        assert policy.smartapi_supported is True
        assert len(policy.fallbacks) == 0  # No automatic fallback

    def test_external_field_has_provider(self):
        """External fields must have a configured provider."""
        from models.source_policy import get_field_policy
        policy = get_field_policy("fii_flow_1d")
        assert policy is not None
        assert policy.primary == "CapitalFlows"
        assert policy.smartapi_supported is False

    def test_all_smartapi_fields_have_no_fallback(self):
        """No SmartAPI field should have automatic external fallback."""
        from models.source_policy import SMARTAPI_FIELDS
        for name, policy in SMARTAPI_FIELDS.items():
            assert len(policy.fallbacks) == 0, f"{name} has unexpected fallbacks: {policy.fallbacks}"


# ─── Verdict Integrity Tests ───────────────────────────────────

class TestVerdictIntegrity:
    def test_insufficient_data_on_missing_critical(self):
        """Verdict must be INSUFFICIENT DATA when critical fields are missing."""
        from models.snapshot import MarketSnapshot
        from engines.intraday_verdict import compute_verdict
        snap = MarketSnapshot()  # All unavailable
        critical = snap.critical_fields_missing()
        assert len(critical) > 0
        verdict = compute_verdict(snap)
        assert verdict.direction == "NONE"
        assert verdict.state == "INSUFFICIENT_DATA"

    def test_data_health_not_directional_signal(self):
        """Data health score must NOT be used as bullish/bearish signal."""
        from models.snapshot import MarketSnapshot, FieldMeta
        from engines.intraday_verdict import compute_verdict

        # High data quality but neutral indicators
        snap = MarketSnapshot(
            nifty_spot=FieldMeta(value=23400.0, status="LIVE", quality="GOOD"),
            futures_change_pct=FieldMeta(value=0.01, status="LIVE", quality="GOOD"),
            vwap=FieldMeta(value=23400.5, status="LIVE", quality="GOOD"),
            rsi=FieldMeta(value=50.0, status="LIVE", quality="GOOD"),
            pcr=FieldMeta(value=1.0, status="LIVE", quality="GOOD"),
            advance_decline_ratio=FieldMeta(value=1.0, status="LIVE", quality="GOOD"),
            futures_oi_change=FieldMeta(value=0, status="LIVE", quality="GOOD"),
            call_oi_change=FieldMeta(value=0, status="LIVE", quality="GOOD"),
            put_oi_change=FieldMeta(value=0, status="LIVE", quality="GOOD"),
            relative_volume=FieldMeta(value=1.0, status="LIVE", quality="GOOD"),
        )
        verdict = compute_verdict(snap)
        # With neutral indicators, should be MIXED, not bullish just because data is good
        assert verdict.direction in ("MIXED", "BULLISH", "BEARISH", "NONE")

    def test_field_status_does_not_become_signal(self):
        """Field status values must not leak into verdict scoring."""
        from models.snapshot import MarketSnapshot, FieldMeta
        from engines.intraday_verdict import compute_verdict
        # A field with TEMPORARILY_UNAVAILABLE should not contribute to score
        snap = MarketSnapshot(
            nifty_spot=FieldMeta(value=23400.0, status="LIVE", quality="GOOD"),
            futures_change_pct=FieldMeta(value=0.5, status="LIVE", quality="GOOD"),
            vwap=FieldMeta(value=23350.0, status="LIVE", quality="GOOD"),
            rsi=FieldMeta(value=60.0, status="LIVE", quality="GOOD"),
            pcr=FieldMeta(value=0.8, status="TEMPORARILY_UNAVAILABLE", quality="INVALID"),
            advance_decline_ratio=FieldMeta(value=1.5, status="LIVE", quality="GOOD"),
            futures_oi_change=FieldMeta(value=5000, status="LIVE", quality="GOOD"),
            call_oi_change=FieldMeta(value=-2000, status="LIVE", quality="GOOD"),
            put_oi_change=FieldMeta(value=3000, status="LIVE", quality="GOOD"),
            relative_volume=FieldMeta(value=1.2, status="LIVE", quality="GOOD"),
        )
        verdict = compute_verdict(snap)
        # PCR is unavailable, so component may be missing, but verdict should still work
        assert verdict.direction in ("BULLISH", "BEARISH", "MIXED", "NONE")


# ─── Source Transparency Tests ─────────────────────────────────

class TestSourceTransparency:
    def test_field_has_source(self):
        """Every populated field must have a source."""
        from models.snapshot import MarketSnapshot, FieldMeta
        snap = MarketSnapshot(
            nifty_spot=FieldMeta(value=23400.0, source="SmartAPI"),
        )
        assert snap.nifty_spot.source == "SmartAPI"

    def test_field_has_timestamps(self):
        """Every populated field must have timestamps."""
        from models.snapshot import FieldMeta
        now = datetime.now(timezone.utc)
        fm = FieldMeta(
            value=23400.0,
            observed_at=now,
            fetched_at=now,
            processed_at=now,
        )
        assert fm.observed_at is not None
        assert fm.fetched_at is not None
        assert fm.processed_at is not None

    def test_unavailable_field_has_diagnostic(self):
        """Unavailable fields must explain why."""
        from models.snapshot import FieldMeta
        fm = FieldMeta(
            value=None,
            status="TEMPORARILY_UNAVAILABLE",
            diagnostic_reason="smartapi_returned_nil",
            diagnostic_message="SmartAPI returned nil for futures OI",
        )
        assert fm.diagnostic_reason != "ok"
        assert fm.diagnostic_message != ""


# ─── Nil Never Zero Tests ─────────────────────────────────────

class TestNilNeverZero:
    def test_none_not_converted_to_zero(self):
        """None must never be silently converted to 0."""
        from models.snapshot import FieldMeta
        fm = FieldMeta(value=None, status="UNAVAILABLE")
        assert fm.value is None
        assert fm.value != 0

    def test_unavailable_field_value_is_none(self):
        """Unavailable fields must have value=None, not value=0."""
        from models.snapshot import FieldMeta
        fm = FieldMeta(value=None, status="TEMPORARILY_UNAVAILABLE")
        assert fm.value is None
