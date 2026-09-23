"""Sector Provider — NSE sector indices performance.

Fetches sector-wise performance from NSE or BSE APIs.
Used for the Participation component in verdict engines.
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Key NSE sector indices
NSE_SECTOR_INDICES = [
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY FMCG",
    "NIFTY PHARMA",
    "NIFTY AUTO",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY MEDIA",
    "NIFTY ENERGY",
    "NIFTY PRIVATE BANK",
    "NIFTY PSU BANK",
]


class SectorProvider(BaseProvider):
    """Fetches sector performance data from NSE/BSE."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._nse_ready = False

    @property
    def name(self) -> str:
        return "Sector"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _field(self, value, source="Sector", status="LIVE", quality="GOOD") -> FieldMeta:
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
            source="Sector",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
        )

    def _ensure_nse_session(self) -> bool:
        """Try to create NSE session. API may work even if homepage is blocked."""
        if self._nse_ready:
            return True
        try:
            r = self._session.get("https://www.nseindia.com", timeout=15)
            # API may work even if homepage returns 403
            if r.status_code == 200:
                self._nse_ready = True
                return True
            # Try the API directly - it may work without session cookies
            r2 = self._session.get("https://www.nseindia.com/api/allIndices", timeout=15)
            if r2.status_code == 200:
                self._nse_ready = True
                return True
        except Exception:
            pass
        return False

    def _fetch_nse_sectors(self) -> Optional[dict]:
        """Fetch sector data from NSE indices API."""
        try:
            if not self._ensure_nse_session():
                return None

            url = "https://www.nseindia.com/api/allIndices"
            r = self._session.get(url, timeout=15)
            if r.status_code != 200:
                return None

            data = r.json()
            if not data or not data.get("data"):
                return None

            sectors = {}
            for idx in data["data"]:
                name = idx.get("index", "") or idx.get("name", "")
                if name in NSE_SECTOR_INDICES:
                    sectors[name] = {
                        "last": idx.get("last"),
                        "pChange": idx.get("percentChange") or idx.get("pChange"),
                        "change": idx.get("variation") or idx.get("change"),
                        "open": idx.get("open"),
                        "high": idx.get("high"),
                        "low": idx.get("low"),
                        "previousClose": idx.get("previousClose"),
                    }

            return sectors if sectors else None
        except Exception as e:
            log.warning(f"NSE sector fetch failed: {e}")
        return None

    def _fetch_bse_sectors(self) -> Optional[dict]:
        """Fallback: fetch sector data from BSE."""
        try:
            url = "https://api.bseindia.com/BseIndiaAPI/api/GetStkCntDt/w"
            r = self._session.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                # Parse BSE format
                if data and isinstance(data, list):
                    sectors = {}
                    for item in data:
                        name = item.get("IndexName", "")
                        if "BANK" in name or "IT" in name or "PHARMA" in name:
                            sectors[name] = {
                                "last": item.get("Last"),
                                "pChange": item.get("PercChange"),
                            }
                    return sectors if sectors else None
        except Exception:
            pass
        return None

    def _compute_sector_summary(self, sectors: dict) -> dict:
        """Compute sector performance summary."""
        if not sectors:
            return {}

        changes = []
        for name, data in sectors.items():
            pct = data.get("pChange")
            if pct is not None:
                changes.append({"name": name, "change": pct})

        if not changes:
            return {}

        # Sort by performance
        changes.sort(key=lambda x: x["change"], reverse=True)

        advancing = sum(1 for c in changes if c["change"] > 0)
        declining = sum(1 for c in changes if c["change"] < 0)
        avg_change = sum(c["change"] for c in changes) / len(changes) if changes else 0

        return {
            "sectors": sectors,
            "top_performer": changes[0] if changes else None,
            "worst_performer": changes[-1] if changes else None,
            "advancing": advancing,
            "declining": declining,
            "avg_change": round(avg_change, 2),
            "participation_score": round(advancing / len(changes), 2) if changes else 0,
        }

    def fetch(self) -> MarketSnapshot:
        """Fetch sector performance data."""
        try:
            sectors = self._fetch_nse_sectors()
            if not sectors:
                sectors = self._fetch_bse_sectors()

            summary = self._compute_sector_summary(sectors)

            if summary:
                registry.record_fetch("sector_performance", "Sector", "NSE/BSE")
            else:
                registry.record_failure("Sector", "No sector data available")

            # Return only the flat sectors dict for downstream consumers
            sector_value = summary.get("sectors", {}) if isinstance(summary, dict) else {}
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="Sector",
                data_status="LIVE" if sector_value else "UNAVAILABLE",
                missing_fields=[],
                sector_performance=self._field(sector_value) if sector_value else self._unavailable_field(),
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
                india_vix=self._unavailable_field(),
                crude_price=self._unavailable_field(),
                usd_inr=self._unavailable_field(),
                us10y_yield=self._unavailable_field(),
                fed_rate=self._unavailable_field(),
                india_policy_rate=self._unavailable_field(),
                inflation=self._unavailable_field(),
                gdp_growth=self._unavailable_field(),
                pmi=self._unavailable_field(),
                fii_flow_1d=self._unavailable_field(),
                fii_flow_5d=self._unavailable_field(),
                fii_flow_20d=self._unavailable_field(),
                fii_flow_month=self._unavailable_field(),
                dii_flow_1d=self._unavailable_field(),
                dii_flow_5d=self._unavailable_field(),
                dii_flow_20d=self._unavailable_field(),
                dii_flow_month=self._unavailable_field(),
                earnings_growth=self._unavailable_field(),
            )

        except Exception as e:
            log.error(f"SectorProvider error: {e}")
            registry.record_failure("Sector", str(e))
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="Sector",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )
