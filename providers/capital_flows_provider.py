"""Capital Flows Provider — FII/DII data from BSE India.

NSE website is blocked from this network, so we use BSE India's
public APIs which are more accessible.

Data sources:
- BSE FII/DII data page
- MoneyControl for FII/DII flows
- Direct BSE API endpoints
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from models.snapshot import MarketSnapshot, FieldMeta
from providers.base import BaseProvider
from providers.cache import cache, get_freshness_window
from providers.source_registry import registry

log = logging.getLogger(__name__)


def _to_float(val) -> Optional[float]:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com",
}

# MoneyControl FII/DII API (public)
MC_FII_DII_URL = "https://www.moneycontrol.com/mc/widget/FIImap/getFiiDiiData"
MC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Referer": "https://www.moneycontrol.com",
}


class CapitalFlowsProvider(BaseProvider):
    """Fetches FII/DII flow data from public sources."""

    def __init__(self):
        self._session = requests.Session()

    @property
    def name(self) -> str:
        return "CapitalFlows"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _field(self, value, source="CapitalFlows", status="LIVE", quality="GOOD") -> FieldMeta:
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
            source="CapitalFlows",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
        )

    def _fetch_nse_fii_dii(self, days: int = 1) -> list:
        """Fetch FII/DII data from NSE India for the last N trading days."""
        results = []
        try:
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            try:
                self._session.get("https://www.nseindia.com", timeout=10)
            except Exception:
                pass

            from datetime import timedelta
            today = datetime.now()
            fetched = 0
            for days_back in range(0, 30):
                date = today - timedelta(days=days_back)
                date_str = date.strftime("%d-%m-%Y")
                url = f"https://www.nseindia.com/api/fiidiiTradeReact?from={date_str}&to={date_str}"
                try:
                    r = self._session.get(url, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        if data and isinstance(data, list) and len(data) > 0:
                            results.extend(data)
                            fetched += 1
                            if fetched >= days:
                                break
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"NSE FII/DII fetch failed: {e}")
        return results

    def _fetch_bse_fii_dii(self) -> Optional[dict]:
        """Fetch FII/DII data from BSE India (fallback, may return HTML)."""
        try:
            url = "https://api.bseindia.com/BseIndiaAPI/api/FIIData/w"
            self._session.headers.update(BSE_HEADERS)
            r = self._session.get(url, timeout=15)
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                data = r.json()
                if data:
                    return data
        except Exception as e:
            log.warning(f"BSE FII/DII fetch failed: {e}")
        return None

    def _fetch_moneycontrol_fii_dii(self) -> Optional[dict]:
        """Fetch FII/DII data from MoneyControl (fallback, may return HTML)."""
        try:
            self._session.headers.update(MC_HEADERS)
            r = self._session.get(MC_FII_DII_URL, timeout=15)
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                data = r.json()
                if data and isinstance(data, dict):
                    return data.get("data", data)
        except Exception as e:
            log.warning(f"MoneyControl FII/DII fetch failed: {e}")
        return None

    def _parse_fii_dii(self, raw_data) -> dict:
        """Parse FII/DII data from various sources into a standard format.

        Handles:
        - NSE format: list of {category: "FII"|"DII", buyValue, sellValue, netValue}
        - MoneyControl format: {fii: {buyValue, sellValue, netValue}, dii: {...}}
        - BSE format: list of {FII_net, DII_net}

        Returns: {fii_1d, dii_1d, fii_buy, fii_sell, dii_buy, dii_sell}
        """
        result = {
            "fii_1d": None,
            "dii_1d": None,
            "fii_buy": None,
            "fii_sell": None,
            "dii_buy": None,
            "dii_sell": None,
        }

        # NSE format: list with category field
        if isinstance(raw_data, list) and raw_data:
            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                cat = item.get("category", "").upper()
                if "FII" in cat:
                    result["fii_buy"] = _to_float(item.get("buyValue"))
                    result["fii_sell"] = _to_float(item.get("sellValue"))
                    result["fii_1d"] = _to_float(item.get("netValue"))
                elif "DII" in cat:
                    result["dii_buy"] = _to_float(item.get("buyValue"))
                    result["dii_sell"] = _to_float(item.get("sellValue"))
                    result["dii_1d"] = _to_float(item.get("netValue"))
            if result["fii_1d"] is not None or result["dii_1d"] is not None:
                return result

        # MoneyControl format
        if isinstance(raw_data, dict) and "fii" in raw_data and "dii" in raw_data:
            fii = raw_data["fii"]
            dii = raw_data["dii"]
            if isinstance(fii, dict):
                result["fii_buy"] = _to_float(fii.get("buyValue"))
                result["fii_sell"] = _to_float(fii.get("sellValue"))
                result["fii_1d"] = _to_float(fii.get("netValue"))
            if isinstance(dii, dict):
                result["dii_buy"] = _to_float(dii.get("buyValue"))
                result["dii_sell"] = _to_float(dii.get("sellValue"))
                result["dii_1d"] = _to_float(dii.get("netValue"))
            return result

        # BSE format: list with FII_net/DII_net
        if isinstance(raw_data, list) and raw_data:
            latest = raw_data[0]
            if isinstance(latest, dict):
                result["fii_1d"] = _to_float(latest.get("FII_net"))
                result["dii_1d"] = _to_float(latest.get("DII_net"))
                return result

        return result

    def fetch(self) -> MarketSnapshot:
        """Fetch FII/DII data from available sources.

        Priority: NSE (real JSON) > BSE (may be HTML) > MoneyControl (may be HTML).
        """
        try:
            fii_data = None
            sources_attempted = []

            # Try NSE first
            sources_attempted.append("NSE")
            fii_data = self._fetch_nse_fii_dii(days=20)

            # Fallback to BSE
            if not fii_data:
                sources_attempted.append("BSE")
                fii_data = self._fetch_bse_fii_dii()

            # Fallback to MoneyControl
            if not fii_data:
                sources_attempted.append("MoneyControl")
                fii_data = self._fetch_moneycontrol_fii_dii()

            # Record attempted sources in registry
            for src in sources_attempted:
                registry.record_fetch("capital_flows_attempt", "CapitalFlows", src)

            parsed = self._parse_fii_dii(fii_data) if fii_data else {}

            fii_1d = parsed.get("fii_1d")
            dii_1d = parsed.get("dii_1d")

            # Compute multi-day aggregates from NSE daily data
            fii_5d = dii_5d = fii_20d = dii_20d = fii_month = dii_month = None
            if isinstance(fii_data, list) and len(fii_data) > 0:
                from datetime import timedelta
                from collections import defaultdict
                daily = defaultdict(lambda: {"fii_net": 0.0, "dii_net": 0.0, "count": 0})
                for item in fii_data:
                    cat = item.get("category", "").upper()
                    net = _to_float(item.get("netValue"))
                    date = item.get("date")
                    if date and net is not None:
                        if "FII" in cat:
                            daily[date]["fii_net"] += net
                            daily[date]["count"] += 1
                        elif "DII" in cat:
                            daily[date]["dii_net"] += net
                            daily[date]["count"] += 1

                unique_dates = sorted(daily.keys(), reverse=True)
                fii_5d = sum(daily[d]["fii_net"] for d in unique_dates[:5]) if len(unique_dates) >= 1 else None
                dii_5d = sum(daily[d]["dii_net"] for d in unique_dates[:5]) if len(unique_dates) >= 1 else None
                fii_20d = sum(daily[d]["fii_net"] for d in unique_dates[:20]) if len(unique_dates) >= 1 else None
                dii_20d = sum(daily[d]["dii_net"] for d in unique_dates[:20]) if len(unique_dates) >= 1 else None
                fii_month = sum(daily[d]["fii_net"] for d in unique_dates) if unique_dates else None
                dii_month = sum(daily[d]["dii_net"] for d in unique_dates) if unique_dates else None

            # Determine which source succeeded
            successful_source = "None"
            if fii_1d is not None or dii_1d is not None:
                if "NSE" in sources_attempted and (fii_1d is not None or dii_1d is not None):
                    successful_source = "NSE"
                elif "BSE" in sources_attempted:
                    successful_source = "BSE"
                elif "MoneyControl" in sources_attempted:
                    successful_source = "MoneyControl"

            log.info(f"Capital flows data sourced from: {successful_source} (attempted: {sources_attempted})")

            # Record in source registry
            if fii_1d is not None or dii_1d is not None:
                registry.record_fetch("fii_flow_1d", "CapitalFlows", successful_source)
            else:
                registry.record_failure("CapitalFlows", "No FII/DII data available")

            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="CapitalFlows",
                data_status="LIVE" if (fii_1d is not None or dii_1d is not None) else "UNAVAILABLE",
                missing_fields=[],
                fii_flow_1d=self._field(fii_1d, source=successful_source) if fii_1d is not None else self._unavailable_field(),
                fii_flow_5d=self._field(fii_5d, source=successful_source) if fii_5d is not None else self._unavailable_field(),
                fii_flow_20d=self._field(fii_20d, source=successful_source) if fii_20d is not None else self._unavailable_field(),
                fii_flow_month=self._field(fii_month, source=successful_source) if fii_month is not None else self._unavailable_field(),
                dii_flow_1d=self._field(dii_1d, source=successful_source) if dii_1d is not None else self._unavailable_field(),
                dii_flow_5d=self._field(dii_5d, source=successful_source) if dii_5d is not None else self._unavailable_field(),
                dii_flow_20d=self._field(dii_20d, source=successful_source) if dii_20d is not None else self._unavailable_field(),
                dii_flow_month=self._field(dii_month, source=successful_source) if dii_month is not None else self._unavailable_field(),
                # Fields this provider does NOT fill
                nifty_spot=self._unavailable_field(),
                nifty_change=self._unavailable_field(),
                nifty_change_pct=self._unavailable_field(),
                nifty_open=self._unavailable_field(),
                nifty_high=self._unavailable_field(),
                nifty_low=self._unavailable_field(),
                futures_price=self._unavailable_field(),
                futures_change_pct=self._unavailable_field(),
                futures_oi=self._unavailable_field(),
                futures_oi_change=self._unavailable_field(),
                futures_expiry=self._unavailable_field(),
                atm_strike=self._unavailable_field(),
                pcr=self._unavailable_field(),
                vwap=self._unavailable_field(),
                rsi=self._unavailable_field(),
                atr=self._unavailable_field(),
                relative_volume=self._unavailable_field(),
                call_oi=self._unavailable_field(),
                put_oi=self._unavailable_field(),
                call_oi_change=self._unavailable_field(),
                put_oi_change=self._unavailable_field(),
                atm_iv=self._unavailable_field(),
                max_pain=self._unavailable_field(),
                advances=self._unavailable_field(),
                declines=self._unavailable_field(),
                unchanged=self._unavailable_field(),
                advance_decline_ratio=self._unavailable_field(),
                sector_performance=self._unavailable_field(),
                india_vix=self._unavailable_field(),
                crude_price=self._unavailable_field(),
                usd_inr=self._unavailable_field(),
                us10y_yield=self._unavailable_field(),
                fed_rate=self._unavailable_field(),
                india_policy_rate=self._unavailable_field(),
                inflation=self._unavailable_field(),
                gdp_growth=self._unavailable_field(),
                pmi=self._unavailable_field(),
                earnings_growth=self._unavailable_field(),
            )

        except Exception as e:
            log.error(f"CapitalFlowsProvider error: {e}")
            registry.record_failure("CapitalFlows", str(e))
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="CapitalFlows",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )
