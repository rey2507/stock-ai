"""Macro Provider — RBI policy rate, inflation, GDP, PMI from live public sources.

Data sources:
- RBI website for policy rate (HTML scraping)
- Yahoo Finance for US Fed proxy (13-week T-bill)
- NSE India for bond yield context
- World Bank API for GDP (quarterly)
- No hardcoded fallbacks — returns UNAVAILABLE when sources fail
"""

import re
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from models.snapshot import MarketSnapshot, FieldMeta
from providers.base import BaseProvider
from providers.cache import cache, get_freshness_window
from providers.source_registry import registry
from providers.history_manager import history_manager

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class MacroProvider(BaseProvider):
    """Fetches macro-economic indicators from live public sources."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    @property
    def name(self) -> str:
        return "Macro"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _field(self, value, source="Macro", status="HISTORICAL", quality="GOOD") -> FieldMeta:
        return FieldMeta(
            value=value,
            timestamp=self._ts(),
            source=source,
            freshness_seconds=0.0,
            status=status,
            quality=quality,
        )

    def _unavailable_field(self) -> FieldMeta:
        return FieldMeta(
            value=None,
            timestamp=self._ts(),
            source="Macro",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
        )

    def _fetch_rbi_policy_rate(self) -> tuple[Optional[float], str, str]:
        """Fetch current RBI repo rate by scraping RBI website.

        Returns: (value, status, source_label)
        """
        cached = cache.get("rbi_policy_rate")
        if cached:
            return cached.value, "STALE", f"Cached ({cached.source})"

        try:
            r = self._session.get(
                "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx",
                timeout=15,
            )
            if r.status_code == 200:
                text = r.text
                match = re.search(
                    r"repo\s+rate\s+(?:at\s+|of\s+|by\s+)?(\d+\.?\d*)\s*(?:per\s*cent|%)",
                    text, re.IGNORECASE,
                )
                if match:
                    rate = float(match.group(1))
                    cache.put("rbi_policy_rate", rate, source="RBI/scrape", freshness_window=86400 * 7)
                    self._save_macro("india_policy_rate", rate, "RBI/scrape")
                    return rate, "LIVE", "RBI/scrape"

                match2 = re.search(
                    r"bank\s+rate.*?(\d+\.?\d*)\s*(?:per\s*cent|%)",
                    text, re.IGNORECASE,
                )
                if match2:
                    rate = float(match2.group(1))
                    cache.put("rbi_policy_rate", rate, source="RBI/scrape", freshness_window=86400 * 7)
                    self._save_macro("india_policy_rate", rate, "RBI/scrape")
                    return rate, "LIVE", "RBI/scrape"
        except Exception as e:
            log.warning(f"RBI policy rate scrape failed: {e}")

        # Try cache file
        macro_cache = history_manager.load_macro_cache()
        cached_rate = macro_cache.get("india_policy_rate")
        if cached_rate is not None:
            cached_ts = macro_cache.get("india_policy_rate_ts", "")
            return cached_rate, "STALE", f"Cached from {cached_ts}"

        return None, "UNAVAILABLE", "Source unavailable"

    def _fetch_fed_rate(self) -> tuple[Optional[float], str, str]:
        """Fetch US Fed rate via Yahoo Finance 13-week T-bill yield (proxy).

        Returns: (value, status, source_label)
        """
        cached = cache.get("fed_rate")
        if cached:
            return cached.value, "STALE", f"Cached ({cached.source})"

        try:
            import yfinance as yf
            t = yf.Ticker("^IRX")
            hist = t.history(period="5d")
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
                cache.put("fed_rate", rate, source="YahooFinance/^IRX", freshness_window=86400 * 2)
                self._save_macro("fed_rate", rate, "YahooFinance/^IRX")
                return rate, "LIVE", "YahooFinance/^IRX"
        except Exception as e:
            log.warning(f"Yahoo Finance fed rate fetch failed: {e}")

        macro_cache = history_manager.load_macro_cache()
        cached_rate = macro_cache.get("fed_rate")
        if cached_rate is not None:
            cached_ts = macro_cache.get("fed_rate_ts", "")
            return cached_rate, "STALE", f"Cached from {cached_ts}"

        return None, "UNAVAILABLE", "Source unavailable"

    def _fetch_inflation(self) -> tuple[Optional[float], str, str]:
        """Fetch India CPI inflation.

        Returns: (value, status, source_label)
        """
        cached = cache.get("india_inflation")
        if cached:
            return cached.value, "STALE", f"Cached ({cached.source})"

        try:
            r = self._session.get(
                "https://tradingeconomics.com/india/inflation-cpi",
                timeout=15,
            )
            if r.status_code == 200:
                match = re.search(
                    r'"india-inflation-cpi"[^>]*>(\d+\.?\d*)\s*%?',
                    r.text,
                )
                if match:
                    val = float(match.group(1))
                    cache.put("india_inflation", val, source="TradingEconomics", freshness_window=86400 * 30)
                    self._save_macro("inflation", val, "TradingEconomics")
                    return val, "LIVE", "TradingEconomics"
        except Exception as e:
            log.warning(f"Trading Economics inflation fetch failed: {e}")

        macro_cache = history_manager.load_macro_cache()
        cached_val = macro_cache.get("inflation")
        if cached_val is not None:
            cached_ts = macro_cache.get("inflation_ts", "")
            return cached_val, "STALE", f"Cached from {cached_ts}"

        return None, "UNAVAILABLE", "Source unavailable"

    def _fetch_gdp_growth(self) -> tuple[Optional[float], str, str]:
        """Fetch India GDP growth from World Bank API.

        Returns: (value, status, source_label)
        """
        cached = cache.get("india_gdp_growth")
        if cached:
            return cached.value, "STALE", f"Cached ({cached.source})"

        try:
            r = self._session.get(
                "https://api.worldbank.org/v2/country/IND/indicator/NY.GDP.MKTP.KD.ZG?format=json&per_page=5&date=2023:2026",
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                if len(data) > 1 and data[1]:
                    for entry in data[1]:
                        if entry.get("value") is not None:
                            gdp = float(entry["value"])
                            cache.put("india_gdp_growth", gdp, source="WorldBank", freshness_window=86400 * 90)
                            self._save_macro("gdp_growth", gdp, "WorldBank")
                            return gdp, "LIVE", "WorldBank"
        except Exception as e:
            log.warning(f"World Bank GDP fetch failed: {e}")

        macro_cache = history_manager.load_macro_cache()
        cached_val = macro_cache.get("gdp_growth")
        if cached_val is not None:
            cached_ts = macro_cache.get("gdp_growth_ts", "")
            return cached_val, "STALE", f"Cached from {cached_ts}"

        return None, "UNAVAILABLE", "Source unavailable"

    def _fetch_pmi(self) -> tuple[Optional[float], str, str]:
        """Fetch India Manufacturing PMI.

        Returns: (value, status, source_label)
        """
        cached = cache.get("india_pmi")
        if cached:
            return cached.value, "STALE", f"Cached ({cached.source})"

        try:
            r = self._session.get(
                "https://tradingeconomics.com/india/manufacturing-pmi",
                timeout=15,
            )
            if r.status_code == 200:
                match = re.search(
                    r'Manufacturing PMI in India decreased to (\d+\.?\d*) points',
                    r.text,
                )
                if match:
                    val = float(match.group(1))
                    cache.put("india_pmi", val, source="TradingEconomics", freshness_window=86400 * 30)
                    self._save_macro("pmi", val, "TradingEconomics")
                    return val, "LIVE", "TradingEconomics"
        except Exception as e:
            log.warning(f"Trading Economics PMI fetch failed: {e}")

        macro_cache = history_manager.load_macro_cache()
        cached_val = macro_cache.get("pmi")
        if cached_val is not None:
            cached_ts = macro_cache.get("pmi_ts", "")
            return cached_val, "STALE", f"Cached from {cached_ts}"

        return None, "UNAVAILABLE", "Source unavailable"

    def _save_macro(self, key: str, value: float, source: str) -> None:
        """Save a macro value to the persistent cache."""
        macro_cache = history_manager.load_macro_cache()
        macro_cache[key] = value
        macro_cache[f"{key}_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M IST")
        macro_cache[f"{key}_source"] = source
        history_manager.save_macro_cache(macro_cache)

    def fetch(self) -> MarketSnapshot:
        """Fetch macro-economic indicators from live sources."""
        try:
            rbi_rate, rbi_status, rbi_source = self._fetch_rbi_policy_rate()
            inflation, inf_status, inf_source = self._fetch_inflation()
            gdp_growth, gdp_status, gdp_source = self._fetch_gdp_growth()
            pmi, pmi_status, pmi_source = self._fetch_pmi()
            fed_rate, fed_status, fed_source = self._fetch_fed_rate()

            available = any([
                rbi_rate is not None,
                inflation is not None,
                gdp_growth is not None,
                pmi is not None,
                fed_rate is not None,
            ])

            if available:
                registry.record_fetch("macro", "Macro", "PublicAPIs")
            else:
                registry.record_failure("Macro", "No macro data available")

            def _make_field(value, status, source_label):
                if value is None:
                    return FieldMeta(
                        value=None,
                        timestamp=self._ts(),
                        source="Macro",
                        freshness_seconds=None,
                        status="UNAVAILABLE",
                        quality="INVALID",
                        diagnostic_reason="source_unavailable",
                        diagnostic_message=f"Data source unavailable. {source_label}",
                    )
                if status == "STALE":
                    return FieldMeta(
                        value=value,
                        timestamp=self._ts(),
                        source="Macro",
                        freshness_seconds=None,
                        status="STALE",
                        quality="POOR",
                        diagnostic_reason="stale_cache",
                        diagnostic_message=f"Using cached value from {source_label}",
                    )
                return FieldMeta(
                    value=value,
                    timestamp=self._ts(),
                    source="Macro",
                    freshness_seconds=0.0,
                    status=status,
                    quality="GOOD",
                    diagnostic_reason="ok",
                    diagnostic_message=f"Fetched from {source_label}",
                )

            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="Macro",
                data_status="LIVE" if available else "UNAVAILABLE",
                missing_fields=[],
                india_policy_rate=_make_field(rbi_rate, rbi_status, rbi_source),
                inflation=_make_field(inflation, inf_status, inf_source),
                gdp_growth=_make_field(gdp_growth, gdp_status, gdp_source),
                pmi=_make_field(pmi, pmi_status, pmi_source),
                fed_rate=_make_field(fed_rate, fed_status, fed_source),
                nifty_spot=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                nifty_change=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                nifty_change_pct=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                nifty_open=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                nifty_high=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                nifty_low=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                futures_price=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                futures_change_pct=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                futures_oi=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                futures_oi_change=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                futures_expiry=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                atm_strike=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                pcr=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                vwap=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                rsi=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                atr=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                relative_volume=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                call_oi=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                put_oi=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                call_oi_change=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                put_oi_change=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                atm_iv=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                max_pain=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                advances=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                declines=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                unchanged=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                advance_decline_ratio=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                sector_performance=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                india_vix=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                crude_price=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                usd_inr=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                us10y_yield=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                fii_flow_1d=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                fii_flow_5d=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                fii_flow_20d=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                fii_flow_month=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                dii_flow_1d=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                dii_flow_5d=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                dii_flow_20d=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                dii_flow_month=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
                earnings_growth=FieldMeta(value=None, timestamp=self._ts(), source="Macro", freshness_seconds=None, status="UNAVAILABLE", quality="INVALID"),
            )

        except Exception as e:
            log.error(f"MacroProvider error: {e}")
            registry.record_failure("Macro", str(e))
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="Macro",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )
