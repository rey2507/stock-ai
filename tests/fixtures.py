"""Deterministic test fixtures for the verdict engine.

Each fixture is a carefully constructed MarketSnapshot that should produce
a specific verdict. Used for automated acceptance testing.
"""

from datetime import datetime, timezone
from models.snapshot import MarketSnapshot, FieldMeta


def _ts():
    return datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


def _ok(value):
    """Create a good-quality FieldMeta."""
    return FieldMeta(
        value=value,
        timestamp=_ts(),
        source="TestFixture",
        freshness_seconds=0.0,
        status="HISTORICAL",
        quality="GOOD",
    )


def _missing():
    """Create an unavailable FieldMeta."""
    return FieldMeta(
        value=None,
        timestamp=_ts(),
        source="TestFixture",
        freshness_seconds=None,
        status="UNAVAILABLE",
        quality="INVALID",
    )


SECTORS_BULLISH = {
    "Nifty Bank": 1.5, "Nifty IT": 1.2, "Nifty Pharma": 0.8,
    "Nifty Auto": 2.0, "Nifty FMCG": 0.5, "Nifty Metal": 1.1,
    "Nifty Realty": 1.8, "Nifty Infra": 0.9, "Nifty Energy": 0.3,
    "Nifty PSU Bank": 1.6,
}

SECTORS_BEARISH = {
    "Nifty Bank": -1.5, "Nifty IT": -2.0, "Nifty Pharma": -0.8,
    "Nifty Auto": -1.2, "Nifty FMCG": -0.5, "Nifty Metal": -2.5,
    "Nifty Realty": -1.8, "Nifty Infra": -0.9, "Nifty Energy": -0.3,
    "Nifty PSU Bank": -1.1,
}

SECTORS_MIXED = {
    "Nifty Bank": 1.5, "Nifty IT": -1.0, "Nifty Pharma": 0.8,
    "Nifty Auto": -0.5, "Nifty FMCG": 0.3, "Nifty Metal": -2.0,
    "Nifty Realty": 1.2, "Nifty Infra": -0.4, "Nifty Energy": 0.1,
    "Nifty PSU Bank": 0.6,
}


def fixture_strong_bullish_intraday() -> MarketSnapshot:
    """Test 1: All components bullish. Expected: BULLISH SETUP (+5)."""
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestFixture",
        data_status="HISTORICAL",
        missing_fields=[],
        # Price above VWAP → bullish momentum
        nifty_spot=_ok(25000.0),
        nifty_change=_ok(200.0),
        nifty_change_pct=_ok(0.8),
        vwap=_ok(24800.0),
        rsi=_ok(65.0),
        relative_volume=_ok(1.5),
        # Futures: price up + OI up → bullish
        futures_change_pct=_ok(0.9),
        futures_oi_change=_ok(500_000),
        # Options: put OI rising, PCR > 1
        call_oi_change=_ok(-100_000),
        put_oi_change=_ok(200_000),
        pcr=_ok(1.15),
        atm_iv=_ok(13.0),
        # Participation: broad advance
        advances=_ok(35),
        declines=_ok(15),
        advance_decline_ratio=_ok(2.33),
        sector_performance=_ok(SECTORS_BULLISH),
    )


def fixture_strong_bearish_intraday() -> MarketSnapshot:
    """Test 2: All components bearish. Expected: BEARISH SETUP (-5)."""
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestFixture",
        data_status="HISTORICAL",
        missing_fields=[],
        nifty_spot=_ok(24500.0),
        nifty_change=_ok(-200.0),
        nifty_change_pct=_ok(-0.8),
        vwap=_ok(24700.0),
        rsi=_ok(35.0),
        relative_volume=_ok(1.4),
        futures_change_pct=_ok(-0.9),
        futures_oi_change=_ok(600_000),
        call_oi_change=_ok(300_000),
        put_oi_change=_ok(-150_000),
        pcr=_ok(0.7),
        atm_iv=_ok(18.0),
        advances=_ok(12),
        declines=_ok(38),
        advance_decline_ratio=_ok(0.32),
        sector_performance=_ok(SECTORS_BEARISH),
    )


def fixture_neutral_intraday() -> MarketSnapshot:
    """Test 3: All components neutral. Expected: MIXED / WAIT (0)."""
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestFixture",
        data_status="HISTORICAL",
        missing_fields=[],
        nifty_spot=_ok(24850.0),
        nifty_change=_ok(0.0),
        nifty_change_pct=_ok(0.0),
        vwap=_ok(24850.0),
        rsi=_ok(50.0),
        relative_volume=_ok(1.0),
        futures_change_pct=_ok(0.0),
        futures_oi_change=_ok(0),
        call_oi_change=_ok(0),
        put_oi_change=_ok(0),
        pcr=_ok(0.95),
        atm_iv=_ok(14.0),
        advances=_ok(25),
        declines=_ok(25),
        advance_decline_ratio=_ok(1.0),
        sector_performance=_ok(SECTORS_MIXED),
    )


def fixture_major_conflict_intraday() -> MarketSnapshot:
    """Test 4: Major conflict — Momentum bullish, Futures & Participation bearish.
    Expected: MIXED / WAIT (conflict overrides score).
    """
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestFixture",
        data_status="HISTORICAL",
        missing_fields=[],
        # Momentum bullish: price above VWAP, RSI > 50
        nifty_spot=_ok(25000.0),
        nifty_change=_ok(100.0),
        nifty_change_pct=_ok(0.4),
        vwap=_ok(24900.0),
        rsi=_ok(58.0),
        relative_volume=_ok(1.0),  # Volume neutral
        # Futures bearish: price down + OI up
        futures_change_pct=_ok(-0.5),
        futures_oi_change=_ok(400_000),
        # Options bullish (confirmation, not primary)
        call_oi_change=_ok(-50_000),
        put_oi_change=_ok(100_000),
        pcr=_ok(1.05),
        atm_iv=_ok(14.0),
        # Participation bearish
        advances=_ok(15),
        declines=_ok(35),
        advance_decline_ratio=_ok(0.43),
        sector_performance=_ok(SECTORS_BEARISH),
    )


def fixture_bullish_bias_intraday() -> MarketSnapshot:
    """Test 5: Score +2, no conflict. Expected: BULLISH BIAS."""
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestFixture",
        data_status="HISTORICAL",
        missing_fields=[],
        # Momentum bullish
        nifty_spot=_ok(25000.0),
        nifty_change=_ok(100.0),
        nifty_change_pct=_ok(0.4),
        vwap=_ok(24900.0),
        rsi=_ok(58.0),
        relative_volume=_ok(1.0),  # Volume neutral
        # Futures bullish
        futures_change_pct=_ok(0.5),
        futures_oi_change=_ok(300_000),
        # Options neutral
        call_oi_change=_ok(50_000),
        put_oi_change=_ok(50_000),
        pcr=_ok(0.95),
        atm_iv=_ok(14.0),
        # Participation neutral
        advances=_ok(24),
        declines=_ok(26),
        advance_decline_ratio=_ok(0.92),
        sector_performance=_ok(SECTORS_MIXED),
    )


def fixture_missing_critical_data() -> MarketSnapshot:
    """Test 6: Nifty price missing. Expected: INSUFFICIENT DATA."""
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestFixture",
        data_status="HISTORICAL",
        missing_fields=["nifty_spot"],
        nifty_spot=_missing(),  # Critical field missing
        nifty_change=_ok(100.0),
        nifty_change_pct=_ok(0.4),
        vwap=_ok(24900.0),
        rsi=_ok(58.0),
        relative_volume=_ok(1.2),
        futures_price=_ok(25050.0),
        futures_change_pct=_ok(0.5),
        futures_oi=_ok(12_000_000),
        futures_oi_change=_ok(300_000),
        call_oi=_ok(8_000_000),
        put_oi=_ok(7_500_000),
        call_oi_change=_ok(50_000),
        put_oi_change=_ok(60_000),
        pcr=_ok(0.94),
        atm_iv=_ok(14.0),
        advances=_ok(28),
        declines=_ok(22),
        advance_decline_ratio=_ok(1.27),
        sector_performance=_ok(SECTORS_MIXED),
    )


def fixture_provider_failure() -> MarketSnapshot:
    """Test 7: All fields unavailable (simulates API failure).
    Expected: INSUFFICIENT DATA.
    """
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="UnknownProvider",
        data_status="UNAVAILABLE",
        missing_fields=["ALL"],
    )


def fixture_source_metadata() -> MarketSnapshot:
    """Test 8: Verify source metadata is preserved."""
    return MarketSnapshot(
        snapshot_timestamp=_ts(),
        timezone="Asia/Kolkata",
        source="TestProvider",
        data_status="LIVE",
        missing_fields=[],
        nifty_spot=_ok(24850.0),
        nifty_change_pct=_ok(0.0),
        vwap=_ok(24850.0),
        rsi=_ok(50.0),
        relative_volume=_ok(1.0),
        futures_change_pct=_ok(0.0),
        futures_oi_change=_ok(0),
        call_oi_change=_ok(0),
        put_oi_change=_ok(0),
        pcr=_ok(1.0),
        advances=_ok(25),
        declines=_ok(25),
        advance_decline_ratio=_ok(1.0),
        sector_performance=_ok(SECTORS_MIXED),
    )
