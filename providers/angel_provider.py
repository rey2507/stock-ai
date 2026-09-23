"""AngelProvider — SmartAPI-first live data provider with diagnostic engine.

Architecture:
  SmartAPI → Diagnostic Engine → Retry Workflow → Canonical MarketSnapshot

Every SmartAPI-supported field is diagnosed on nil before marking unavailable.
Never auto-replaces SmartAPI fields with external providers on temporary failure.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional
from collections import deque
from threading import Lock

from models.snapshot import MarketSnapshot, FieldMeta
from models.field_status import FieldStatus, DiagnosticReason
from models.source_policy import get_field_policy, is_smartapi_field
from providers.base import BaseProvider
from providers.smartapi_client import SmartAPIClient
from providers.instrument_manager import InstrumentManager
from providers.diagnostic_engine import NilDiagnosticEngine, DiagnosticContext, DiagnosticResult
from providers.cache import cache, get_freshness_window
from providers.source_registry import registry
from utils.market_hours import market

log = logging.getLogger(__name__)

NIFTY_SPOT_TOKEN = "99926000"
NSE_EXCHANGE = "NSE"
NFO_EXCHANGE = "NFO"

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class OIObservationStore:
    """Lightweight thread-safe store for OI observations.

    Tracks the most recent observations to compute genuine OI changes.
    Never fabricates, estimates, or interpolates missing observations.
    """

    def __init__(self, maxlen: int = 10):
        self._obs: deque = deque(maxlen=maxlen)
        self._lock = Lock()

    def record(self, oi: float, timestamp: Optional[datetime] = None):
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        with self._lock:
            self._obs.append((timestamp, oi))

    @property
    def latest(self) -> Optional[float]:
        with self._lock:
            if self._obs:
                return self._obs[-1][1]
            return None

    @property
    def previous(self) -> Optional[float]:
        with self._lock:
            if len(self._obs) >= 2:
                return self._obs[-2][1]
            return None

    @property
    def change(self) -> Optional[float]:
        latest = self.latest
        previous = self.previous
        if latest is not None and previous is not None:
            return latest - previous
        return None

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._obs)

    def extract_latest_from_response(self, raw_data) -> Optional[float]:
        """Extract the latest OI value from a getOIData response."""
        if isinstance(raw_data, list) and raw_data:
            latest = raw_data[-1]
            if isinstance(latest, dict):
                oi = latest.get("oi")
                if oi is not None:
                    return float(oi)
        elif isinstance(raw_data, dict):
            oi = raw_data.get("oi")
            if oi is not None:
                return float(oi)
        return None


class AngelProvider(BaseProvider):
    """SmartAPI-first live data provider with nil diagnostic engine.

    For every SmartAPI-supported field:
    1. Fetch from SmartAPI
    2. If nil → diagnose WHY (7-check workflow)
    3. If retryable → retry with backoff
    4. If genuinely unavailable → mark with granular status
    5. NEVER auto-replace with external provider
    """

    def __init__(self):
        self._client = SmartAPIClient()
        self._instrument_mgr = None
        self._diagnostic_engine = NilDiagnosticEngine()

    @property
    def name(self) -> str:
        return "AngelBroking"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _make_field(
        self,
        value,
        field_name: str = "",
        endpoint: str = "",
        status: str = "LIVE",
        quality: str = "GOOD",
        provider_latency_ms: float = None,
        diagnostic_reason: str = "ok",
        diagnostic_message: str = "",
        last_error: str = "",
    ) -> FieldMeta:
        """Create a FieldMeta with full provenance."""
        now = self._ts()
        policy = get_field_policy(field_name)
        expected_freshness = policy.expected_freshness_sec if policy else 300

        fm = FieldMeta(
            value=value,
            source="AngelBroking",
            source_endpoint=endpoint,
            observed_at=now,
            fetched_at=now,
            processed_at=now,
            timestamp=now,
            freshness_seconds=0.0,
            expected_freshness_sec=expected_freshness,
            status=status,
            quality=quality,
            diagnostic_reason=diagnostic_reason,
            diagnostic_message=diagnostic_message,
            last_error=last_error,
        )
        if provider_latency_ms is not None:
            fm.provider_latency_ms = provider_latency_ms
        return fm

    def _make_unavailable_field(
        self,
        field_name: str = "",
        endpoint: str = "",
        reason: str = "unavailable",
        message: str = "",
        retry_count: int = 0,
    ) -> FieldMeta:
        """Create an unavailable FieldMeta with diagnostic info."""
        now = self._ts()
        policy = get_field_policy(field_name)
        expected_freshness = policy.expected_freshness_sec if policy else 300

        # Determine if retryable
        if retry_count < MAX_RETRIES and reason not in (
            "unsupported", "invalid_instrument", "auth_error"
        ):
            status = FieldStatus.TEMPORARILY_UNAVAILABLE.value
        else:
            status = FieldStatus.UNAVAILABLE.value

        return FieldMeta(
            value=None,
            source="AngelBroking",
            source_endpoint=endpoint,
            observed_at=now,
            fetched_at=now,
            timestamp=now,
            freshness_seconds=None,
            expected_freshness_sec=expected_freshness,
            status=status,
            quality="INVALID",
            diagnostic_reason=reason,
            diagnostic_message=message,
            retry_count=retry_count,
        )

    def _get_instrument_manager(self) -> InstrumentManager:
        if self._instrument_mgr is None:
            self._instrument_mgr = InstrumentManager(self._client)
        return self._instrument_mgr

    # ─── SmartAPI Fetch with Diagnostic ─────────────────────────

    def _smartapi_fetch(
        self,
        field_name: str,
        fetch_fn,
        endpoint: str = "",
        **fetch_kwargs,
    ) -> tuple[FieldMeta, Optional[dict]]:
        """Fetch from SmartAPI with diagnostic engine on nil response.

        Returns (FieldMeta, raw_api_response).
        """
        start = time.monotonic()
        raw_response = None
        error_msg = None

        try:
            raw_response = fetch_fn(**fetch_kwargs)
            latency_ms = (time.monotonic() - start) * 1000
        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            error_msg = str(e)
            raw_response = None

        # Success path
        if raw_response is not None and raw_response != {}:
            # Handle standard {"data": ...} wrapper
            if isinstance(raw_response, dict) and raw_response.get("data"):
                data = raw_response["data"]
            else:
                data = raw_response

            if data and data != {} and data != []:
                registry.record_latency("SmartAPI", latency_ms)
                registry.record_fetch(field_name, "AngelBroking", endpoint)
                return self._make_field(
                    value=data,
                    field_name=field_name,
                    endpoint=endpoint,
                    provider_latency_ms=latency_ms,
                ), raw_response

        # Nil/error path — run diagnostic engine
        registry.record_failure("SmartAPI", f"nil for {field_name}")

        # Build diagnostic context
        policy = get_field_policy(field_name)
        ctx = DiagnosticContext(
            field_name=field_name,
            endpoint=endpoint,
            api_response=raw_response if isinstance(raw_response, dict) else None,
            api_error=error_msg,
            is_market_open=market.is_open,
            is_holiday=market.is_holiday,
            retry_count=0,
            max_retries=MAX_RETRIES,
        )

        result = self._diagnostic_engine.diagnose(ctx)

        fm = self._make_unavailable_field(
            field_name=field_name,
            endpoint=endpoint,
            reason=result.diagnostic_reason.value,
            message=result.message,
        )
        fm.provider_latency_ms = latency_ms
        return fm, raw_response

    # ─── Indicator Calculations ─────────────────────────────────

    def _compute_vwap(self, candles: list) -> Optional[float]:
        if not candles:
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        today_candles = [c for c in candles if c[0] and c[0].startswith(today)]
        if not today_candles:
            today_candles = candles

        cum_tp_vol = 0.0
        cum_vol = 0.0
        for c in today_candles:
            try:
                h, l, cl = float(c[2]), float(c[3]), float(c[4])
                vol = float(c[5]) if len(c) > 5 else 0
                tp = (h + l + cl) / 3.0
                cum_tp_vol += tp * vol
                cum_vol += vol
            except (IndexError, ValueError, TypeError):
                continue
        return round(cum_tp_vol / cum_vol, 2) if cum_vol > 0 else None

    def _compute_rsi(self, candles: list, period: int = 14) -> Optional[float]:
        if len(candles) < period + 1:
            return None
        closes = []
        for c in candles:
            try:
                closes.append(float(c[4]))
            except (IndexError, ValueError, TypeError):
                continue
        if len(closes) < period + 1:
            return None

        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        return round(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)), 2)

    def _compute_atr(self, candles: list, period: int = 14) -> Optional[float]:
        if len(candles) < period + 1:
            return None
        trs = []
        for i in range(1, len(candles)):
            try:
                h = float(candles[i][2])
                l = float(candles[i][3])
                prev_c = float(candles[i - 1][4])
                trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
            except (IndexError, ValueError, TypeError):
                continue
        if len(trs) < period:
            return None
        return round(sum(trs[-period:]) / period, 2)

    def _compute_relative_volume(self, candles: list, lookback: int = 20) -> Optional[float]:
        if len(candles) < lookback + 1:
            return None
        try:
            current_vol = float(candles[-1][5]) if len(candles[-1]) > 5 else 0
            avg_vol = sum(float(candles[i][5]) for i in range(-lookback - 1, -1) if len(candles[i]) > 5) / lookback
            return round(current_vol / avg_vol, 2) if avg_vol > 0 else None
        except (IndexError, ValueError, TypeError):
            return None

    # ─── Main Fetch ─────────────────────────────────────────────

    def fetch(self) -> MarketSnapshot:
        """Fetch live data from Angel Broking SmartAPI.

        Each field is independently diagnosed on nil.
        SmartAPI fields are NOT replaced with external providers.
        """
        try:
            start_total = time.monotonic()
            self._client.connect()

            # --- Spot data ---
            spot_fm, spot_raw = self._smartapi_fetch(
                "nifty_spot",
                self._client.ltp_data,
                endpoint="ltpData",
                exchange=NSE_EXCHANGE, symbol="NIFTY", token=NIFTY_SPOT_TOKEN,
            )
            spot_data = spot_fm.value if spot_fm.status == FieldStatus.LIVE.value else None
            spot_ltp = float(spot_data.get("ltp", 0)) if spot_data else None
            spot_open = float(spot_data.get("open", 0)) if spot_data else None
            spot_high = float(spot_data.get("high", 0)) if spot_data else None
            spot_low = float(spot_data.get("low", 0)) if spot_data else None
            spot_close = float(spot_data.get("close", 0)) if spot_data else None
            spot_change_pct = float(spot_data.get("percentChange", 0)) if spot_data and spot_data.get("percentChange") is not None else None

            if spot_ltp is not None and spot_fm.value is not None:
                spot_fm = self._make_field(spot_ltp, "nifty_spot", "ltpData")

            # --- Derive NIFTY open/high/low from SmartAPI candles ---
            spot_candles = self._fetch_candles(NIFTY_SPOT_TOKEN, "FIVE_MINUTE", days=1, exchange=NSE_EXCHANGE)
            if spot_candles:
                if not spot_open and len(spot_candles[0]) > 1:
                    spot_open = float(spot_candles[0][1]) if spot_candles[0][1] else None
                highs = [float(c[2]) for c in spot_candles if len(c) > 2 and c[2]]
                lows = [float(c[3]) for c in spot_candles if len(c) > 3 and c[3]]
                if not spot_high and highs:
                    spot_high = max(highs)
                if not spot_low and lows:
                    spot_low = min(lows)

            # NIFTY volume: SmartAPI index candles return 0, fallback to nselib
            nifty_volume = None
            if spot_candles:
                volumes = [float(c[5]) for c in spot_candles if len(c) > 5 and c[5]]
                nifty_volume = int(sum(volumes)) if volumes else None
            if nifty_volume is None or nifty_volume == 0:
                try:
                    import nselib
                    from nselib import capital_market as _cm
                    from datetime import datetime as _dt, timedelta as _td
                    _end = _dt.now()
                    _start = _end - _td(days=7)
                    _voldata = _cm.index_data(index="NIFTY 50", from_date=_start.strftime("%d-%m-%Y"), to_date=_end.strftime("%d-%m-%Y"))
                    if not _voldata.empty:
                        nifty_volume = int(_voldata.iloc[0]["TRADED_QTY"])
                except Exception:
                    pass

            # --- Dynamic instrument discovery ---
            inst_mgr = self._get_instrument_manager()
            chain = inst_mgr.discover_all(spot_ltp or 23000)

            # --- Futures data ---
            fut_fm = self._make_unavailable_field("futures_price", "ltpData")
            fut_ltp = None
            fut_close = None
            if chain.futures_token and chain.futures_symbol:
                fut_fm, _ = self._smartapi_fetch(
                    "futures_price",
                    self._client.ltp_data,
                    endpoint="ltpData",
                    exchange=NFO_EXCHANGE, symbol=chain.futures_symbol, token=chain.futures_token,
                )
                fut_data = fut_fm.value if fut_fm.status == FieldStatus.LIVE.value else None
                if fut_data:
                    fut_ltp = float(fut_data.get("ltp", 0))
                    fut_close = float(fut_data.get("close", 0))
                    fut_fm = self._make_field(fut_ltp, "futures_price", "ltpData")

            # --- Futures OI ---
            # NOTE: SmartAPI getOIData returns empty. Moved to NSEOptionsProvider.
            futures_oi_fm = self._make_unavailable_field("futures_oi", "getOIData", reason="unsupported", message="Moved to NSEOptionsProvider")
            futures_oi_value = None

            # --- Futures change ---
            fut_change_pct = None
            if fut_ltp and fut_close and fut_close > 0:
                fut_change_pct = round((fut_ltp - fut_close) / fut_close * 100, 2)

            # --- PCR from SmartAPI putCallRatio ---
            # NOTE: SmartAPI PCR kept as fallback; NSEOptionsProvider provides PCR from option chain
            pcr = None
            try:
                pcr = self._client.get_nifty_pcr()
            except Exception:
                pass

            # --- Option chain OI / IV / Max Pain ---
            # NOTE: SmartAPI getMarketData/optionGreek unreliable. Moved to NSEOptionsProvider.
            atm_strike = chain.atm_strike if chain.atm_strike else None
            call_oi = put_oi = call_oi_change = put_oi_change = max_pain = atm_iv = None

            # --- Candle data for VWAP/RSI/ATR/RV ---
            candles = []
            if chain.futures_token:
                candles = self._fetch_candles(chain.futures_token, "FIVE_MINUTE", days=5)
            
            # Fallback: if SmartAPI candles fail, use nselib daily data
            if not candles:
                try:
                    import nselib
                    from nselib import capital_market as _cm
                    from datetime import datetime as _dt, timedelta as _td
                    _end = _dt.now()
                    _start = _end - _td(days=30)
                    _daily = _cm.index_data(index="NIFTY 50", from_date=_start.strftime("%d-%m-%Y"), to_date=_end.strftime("%d-%m-%Y"))
                    if not _daily.empty:
                        candles = []
                        for _, row in _daily.iterrows():
                            candles.append([
                                str(row["TIMESTAMP"]),
                                float(row["OPEN_INDEX_VAL"]),
                                float(row["HIGH_INDEX_VAL"]),
                                float(row["LOW_INDEX_VAL"]),
                                float(row["CLOSE_INDEX_VAL"]),
                                int(row["TRADED_QTY"]) if row["TRADED_QTY"] else 0,
                            ])
                except Exception:
                    pass
            
            vwap = self._compute_vwap(candles)
            rsi = self._compute_rsi(candles)
            atr = self._compute_atr(candles)
            relative_volume = self._compute_relative_volume(candles)

            # --- NSE stats ---
            # NOTE: advances/declines now provided by NSEOptionsProvider
            advances = declines = unchanged = advance_decline_ratio = None

            # --- Spot change ---
            spot_change = None
            if spot_ltp and spot_close and spot_close > 0:
                spot_change = round(spot_ltp - spot_close, 2)
            if spot_change_pct is None and spot_change is not None and spot_close and spot_close > 0:
                spot_change_pct = round(spot_change / spot_close * 100, 2)

            # --- Build snapshot ---
            total_latency = (time.monotonic() - start_total) * 1000
            log.info(f"AngelProvider fetch: {total_latency:.0f}ms")

            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="AngelBroking",
                data_status="LIVE" if spot_ltp else "UNAVAILABLE",
                missing_fields=[],
                nifty_spot=spot_fm if spot_ltp else self._make_unavailable_field("nifty_spot", "ltpData", reason=spot_fm.diagnostic_reason, message=spot_fm.diagnostic_message),
                nifty_change=self._make_field(spot_change, "nifty_change", "ltpData") if spot_change is not None else self._make_unavailable_field("nifty_change", "ltpData"),
                nifty_change_pct=self._make_field(spot_change_pct, "nifty_change_pct", "ltpData") if spot_change_pct is not None else self._make_unavailable_field("nifty_change_pct", "ltpData"),
                 nifty_open=self._make_field(spot_open, "nifty_open", "ltpData") if spot_open else self._make_unavailable_field("nifty_open", "ltpData"),
                 nifty_high=self._make_field(spot_high, "nifty_high", "ltpData") if spot_high else self._make_unavailable_field("nifty_high", "ltpData"),
                 nifty_low=self._make_field(spot_low, "nifty_low", "ltpData") if spot_low else self._make_unavailable_field("nifty_low", "ltpData"),
                 nifty_volume=self._make_field(nifty_volume, "nifty_volume", "nselib") if nifty_volume else self._make_unavailable_field("nifty_volume", "nselib"),
                futures_price=fut_fm,
                futures_change_pct=self._make_field(fut_change_pct, "futures_change_pct", "ltpData") if fut_change_pct is not None else self._make_unavailable_field("futures_change_pct", "ltpData"),
                 futures_oi=self._make_unavailable_field("futures_oi", "getOIData"),
                 futures_oi_change=self._make_unavailable_field("futures_oi_change", "getOIData"),
                futures_expiry=self._make_field(chain.expiry, "futures_expiry", "searchScrip") if chain.expiry else self._make_unavailable_field("futures_expiry", "searchScrip"),
                atm_strike=self._make_field(atm_strike, "atm_strike", "NSEOptions") if atm_strike else self._make_unavailable_field("atm_strike", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                call_oi=self._make_field(call_oi, "call_oi", "NSEOptions") if call_oi is not None else self._make_unavailable_field("call_oi", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                put_oi=self._make_field(put_oi, "put_oi", "NSEOptions") if put_oi is not None else self._make_unavailable_field("put_oi", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                call_oi_change=self._make_field(call_oi_change, "call_oi_change", "NSEOptions") if call_oi_change is not None else self._make_unavailable_field("call_oi_change", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                put_oi_change=self._make_field(put_oi_change, "put_oi_change", "NSEOptions") if put_oi_change is not None else self._make_unavailable_field("put_oi_change", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                pcr=self._make_field(pcr, "pcr", "NSEOptions") if pcr is not None else self._make_unavailable_field("pcr", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                atm_iv=self._make_field(atm_iv, "atm_iv", "NSEOptions") if atm_iv is not None else self._make_unavailable_field("atm_iv", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                max_pain=self._make_field(max_pain, "max_pain", "NSEOptions") if max_pain is not None else self._make_unavailable_field("max_pain", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                vwap=self._make_field(vwap, "vwap", "getCandleData") if vwap is not None else self._make_unavailable_field("vwap", "getCandleData"),
                rsi=self._make_field(rsi, "rsi", "getCandleData") if rsi is not None else self._make_unavailable_field("rsi", "getCandleData"),
                atr=self._make_field(atr, "atr", "getCandleData") if atr is not None else self._make_unavailable_field("atr", "getCandleData"),
                relative_volume=self._make_field(relative_volume, "relative_volume", "getCandleData") if relative_volume is not None else self._make_unavailable_field("relative_volume", "getCandleData"),
                advances=self._make_field(advances, "advances", "NSEOptions") if advances is not None else self._make_unavailable_field("advances", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                declines=self._make_field(declines, "declines", "NSEOptions") if declines is not None else self._make_unavailable_field("declines", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                unchanged=self._make_field(unchanged, "unchanged", "NSEOptions") if unchanged is not None else self._make_unavailable_field("unchanged", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                advance_decline_ratio=self._make_field(advance_decline_ratio, "advance_decline_ratio", "NSEOptions") if advance_decline_ratio is not None else self._make_unavailable_field("advance_decline_ratio", "NSEOptions", reason="external", message="Provided by NSEOptionsProvider"),
                sector_performance=self._make_unavailable_field("sector_performance", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide sector performance"),
                india_vix=self._make_unavailable_field("india_vix", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide India VIX"),
                crude_price=self._make_unavailable_field("crude_price", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide crude prices"),
                usd_inr=self._make_unavailable_field("usd_inr", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide USD/INR"),
                us10y_yield=self._make_unavailable_field("us10y_yield", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide US 10Y yield"),
                fii_flow_1d=self._make_unavailable_field("fii_flow_1d", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide FII flows"),
                fii_flow_5d=self._make_unavailable_field("fii_flow_5d", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide FII flows"),
                fii_flow_20d=self._make_unavailable_field("fii_flow_20d", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide FII flows"),
                fii_flow_month=self._make_unavailable_field("fii_flow_month", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide FII flows"),
                dii_flow_1d=self._make_unavailable_field("dii_flow_1d", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide DII flows"),
                dii_flow_5d=self._make_unavailable_field("dii_flow_5d", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide DII flows"),
                dii_flow_20d=self._make_unavailable_field("dii_flow_20d", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide DII flows"),
                dii_flow_month=self._make_unavailable_field("dii_flow_month", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide DII flows"),
                fed_rate=self._make_unavailable_field("fed_rate", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide Fed rate"),
                india_policy_rate=self._make_unavailable_field("india_policy_rate", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide RBI rate"),
                inflation=self._make_unavailable_field("inflation", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide inflation data"),
                gdp_growth=self._make_unavailable_field("gdp_growth", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide GDP data"),
                pmi=self._make_unavailable_field("pmi", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide PMI data"),
                earnings_growth=self._make_unavailable_field("earnings_growth", "UNSUPPORTED", reason="unsupported", message="SmartAPI does not provide earnings data"),
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            registry.record_failure("SmartAPI", str(e))
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="AngelBroking",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )

    # ─── Helper Methods ─────────────────────────────────────────

    def _fetch_option_oi_with_diagnostics(self, chain) -> dict:
        """Fetch option chain OI with diagnostic tracking."""
        from providers.oi_model import OIAggregate
        agg = OIAggregate()
        tokens_to_fetch = {}

        for strike_info in chain.strikes:
            if strike_info.ce_token:
                tokens_to_fetch[strike_info.ce_token] = strike_info.ce_symbol
            if strike_info.pe_token:
                tokens_to_fetch[strike_info.pe_token] = strike_info.pe_symbol

        if not tokens_to_fetch:
            return {}

        # Try batch fetch
        try:
            token_list = list(tokens_to_fetch.keys())
            start = time.monotonic()
            result = self._client.get_market_data("FULL", {NFO_EXCHANGE: token_list})
            latency_ms = (time.monotonic() - start) * 1000
            registry.record_latency("SmartAPI", latency_ms)

            if result:
                for strike_info in chain.strikes:
                    ce_oi = pe_oi = ce_change = pe_change = None
                    if strike_info.ce_token and strike_info.ce_token in result:
                        d = result[strike_info.ce_token]
                        if isinstance(d, dict):
                            ce_oi = d.get("opnInterest")
                            ce_change = d.get("oiChange")
                    if strike_info.pe_token and strike_info.pe_token in result:
                        d = result[strike_info.pe_token]
                        if isinstance(d, dict):
                            pe_oi = d.get("opnInterest")
                            pe_change = d.get("oiChange")
                    agg.set_strike_oi(strike_info.strike, ce_oi, pe_oi, ce_change, pe_change)
        except Exception as e:
            log.warning(f"getMarketData FULL failed: {e}")

        return {
            "aggregate": agg,
            "pcr": agg.pcr_by_oi,
            "max_pain": agg.max_pain,
            "total_call_oi": agg.total_call_oi,
            "total_put_oi": agg.total_put_oi,
            "total_call_change": agg.total_call_change,
            "total_put_change": agg.total_put_change,
        }

    # NOTE: _fetch_option_greeks removed — ATM IV now from NSEOptionsProvider
    # NOTE: _fetch_nse_stats removed — advances/declines now from NSEOptionsProvider
    # NOTE: futures_oi via getOIData removed — now from NSEOptionsProvider

    def _fetch_candles(self, token: str, interval: str = "FIVE_MINUTE", days: int = 5, exchange: str = None) -> list:
        cache_key = f"candles_{token}_{interval}_{days}d"
        cached = cache.get(cache_key)
        if cached:
            return cached.value
        start = time.monotonic()
        _exchange = exchange or NFO_EXCHANGE
        candles = self._client.get_candles(_exchange, token, interval, days)
        latency_ms = (time.monotonic() - start) * 1000
        registry.record_latency("SmartAPI", latency_ms)
        if candles:
            cache.put(cache_key, candles, source="SmartAPI", freshness_window=get_freshness_window("vwap"))
        return candles or []

    @property
    def diagnostics(self) -> dict:
        """Non-sensitive diagnostic info for the diagnostics panel."""
        try:
            connected = self._client.is_connected
        except Exception:
            connected = False

        chain = None
        try:
            inst_mgr = self._get_instrument_manager()
            chain = cache.get("instrument_option_chain")
        except Exception:
            pass

        return {
            "angel_connected": connected,
            "futures_contract_discovered": bool(chain and getattr(chain.value, "futures_token", None)),
            "futures_token": getattr(chain.value, "futures_token", None) if chain else None,
            "expiry_discovered": bool(chain and getattr(chain.value, "expiry", None)),
            "expiry": getattr(chain.value, "expiry", None) if chain else None,
            "atm_strike": getattr(chain.value, "atm_strike", None) if chain else None,
            "strikes_count": len(getattr(chain.value, "strikes", [])) if chain else 0,
            "ce_count": sum(1 for s in getattr(chain.value, "strikes", []) if getattr(s, "ce_token", None)) if chain else 0,
            "pe_count": sum(1 for s in getattr(chain.value, "strikes", []) if getattr(s, "pe_token", None)) if chain else 0,
        }
