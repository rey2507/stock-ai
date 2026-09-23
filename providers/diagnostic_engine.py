"""Nil Diagnostic Engine.

When SmartAPI returns None/nil for a field, this engine investigates WHY
before marking it unavailable. Follows the 7-check diagnostic workflow:

1. Market state check
2. Instrument validity check
3. Contract validity check
4. Endpoint capability check
5. API response status check
6. Temporary provider issue check
7. Genuine unavailability classification

Never immediately conclude "Data unavailable" — diagnose first.
Never automatically switch to external providers for SmartAPI-supported fields.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from models.field_status import FieldStatus, DiagnosticReason
from models.snapshot import FieldMeta
from models.source_policy import get_field_policy, is_smartapi_field

log = logging.getLogger(__name__)


@dataclass
class DiagnosticContext:
    """Context for diagnosing a nil SmartAPI response."""
    field_name: str
    exchange: str = ""
    symbol: str = ""
    token: str = ""
    instrument_type: str = ""   # INDEX | FUTIDX | OPTIDX
    expiry: str = ""
    strike: float = 0
    option_type: str = ""       # CE | PE
    endpoint: str = ""          # ltpData | getMarketData | getOIData | etc.
    api_response: Optional[dict] = None
    api_error: Optional[str] = None
    api_status_code: Optional[int] = None
    is_market_open: bool = True
    is_holiday: bool = False
    retry_count: int = 0
    max_retries: int = 3


@dataclass
class DiagnosticResult:
    """Result of the diagnostic investigation."""
    field_status: FieldStatus
    diagnostic_reason: DiagnosticReason
    message: str
    should_retry: bool = False
    retry_delay_sec: float = 0
    fallback_provider: Optional[str] = None  # Only for external fields

    def apply_to_field(self, fm: FieldMeta):
        """Apply diagnostic result to a FieldMeta instance."""
        fm.status = self.field_status.value
        fm.quality = "INVALID" if not self.field_status.is_healthy else "GOOD"
        fm.diagnostic_reason = self.diagnostic_reason.value
        fm.diagnostic_message = self.message
        if self.should_retry:
            fm.next_retry_at = datetime.now(timezone.utc)
        fm.mark_unavailable(self.diagnostic_reason.value, self.message)


class NilDiagnosticEngine:
    """Diagnoses why SmartAPI returned nil for a field.

    Follows the 7-check workflow. Never auto-replaces SmartAPI fields
    with external providers — only classifies the issue.
    """

    def diagnose(self, ctx: DiagnosticContext) -> DiagnosticResult:
        """Run the full 7-check diagnostic workflow.

        Returns a DiagnosticResult with status, reason, and whether to retry.
        """
        log.info(f"Diagnosing nil for {ctx.field_name} (endpoint={ctx.endpoint}, token={ctx.token})")

        # Check 1: Market state
        result = self._check_market_state(ctx)
        if result is not None:
            return result

        # Check 2: Instrument validity
        result = self._check_instrument_validity(ctx)
        if result is not None:
            return result

        # Check 3: Contract validity
        result = self._check_contract_validity(ctx)
        if result is not None:
            return result

        # Check 4: Endpoint capability
        result = self._check_endpoint_capability(ctx)
        if result is not None:
            return result

        # Check 5: API response status
        result = self._check_api_response(ctx)
        if result is not None:
            return result

        # Check 6: Temporary provider issue
        result = self._check_temporary_issue(ctx)
        if result is not None:
            return result

        # Check 7: Genuine unavailability
        return self._classify_genuine_unavailability(ctx)

    # ─── Check 1: Market State ──────────────────────────────────

    def _check_market_state(self, ctx: DiagnosticContext) -> Optional[DiagnosticResult]:
        """Check if market is open and whether data is expected."""
        if ctx.is_holiday:
            return DiagnosticResult(
                field_status=FieldStatus.MARKET_CLOSED,
                diagnostic_reason=DiagnosticReason.MARKET_HOLIDAY,
                message="Market holiday — no live data expected",
            )

        if not ctx.is_market_open:
            # For historical/daily fields, data may still be valid
            policy = get_field_policy(ctx.field_name)
            if policy and policy.frequency in ("daily", "weekly", "monthly", "quarterly"):
                return None  # Historical data expected even when market closed

            return DiagnosticResult(
                field_status=FieldStatus.MARKET_CLOSED,
                diagnostic_reason=DiagnosticReason.MARKET_CLOSED,
                message="Market closed — live data not expected",
            )

        return None  # Market is open, continue diagnosis

    # ─── Check 2: Instrument Validity ───────────────────────────

    def _check_instrument_validity(self, ctx: DiagnosticContext) -> Optional[DiagnosticResult]:
        """Verify the instrument token/exchange/symbol are valid."""
        if not ctx.token and ctx.endpoint in ("ltpData", "getMarketData", "getOIData"):
            return DiagnosticResult(
                field_status=FieldStatus.INVALID_INSTRUMENT,
                diagnostic_reason=DiagnosticReason.INSTRUMENT_NOT_FOUND,
                message=f"No token provided for {ctx.field_name}",
            )

        if not ctx.exchange and ctx.endpoint in ("ltpData", "getMarketData"):
            return DiagnosticResult(
                field_status=FieldStatus.INVALID_INSTRUMENT,
                diagnostic_reason=DiagnosticReason.INVALID_EXCHANGE,
                message=f"No exchange specified for {ctx.field_name}",
            )

        # Validate NFO-specific instruments
        if ctx.exchange == "NFO" and ctx.instrument_type in ("FUTIDX", "OPTIDX"):
            if not ctx.expiry:
                return DiagnosticResult(
                    field_status=FieldStatus.INVALID_INSTRUMENT,
                    diagnostic_reason=DiagnosticReason.INVALID_CONTRACT,
                    message=f"No expiry specified for F&O contract {ctx.field_name}",
                )

        return None  # Instrument looks valid

    # ─── Check 3: Contract Validity ─────────────────────────────

    def _check_contract_validity(self, ctx: DiagnosticContext) -> Optional[DiagnosticResult]:
        """Verify the contract hasn't expired and is tradable."""
        if ctx.expiry and ctx.instrument_type in ("FUTIDX", "OPTIDX"):
            # Parse expiry date
            from providers.instrument_manager import parse_expiry_date
            expiry_date = parse_expiry_date(ctx.expiry)
            if expiry_date:
                from datetime import datetime as dt
                now = dt.now(timezone.utc)
                if expiry_date.replace(tzinfo=timezone.utc) < now:
                    return DiagnosticResult(
                        field_status=FieldStatus.INVALID_INSTRUMENT,
                        diagnostic_reason=DiagnosticReason.EXPIRED_CONTRACT,
                        message=f"Contract expired: {ctx.expiry}",
                    )

        return None  # Contract looks valid

    # ─── Check 4: Endpoint Capability ───────────────────────────

    def _check_endpoint_capability(self, ctx: DiagnosticContext) -> Optional[DiagnosticResult]:
        """Check if the SmartAPI endpoint actually supports this field."""
        # getOIData only works for F&O contracts
        if ctx.endpoint == "getOIData" and ctx.instrument_type == "INDEX":
            return DiagnosticResult(
                field_status=FieldStatus.UNSUPPORTED,
                diagnostic_reason=DiagnosticReason.SMARTAPI_UNSUPPORTED_ENDPOINT,
                message=f"getOIData does not support INDEX instruments for {ctx.field_name}",
            )

        # optionGreek only works for option series
        if ctx.endpoint == "optionGreek" and ctx.instrument_type not in ("OPTIDX", "OPTSTK"):
            return DiagnosticResult(
                field_status=FieldStatus.UNSUPPORTED,
                diagnostic_reason=DiagnosticReason.SMARTAPI_UNSUPPORTED_ENDPOINT,
                message=f"optionGreek requires OPTIDX/OPTSTK for {ctx.field_name}",
            )

        # nseIntraday has specific requirements
        if ctx.endpoint == "nseIntraday" and ctx.field_name not in ("advances", "declines", "unchanged", "advance_decline_ratio"):
            return DiagnosticResult(
                field_status=FieldStatus.UNSUPPORTED,
                diagnostic_reason=DiagnosticReason.SMARTAPI_UNSUPPORTED_ENDPOINT,
                message=f"nseIntraday does not support field {ctx.field_name}",
            )

        return None  # Endpoint should support this field

    # ─── Check 5: API Response Status ───────────────────────────

    def _check_api_response(self, ctx: DiagnosticContext) -> Optional[DiagnosticResult]:
        """Inspect the actual API response for error indicators."""
        if ctx.api_error:
            error_lower = ctx.api_error.lower()

            # Authentication errors
            if "session" in error_lower or "auth" in error_lower or "login" in error_lower:
                return DiagnosticResult(
                    field_status=FieldStatus.AUTH_ERROR,
                    diagnostic_reason=DiagnosticReason.SMARTAPI_AUTH_FAILED,
                    message=f"SmartAPI authentication failed: {ctx.api_error}",
                )

            # Rate limiting
            if "rate" in error_lower or "limit" in error_lower or "throttle" in error_lower:
                return DiagnosticResult(
                    field_status=FieldStatus.RATE_LIMITED,
                    diagnostic_reason=DiagnosticReason.SMARTAPI_RATE_LIMITED,
                    message=f"SmartAPI rate limit: {ctx.api_error}",
                    should_retry=True,
                    retry_delay_sec=5.0,
                )

            # Timeout
            if "timeout" in error_lower or "timed out" in error_lower:
                if ctx.retry_count < ctx.max_retries:
                    return DiagnosticResult(
                        field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                        diagnostic_reason=DiagnosticReason.SMARTAPI_TIMEOUT,
                        message=f"SmartAPI timeout (attempt {ctx.retry_count + 1}/{ctx.max_retries})",
                        should_retry=True,
                        retry_delay_sec=2.0 * (2 ** ctx.retry_count),  # Exponential backoff
                    )
                return DiagnosticResult(
                    field_status=FieldStatus.TIMEOUT,
                    diagnostic_reason=DiagnosticReason.RETRY_EXHAUSTED,
                    message=f"SmartAPI timeout after {ctx.max_retries} retries",
                )

            # Server error
            if "server" in error_lower or "500" in error_lower or "502" in error_lower or "503" in error_lower:
                if ctx.retry_count < ctx.max_retries:
                    return DiagnosticResult(
                        field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                        diagnostic_reason=DiagnosticReason.SMARTAPI_SERVER_ERROR,
                        message=f"SmartAPI server error (attempt {ctx.retry_count + 1}/{ctx.max_retries})",
                        should_retry=True,
                        retry_delay_sec=3.0 * (2 ** ctx.retry_count),
                    )
                return DiagnosticResult(
                    field_status=FieldStatus.API_ERROR,
                    diagnostic_reason=DiagnosticReason.RETRY_EXHAUSTED,
                    message=f"SmartAPI server error after {ctx.max_retries} retries",
                )

        # Check HTTP status code
        if ctx.api_status_code:
            if ctx.api_status_code == 401:
                return DiagnosticResult(
                    field_status=FieldStatus.AUTH_ERROR,
                    diagnostic_reason=DiagnosticReason.SMARTAPI_AUTH_FAILED,
                    message=f"HTTP 401 Unauthorized",
                )
            if ctx.api_status_code == 429:
                return DiagnosticResult(
                    field_status=FieldStatus.RATE_LIMITED,
                    diagnostic_reason=DiagnosticReason.SMARTAPI_RATE_LIMITED,
                    message=f"HTTP 429 Too Many Requests",
                    should_retry=True,
                    retry_delay_sec=10.0,
                )
            if ctx.api_status_code >= 500:
                if ctx.retry_count < ctx.max_retries:
                    return DiagnosticResult(
                        field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                        diagnostic_reason=DiagnosticReason.SMARTAPI_SERVER_ERROR,
                        message=f"HTTP {ctx.api_status_code} (attempt {ctx.retry_count + 1}/{ctx.max_retries})",
                        should_retry=True,
                        retry_delay_sec=3.0 * (2 ** ctx.retry_count),
                    )

        # Check for empty/nil response
        if ctx.api_response is not None:
            data = ctx.api_response.get("data")
            status = ctx.api_response.get("status")
            message = ctx.api_response.get("message", "")

            if status is False or status == "false":
                # API explicitly returned failure
                if "no data" in str(message).lower() or data is None:
                    if ctx.retry_count < ctx.max_retries:
                        return DiagnosticResult(
                            field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                            diagnostic_reason=DiagnosticReason.SMARTAPI_RETURNED_NIL,
                            message=f"SmartAPI returned nil (attempt {ctx.retry_count + 1}/{ctx.max_retries}): {message}",
                            should_retry=True,
                            retry_delay_sec=2.0 * (2 ** ctx.retry_count),
                        )
                    return DiagnosticResult(
                        field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                        diagnostic_reason=DiagnosticReason.RETRY_EXHAUSTED,
                        message=f"SmartAPI nil after {ctx.max_retries} retries: {message}",
                    )

            if isinstance(data, (list, dict)) and not data:
                # Empty data
                if ctx.retry_count < ctx.max_retries:
                    return DiagnosticResult(
                        field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                        diagnostic_reason=DiagnosticReason.SMARTAPI_EMPTY_RESPONSE,
                        message=f"SmartAPI empty response (attempt {ctx.retry_count + 1}/{ctx.max_retries})",
                        should_retry=True,
                        retry_delay_sec=2.0 * (2 ** ctx.retry_count),
                    )

        return None  # No response-level issues detected

    # ─── Check 6: Temporary Provider Issue ──────────────────────

    def _check_temporary_issue(self, ctx: DiagnosticContext) -> Optional[DiagnosticResult]:
        """Check for known temporary issues."""
        # WebSocket disconnect
        if ctx.endpoint == "websocket" and ctx.api_error:
            return DiagnosticResult(
                field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                diagnostic_reason=DiagnosticReason.SMARTAPI_WEBSOCKET_DISCONNECTED,
                message=f"WebSocket disconnected: {ctx.api_error}",
                should_retry=True,
                retry_delay_sec=1.0,
            )

        return None

    # ─── Check 7: Genuine Unavailability ────────────────────────

    def _classify_genuine_unavailability(self, ctx: DiagnosticContext) -> DiagnosticResult:
        """After all checks, classify as genuine unavailability or unknown."""
        # Check if the field is from an external provider
        policy = get_field_policy(ctx.field_name)
        if policy and not policy.smartapi_supported:
            return DiagnosticResult(
                field_status=FieldStatus.UNSUPPORTED,
                diagnostic_reason=DiagnosticReason.SMARTAPI_UNSUPPORTED_ENDPOINT,
                message=f"SmartAPI does not provide {ctx.field_name} — use external provider",
                fallback_provider=policy.primary,
            )

        # SmartAPI field with nil — final attempt with retry
        if ctx.retry_count < ctx.max_retries:
            return DiagnosticResult(
                field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
                diagnostic_reason=DiagnosticReason.SMARTAPI_RETURNED_NIL,
                message=f"SmartAPI nil (attempt {ctx.retry_count + 1}/{ctx.max_retries})",
                should_retry=True,
                retry_delay_sec=2.0 * (2 ** ctx.retry_count),
            )

        # Exhausted all retries
        return DiagnosticResult(
            field_status=FieldStatus.TEMPORARILY_UNAVAILABLE,
            diagnostic_reason=DiagnosticReason.RETRY_EXHAUSTED,
            message=f"SmartAPI nil after {ctx.max_retries} retries — data temporarily unavailable",
        )
