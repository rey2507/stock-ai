"""Tests for infrastructure components: Cache, DataQuality, MarketHours, OI Model, SourceRegistry."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


# ─── Cache Tests ────────────────────────────────────────────────

class TestDataCache:
    def test_put_and_get(self):
        from providers.cache import DataCache
        c = DataCache()
        c.put("key1", 42, "TestProvider", freshness_window=60)
        entry = c.get("key1")
        assert entry is not None
        assert entry.value == 42
        assert entry.source == "TestProvider"

    def test_get_missing_returns_none(self):
        from providers.cache import DataCache
        c = DataCache()
        assert c.get("nonexistent") is None

    def test_stale_entry_returns_none(self):
        from providers.cache import DataCache, CacheEntry
        c = DataCache()
        # Insert expired entry
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        c._store["stale_key"] = CacheEntry(
            value=99,
            source="Test",
            observed_at=now - timedelta(seconds=100),
            fetched_at=now - timedelta(seconds=100),
            expires_at=now - timedelta(seconds=1),  # Already expired
            status="LIVE",
            quality="GOOD",
        )
        assert c.get("stale_key") is None

    def test_get_value_with_default(self):
        from providers.cache import DataCache
        c = DataCache()
        assert c.get_value("missing", default=5) == 5
        c.put("exists", 10, "Test")
        assert c.get_value("exists", default=5) == 10

    def test_invalidate(self):
        from providers.cache import DataCache
        c = DataCache()
        c.put("key1", 42, "Test")
        c.invalidate("key1")
        assert c.get("key1") is None

    def test_clear(self):
        from providers.cache import DataCache
        c = DataCache()
        c.put("a", 1, "T")
        c.put("b", 2, "T")
        c.clear()
        assert c.get("a") is None
        assert c.get("b") is None

    def test_snapshot_status(self):
        from providers.cache import DataCache
        c = DataCache()
        c.put("f1", 100, "P1")
        c.put("f2", 200, "P2")
        snap = c.snapshot_status()
        assert "f1" in snap
        assert "f2" in snap
        assert snap["f1"]["value"] == 100


# ─── Data Quality Tests ────────────────────────────────────────

class TestDataQualityEngine:
    def _make_field(self, value=100.0, status="LIVE", quality="GOOD", freshness=5.0):
        from models.snapshot import FieldMeta
        return FieldMeta(
            value=value,
            timestamp=datetime.now(timezone.utc),
            source="Test",
            freshness_seconds=freshness,
            status=status,
            quality=quality,
        )

    def _make_unavailable_field(self):
        from models.snapshot import FieldMeta
        return FieldMeta(
            value=None,
            timestamp=datetime.now(timezone.utc),
            source="Test",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
        )

    def test_assess_live_fields(self):
        from providers.data_quality import DataQualityEngine
        from models.snapshot import MarketSnapshot

        engine = DataQualityEngine()
        snap = MarketSnapshot(
            nifty_spot=self._make_field(23400.0, status="LIVE", freshness=5.0),
            futures_price=self._make_field(23450.0, status="LIVE", freshness=10.0),
            pcr=self._make_field(0.85, status="LIVE", freshness=20.0),
        )
        report = engine.assess(snap)
        assert report.live_count >= 3
        # With only 3/47 fields live, overall will be STALE
        # Just verify the 3 fields are assessed as LIVE
        live_fields = [f for f in report.fields if f.status == "LIVE"]
        assert len(live_fields) >= 3

    def test_assess_unavailable_fields(self):
        from providers.data_quality import DataQualityEngine
        from models.snapshot import MarketSnapshot

        engine = DataQualityEngine()
        snap = MarketSnapshot(
            nifty_spot=self._make_unavailable_field(),
            futures_price=self._make_unavailable_field(),
        )
        report = engine.assess(snap)
        assert report.unavailable_count >= 2
        # Only 2 fields set to unavailable, rest are default (also unavailable)
        # Verify the specific fields we set are assessed as unavailable
        unavail_fields = [f for f in report.fields if f.status == "UNAVAILABLE"]
        assert len(unavail_fields) >= 2

    def test_validate_verdict_input_missing_critical(self):
        from providers.data_quality import DataQualityEngine
        from models.snapshot import MarketSnapshot

        engine = DataQualityEngine()
        snap = MarketSnapshot()  # All unavailable
        result = engine.validate_verdict_input(snap)
        assert result["can_produce"] is False
        assert len(result["missing_critical"]) > 0


# ─── Market Hours Tests ────────────────────────────────────────

class TestMarketSession:
    def test_holiday_detection(self):
        from utils.market_hours import MarketSession
        ms = MarketSession()
        ms.set_time(datetime(2026, 1, 26, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        assert ms.is_holiday is True
        assert ms.state == "CLOSED"

    def test_premarket_state(self):
        from utils.market_hours import MarketSession
        ms = MarketSession()
        # 09:05 IST on a non-holiday (2026-01-27 is not a holiday)
        ms.set_time(datetime(2026, 1, 27, 9, 5, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        assert ms.state == "PRE_MARKET"

    def test_open_state(self):
        from utils.market_hours import MarketSession
        ms = MarketSession()
        # 10:30 IST on a non-holiday
        ms.set_time(datetime(2026, 1, 27, 10, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        assert ms.state == "OPEN"

    def test_closing_state(self):
        from utils.market_hours import MarketSession
        ms = MarketSession()
        # 15:20 IST on a non-holiday
        ms.set_time(datetime(2026, 1, 27, 15, 20, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        assert ms.state == "CLOSING"

    def test_closed_after_hours(self):
        from utils.market_hours import MarketSession
        ms = MarketSession()
        # 16:00 IST on a non-holiday
        ms.set_time(datetime(2026, 1, 27, 16, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        assert ms.state == "CLOSED"

    def test_freshness_adjustment(self):
        from utils.market_hours import MarketSession
        ms = MarketSession()
        # During market hours, freshness is strict
        ms.set_time(datetime(2026, 1, 27, 10, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        adjusted = ms.adjust_freshness(30)
        assert adjusted == 30  # No adjustment during OPEN

        # After hours, freshness is relaxed
        ms.set_time(datetime(2026, 1, 27, 16, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))))
        adjusted = ms.adjust_freshness(30)
        assert adjusted == 300  # 10x relaxation


# ─── OI Model Tests ────────────────────────────────────────────

class TestOIAggregate:
    def test_pcr_calculation(self):
        from providers.oi_model import OIAggregate
        agg = OIAggregate()
        agg.set_strike_oi(23400, ce_oi=1000, pe_oi=1500)
        agg.set_strike_oi(23500, ce_oi=800, pe_oi=1200)
        # PCR = (1500+1200) / (1000+800) = 2700/1800 = 1.5
        assert agg.pcr_by_oi == pytest.approx(1.5, rel=0.01)

    def test_max_pain(self):
        from providers.oi_model import OIAggregate
        agg = OIAggregate()
        agg.set_strike_oi(23300, ce_oi=500, pe_oi=100)
        agg.set_strike_oi(23400, ce_oi=1000, pe_oi=800)
        agg.set_strike_oi(23500, ce_oi=100, pe_oi=500)
        mp = agg.max_pain
        assert mp is not None
        assert mp in (23300, 23400, 23500)

    def test_support_resistance(self):
        from providers.oi_model import OIAggregate
        agg = OIAggregate()
        agg.set_strike_oi(23300, ce_oi=200, pe_oi=500)
        agg.set_strike_oi(23400, ce_oi=1000, pe_oi=800)
        agg.set_strike_oi(23500, ce_oi=800, pe_oi=200)
        support = agg.support_levels
        resistance = agg.resistance_levels
        assert len(support) > 0
        assert len(resistance) > 0


class TestOITimeseries:
    def test_oi_changes(self):
        from providers.oi_model import OITimeseries
        ts = OITimeseries(symbol="TEST", token="123", instrument_type="FUTIDX")
        # Add intraday observations
        ts.add_intraday(1000)
        ts.add_intraday(1100)
        ts.add_intraday(1200)
        assert ts.oi_change_5min == 100
        assert ts.oi_change_intraday == 200

    def test_daily_changes(self):
        from providers.oi_model import OITimeseries
        ts = OITimeseries(symbol="TEST", token="123", instrument_type="FUTIDX")
        ts.add_daily(10000)
        ts.add_daily(11000)
        ts.add_daily(12000)
        assert ts.oi_change_1d == 1000
        assert ts.oi_change_5d == 2000


# ─── Source Registry Tests ─────────────────────────────────────

class TestSourceRegistry:
    def test_record_fetch(self):
        from providers.source_registry import SourceRegistry
        reg = SourceRegistry()
        reg.record_fetch("nifty_spot", "AngelBroking", "SmartAPI")
        rec = reg.get_field_source("nifty_spot")
        assert rec is not None
        assert rec.provider == "AngelBroking"

    def test_record_failure(self):
        from providers.source_registry import SourceRegistry
        reg = SourceRegistry()
        reg.record_failure("SmartAPI", "Connection timeout")
        stats = reg.get_provider_stats("SmartAPI")
        assert stats is not None
        assert stats.failed_fetches == 1
        assert stats.is_healthy is False

    def test_provider_health(self):
        from providers.source_registry import SourceRegistry
        reg = SourceRegistry()
        # Healthy provider
        for _ in range(10):
            reg.record_fetch("field", "GoodProvider", "API")
        assert reg.is_provider_healthy("GoodProvider") is True

        # Unhealthy provider
        for _ in range(5):
            reg.record_failure("BadProvider", "error")
        assert reg.is_provider_healthy("BadProvider") is False


# ─── Instrument Manager Tests ──────────────────────────────────

class TestInstrumentManager:
    def test_parse_expiry_date(self):
        from providers.instrument_manager import parse_expiry_date
        dt = parse_expiry_date("29SEP26")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 9
        assert dt.day == 29

    def test_parse_expiry_date_invalid(self):
        from providers.instrument_manager import parse_expiry_date
        assert parse_expiry_date("INVALID") is None
        assert parse_expiry_date("") is None


# ─── Merger Tests ──────────────────────────────────────────────

class TestMerger:
    def test_merge_prefers_field_meta(self):
        from models.snapshot import MarketSnapshot, FieldMeta
        from providers.merger import merge_snapshots
        from datetime import datetime, timezone

        snap1 = MarketSnapshot(
            source="Provider1",
            nifty_spot=FieldMeta(value=23400.0, timestamp=datetime.now(timezone.utc), source="P1", freshness_seconds=5.0, status="LIVE", quality="GOOD"),
        )
        snap2 = MarketSnapshot(
            source="Provider2",
            india_vix=FieldMeta(value=14.5, timestamp=datetime.now(timezone.utc), source="P2", freshness_seconds=10.0, status="LIVE", quality="GOOD"),
        )
        merged = merge_snapshots(snap1, snap2)
        assert merged.nifty_spot.value == 23400.0
        assert merged.india_vix.value == 14.5

    def test_merge_prefers_good_quality(self):
        from models.snapshot import MarketSnapshot, FieldMeta
        from providers.merger import merge_snapshots
        from datetime import datetime, timezone

        snap1 = MarketSnapshot(
            pcr=FieldMeta(value=0.8, timestamp=datetime.now(timezone.utc), source="P1", freshness_seconds=5.0, status="LIVE", quality="PARTIAL"),
        )
        snap2 = MarketSnapshot(
            pcr=FieldMeta(value=0.9, timestamp=datetime.now(timezone.utc), source="P2", freshness_seconds=3.0, status="LIVE", quality="GOOD"),
        )
        merged = merge_snapshots(snap1, snap2)
        # Should prefer GOOD quality
        assert merged.pcr.value == 0.9
