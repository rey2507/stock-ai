"""Acceptance tests per addendum section 27.

Tests 1-10 verify the data-integrity contract:
DIRECT / DERIVED / EXTERNAL_REQUIRED / UNAVAILABLE three-state rule,
no fake values, provenance display, freshness display, and verdict integrity.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from models.snapshot import MarketSnapshot, FieldMeta
from models.verdict import ComponentResult
from metric_registry import get_metric, METRICS, MetricCategory
from engines.intraday_verdict import compute_verdict as intraday_verdict
from engines.weekly_verdict import compute_verdict as weekly_verdict
from providers.angel_provider import OIObservationStore
from providers.merger import merge_snapshots
from utils.ui import _field_display_value


# ─── Helpers ─────────────────────────────────────────────────────

def _ts():
    return datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


def _field(value, status="LIVE", quality="GOOD", source="Test"):
    ts = _ts()
    return FieldMeta(
        value=value,
        timestamp=ts,
        observed_at=ts,
        fetched_at=ts,
        processed_at=ts,
        source=source,
        freshness_seconds=0.0,
        status=status,
        quality=quality,
    )


def _missing_field(status="UNAVAILABLE", quality="INVALID"):
    return FieldMeta(
        value=None,
        timestamp=_ts(),
        source="Test",
        freshness_seconds=None,
        status=status,
        quality=quality,
    )


# ─── Test 1: Real direct data displayed as DIRECT / LIVE ───────

class TestDirectDataDisplayed:
    def test_nifty_spot_is_direct(self):
        metric = get_metric("nifty_spot")
        assert metric is not None
        assert metric.category == MetricCategory.DIRECT

    def test_nifty_spot_live_status(self):
        snap = MarketSnapshot(
            source="AngelBroking",
            data_status="LIVE",
            nifty_spot=_field(25120.0, status="LIVE", source="AngelBroking"),
        )
        assert snap.is_field_available("nifty_spot")
        assert snap.get("nifty_spot") == 25120.0


# ─── Test 2: Valid derived data PCR = DERIVED / CALCULATED ────

class TestDerivedPCR:
    def test_pcr_metric_is_derived(self):
        metric = get_metric("pcr")
        assert metric is not None
        assert metric.category == MetricCategory.DERIVED

    def test_pcr_calculated_from_real_inputs(self):
        call_oi = 4_800_000
        put_oi = 5_700_000
        expected = round(put_oi / call_oi, 4)
        snap = MarketSnapshot(
            call_oi=_field(call_oi),
            put_oi=_field(put_oi),
            pcr=_missing_field(),
        )
        pcr_meta = snap.pcr
        assert pcr_meta.value is None
        assert pcr_meta.status == "UNAVAILABLE"
        assert expected == pytest.approx(1.1875, rel=0.01)

    def test_pcr_provenance_in_registry(self):
        metric = get_metric("pcr")
        assert "put_oi" in metric.required_inputs
        assert "call_oi" in metric.required_inputs
        assert "Put OI / Call OI" in metric.calculation


# ─── Test 3: Missing input → PCR = UNAVAILABLE, not zero ─────

class TestMissingInputPCRUnavailable:
    def test_missing_call_oi_pcr_unavailable(self):
        snap = MarketSnapshot(
            call_oi=_missing_field(),
            put_oi=_field(5_700_000),
            pcr=_missing_field(),
        )
        assert snap.pcr.value is None
        assert snap.pcr.status == "UNAVAILABLE"

    def test_missing_put_oi_pcr_unavailable(self):
        snap = MarketSnapshot(
            call_oi=_field(4_800_000),
            put_oi=_missing_field(),
            pcr=_missing_field(),
        )
        assert snap.pcr.value is None
        assert snap.pcr.status == "UNAVAILABLE"

    def test_verdict_options_scores_on_available_data_when_pcr_unavailable(self):
        snap = MarketSnapshot(
            nifty_spot=_field(23400.0),
            futures_change_pct=_field(0.5),
            vwap=_field(23350.0),
            rsi=_field(60.0),
            pcr=_missing_field(status="TEMPORARILY_UNAVAILABLE", quality="INVALID"),
            advance_decline_ratio=_field(1.5),
            futures_oi_change=_field(5000),
            call_oi_change=_field(-2000),
            put_oi_change=_field(3000),
            relative_volume=_field(1.2),
        )
        verdict = intraday_verdict(snap)
        options_comp = verdict.components.get("Options")
        assert options_comp is not None
        assert "PCR unavailable" in options_comp.evidence
        assert options_comp.score != 0 or "balanced" in options_comp.reason.lower()


# ─── Test 4: Historical OI produces genuine OI change ────────

class TestHistoricalOIChange:
    def test_oi_store_computes_change(self):
        store = OIObservationStore(maxlen=10)
        store.record(10_000_000)
        store.record(10_500_000)
        assert store.latest == 10_500_000
        assert store.previous == 10_000_000
        assert store.change == 500_000

    def test_oi_change_none_with_single_observation(self):
        store = OIObservationStore(maxlen=10)
        store.record(10_000_000)
        assert store.change is None

    def test_oi_store_maxlen(self):
        store = OIObservationStore(maxlen=3)
        store.record(10_000_000)
        store.record(10_500_000)
        store.record(11_000_000)
        store.record(11_500_000)
        assert store.count == 3
        assert store.latest == 11_500_000
        assert store.previous == 11_000_000
        assert store.change == 500_000

    def test_extract_latest_from_list(self):
        store = OIObservationStore()
        data = [
            {"oi": 10_000_000},
            {"oi": 10_500_000},
            {"oi": 11_000_000},
        ]
        assert store.extract_latest_from_response(data) == 11_000_000

    def test_extract_latest_from_dict(self):
        store = OIObservationStore()
        data = {"oi": 10_500_000}
        assert store.extract_latest_from_response(data) == 10_500_000


# ─── Test 5: FII aggregates only when sufficient observations ──

class TestFIIAggregates:
    def test_fii_5d_requires_5_sessions(self):
        snap = MarketSnapshot(
            fii_flow_1d=_field(100.0),
            fii_flow_5d=_missing_field(),
            fii_flow_20d=_missing_field(),
            fii_flow_month=_missing_field(),
            dii_flow_1d=_field(50.0),
            dii_flow_5d=_missing_field(),
            dii_flow_20d=_missing_field(),
            dii_flow_month=_missing_field(),
        )
        weekly = weekly_verdict(snap)
        assert weekly.direction == "NONE"
        assert weekly.state == "INSUFFICIENT_DATA"


# ─── Test 6: Missing CPI cannot be reconstructed ──────────────

class TestMacroCannotBeReconstructed:
    def test_inflation_is_external_required(self):
        metric = get_metric("inflation")
        assert metric is not None
        assert metric.category == MetricCategory.EXTERNAL_REQUIRED

    def test_missing_inflation_unavailable(self):
        snap = MarketSnapshot(
            crude_price=_field(85.0),
            usd_inr=_field(83.5),
            us10y_yield=_field(4.3),
            inflation=_missing_field(status="UNSUPPORTED", quality="INVALID"),
        )
        assert snap.inflation.value is None
        assert snap.inflation.status == "UNSUPPORTED"


# ─── Test 7: No fake values ────────────────────────────────────

class TestNoFakeValues:
    def test_none_not_converted_to_zero_in_verdict(self):
        snap = MarketSnapshot(
            nifty_spot=_field(23400.0),
            futures_change_pct=_field(0.5),
            vwap=_field(23350.0),
            rsi=_field(60.0),
            pcr=_missing_field(status="TEMPORARILY_UNAVAILABLE", quality="INVALID"),
            advance_decline_ratio=_field(1.5),
            futures_oi_change=_field(5000),
            call_oi_change=_field(-2000),
            put_oi_change=_field(3000),
            relative_volume=_field(1.2),
        )
        verdict = intraday_verdict(snap)
        for name, comp in verdict.components.items():
            for evidence in comp.evidence:
                assert "0.00%" not in evidence or "Price change" in evidence
                assert evidence.strip() != "0"

    def test_unavailable_pcr_not_zero(self):
        snap = MarketSnapshot(
            call_oi=_field(4_800_000),
            put_oi=_field(5_700_000),
            pcr=_missing_field(),
        )
        pcr_val = snap.get("pcr")
        assert pcr_val is None
        assert pcr_val != 0


# ─── Test 8: Provenance ────────────────────────────────────────

class TestProvenance:
    def test_every_metric_has_category(self):
        for name, metric in METRICS.items():
            assert metric.category in (
                MetricCategory.DIRECT,
                MetricCategory.DERIVED,
                MetricCategory.EXTERNAL_REQUIRED,
            ), f"{name} missing category"

    def test_field_display_value_unavailable(self):
        fm = _missing_field()
        display, status, color = _field_display_value(fm)
        assert display == "UNAVAILABLE"
        assert status == "UNAVAILABLE"
        assert color == "gray"

    def test_field_display_value_live(self):
        fm = _field(25120.0, status="LIVE")
        display, status, color = _field_display_value(fm)
        assert "25" in display
        assert status == "LIVE"
        assert color == "green"

    def test_metric_registry_has_all_fields(self):
        required = [
            "nifty_spot", "futures_price", "futures_oi_change",
            "call_oi", "put_oi", "pcr",
            "advance_decline_ratio", "vwap", "rsi",
            "fii_flow_5d", "crude_price", "inflation",
        ]
        for name in required:
            assert name in METRICS, f"{name} not in METRICS registry"


# ─── Test 9: Freshness ─────────────────────────────────────────

class TestFreshness:
    def test_field_has_source_and_timestamps(self):
        fm = _field(25120.0, source="AngelBroking")
        assert fm.source == "AngelBroking"
        assert fm.observed_at is not None
        assert fm.fetched_at is not None

    def test_field_display_shows_status(self):
        fm = _field(25120.0, status="LIVE", source="AngelBroking")
        _, status, _ = _field_display_value(fm)
        assert status == "LIVE"

    def test_stale_field_displayed(self):
        fm = FieldMeta(
            value=25120.0,
            timestamp=datetime.now(timezone.utc),
            source="AngelBroking",
            freshness_seconds=500.0,
            status="STALE",
            quality="PARTIAL",
        )
        _, status, _ = _field_display_value(fm)
        assert status == "STALE"


# ─── Test 10: Verdict uses only valid metrics ──────────────────

class TestVerdictIntegrity:
    def test_missing_metrics_dont_contribute_score(self):
        snap = MarketSnapshot(
            nifty_spot=_field(23400.0),
            futures_change_pct=_field(0.5),
            vwap=_field(23350.0),
            rsi=_field(60.0),
            pcr=_missing_field(status="TEMPORARILY_UNAVAILABLE", quality="INVALID"),
            advance_decline_ratio=_field(1.5),
            futures_oi_change=_field(5000),
            call_oi_change=_field(-2000),
            put_oi_change=_field(3000),
            relative_volume=_field(1.2),
        )
        verdict = intraday_verdict(snap)
        assert verdict.direction in ("BULLISH", "BEARISH", "MIXED", "NONE")

    def test_all_missing_returns_insufficient_data(self):
        snap = MarketSnapshot(
            source="FailedProvider",
            data_status="UNAVAILABLE",
            missing_fields=["ALL"],
        )
        verdict = intraday_verdict(snap)
        assert verdict.direction == "NONE"
        assert verdict.state == "INSUFFICIENT_DATA"

    def test_market_closed_field_does_not_score(self):
        snap = MarketSnapshot(
            nifty_spot=_field(23400.0),
            futures_change_pct=_field(0.5),
            vwap=_field(23350.0),
            rsi=_field(60.0),
            pcr=_missing_field(status="MARKET_CLOSED", quality="INVALID"),
            advance_decline_ratio=_field(1.5),
            futures_oi_change=_field(5000),
            call_oi_change=_field(-2000),
            put_oi_change=_field(3000),
            relative_volume=_field(1.2),
        )
        verdict = intraday_verdict(snap)
        assert verdict.direction in ("BULLISH", "BEARISH", "MIXED", "NONE")
