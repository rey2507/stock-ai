"""NSEOptionsProvider — no-login option chain data via NSEIndiaApi.

Fetches:
- call_oi / put_oi (ATM +/- 2 strikes, or full chain)
- atm_iv
- max_pain
- pcr (by OI)
- atm_strike
- call_oi_change / put_oi_change
- futures_oi / futures_oi_change

Source: NSEIndiaApi library (`pip install nse`) + optional nsepython fallback.
No SmartAPI calls. No login required.
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timezone
from typing import Optional

from models.snapshot import MarketSnapshot, FieldMeta
from providers.base import BaseProvider
from providers.cache import cache, get_freshness_window
from providers.source_registry import registry

log = logging.getLogger(__name__)


class NSEOptionsProvider(BaseProvider):
    """Fetches options/derivatives data from NSE public APIs via NSEIndiaApi."""

    def __init__(self):
        self._last_fetch_ts: Optional[datetime] = None

    @property
    def name(self) -> str:
        return "NSEOptions"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _field(self, value, source="NSEOptions", status="LIVE", quality="GOOD") -> FieldMeta:
        return FieldMeta(
            value=value,
            timestamp=self._ts(),
            source=source,
            freshness_seconds=0.0,
            status=status,
            quality=quality,
        )

    def _unavailable_field(self, reason: str = "unavailable", message: str = "") -> FieldMeta:
        return FieldMeta(
            value=None,
            timestamp=self._ts(),
            source="NSEOptions",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
            diagnostic_reason=reason,
            diagnostic_message=message,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve_expiry(self, nse_client) -> Optional[str]:
        """Resolve nearest valid expiry from NSE."""
        try:
            chain = nse_client.optionChain("nifty")
            records = chain.get("records", {})
            expiry_dates = records.get("expiryDates", [])
            if not expiry_dates:
                return None
            today = datetime.now().strftime("%d-%b-%Y")
            for exp in expiry_dates:
                if exp >= today:
                    return exp
            return expiry_dates[0]
        except Exception as e:
            log.warning(f"NSE expiry resolution failed: {e}")
            return None

    def _parse_option_chain(self, nse_client, expiry_str: str, spot: float) -> dict:
        """Parse NSE option chain and compute derived metrics."""
        try:
            chain = nse_client.optionChain("nifty")
            records = chain.get("records", {})

            raw_data = records.get("data", [])
            underlying = records.get("underlyingValue", spot)

            # Filter by expiry and build strike map
            strike_map: dict[float, dict] = {}
            for entry in raw_data:
                if entry.get("expiryDates") != expiry_str:
                    continue
                strike = entry.get("strikePrice")
                if strike is None:
                    continue
                ce = entry.get("CE") or {}
                pe = entry.get("PE") or {}
                strike_map[strike] = {
                    "ce_oi": ce.get("openInterest", 0) or 0,
                    "pe_oi": pe.get("openInterest", 0) or 0,
                    "ce_change": ce.get("changeinOpenInterest", 0) or 0,
                    "pe_change": pe.get("changeinOpenInterest", 0) or 0,
                    "ce_iv": ce.get("impliedVolatility"),
                    "pe_iv": pe.get("impliedVolatility"),
                    "ce_last": ce.get("lastPrice", 0) or 0,
                    "pe_last": pe.get("lastPrice", 0) or 0,
                }

            if not strike_map:
                return {}

            strikes = sorted(strike_map.keys())
            atm = min(strikes, key=lambda s: abs(s - underlying)) if strikes else 0

            # ATM IV (use CE ATM IV as proxy)
            atm_iv = None
            if atm in strike_map:
                atm_iv = strike_map[atm].get("ce_iv")
                if atm_iv is not None:
                    try:
                        atm_iv = float(atm_iv)
                    except (TypeError, ValueError):
                        atm_iv = None

            # Max pain: minimize total pain across strikes
            max_pain = self._compute_max_pain(strike_map)

            # Total OI for PCR
            total_call_oi = sum(v["ce_oi"] for v in strike_map.values())
            total_put_oi = sum(v["pe_oi"] for v in strike_map.values())
            pcr = round(total_put_oi / total_call_oi, 4) if total_call_oi else None

            # OI changes
            total_call_change = sum(v["ce_change"] for v in strike_map.values())
            total_put_change = sum(v["pe_change"] for v in strike_map.values())

            # Total option chain volume (all strikes, both CE and PE)
            total_call_volume = sum(v.get("ce_last", 0) for v in strike_map.values() if v.get("ce_last"))
            total_put_volume = sum(v.get("pe_last", 0) for v in strike_map.values() if v.get("pe_last"))
            total_option_volume = total_call_volume + total_put_volume

            return {
                "underlying": underlying,
                "expiry": expiry_str,
                "atm_strike": atm,
                "atm_iv": atm_iv,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "total_call_change": total_call_change,
                "total_put_change": total_put_change,
                "total_call_volume": total_call_volume,
                "total_put_volume": total_put_volume,
                "total_option_volume": total_option_volume,
                "pcr": pcr,
                "max_pain": max_pain,
                "strike_map": strike_map,
                "strikes": strikes,
            }
        except Exception as e:
            log.warning(f"NSE option chain parse failed: {e}")
            return {}

    def _compute_max_pain(self, strike_map: dict[float, dict]) -> Optional[float]:
        """Compute max pain strike from strike_map."""
        if not strike_map:
            return None
        strikes = sorted(strike_map.keys())
        min_pain = None
        min_pain_strike = None

        for test_strike in strikes:
            pain = 0.0
            for strike, data in strike_map.items():
                if strike < test_strike:
                    pain += (test_strike - strike) * data["ce_oi"]
                if strike > test_strike:
                    pain += (strike - test_strike) * data["pe_oi"]
            if min_pain is None or pain < min_pain:
                min_pain = pain
                min_pain_strike = test_strike

        return min_pain_strike

    def _build_snapshot_from_chain(self, chain_data: dict) -> MarketSnapshot:
        """Build MarketSnapshot from parsed option chain data."""
        if not chain_data:
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="NSEOptions",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )

        atm_iv = chain_data.get("atm_iv")
        pcr = chain_data.get("pcr")
        max_pain = chain_data.get("max_pain")
        atm_strike = chain_data.get("atm_strike")
        total_call_oi = chain_data.get("total_call_oi")
        total_put_oi = chain_data.get("total_put_oi")
        total_call_change = chain_data.get("total_call_change")
        total_put_change = chain_data.get("total_put_change")
        total_option_volume = chain_data.get("total_option_volume")

        # ... rest of method stays the same until return
        return MarketSnapshot(
            snapshot_timestamp=self._ts(),
            timezone="Asia/Kolkata",
            source="NSEOptions",
            data_status="LIVE" if any([total_call_oi, total_put_oi, atm_iv, pcr, max_pain]) else "UNAVAILABLE",
            missing_fields=[],
            call_oi=self._field(total_call_oi) if total_call_oi is not None else self._unavailable_field(),
            put_oi=self._field(total_put_oi) if total_put_oi is not None else self._unavailable_field(),
            call_oi_change=self._field(total_call_change) if total_call_change is not None else self._unavailable_field(),
            put_oi_change=self._field(total_put_change) if total_put_change is not None else self._unavailable_field(),
            pcr=self._field(pcr) if pcr is not None else self._unavailable_field(),
            atm_iv=self._field(atm_iv) if atm_iv is not None else self._unavailable_field(),
            max_pain=self._field(max_pain) if max_pain is not None else self._unavailable_field(),
            atm_strike=self._field(atm_strike) if atm_strike else self._unavailable_field(),
            total_option_volume=self._field(total_option_volume) if total_option_volume else self._unavailable_field(),
            sector_performance=self._unavailable_field(),
            india_vix=self._unavailable_field(),
            crude_price=self._unavailable_field(),
            usd_inr=self._unavailable_field(),
            us10y_yield=self._unavailable_field(),
            fii_flow_1d=self._unavailable_field(),
            fii_flow_5d=self._unavailable_field(),
            fii_flow_20d=self._unavailable_field(),
            fii_flow_month=self._unavailable_field(),
            dii_flow_1d=self._unavailable_field(),
            dii_flow_5d=self._unavailable_field(),
            dii_flow_20d=self._unavailable_field(),
            dii_flow_month=self._unavailable_field(),
            fed_rate=self._unavailable_field(),
            india_policy_rate=self._unavailable_field(),
            inflation=self._unavailable_field(),
            gdp_growth=self._unavailable_field(),
            pmi=self._unavailable_field(),
            earnings_growth=self._unavailable_field(),
        )

    # ── Main fetch ───────────────────────────────────────────────────

    def fetch(self) -> MarketSnapshot:
        """Fetch option chain data from NSE via NSEIndiaApi."""
        try:
            from nse import NSE as NSEIndia

            # Use cache to reduce NSE API calls
            cache_key = "nse_options_chain"
            cached = cache.get(cache_key)
            if cached:
                log.debug("Using cached NSE option chain")
                snapshot = cached.value
                # Refresh futures_oi_change from delta cache even on cached snapshot
                _fut_oi_fm = snapshot.futures_oi
                _fut_oi_chg_fm = snapshot.futures_oi_change
                if _fut_oi_fm and _fut_oi_fm.value is not None:
                    _prev = cache.get("nse_futures_oi")
                    if _prev and _prev.value is not None:
                        _delta = int(_fut_oi_fm.value) - int(_prev.value)
                        _fut_oi_chg_fm = self._field(_delta, source="NSEOptions", status="LIVE", quality="GOOD")
                    cache.put("nse_futures_oi", int(_fut_oi_fm.value), source="NSEOptions/nsefin", freshness_window=86400)
                    snapshot.futures_oi_change = _fut_oi_chg_fm
                return snapshot

            start = time.monotonic()
            with NSEIndia(download_folder="") as nse_client:
                expiry_str = self._resolve_expiry(nse_client)
                if not expiry_str:
                    log.warning("Could not resolve NSE option expiry")
                    return self._build_snapshot_from_chain({})

                chain_data = self._parse_option_chain(nse_client, expiry_str, spot=0.0)

            latency_ms = (time.monotonic() - start) * 1000
            registry.record_latency("NSEOptions", latency_ms)

            snapshot = self._build_snapshot_from_chain(chain_data)
            if snapshot.data_status == "LIVE":
                registry.record_fetch("call_oi", "NSEOptions", "NSEIndiaApi")
                registry.record_fetch("put_oi", "NSEOptions", "NSEIndiaApi")
                registry.record_fetch("atm_iv", "NSEOptions", "NSEIndiaApi")
                registry.record_fetch("max_pain", "NSEOptions", "NSEIndiaApi")
                registry.record_fetch("pcr", "NSEOptions", "NSEIndiaApi")
                registry.record_fetch("atm_strike", "NSEOptions", "NSEIndiaApi")
                cache.put(
                    cache_key,
                    snapshot,
                    source="NSEIndiaApi",
                    freshness_window=get_freshness_window("oi"),
                )
            else:
                registry.record_failure("NSEOptions", "Empty option chain")

            return snapshot

        except Exception as e:
            log.error(f"NSEOptionsProvider error: {e}")
            registry.record_failure("NSEOptions", str(e))
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="NSEOptions",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )

    # ── Diagnostics ──────────────────────────────────────────────────

    @property
    def diagnostics(self) -> dict:
        return {
            "nseoptions_connected": True,
            "option_chain_fetched": cache.get("nse_options_chain") is not None,
            "source": "NSEIndiaApi",
        }
