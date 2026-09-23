"""Market Hours Awareness.

Tracks NSE market sessions, holidays, and derived state.
All data freshness and status decisions are influenced by market hours.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

# NSE market hours (IST)
MARKET_OPEN = (9, 15)   # 09:15 AM
MARKET_CLOSE = (15, 30)  # 03:30 PM
PRE_MARKET_START = (9, 0)   # 09:00 AM

# NSE holidays 2026 (public holidays + trading holidays)
NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-10",  # Holi
    "2026-03-30",  # Eid
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-04-21",  # Ram Navami
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-08-27",  # Ganesh Chaturthi
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-21",  # Diwali Laxmi Pujan
    "2026-11-05",  # Prakash Gurpurab
    "2026-12-25",  # Christmas
}


class MarketSession:
    """Tracks current NSE market session state."""

    def __init__(self):
        self._now = None

    def _current_time(self) -> datetime:
        """Get current IST time. Overrideable for testing."""
        if self._now:
            return self._now
        return datetime.now(IST)

    def set_time(self, dt: datetime):
        """Override current time (for testing)."""
        self._now = dt

    def reset_time(self):
        """Reset to real clock."""
        self._now = None

    @property
    def today_str(self) -> str:
        """Today as YYYY-MM-DD string."""
        return self._current_time().strftime("%Y-%m-%d")

    @property
    def is_holiday(self) -> bool:
        """Check if today is an NSE trading holiday."""
        return self.today_str in NSE_HOLIDAYS_2026

    @property
    def state(self) -> str:
        """Current market session state.

        Returns one of:
        - CLOSED: Before 09:00 or after 15:30, or holiday
        - PRE_MARKET: 09:00 - 09:15
        - OPEN: 09:15 - 15:30
        - CLOSING: Last 15 minutes (15:15 - 15:30)
        """
        if self.is_holiday:
            return "CLOSED"

        now = self._current_time()
        h, m = now.hour, now.minute
        time_minutes = h * 60 + m

        open_minutes = MARKET_OPEN[0] * 60 + MARKET_OPEN[1]   # 555
        close_minutes = MARKET_CLOSE[0] * 60 + MARKET_CLOSE[1] # 930
        pre_start = PRE_MARKET_START[0] * 60 + PRE_MARKET_START[1]  # 540
        closing_start = close_minutes - 15  # 915

        if time_minutes < pre_start:
            return "CLOSED"
        elif time_minutes < open_minutes:
            return "PRE_MARKET"
        elif time_minutes >= close_minutes:
            return "CLOSED"
        elif time_minutes >= closing_start:
            return "CLOSING"
        else:
            return "OPEN"

    @property
    def is_open(self) -> bool:
        """Check if market is currently open (regular trading)."""
        return self.state in ("OPEN", "CLOSING")

    @property
    def is_trading_hours(self) -> bool:
        """Check if market is in any active session."""
        return self.state in ("PRE_MARKET", "OPEN", "CLOSING")

    @property
    def minutes_to_open(self) -> Optional[int]:
        """Minutes until market opens. None if already open or today is holiday."""
        if self.is_holiday:
            return None
        now = self._current_time()
        open_time = now.replace(
            hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0
        )
        if now >= open_time:
            return None
        return int((open_time - now).total_seconds() / 60)

    @property
    def minutes_to_close(self) -> Optional[int]:
        """Minutes until market closes. None if not open."""
        if not self.is_open:
            return None
        now = self._current_time()
        close_time = now.replace(
            hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0
        )
        delta = (close_time - now).total_seconds() / 60
        return max(0, int(delta))

    def adjust_freshness(self, base_seconds: float) -> float:
        """Adjust freshness window based on market state.

        During market hours: tighter windows (real-time matters)
        After hours: relaxed windows (data won't change)
        """
        state = self.state
        if state in ("OPEN", "CLOSING"):
            return base_seconds  # Full strictness
        elif state == "PRE_MARKET":
            return base_seconds * 2  # Relaxed
        else:  # CLOSED
            return base_seconds * 10  # Very relaxed

    def data_status_for_field(self, freshness_seconds: float, field_type: str = "price") -> str:
        """Determine data status accounting for market hours.

        Args:
            freshness_seconds: How old the data is
            field_type: Category of field ("price", "oi", "macro", etc.)

        Returns: LIVE | DELAYED | STALE | UNAVAILABLE
        """
        adjusted = self.adjust_freshness(
            self._freshness_thresholds(field_type)
        )

        if freshness_seconds is None:
            return "UNAVAILABLE"

        if freshness_seconds <= adjusted:
            return "LIVE"
        elif freshness_seconds <= adjusted * 5:
            return "DELAYED"
        else:
            return "STALE"

    def _freshness_thresholds(self, field_type: str) -> float:
        """Base freshness thresholds by field type."""
        thresholds = {
            "price": 30,
            "oi": 60,
            "pcr": 60,
            "iv": 120,
            "greeks": 120,
            "vwap": 30,
            "rsi": 30,
            "macro": 600,
            "fii_dii": 86400,
        }
        return thresholds.get(field_type, 300)

    def get_state_display(self) -> dict:
        """Get display info for the current market state."""
        state = self.state
        display_map = {
            "CLOSED": {"label": "Market Closed", "color": "gray", "icon": "⏸️"},
            "PRE_MARKET": {"label": "Pre-Market", "color": "orange", "icon": "🔔"},
            "OPEN": {"label": "Market Open", "color": "green", "icon": "📈"},
            "CLOSING": {"label": "Closing Session", "color": "yellow", "icon": "⏰"},
        }
        info = display_map.get(state, display_map["CLOSED"])

        now = self._current_time()
        info["time"] = now.strftime("%H:%M IST")
        info["date"] = now.strftime("%d %b %Y")

        if state == "CLOSED" and not self.is_holiday:
            mins = self.minutes_to_open
            if mins is not None:
                info["next_event"] = f"Opens in {mins} min"
        elif state == "OPEN":
            mins = self.minutes_to_close
            if mins is not None:
                info["next_event"] = f"Closes in {mins} min"
        elif self.is_holiday:
            info["next_event"] = "Holiday"

        return info


# Global market session instance
market = MarketSession()
