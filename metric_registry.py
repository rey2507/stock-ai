"""Metric Classification Registry — single source of truth for every dashboard metric.

Every metric explicitly declares:
- DIRECT / DERIVED / EXTERNAL_REQUIRED
- Source provider
- Required inputs (for derived metrics)
- Calculation formula (for derived metrics)
- Freshness requirements
- Criticality level
- Missing-data behavior

No undocumented calculations. No fabricated values.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class MetricCategory(Enum):
    DIRECT = "DIRECT"
    DERIVED = "DERIVED"
    EXTERNAL_REQUIRED = "EXTERNAL_REQUIRED"


class MetricCriticality(Enum):
    CRITICAL = "CRITICAL"       # Verdict engine needs this
    HIGH = "HIGH"               # Dashboard prominently displays this
    MEDIUM = "MEDIUM"           # Dashboard shows but not verdict-critical
    LOW = "LOW"                 # Nice-to-have, cosmetic


class MissingDataBehavior(Enum):
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    CANNOT_CALCULATE = "CANNOT_CALCULATE"


@dataclass
class MetricDefinition:
    name: str
    category: MetricCategory
    source: str
    required_inputs: list[str] = field(default_factory=list)
    calculation: str = ""
    freshness_sec: int = 300
    criticality: MetricCriticality = MetricCriticality.MEDIUM
    missing_behavior: MissingDataBehavior = MissingDataBehavior.UNAVAILABLE
    description: str = ""
    smartapi_endpoint: str = ""


METRICS: dict[str, MetricDefinition] = {}

# ─── NIFTY / INDEX (DIRECT) ─────────────────────────────────────

METRICS["nifty_spot"] = MetricDefinition(
    name="nifty_spot",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.CRITICAL,
    description="NIFTY 50 spot index LTP",
)

METRICS["nifty_change"] = MetricDefinition(
    name="nifty_change",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.HIGH,
    description="NIFTY spot change from previous close",
)

METRICS["nifty_change_pct"] = MetricDefinition(
    name="nifty_change_pct",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.HIGH,
    description="NIFTY spot percentage change",
)

METRICS["nifty_open"] = MetricDefinition(
    name="nifty_open",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.MEDIUM,
    description="NIFTY opening price",
)

METRICS["nifty_high"] = MetricDefinition(
    name="nifty_high",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.MEDIUM,
    description="NIFTY intraday high",
)

METRICS["nifty_low"] = MetricDefinition(
    name="nifty_low",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.MEDIUM,
    description="NIFTY intraday low",
)

METRICS["nifty_volume"] = MetricDefinition(
    name="nifty_volume",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NSE, NIFTY, 99926000)",
    freshness_sec=5,
    criticality=MetricCriticality.MEDIUM,
    description="NIFTY trading volume",
)

# ─── FUTURES (DIRECT) ───────────────────────────────────────────

METRICS["futures_price"] = MetricDefinition(
    name="futures_price",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NFO, NIFTY_FUT, token)",
    freshness_sec=5,
    criticality=MetricCriticality.CRITICAL,
    description="Nearest expiry NIFTY futures LTP",
)

METRICS["futures_change_pct"] = MetricDefinition(
    name="futures_change_pct",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="ltpData(NFO, NIFTY_FUT, token)",
    freshness_sec=5,
    criticality=MetricCriticality.CRITICAL,
    description="NIFTY futures percentage change from previous close",
)

METRICS["futures_oi"] = MetricDefinition(
    name="futures_oi",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="getMarketData(FULL, {NFO: [token]})",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    description="NIFTY futures open interest",
)

METRICS["futures_oi_change"] = MetricDefinition(
    name="futures_oi_change",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["futures_oi"],
    calculation="Current futures OI - Previous observed futures OI",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
    description="Futures OI change from previous observation",
)

METRICS["futures_expiry"] = MetricDefinition(
    name="futures_expiry",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="InstrumentManager.discover_all()",
    freshness_sec=86400,
    criticality=MetricCriticality.LOW,
    description="Current nearest futures expiry date",
)

# ─── OPTIONS (DIRECT) ───────────────────────────────────────────

METRICS["atm_strike"] = MetricDefinition(
    name="atm_strike",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="InstrumentManager (dynamic ATM detection)",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    description="At-the-money strike price (closest to spot)",
)

METRICS["call_oi"] = MetricDefinition(
    name="call_oi",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="getMarketData(FULL, {NFO: [CE tokens]})",
    freshness_sec=30,
    criticality=MetricCriticality.CRITICAL,
    description="Total call option OI across ATM +/- 2 strikes",
)

METRICS["put_oi"] = MetricDefinition(
    name="put_oi",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="getMarketData(FULL, {NFO: [PE tokens]})",
    freshness_sec=30,
    criticality=MetricCriticality.CRITICAL,
    description="Total put option OI across ATM +/- 2 strikes",
)

METRICS["call_oi_change"] = MetricDefinition(
    name="call_oi_change",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["call_oi"],
    calculation="Current call OI - Previous observed call OI",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
    description="Call OI change from previous observation",
)

METRICS["put_oi_change"] = MetricDefinition(
    name="put_oi_change",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["put_oi"],
    calculation="Current put OI - Previous observed put OI",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
    description="Put OI change from previous observation",
)

METRICS["pcr"] = MetricDefinition(
    name="pcr",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["put_oi", "call_oi"],
    calculation="Put OI / Call OI",
    freshness_sec=30,
    criticality=MetricCriticality.CRITICAL,
    missing_behavior=MissingDataBehavior.CANNOT_CALCULATE,
    description="Put-Call Ratio by OI. Requires both put_oi and call_oi.",
)

METRICS["atm_iv"] = MetricDefinition(
    name="atm_iv",
    category=MetricCategory.DIRECT,
    source="AngelBroking",
    smartapi_endpoint="optionGreek({name: NIFTY, expirydate: ...})",
    freshness_sec=120,
    criticality=MetricCriticality.HIGH,
    description="ATM implied volatility from option Greeks",
)

METRICS["max_pain"] = MetricDefinition(
    name="max_pain",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["call_oi", "put_oi"],
    calculation="Strike where total OI ofCalls + Puts is minimized",
    freshness_sec=120,
    criticality=MetricCriticality.MEDIUM,
    missing_behavior=MissingDataBehavior.CANNOT_CALCULATE,
    description="Max pain strike price",
)

# ─── DERIVED INDICATORS (DERIVED from candles) ──────────────────

METRICS["vwap"] = MetricDefinition(
    name="vwap",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["nifty_volume"],
    calculation="Cumulative(typical_price * volume) / Cumulative(volume)",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    missing_behavior=MissingDataBehavior.CANNOT_CALCULATE,
    description="Volume-Weighted Average Price from intraday candles",
)

METRICS["rsi"] = MetricDefinition(
    name="rsi",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["nifty_spot"],
    calculation="14-period RSI from historical candle close prices",
    freshness_sec=30,
    criticality=MetricCriticality.HIGH,
    missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
    description="Relative Strength Index (14-period)",
)

METRICS["atr"] = MetricDefinition(
    name="atr",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["nifty_high", "nifty_low"],
    calculation="14-period ATR from historical candle data",
    freshness_sec=60,
    criticality=MetricCriticality.MEDIUM,
    missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
    description="Average True Range (14-period)",
)

METRICS["relative_volume"] = MetricDefinition(
    name="relative_volume",
    category=MetricCategory.DERIVED,
    source="AngelBroking",
    required_inputs=["nifty_volume"],
    calculation="Current session volume / Average comparable historical volume",
    freshness_sec=60,
    criticality=MetricCriticality.HIGH,
    missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
    description="Current volume relative to historical average",
)

# ─── MARKET BREADTH (DIRECT from NSE) ───────────────────────────

METRICS["advances"] = MetricDefinition(
    name="advances",
    category=MetricCategory.DIRECT,
    source="NSE",
    smartapi_endpoint="NSE API /allIndices top-level advances",
    freshness_sec=120,
    criticality=MetricCriticality.HIGH,
    description="Number of advancing stocks",
)

METRICS["declines"] = MetricDefinition(
    name="declines",
    category=MetricCategory.DIRECT,
    source="NSE",
    smartapi_endpoint="NSE API /allIndices top-level declines",
    freshness_sec=120,
    criticality=MetricCriticality.HIGH,
    description="Number of declining stocks",
)

METRICS["unchanged"] = MetricDefinition(
    name="unchanged",
    category=MetricCategory.DIRECT,
    source="NSE",
    smartapi_endpoint="NSE API /allIndices top-level unchanged",
    freshness_sec=120,
    criticality=MetricCriticality.LOW,
    description="Number of unchanged stocks",
)

METRICS["advance_decline_ratio"] = MetricDefinition(
    name="advance_decline_ratio",
    category=MetricCategory.DERIVED,
    source="NSE",
    required_inputs=["advances", "declines"],
    calculation="Advances / Declines",
    freshness_sec=120,
    criticality=MetricCriticality.CRITICAL,
    missing_behavior=MissingDataBehavior.CANNOT_CALCULATE,
    description="Advance-Decline ratio. Requires both advances and declines.",
)

# ─── CAPITAL FLOWS (EXTERNAL_REQUIRED) ──────────────────────────

for period in ["1d", "5d", "20d", "month"]:
    days = {"1d": 1, "5d": 5, "20d": 20, "month": 30}[period]
    METRICS[f"fii_flow_{period}"] = MetricDefinition(
        name=f"fii_flow_{period}",
        category=MetricCategory.EXTERNAL_REQUIRED,
        source="NSE",
        calculation=f"Sum of last {days} trading-day FII net flow observations",
        freshness_sec=86400 * days,
        criticality=MetricCriticality.HIGH if period in ("1d", "5d") else MetricCriticality.MEDIUM,
        missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
        description=f"FII/FPI net flow ({period})",
    )
    METRICS[f"dii_flow_{period}"] = MetricDefinition(
        name=f"dii_flow_{period}",
        category=MetricCategory.EXTERNAL_REQUIRED,
        source="NSE",
        calculation=f"Sum of last {days} trading-day DII net flow observations",
        freshness_sec=86400 * days,
        criticality=MetricCriticality.HIGH if period in ("1d", "5d") else MetricCriticality.MEDIUM,
        missing_behavior=MissingDataBehavior.INSUFFICIENT_HISTORY,
        description=f"DII net flow ({period})",
    )

# ─── VOLATILITY / MACRO (EXTERNAL_REQUIRED) ─────────────────────

METRICS["india_vix"] = MetricDefinition(
    name="india_vix",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="YahooFinance",
    smartapi_endpoint="",
    freshness_sec=300,
    criticality=MetricCriticality.HIGH,
    description="India VIX volatility index",
)

METRICS["crude_price"] = MetricDefinition(
    name="crude_price",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="YahooFinance",
    freshness_sec=300,
    criticality=MetricCriticality.HIGH,
    description="Brent crude oil price (USD)",
)

METRICS["usd_inr"] = MetricDefinition(
    name="usd_inr",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="YahooFinance",
    freshness_sec=300,
    criticality=MetricCriticality.HIGH,
    description="USD/INR exchange rate",
)

METRICS["us10y_yield"] = MetricDefinition(
    name="us10y_yield",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="YahooFinance",
    freshness_sec=300,
    criticality=MetricCriticality.MEDIUM,
    description="US 10-Year Treasury yield",
)

# ─── MACRO ECONOMY (EXTERNAL_REQUIRED) ──────────────────────────

METRICS["fed_rate"] = MetricDefinition(
    name="fed_rate",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="YahooFinance",
    freshness_sec=86400 * 7,
    criticality=MetricCriticality.HIGH,
    description="US Federal Reserve policy rate (proxied via 13-week T-bill)",
)

METRICS["india_policy_rate"] = MetricDefinition(
    name="india_policy_rate",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="RBI",
    freshness_sec=86400 * 7,
    criticality=MetricCriticality.HIGH,
    description="RBI repo rate",
)

METRICS["inflation"] = MetricDefinition(
    name="inflation",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="External",
    freshness_sec=86400 * 30,
    criticality=MetricCriticality.MEDIUM,
    description="India CPI inflation",
)

METRICS["gdp_growth"] = MetricDefinition(
    name="gdp_growth",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="WorldBank",
    freshness_sec=86400 * 90,
    criticality=MetricCriticality.MEDIUM,
    description="India GDP growth rate (quarterly)",
)

METRICS["pmi"] = MetricDefinition(
    name="pmi",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="External",
    freshness_sec=86400 * 30,
    criticality=MetricCriticality.MEDIUM,
    description="India Manufacturing PMI",
)

# ─── EARNINGS (EXTERNAL_REQUIRED) ───────────────────────────────

METRICS["earnings_growth"] = MetricDefinition(
    name="earnings_growth",
    category=MetricCategory.EXTERNAL_REQUIRED,
    source="External",
    freshness_sec=86400 * 90,
    criticality=MetricCriticality.HIGH,
    description="Nifty aggregate earnings growth",
)

# ─── SECTOR PERFORMANCE (DIRECT from NSE) ───────────────────────

METRICS["sector_performance"] = MetricDefinition(
    name="sector_performance",
    category=MetricCategory.DIRECT,
    source="NSE",
    smartapi_endpoint="NSE /allIndices filtered by sector names",
    freshness_sec=300,
    criticality=MetricCriticality.HIGH,
    description="Sector-wise performance from NSE sector indices",
)


# ─── ACCESSORS ──────────────────────────────────────────────────

def get_metric(name: str) -> Optional[MetricDefinition]:
    return METRICS.get(name)


def get_all_direct() -> dict[str, MetricDefinition]:
    return {k: v for k, v in METRICS.items() if v.category == MetricCategory.DIRECT}


def get_all_derived() -> dict[str, MetricDefinition]:
    return {k: v for k, v in METRICS.items() if v.category == MetricCategory.DERIVED}


def get_all_external() -> dict[str, MetricDefinition]:
    return {k: v for k, v in METRICS.items() if v.category == MetricCategory.EXTERNAL_REQUIRED}


def get_inputs_for(metric_name: str) -> list[str]:
    m = METRICS.get(metric_name)
    return m.required_inputs if m else []


def can_calculate(metric_name: str, available_fields: set[str]) -> bool:
    m = METRICS.get(metric_name)
    if m is None:
        return False
    if m.category != MetricCategory.DERIVED:
        return True
    return all(inp in available_fields for inp in m.required_inputs)


def audit_field(field_name: str) -> dict:
    m = METRICS.get(field_name)
    if m is None:
        return {"metric": field_name, "status": "UNREGISTERED"}
    return {
        "metric": m.name,
        "category": m.category.value,
        "source": m.source,
        "required_inputs": m.required_inputs,
        "calculation": m.calculation or "(direct)",
        "freshness_sec": m.freshness_sec,
        "criticality": m.criticality.value,
        "missing_behavior": m.missing_behavior.value,
        "smartapi_endpoint": m.smartapi_endpoint or "(external)",
        "description": m.description,
    }


def full_audit() -> list[dict]:
    return [audit_field(name) for name in sorted(METRICS.keys())]
