"""Granular field status enumeration.

Replaces the simple LIVE/UNAVAILABLE binary with production-grade status tracking.
Every field in MarketSnapshot carries one of these statuses.
"""

from enum import Enum


class FieldStatus(str, Enum):
    """Granular status for a single data field.

    Not just available/unavailable — captures WHY data is missing or stale.
    """

    # ─── Healthy ────────────────────────────────────────────────
    LIVE = "LIVE"                          # Fresh, within expected window
    DELAYED = "DELAYED"                    # Available but older than ideal
    HISTORICAL = "HISTORICAL"              # Daily/periodic data, not real-time

    # ─── Temporary issues ──────────────────────────────────────
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"  # API returned nil, retrying
    STALE = "STALE"                        # Was live, now expired
    RATE_LIMITED = "RATE_LIMITED"          # Too many requests
    TIMEOUT = "TIMEOUT"                    # Request timed out

    # ─── Structural issues ─────────────────────────────────────
    UNSUPPORTED = "UNSUPPORTED"            # Provider cannot supply this field
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"  # Bad token/expiry/contract
    API_ERROR = "API_ERROR"                # Non-retryable API error
    AUTH_ERROR = "AUTH_ERROR"              # Authentication failure

    # ─── Contextual ────────────────────────────────────────────
    MARKET_CLOSED = "MARKET_CLOSED"        # Market not open
    NOT_APPLICABLE = "NOT_APPLICABLE"      # Field doesn't apply in current context

    # ─── Final state ───────────────────────────────────────────
    UNAVAILABLE = "UNAVAILABLE"            # Genuinely no data source
    UNKNOWN = "UNKNOWN"                    # Initial/unknown state

    @property
    def is_healthy(self) -> bool:
        """Status indicates data is usable."""
        return self in (FieldStatus.LIVE, FieldStatus.HISTORICAL)

    @property
    def is_retryable(self) -> bool:
        """Status indicates a retry might succeed."""
        return self in (
            FieldStatus.TEMPORARILY_UNAVAILABLE,
            FieldStatus.RATE_LIMITED,
            FieldStatus.TIMEOUT,
            FieldStatus.STALE,
        )

    @property
    def is_terminal(self) -> bool:
        """Status indicates no further retries will help."""
        return self in (
            FieldStatus.UNSUPPORTED,
            FieldStatus.INVALID_INSTRUMENT,
            FieldStatus.API_ERROR,
            FieldStatus.AUTH_ERROR,
            FieldStatus.UNAVAILABLE,
            FieldStatus.NOT_APPLICABLE,
        )

    @property
    def display_label(self) -> str:
        """Human-readable status label."""
        labels = {
            FieldStatus.LIVE: "Live",
            FieldStatus.DELAYED: "Delayed",
            FieldStatus.HISTORICAL: "Historical",
            FieldStatus.TEMPORARILY_UNAVAILABLE: "Temporarily Unavailable",
            FieldStatus.STALE: "Stale",
            FieldStatus.RATE_LIMITED: "Rate Limited",
            FieldStatus.TIMEOUT: "Timeout",
            FieldStatus.UNSUPPORTED: "Unsupported",
            FieldStatus.INVALID_INSTRUMENT: "Invalid Instrument",
            FieldStatus.API_ERROR: "API Error",
            FieldStatus.AUTH_ERROR: "Auth Error",
            FieldStatus.MARKET_CLOSED: "Market Closed",
            FieldStatus.NOT_APPLICABLE: "N/A",
            FieldStatus.UNAVAILABLE: "Unavailable",
            FieldStatus.UNKNOWN: "Unknown",
        }
        return labels.get(self, self.value)

    @property
    def emoji(self) -> str:
        """Status indicator emoji."""
        emojis = {
            FieldStatus.LIVE: "🟢",
            FieldStatus.DELAYED: "🟡",
            FieldStatus.HISTORICAL: "🔵",
            FieldStatus.TEMPORARILY_UNAVAILABLE: "🟠",
            FieldStatus.STALE: "🟠",
            FieldStatus.RATE_LIMITED: "🔴",
            FieldStatus.TIMEOUT: "🔴",
            FieldStatus.UNSUPPORTED: "⚫",
            FieldStatus.INVALID_INSTRUMENT: "⚫",
            FieldStatus.API_ERROR: "🔴",
            FieldStatus.AUTH_ERROR: "🔴",
            FieldStatus.MARKET_CLOSED: "⚪",
            FieldStatus.NOT_APPLICABLE: "⚪",
            FieldStatus.UNAVAILABLE: "⚫",
            FieldStatus.UNKNOWN: "❓",
        }
        return emojis.get(self, "❓")


class DiagnosticReason(str, Enum):
    """Why a field has its current status.

    Attached to FieldMeta for full traceability.
    """

    # No issue
    OK = "ok"

    # SmartAPI issues
    SMARTAPI_RETURNED_NIL = "smartapi_returned_nil"
    SMARTAPI_EMPTY_RESPONSE = "smartapi_empty_response"
    SMARTAPI_RATE_LIMITED = "smartapi_rate_limited"
    SMARTAPI_AUTH_FAILED = "smartapi_auth_failed"
    SMARTAPI_TIMEOUT = "smartapi_timeout"
    SMARTAPI_INVALID_TOKEN = "smartapi_invalid_token"
    SMARTAPI_UNSUPPORTED_ENDPOINT = "smartapi_unsupported_endpoint"
    SMARTAPI_SERVER_ERROR = "smartapi_server_error"
    SMARTAPI_WEBSOCKET_DISCONNECTED = "smartapi_websocket_disconnected"

    # Instrument issues
    INVALID_EXCHANGE = "invalid_exchange"
    INVALID_CONTRACT = "invalid_contract"
    EXPIRED_CONTRACT = "expired_contract"
    INSTRUMENT_NOT_FOUND = "instrument_not_found"

    # Market context
    MARKET_CLOSED = "market_closed"
    MARKET_HOLIDAY = "market_holiday"
    PRE_MARKET = "pre_market"

    # Data issues
    DATA_STALE = "data_stale"
    NO_HISTORICAL_DATA = "no_historical_data"

    # Provider issues
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    EXTERNAL_API_ERROR = "external_api_error"

    # Retry state
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_EXHAUSTED = "retry_exhausted"

    @property
    def display_message(self) -> str:
        """Human-readable diagnostic message."""
        messages = {
            DiagnosticReason.OK: "Data received successfully",
            DiagnosticReason.SMARTAPI_RETURNED_NIL: "SmartAPI returned empty value",
            DiagnosticReason.SMARTAPI_EMPTY_RESPONSE: "SmartAPI returned empty response",
            DiagnosticReason.SMARTAPI_RATE_LIMITED: "SmartAPI rate limit exceeded",
            DiagnosticReason.SMARTAPI_AUTH_FAILED: "SmartAPI authentication failed",
            DiagnosticReason.SMARTAPI_TIMEOUT: "SmartAPI request timed out",
            DiagnosticReason.SMARTAPI_INVALID_TOKEN: "Invalid instrument token",
            DiagnosticReason.SMARTAPI_UNSUPPORTED_ENDPOINT: "SmartAPI endpoint does not support this field",
            DiagnosticReason.SMARTAPI_SERVER_ERROR: "SmartAPI server error",
            DiagnosticReason.SMARTAPI_WEBSOCKET_DISCONNECTED: "WebSocket connection lost",
            DiagnosticReason.INVALID_EXCHANGE: "Invalid exchange specified",
            DiagnosticReason.INVALID_CONTRACT: "Invalid contract parameters",
            DiagnosticReason.EXPIRED_CONTRACT: "Contract has expired",
            DiagnosticReason.INSTRUMENT_NOT_FOUND: "Instrument not found in search",
            DiagnosticReason.MARKET_CLOSED: "Market is currently closed",
            DiagnosticReason.MARKET_HOLIDAY: "Market holiday — no trading",
            DiagnosticReason.PRE_MARKET: "Pre-market session — limited data",
            DiagnosticReason.DATA_STALE: "Data has exceeded freshness window",
            DiagnosticReason.NO_HISTORICAL_DATA: "No historical data available",
            DiagnosticReason.PROVIDER_UNAVAILABLE: "Data provider is unavailable",
            DiagnosticReason.PROVIDER_TIMEOUT: "Data provider timed out",
            DiagnosticReason.EXTERNAL_API_ERROR: "External API returned error",
            DiagnosticReason.RETRY_SCHEDULED: "Retry scheduled",
            DiagnosticReason.RETRY_EXHAUSTED: "Maximum retries exhausted",
        }
        return messages.get(self, self.value)
