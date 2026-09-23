"""Field-Level Source Ownership Policy.

Defines which provider owns each canonical field.
SmartAPI is primary for all market data it supports.
External providers only for fields SmartAPI genuinely cannot provide.

No application-wide fallback — each field has its own source chain.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SourcePolicy:
    """Source policy for a single field."""
    field_name: str
    primary: str                # Primary provider name
    fallbacks: list[str]        # Fallback chain (empty = no automatic fallback)
    frequency: str              # "realtime" | "intraday" | "daily" | "weekly"
    expected_freshness_sec: int # Expected freshness window in seconds
    smartapi_supported: bool    # Whether SmartAPI can provide this field
    description: str = ""       # Human-readable description


# ─── SmartAPI-owned fields ──────────────────────────────────────
# These fields MUST come from SmartAPI when available.
# Do NOT substitute with external providers on temporary failure.

SMARTAPI_FIELDS = {
    # Market prices
    "nifty_spot": SourcePolicy(
        field_name="nifty_spot",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY 50 spot index price",
    ),
    "nifty_change": SourcePolicy(
        field_name="nifty_change",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY spot change from previous close",
    ),
    "nifty_change_pct": SourcePolicy(
        field_name="nifty_change_pct",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY spot percentage change",
    ),
    "nifty_open": SourcePolicy(
        field_name="nifty_open",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY opening price",
    ),
    "nifty_high": SourcePolicy(
        field_name="nifty_high",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY intraday high",
    ),
    "nifty_low": SourcePolicy(
        field_name="nifty_low",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY intraday low",
    ),
    "nifty_volume": SourcePolicy(
        field_name="nifty_volume",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY trading volume",
    ),

    # Futures
    "futures_price": SourcePolicy(
        field_name="futures_price",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="Nearest expiry NIFTY futures price",
    ),
    "futures_change_pct": SourcePolicy(
        field_name="futures_change_pct",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=5,
        smartapi_supported=True,
        description="NIFTY futures percentage change",
    ),
    "futures_oi": SourcePolicy(
        field_name="futures_oi",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="NIFTY futures open interest",
    ),
    "futures_oi_change": SourcePolicy(
        field_name="futures_oi_change",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="NIFTY futures OI change",
    ),
    "futures_expiry": SourcePolicy(
        field_name="futures_expiry",
        primary="AngelBroking",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400,
        smartapi_supported=True,
        description="Current futures expiry date",
    ),

    # Options
    "atm_strike": SourcePolicy(
        field_name="atm_strike",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="At-the-money strike price",
    ),
    "call_oi": SourcePolicy(
        field_name="call_oi",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="Total call option OI (ATM ± 2 strikes)",
    ),
    "put_oi": SourcePolicy(
        field_name="put_oi",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="Total put option OI (ATM ± 2 strikes)",
    ),
    "call_oi_change": SourcePolicy(
        field_name="call_oi_change",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="Call option OI change",
    ),
    "put_oi_change": SourcePolicy(
        field_name="put_oi_change",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="Put option OI change",
    ),
    "pcr": SourcePolicy(
        field_name="pcr",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=False,
        description="Put-Call Ratio by OI",
    ),
    "atm_iv": SourcePolicy(
        field_name="atm_iv",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=120,
        smartapi_supported=False,
        description="At-the-money implied volatility",
    ),
    "max_pain": SourcePolicy(
        field_name="max_pain",
        primary="NSEOptions",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=120,
        smartapi_supported=False,
        description="Max pain strike price",
    ),

    # Derived from candles
    "vwap": SourcePolicy(
        field_name="vwap",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=True,
        description="Volume-weighted average price",
    ),
    "rsi": SourcePolicy(
        field_name="rsi",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=30,
        smartapi_supported=True,
        description="Relative Strength Index (14-period)",
    ),
    "atr": SourcePolicy(
        field_name="atr",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=60,
        smartapi_supported=True,
        description="Average True Range",
    ),
    "relative_volume": SourcePolicy(
        field_name="relative_volume",
        primary="AngelBroking",
        fallbacks=[],
        frequency="realtime",
        expected_freshness_sec=60,
        smartapi_supported=True,
        description="Current volume / average volume",
    ),

    # NSE stats from external source
    "advances": SourcePolicy(
        field_name="advances",
        primary="NSEOptions",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=120,
        smartapi_supported=False,
        description="Advancing stocks count",
    ),
    "declines": SourcePolicy(
        field_name="declines",
        primary="NSEOptions",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=120,
        smartapi_supported=False,
        description="Declining stocks count",
    ),
    "unchanged": SourcePolicy(
        field_name="unchanged",
        primary="NSEOptions",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=120,
        smartapi_supported=False,
        description="Unchanged stocks count",
    ),
    "advance_decline_ratio": SourcePolicy(
        field_name="advance_decline_ratio",
        primary="NSEOptions",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=120,
        smartapi_supported=False,
        description="Advances / Declines ratio",
    ),
}

# ─── External-only fields ───────────────────────────────────────
# SmartAPI genuinely does not provide these.
# External providers are REQUIRED, not fallbacks.

EXTERNAL_FIELDS = {
    # Capital flows
    "fii_flow_1d": SourcePolicy(
        field_name="fii_flow_1d",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400,
        smartapi_supported=False,
        description="FII/FPI net flow (1 day)",
    ),
    "fii_flow_5d": SourcePolicy(
        field_name="fii_flow_5d",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400 * 5,
        smartapi_supported=False,
        description="FII/FPI net flow (5 day)",
    ),
    "fii_flow_20d": SourcePolicy(
        field_name="fii_flow_20d",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400 * 20,
        smartapi_supported=False,
        description="FII/FPI net flow (20 day)",
    ),
    "fii_flow_month": SourcePolicy(
        field_name="fii_flow_month",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400 * 30,
        smartapi_supported=False,
        description="FII/FPI net flow (calendar month)",
    ),
    "dii_flow_1d": SourcePolicy(
        field_name="dii_flow_1d",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400,
        smartapi_supported=False,
        description="DII net flow (1 day)",
    ),
    "dii_flow_5d": SourcePolicy(
        field_name="dii_flow_5d",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400 * 5,
        smartapi_supported=False,
        description="DII net flow (5 day)",
    ),
    "dii_flow_20d": SourcePolicy(
        field_name="dii_flow_20d",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400 * 20,
        smartapi_supported=False,
        description="DII net flow (20 day)",
    ),
    "dii_flow_month": SourcePolicy(
        field_name="dii_flow_month",
        primary="CapitalFlows",
        fallbacks=[],
        frequency="daily",
        expected_freshness_sec=86400 * 30,
        smartapi_supported=False,
        description="DII net flow (calendar month)",
    ),

    # Macro
    "crude_price": SourcePolicy(
        field_name="crude_price",
        primary="WebSource",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=300,
        smartapi_supported=False,
        description="Brent crude oil price (USD)",
    ),
    "usd_inr": SourcePolicy(
        field_name="usd_inr",
        primary="WebSource",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=300,
        smartapi_supported=False,
        description="USD/INR exchange rate",
    ),
    "us10y_yield": SourcePolicy(
        field_name="us10y_yield",
        primary="WebSource",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=300,
        smartapi_supported=False,
        description="US 10-Year Treasury yield",
    ),
    "india_vix": SourcePolicy(
        field_name="india_vix",
        primary="WebSource",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=300,
        smartapi_supported=False,
        description="India VIX volatility index",
    ),

    # Economy
    "fed_rate": SourcePolicy(
        field_name="fed_rate",
        primary="Macro",
        fallbacks=[],
        frequency="weekly",
        expected_freshness_sec=86400 * 7,
        smartapi_supported=False,
        description="US Federal Reserve rate",
    ),
    "india_policy_rate": SourcePolicy(
        field_name="india_policy_rate",
        primary="Macro",
        fallbacks=[],
        frequency="weekly",
        expected_freshness_sec=86400 * 7,
        smartapi_supported=False,
        description="RBI repo rate",
    ),
    "inflation": SourcePolicy(
        field_name="inflation",
        primary="Macro",
        fallbacks=[],
        frequency="monthly",
        expected_freshness_sec=86400 * 30,
        smartapi_supported=False,
        description="India CPI inflation",
    ),
    "gdp_growth": SourcePolicy(
        field_name="gdp_growth",
        primary="Macro",
        fallbacks=[],
        frequency="quarterly",
        expected_freshness_sec=86400 * 90,
        smartapi_supported=False,
        description="India GDP growth rate",
    ),
    "pmi": SourcePolicy(
        field_name="pmi",
        primary="Macro",
        fallbacks=[],
        frequency="monthly",
        expected_freshness_sec=86400 * 30,
        smartapi_supported=False,
        description="India Manufacturing PMI",
    ),

    # Earnings
    "earnings_growth": SourcePolicy(
        field_name="earnings_growth",
        primary="Macro",
        fallbacks=[],
        frequency="quarterly",
        expected_freshness_sec=86400 * 90,
        smartapi_supported=False,
        description="Aggregate earnings growth",
    ),
}

# ─── Sector performance ─────────────────────────────────────────
# Can come from SmartAPI (gainersLosers) or NSE/BSE.

SECTOR_FIELDS = {
    "sector_performance": SourcePolicy(
        field_name="sector_performance",
        primary="Sector",
        fallbacks=[],
        frequency="intraday",
        expected_freshness_sec=300,
        smartapi_supported=False,
        description="Sector-wise performance breakdown",
    ),
}


# ─── Combined registry ──────────────────────────────────────────

ALL_FIELD_POLICIES: dict[str, SourcePolicy] = {}
ALL_FIELD_POLICIES.update(SMARTAPI_FIELDS)
ALL_FIELD_POLICIES.update(EXTERNAL_FIELDS)
ALL_FIELD_POLICIES.update(SECTOR_FIELDS)


def get_field_policy(field_name: str) -> Optional[SourcePolicy]:
    """Get the source policy for a specific field."""
    return ALL_FIELD_POLICIES.get(field_name)


def is_smartapi_field(field_name: str) -> bool:
    """Check if a field should come from SmartAPI."""
    policy = ALL_FIELD_POLICIES.get(field_name)
    return policy is not None and policy.smartapi_supported


def get_expected_freshness(field_name: str) -> int:
    """Get expected freshness window for a field."""
    policy = ALL_FIELD_POLICIES.get(field_name)
    if policy:
        return policy.expected_freshness_sec
    return 300  # Default 5 minutes
