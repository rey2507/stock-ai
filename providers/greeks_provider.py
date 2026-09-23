"""GreeksProvider — Black-Scholes Greeks calculation for NIFTY options.

Calculates Delta, Gamma, Theta, Vega for ATM ± 2 strikes across
current and next 2 expiries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from models.snapshot import MarketSnapshot, FieldMeta
from providers.base import BaseProvider
from providers.greeks_calculator import GreeksCalculator, GreeksResult
from providers.cache import cache, get_freshness_window

log = logging.getLogger(__name__)


class GreeksProvider(BaseProvider):
    """Calculates Black-Scholes Greeks for NIFTY option contracts."""

    def __init__(self, risk_free_rate: float = 0.065, dividend_yield: float = 0.0):
        self.calculator = GreeksCalculator(
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
        )

    @property
    def name(self) -> str:
        return "Greeks"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _field(self, value, source="Greeks", status="LIVE", quality="GOOD") -> FieldMeta:
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
            source="Greeks",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
            diagnostic_reason=reason,
            diagnostic_message=message,
        )

    def fetch(self) -> MarketSnapshot:
        try:
            from nse import NSE as NSEIndia

            cache_key = "nse_options_chain"
            cached = cache.get(cache_key)
            if cached:
                chain_data = cached.value
            else:
                with NSEIndia(download_folder="") as nse_client:
                    chain = nse_client.optionChain("nifty")
                    records = chain.get("records", {})
                    expiry_dates = records.get("expiryDates", [])
                    raw_data = records.get("data", [])
                    underlying = records.get("underlyingValue", 0)

                    if not expiry_dates or not raw_data:
                        return self._empty_snapshot()

                    today = datetime.now().strftime("%d-%b-%Y")
                    current_expiry = None
                    next_expiries = []
                    for exp in expiry_dates:
                        if exp >= today and current_expiry is None:
                            current_expiry = exp
                        elif exp >= today and current_expiry is not None:
                            next_expiries.append(exp)
                        if len(next_expiries) >= 2:
                            break

                    if not current_expiry:
                        current_expiry = expiry_dates[0]
                        next_expiries = [e for e in expiry_dates[1:3] if e >= today]

                    target_expiries = [current_expiry] + next_expiries[:2]

                    strike_map: dict[float, dict] = {}
                    for entry in raw_data:
                        if entry.get("expiryDates") != current_expiry:
                            continue
                        strike = entry.get("strikePrice")
                        if strike is None:
                            continue
                        ce = entry.get("CE") or {}
                        pe = entry.get("PE") or {}
                        strike_map[strike] = {
                            "ce_iv": ce.get("impliedVolatility"),
                            "pe_iv": pe.get("impliedVolatility"),
                            "ce_last": ce.get("lastPrice", 0) or 0,
                            "pe_last": pe.get("lastPrice", 0) or 0,
                            "days_to_expiry": self._days_to_expiry(current_expiry),
                        }

                    strikes = sorted(strike_map.keys())
                    atm = min(strikes, key=lambda s: abs(s - underlying)) if strikes else 0

                    chain_data = {
                        "underlying": underlying,
                        "expiry": current_expiry,
                        "atm_strike": atm,
                        "strike_map": strike_map,
                        "strikes": strikes,
                        "expiry_dates": target_expiries,
                    }

            return self._build_snapshot(chain_data)

        except Exception as e:
            log.error(f"GreeksProvider error: {e}")
            return self._empty_snapshot()

    def _days_to_expiry(self, expiry_str: str) -> int:
        try:
            exp = datetime.strptime(expiry_str, "%d-%b-%Y")
            return max((exp.date() - datetime.now().date()).days, 0)
        except Exception:
            return 0

    def _empty_snapshot(self) -> MarketSnapshot:
        return MarketSnapshot(
            snapshot_timestamp=self._ts(),
            timezone="Asia/Kolkata",
            source="Greeks",
            data_status="UNAVAILABLE",
            missing_fields=["ALL"],
        )

    def _build_snapshot(self, chain_data: dict) -> MarketSnapshot:
        underlying = chain_data.get("underlying", 0)
        atm = chain_data.get("atm_strike", 0)
        strike_map = chain_data.get("strike_map", {})
        expiry_dates = chain_data.get("expiry_dates", [])
        all_strikes = chain_data.get("strikes", [])

        if not all_strikes or not expiry_dates:
            return self._empty_snapshot()

        atm_idx = all_strikes.index(atm) if atm in all_strikes else len(all_strikes) // 2
        selected_strikes = []
        for offset in [0, -1, 1, -2, 2]:
            idx = atm_idx + offset
            if 0 <= idx < len(all_strikes):
                selected_strikes.append(all_strikes[idx])
        selected_strikes = sorted(set(selected_strikes))

        greeks_by_strike: dict[float, dict[str, list[GreeksResult]]] = {}
        model_fits: List[str] = []

        for expiry in expiry_dates:
            days_to_expiry = self._days_to_expiry(expiry)
            if days_to_expiry < 0:
                continue

            for strike in selected_strikes:
                data = strike_map.get(strike, {})
                ce_iv = data.get("ce_iv")
                pe_iv = data.get("pe_iv")
                ce_last = data.get("ce_last", 0) or 0
                pe_last = data.get("pe_last", 0) or 0

                if ce_iv is not None and ce_last > 0:
                    try:
                        ce_iv_f = float(ce_iv)
                        result = self.calculator.calculate_greeks(
                            spot=underlying,
                            strike=strike,
                            days_to_expiry=days_to_expiry,
                            implied_vol=ce_iv_f,
                            market_premium=ce_last,
                            option_type="CALL",
                            expiry_date=expiry,
                        )
                        greeks_by_strike.setdefault(strike, {})[expiry] = greeks_by_strike.get(strike, {}).get(expiry, []) + [result]
                        model_fits.append(result.model_fit)
                    except Exception as e:
                        log.warning(f"Greeks calc failed for CALL {strike} {expiry}: {e}")

                if pe_iv is not None and pe_last > 0:
                    try:
                        pe_iv_f = float(pe_iv)
                        result = self.calculator.calculate_greeks(
                            spot=underlying,
                            strike=strike,
                            days_to_expiry=days_to_expiry,
                            implied_vol=pe_iv_f,
                            market_premium=pe_last,
                            option_type="PUT",
                            expiry_date=expiry,
                        )
                        greeks_by_strike.setdefault(strike, {})[expiry] = greeks_by_strike.get(strike, {}).get(expiry, []) + [result]
                        model_fits.append(result.model_fit)
                    except Exception as e:
                        log.warning(f"Greeks calc failed for PUT {strike} {expiry}: {e}")

        poor_count = sum(1 for f in model_fits if f == "POOR")
        total = len(model_fits)
        if total > 0 and poor_count / total > 0.2:
            log.warning(f"High model divergence: {poor_count/total*100:.1f}% poor fit")

        snapshot = MarketSnapshot(
            snapshot_timestamp=self._ts(),
            timezone="Asia/Kolkata",
            source="Greeks",
            data_status="LIVE" if greeks_by_strike else "UNAVAILABLE",
            missing_fields=[] if greeks_by_strike else ["ALL"],
            greeks_by_strike=greeks_by_strike,
            nifty_spot=self._field(underlying) if underlying else self._unavailable_field(),
            atm_strike=self._field(atm) if atm else self._unavailable_field(),
        )

        return snapshot

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "greeks_provider_connected": True,
            "calculator_ready": self.calculator is not None,
        }
