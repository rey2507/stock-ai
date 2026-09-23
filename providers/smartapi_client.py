"""Low-level SmartAPI client wrapper.

Wraps all SmartAPI methods with:
- Authentication with TOTP
- Rate limiting / exponential backoff
- Proper error handling
- Method catalog for OI, Greeks, market data, NSE stats

Does NOT produce MarketSnapshot — that's the provider layer's job.
"""

from __future__ import annotations
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from functools import wraps

import pyotp

log = logging.getLogger(__name__)

# Rate limiting: max requests per second
RATE_LIMIT_RPS = 5
_min_request_interval = 1.0 / RATE_LIMIT_RPS
_last_request_time = 0.0


def _rate_limit():
    """Simple token-bucket rate limiter."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _min_request_interval:
        time.sleep(_min_request_interval - elapsed)
    _last_request_time = time.monotonic()


def _retry_with_backoff(max_retries=3, base_delay=1.0):
    """Decorator for exponential backoff on failures."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    delay = base_delay * (2 ** attempt)
                    log.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_retries} "
                        f"failed: {e}. Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
            log.error(f"{func.__name__} failed after {max_retries} retries: {last_error}")
            raise last_error
        return wrapper
    return decorator


class SmartAPIClient:
    """Low-level wrapper around SmartAPI with all methods.

    Handles authentication, rate limiting, and error recovery.
    Does NOT cache — caching is handled by the InstrumentManager/DataCache.
    """

    def __init__(self):
        self._api_key = os.getenv("ANGEL_API_KEY", "")
        self._client_code = os.getenv("ANGEL_CLIENT_CODE", "")
        self._pin = os.getenv("ANGEL_PIN", "")
        self._totp_secret = os.getenv("ANGEL_TOTP_SECRET", "")
        self._api = None
        self._feed_token = None
        self._jwt_token = None
        self._session_active = False

    # ─── Authentication ──────────────────────────────────────────

    def connect(self):
        """Authenticate with SmartAPI using credentials + TOTP."""
        if self._session_active and self._api:
            return

        if not all([self._api_key, self._client_code, self._pin, self._totp_secret]):
            raise ConnectionError(
                "Angel Broking credentials not configured. "
                "Set ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET in .env"
            )

        from SmartApi.smartConnect import SmartConnect

        self._api = SmartConnect(api_key=self._api_key)
        totp = pyotp.TOTP(self._totp_secret).now()
        session = self._api.generateSession(self._client_code, self._pin, totp)
        if not session.get("status"):
            raise ConnectionError(f"SmartAPI login failed: {session}")

        # Store JWT token for WebSocket auth
        session_data = session.get("data", {})
        self._jwt_token = session_data.get("jwtToken", "")
        self._feed_token = session_data.get("feedToken", "") or self._api.getfeedToken()
        self._session_active = True
        log.info("SmartAPI session established")

    def disconnect(self):
        """Logout and cleanup."""
        if self._api and self._session_active:
            try:
                self._api.terminateSession(self._client_code)
            except Exception:
                pass
            self._session_active = False
            self._api = None
            log.info("SmartAPI session terminated")

    @property
    def feed_token(self) -> Optional[str]:
        return self._feed_token

    @property
    def is_connected(self) -> bool:
        return self._session_active and self._api is not None

    # ─── LTP / Market Data ──────────────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def ltp_data(self, exchange: str, symbol: str, token: str) -> Optional[dict]:
        """Get LTP data for a single instrument.

        Returns: {exchange, tradingsymbol, symboltoken, open, high, low, close, ltp}
        """
        _rate_limit()
        self.connect()
        try:
            data = self._api.ltpData(exchange, symbol, token)
            if data and data.get("data"):
                return data["data"]
        except Exception as e:
            log.warning(f"ltpData failed for {symbol}: {e}")
        return None

    @_retry_with_backoff(max_retries=3)
    def get_market_data(self, mode: str, exchange_tokens: dict[str, list[str]]) -> Optional[dict]:
        """Get full market data (quotes, OI, volume).

        Args:
            mode: "FULL" or "QUOTE"
            exchange_tokens: {"NFO": ["token1", "token2"], "NSE": ["token3"]}

        Returns: Dict mapping token -> data dict, or None on failure.

        Note: The SmartAPI response has top-level keys:
          - "fetched": list of successfully retrieved instruments
          - "unfetched": list of failed instruments
        """
        _rate_limit()
        self.connect()
        try:
            result = self._api.getMarketData(mode, exchange_tokens)
            if result and result.get("status") is not False:
                fetched = result.get("fetched") or []
                token_map = {}
                for item in fetched:
                    token = item.get("symbolToken") or item.get("symboltoken")
                    if token:
                        token_map[str(token)] = item
                if token_map:
                    return token_map
        except Exception as e:
            log.warning(f"getMarketData failed: {e}")
        return None

    # ─── Search / Instrument Discovery ──────────────────────────

    @_retry_with_backoff(max_retries=3)
    def search_scrip(self, exchange: str, query: str) -> list[dict]:
        """Search for instruments on an exchange.

        Returns: List of {symboltoken, exchange, tradingsymbol, ...}
        """
        _rate_limit()
        self.connect()
        try:
            result = self._api.searchScrip(exchange, query)
            if result and result.get("data"):
                return result["data"]
        except Exception as e:
            log.warning(f"searchScrip failed for {exchange}/{query}: {e}")
        return []

    # ─── Historical Candles ─────────────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def get_candles(
        self,
        exchange: str,
        token: str,
        interval: str = "FIVE_MINUTE",
        days: int = 5,
    ) -> list[list]:
        """Get OHLCV candle data.

        Returns: List of [timestamp, open, high, low, close, volume]
        """
        _rate_limit()
        self.connect()
        try:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
                "todate": to_date.strftime("%Y-%m-%d %H:%M"),
            }
            result = self._api.getCandleData(params)
            if result and result.get("data"):
                return result["data"]
        except Exception as e:
            log.warning(f"getCandleData failed for {token}: {e}")
        return []

    # ─── OI Data (NEW) ─────────────────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def get_oi_data(self, exchange: str, token: str, expiry: str) -> Optional[dict]:
        """Get historical OI for an F&O contract.

        Args:
            exchange: "NFO"
            token: Symbol token
            expiry: Expiry date string (e.g., "29SEP26")

        Returns: OI data dict, or None on failure.
        """
        _rate_limit()
        self.connect()
        try:
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "expirydate": expiry,
            }
            result = self._api.getOIData(params)
            if result and result.get("data"):
                return result["data"]
        except Exception as e:
            log.warning(f"getOIData failed for token {token}: {e}")
        return None

    # ─── Option Greeks / IV (NEW) ──────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def option_greek(self, name: str, expiry_date: str) -> Optional[dict]:
        """Get IV and Greeks for all strikes of an option series.

        Args:
            name: "NIFTY"
            expiry_date: Expiry date string (e.g., "29SEP26", "2026-09-29", "29-SEP-26")

        Returns: Dict with strike-wise IV/Greeks, or None on failure.
        """
        _rate_limit()
        self.connect()

        # Try multiple expiry formats that SmartAPI might accept
        formats_to_try = [expiry_date]

        # If input is DDMMMYY, also try DD-MMM-YY and YYYY-MM-DD
        if len(expiry_date) == 7 and expiry_date[2:5].isalpha():
            day = expiry_date[0:2]
            month = expiry_date[2:5]
            year = expiry_date[5:7]
            formats_to_try.extend([
                f"{day}-{month}-{year}",
                f"20{year}-{month}-{day}",
                f"{day}{month}{year}",
            ])

        last_error = None
        for fmt in formats_to_try:
            try:
                result = self._api.optionGreek({"name": name, "expirydate": fmt})
                if result and result.get("status") is not False and result.get("data"):
                    log.info(f"optionGreek succeeded with expiry format: {fmt}")
                    return result["data"]
                last_error = result.get("message", "Empty data")
            except Exception as e:
                last_error = str(e)
                log.debug(f"optionGreek attempt with '{fmt}' failed: {e}")

        log.warning(f"optionGreek failed for {name}/{expiry_date}: {last_error}")
        return None

    # ─── OI Buildup (NEW) ──────────────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def oi_buildup(self, exchange: str, token: str, expiry: str, resolution: str = "5") -> Optional[dict]:
        """Get OI buildup data for an F&O contract.

        Args:
            exchange: "NFO"
            token: Symbol token
            expiry: Expiry date string
            resolution: Time resolution in minutes ("1", "5", "15", "60")

        Returns: OI buildup data, or None on failure.
        """
        _rate_limit()
        self.connect()
        try:
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "expirydate": expiry,
                "resolution": resolution,
            }
            result = self._api.oIBuildup(params)
            if result and result.get("data"):
                return result["data"]
        except Exception as e:
            log.warning(f"oIBuildup failed for token {token}: {e}")
        return None

    # ─── NSE Intraday Market Stats (NEW) ───────────────────────

    @_retry_with_backoff(max_retries=3)
    def nse_intraday(self) -> Optional[dict]:
        """Get NSE intraday market stats (advances/declines, etc.).

        Returns: Dict with market stats, or None on failure.
        """
        _rate_limit()
        self.connect()
        try:
            result = self._api.nseIntraday()
            if result and result.get("data"):
                return result["data"]
        except Exception as e:
            log.warning(f"nseIntraday failed: {e}")
        return None

    # ─── Gainers/Losers (NEW) ──────────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def gainers_losers(self, data_type: str = "PercPriceGainers") -> Optional[dict]:
        """Get top gainers/losers.

        Args:
            data_type: "PercPriceGainers", "PercPriceLosers", etc.

        Returns: List of gainers/losers, or None on failure.
        """
        _rate_limit()
        self.connect()
        try:
            result = self._api.gainersLosers({"datatype": data_type})
            if result and result.get("data"):
                return result["data"]
        except Exception as e:
            log.warning(f"gainersLosers failed: {e}")
        return None

    # ─── Put-Call Ratio (existing) ─────────────────────────────

    @_retry_with_backoff(max_retries=3)
    def put_call_ratio(self) -> list[dict]:
        """Get PCR for all indices.

        Returns: List of {tradingSymbol, pcr}
        """
        _rate_limit()
        self.connect()
        try:
            result = self._api.putCallRatio()
            if result and result.get("data"):
                data = result["data"]
                if isinstance(data, list):
                    return data
        except Exception as e:
            log.warning(f"putCallRatio failed: {e}")
        return []

    def get_nifty_pcr(self) -> Optional[float]:
        """Get PCR specifically for NIFTY (excludes NIFTYNXT, FINNIFTY, MIDCPNIFTY)."""
        ratios = self.put_call_ratio()
        for item in ratios:
            name = item.get("tradingSymbol", "")
            if (
                name.startswith("NIFTY")
                and "NXT" not in name
                and "FIN" not in name
                and "MID" not in name
            ):
                return float(item.get("pcr", 0))
        return None

    # ─── WebSocket Feed ─────────────────────────────────────────

    def get_websocket_connection(self):
        """Get a SmartWebSocketV2 instance for live streaming.

        Returns: SmartWebSocketV2 instance (not connected)
        Caller must call sws.connect() and subscribe.
        """
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        if not all([self._jwt_token, self._api_key, self._client_code, self._feed_token]):
            log.error(
                "Cannot create WebSocket: missing credentials. "
                f"jwt_token={'set' if self._jwt_token else 'MISSING'}, "
                f"api_key={'set' if self._api_key else 'MISSING'}, "
                f"client_code={'set' if self._client_code else 'MISSING'}, "
                f"feed_token={'set' if self._feed_token else 'MISSING'}"
            )
            raise ConnectionError("SmartAPI WebSocket credentials incomplete")

        return SmartWebSocketV2(
            auth_token=self._jwt_token,
            api_key=self._api_key,
            client_code=self._client_code,
            feed_token=self._feed_token,
        )
