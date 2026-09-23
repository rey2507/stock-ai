"""WebSourceProvider — fetches macro/market data from free web sources.

Covers fields that SmartAPI cannot provide:
  - India VIX, crude oil, USD/INR, US 10Y yield (via Yahoo Finance)
  - Advances/Declines, Option Chain OI/IV/Max Pain (via NSE API)
  - FII/DII flows (via NSE API, with graceful fallback)

Yahoo Finance requires no authentication. NSE APIs require session cookies
and are best-effort (may fail outside market hours or from some networks).
"""

import traceback
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from models.snapshot import MarketSnapshot, FieldMeta
from providers.base import BaseProvider
from providers.source_registry import registry

log = logging.getLogger(__name__)

# Yahoo Finance tickers
YF_VIX = "^INDIAVIX"
YF_CRUDE_WTI = "CL=F"
YF_CRUDE_BRENT = "BZ=F"
YF_USDINR = "USDINR=X"
YF_US10Y = "^TNX"
YF_SENSEX = "^BSESN"
YF_BANKNIFTY = "^NSEBANK"
YF_GIFTNIFTY = "^NSEI"

# NSE session config
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
}


class WebSourceProvider(BaseProvider):
    """Fetches macro/market data from Yahoo Finance and NSE APIs."""

    def __init__(self):
        self._nse_session: Optional[requests.Session] = None
        self._nse_ready = False

    @property
    def name(self) -> str:
        return "WebSource"

    def _ts(self) -> datetime:
        return datetime.now(timezone.utc)

    def _field(self, value, source="WebSource", status="LIVE", quality="GOOD") -> FieldMeta:
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
            source="WebSource",
            freshness_seconds=None,
            status="UNAVAILABLE",
            quality="INVALID",
        )

    # ── Yahoo Finance helpers ──────────────────────────────────────

    def _yf_latest(self, ticker: str) -> Optional[float]:
        """Get latest close from Yahoo Finance."""
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        return None

    def _yf_previous(self, ticker: str) -> Optional[float]:
        """Get second-to-last close from Yahoo Finance."""
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) >= 2:
                return float(hist["Close"].iloc[-2])
        except Exception:
            pass
        return None

    def _fetch_yahoo_data(self) -> dict:
        """Fetch all Yahoo Finance data in batch."""
        results = {}
        try:
            import yfinance as yf
            tickers = {
                "vix": YF_VIX,
                "crude_wti": YF_CRUDE_WTI,
                "crude_brent": YF_CRUDE_BRENT,
                "usdinr": YF_USDINR,
                "us10y": YF_US10Y,
                "sensex": YF_SENSEX,
                "banknifty": YF_BANKNIFTY,
                "giftnifty": YF_GIFTNIFTY,
            }
            for key, symbol in tickers.items():
                try:
                    t = yf.Ticker(symbol)
                    hist = t.history(period="5d")
                    if not hist.empty:
                        results[key] = {
                            "current": float(hist["Close"].iloc[-1]),
                            "previous": float(hist["Close"].iloc[-2]) if len(hist) >= 2 else None,
                        }
                except Exception:
                    results[key] = None
        except ImportError:
            pass
        return results

    # ── NSE session helpers ────────────────────────────────────────

    def _ensure_nse_session(self) -> bool:
        """Create NSE session with cookies from homepage."""
        if self._nse_ready and self._nse_session:
            return True

        try:
            self._nse_session = requests.Session()
            self._nse_session.headers.update(NSE_HEADERS)
            r = self._nse_session.get("https://www.nseindia.com", timeout=15)
            if r.status_code == 200:
                self._nse_ready = True
                return True
            # Even 403 sometimes gives cookies we can try
            if self._nse_session.cookies:
                self._nse_ready = True
                return True
        except Exception:
            pass
        return False

    def _nse_get(self, url: str) -> Optional[dict]:
        """GET from NSE API with session handling."""
        if not self._ensure_nse_session():
            return None
        try:
            r = self._nse_session.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # ── NSE data fetchers ──────────────────────────────────────────

    def _fetch_vix(self) -> Optional[float]:
        """India VIX from NSE indices API."""
        data = self._nse_get("https://www.nseindia.com/api/allIndices")
        if data and data.get("data"):
            for idx in data["data"]:
                if "INDIA VIX" in idx.get("name", "").upper():
                    return float(idx.get("last", 0))
        return None

    def _fetch_advances_declines(self) -> Optional[dict]:
        """Advances/Declines from NSE."""
        data = self._nse_get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050")
        if data and data.get("data"):
            stocks = data["data"]
            advances = sum(1 for s in stocks if s.get("pChange", 0) > 0)
            declines = sum(1 for s in stocks if s.get("pChange", 0) < 0)
            unchanged = sum(1 for s in stocks if s.get("pChange", 0) == 0)
            ratio = round(advances / declines, 2) if declines > 0 else None
            return {
                "advances": advances,
                "declines": declines,
                "unchanged": unchanged,
                "ratio": ratio,
            }

        try:
            from nse import NSE as NSEIndia
            with NSEIndia(download_folder="") as nse_client:
                ad = nse_client.advanceDecline()
                if ad:
                    advances = int(ad.get("advances", 0))
                    declines = int(ad.get("declines", 0))
                    unchanged = int(ad.get("unchanged", 0))
                    ratio = round(advances / declines, 2) if declines > 0 else None
                    return {
                        "advances": advances,
                        "declines": declines,
                        "unchanged": unchanged,
                        "ratio": ratio,
                    }
        except Exception:
            pass

        return None

    def _fetch_option_chain(self) -> Optional[dict]:
        """Option chain data from NSE — OI, IV, max pain, PCR (OI-based)."""
        data = self._nse_get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY")
        if not data or not data.get("records"):
            return None

        records = data["records"]
        spot = float(records.get("underlyingValue", 0))
        expiry_dates = records.get("expiryDates", [])
        if not expiry_dates:
            return None

        nearest_expiry = expiry_dates[0]
        ce_all = records.get("CE", [])
        pe_all = records.get("PE", [])

        ce = [o for o in ce_all if o.get("expiryDate") == nearest_expiry]
        pe = [o for o in pe_all if o.get("expiryDate") == nearest_expiry]

        if not ce or not pe:
            return None

        strikes = sorted(set(o.get("strikePrice") for o in ce))
        atm = min(strikes, key=lambda s: abs(s - spot)) if strikes else 0

        # ATM IV
        atm_ce = [o for o in ce if o.get("strikePrice") == atm]
        atm_pe = [o for o in pe if o.get("strikePrice") == atm]
        atm_iv = None
        if atm_ce:
            atm_iv = atm_ce[0].get("impliedVolatility")

        # ATM OI
        call_oi = atm_ce[0].get("openInterest", 0) if atm_ce else 0
        put_oi = atm_pe[0].get("openInterest", 0) if atm_pe else 0

        # Total OI for PCR (all strikes)
        total_ce_oi = sum(o.get("openInterest", 0) for o in ce)
        total_pe_oi = sum(o.get("openInterest", 0) for o in pe)
        pcr_oi = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else None

        # Max Pain
        all_strikes = sorted(set(o.get("strikePrice") for o in ce + pe))
        min_pain = float("inf")
        max_pain_strike = 0
        for strike in all_strikes:
            pain = 0
            for o in ce:
                sp = o.get("strikePrice", 0)
                if sp < strike:
                    pain += (strike - sp) * o.get("openInterest", 0)
            for o in pe:
                sp = o.get("strikePrice", 0)
                if sp > strike:
                    pain += (sp - strike) * o.get("openInterest", 0)
            if pain < min_pain:
                min_pain = pain
                max_pain_strike = strike

        return {
            "atm_strike": atm,
            "atm_ce_oi": call_oi,
            "atm_pe_oi": put_oi,
            "atm_iv": atm_iv,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "pcr_oi": pcr_oi,
            "max_pain": max_pain_strike,
            "expiry": nearest_expiry,
        }

    # ── Main fetch ─────────────────────────────────────────────────

    def fetch(self) -> MarketSnapshot:
        """Fetch data from Yahoo Finance and NSE.

        Returns a snapshot with all fields it can get. Fields that fail
        are marked UNAVAILABLE. Never raises to caller.
        """
        try:
            sources_attempted = []

            # Yahoo Finance data
            sources_attempted.append("YahooFinance")
            yahoo = self._fetch_yahoo_data()

            # NSE data (best-effort)
            sources_attempted.append("NSE")
            nse_vix = self._fetch_vix()
            nse_ad = self._fetch_advances_declines()
            nse_oc = self._fetch_option_chain()

            # Record attempted sources
            for src in sources_attempted:
                registry.record_fetch("websource_attempt", "WebSource", src)

            # Determine successful sources
            yahoo_success = any([
                yahoo.get("vix", {}).get("current"),
                yahoo.get("crude_brent", {}).get("current"),
                yahoo.get("usdinr", {}).get("current"),
                yahoo.get("us10y", {}).get("current"),
            ])
            nse_success = any([nse_vix, nse_ad, nse_oc])

            successful_sources = []
            if yahoo_success:
                successful_sources.append("YahooFinance")
            if nse_success:
                successful_sources.append("NSE")

            primary_source = "+".join(successful_sources) if successful_sources else "None"
            log.info(f"WebSource data from: {primary_source} (attempted: {sources_attempted})")

            # VIX: prefer NSE, fallback to Yahoo
            vix_val = nse_vix or (yahoo.get("vix", {}).get("current") if yahoo.get("vix") else None)
            vix_source = "NSE" if nse_vix else ("YahooFinance" if yahoo.get("vix", {}).get("current") else None)

            # Crude: use WTI as primary, Brent as reference
            crude_wti = yahoo.get("crude_wti", {}).get("current") if yahoo.get("crude_wti") else None
            crude_brent = yahoo.get("crude_brent", {}).get("current") if yahoo.get("crude_brent") else None
            crude_val = crude_brent or crude_wti  # Brent is more relevant for India
            crude_source = "YahooFinance/Brent" if crude_brent else ("YahooFinance/WTI" if crude_wti else None)

            # USD/INR
            usdinr = yahoo.get("usdinr", {}).get("current") if yahoo.get("usdinr") else None
            usdinr_source = "YahooFinance" if usdinr else None

            # US 10Y
            us10y = yahoo.get("us10y", {}).get("current") if yahoo.get("us10y") else None
            us10y_source = "YahooFinance" if us10y else None

            # Related indices
            sensex = yahoo.get("sensex", {}).get("current") if yahoo.get("sensex") else None
            sensex_prev = yahoo.get("sensex", {}).get("previous") if yahoo.get("sensex") else None
            banknifty = yahoo.get("banknifty", {}).get("current") if yahoo.get("banknifty") else None
            banknifty_prev = yahoo.get("banknifty", {}).get("previous") if yahoo.get("banknifty") else None
            giftnifty = yahoo.get("giftnifty", {}).get("current") if yahoo.get("giftnifty") else None
            giftnifty_prev = yahoo.get("giftnifty", {}).get("previous") if yahoo.get("giftnifty") else None

            sensex_change_pct = None
            if sensex and sensex_prev and sensex_prev != 0:
                sensex_change_pct = round((sensex - sensex_prev) / sensex_prev * 100, 2)
            banknifty_change_pct = None
            if banknifty and banknifty_prev and banknifty_prev != 0:
                banknifty_change_pct = round((banknifty - banknifty_prev) / banknifty_prev * 100, 2)
            giftnifty_change_pct = None
            if giftnifty and giftnifty_prev and giftnifty_prev != 0:
                giftnifty_change_pct = round((giftnifty - giftnifty_prev) / giftnifty_prev * 100, 2)

            data_status = "LIVE" if any([vix_val, crude_val, usdinr, us10y, nse_ad, nse_oc, sensex, banknifty, giftnifty]) else "UNAVAILABLE"

            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="WebSource",
                data_status=data_status,
                missing_fields=[],
                # Volatility
                india_vix=self._field(vix_val, source=vix_source or "WebSource") if vix_val else self._unavailable_field(),
                # Macro
                crude_price=self._field(crude_val, source=crude_source or "WebSource") if crude_val else self._unavailable_field(),
                usd_inr=self._field(usdinr, source=usdinr_source or "WebSource") if usdinr else self._unavailable_field(),
                us10y_yield=self._field(us10y, source=us10y_source or "WebSource") if us10y else self._unavailable_field(),
                # Participation (from NSE)
                advances=self._field(nse_ad["advances"], source="NSE") if nse_ad else self._unavailable_field(),
                declines=self._field(nse_ad["declines"], source="NSE") if nse_ad else self._unavailable_field(),
                unchanged=self._field(nse_ad["unchanged"], source="NSE") if nse_ad else self._unavailable_field(),
                advance_decline_ratio=self._field(nse_ad["ratio"], source="NSE") if nse_ad and nse_ad.get("ratio") else self._unavailable_field(),
                # Options (from NSE option chain)
                call_oi=self._field(nse_oc["atm_ce_oi"], source="NSE") if nse_oc else self._unavailable_field(),
                put_oi=self._field(nse_oc["atm_pe_oi"], source="NSE") if nse_oc else self._unavailable_field(),
                pcr=self._field(nse_oc["pcr_oi"], source="NSE") if nse_oc and nse_oc.get("pcr_oi") else self._unavailable_field(),
                atm_iv=self._field(nse_oc["atm_iv"], source="NSE") if nse_oc and nse_oc.get("atm_iv") else self._unavailable_field(),
                max_pain=self._field(nse_oc["max_pain"], source="NSE") if nse_oc else self._unavailable_field(),
                # Related indices
                sensex_spot=self._field(sensex, source="YahooFinance") if sensex is not None else self._unavailable_field(),
                sensex_change_pct=self._field(sensex_change_pct, source="YahooFinance") if sensex_change_pct is not None else self._unavailable_field(),
                banknifty_spot=self._field(banknifty, source="YahooFinance") if banknifty is not None else self._unavailable_field(),
                banknifty_change_pct=self._field(banknifty_change_pct, source="YahooFinance") if banknifty_change_pct is not None else self._unavailable_field(),
                giftnifty_spot=self._field(giftnifty, source="YahooFinance") if giftnifty is not None else self._unavailable_field(),
                giftnifty_change_pct=self._field(giftnifty_change_pct, source="YahooFinance") if giftnifty_change_pct is not None else self._unavailable_field(),
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
                call_oi_change=self._unavailable_field(),
                put_oi_change=self._unavailable_field(),
                sector_performance=self._unavailable_field(),
                vwap=self._unavailable_field(),
                rsi=self._unavailable_field(),
                atr=self._unavailable_field(),
                relative_volume=self._unavailable_field(),
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

        except Exception as e:
            traceback.print_exc()
            return MarketSnapshot(
                snapshot_timestamp=self._ts(),
                timezone="Asia/Kolkata",
                source="WebSource",
                data_status="UNAVAILABLE",
                missing_fields=["ALL"],
            )
