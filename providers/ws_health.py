"""WebSocket Health Monitor for SmartAPI streaming.

Tracks connection state, message latency, reconnect count,
and provides health status for the UI sidebar.

States: CONNECTED | DELAYED | DISCONNECTED | RECONNECTING | FAILED
"""

from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Callable
from threading import Lock

log = logging.getLogger(__name__)


class StreamState(str, Enum):
    """WebSocket connection state."""
    CONNECTED = "CONNECTED"
    DELAYED = "DELAYED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"

    @property
    def emoji(self) -> str:
        return {
            StreamState.CONNECTED: "🟢",
            StreamState.DELAYED: "🟡",
            StreamState.DISCONNECTED: "🔴",
            StreamState.RECONNECTING: "🟠",
            StreamState.FAILED: "⚫",
        }.get(self, "❓")

    @property
    def label(self) -> str:
        return {
            StreamState.CONNECTED: "Connected",
            StreamState.DELAYED: "Stream Delayed",
            StreamState.DISCONNECTED: "Disconnected",
            StreamState.RECONNECTING: "Reconnecting...",
            StreamState.FAILED: "Connection Failed",
        }.get(self, "Unknown")


@dataclass
class StreamHealth:
    """Health status of a WebSocket stream."""
    state: StreamState = StreamState.DISCONNECTED
    last_tick_at: Optional[datetime] = None
    last_tick_age_sec: Optional[float] = None
    latency_ms: Optional[float] = None
    reconnect_count: int = 0
    connection_age_sec: Optional[float] = None
    total_messages: int = 0
    failed_messages: int = 0
    last_error: str = ""
    connected_at: Optional[datetime] = None

    @property
    def health_pct(self) -> float:
        """Message success rate."""
        if self.total_messages == 0:
            return 0.0
        return ((self.total_messages - self.failed_messages) / self.total_messages) * 100

    @property
    def is_healthy(self) -> bool:
        """Stream is considered healthy if connected and recent ticks."""
        return self.state == StreamState.CONNECTED and (
            self.last_tick_age_sec is not None and self.last_tick_age_sec < 5
        )

    @property
    def display_text(self) -> str:
        """Formatted text for sidebar display."""
        lines = [f"{self.state.emoji} {self.state.label}"]

        if self.latency_ms is not None:
            lines.append(f"Latency: {self.latency_ms:.0f}ms")

        if self.last_tick_age_sec is not None:
            if self.last_tick_age_sec < 1:
                lines.append("Last tick: <1 sec ago")
            else:
                lines.append(f"Last tick: {self.last_tick_age_sec:.1f} sec ago")

        if self.reconnect_count > 0:
            lines.append(f"Reconnects: {self.reconnect_count}")

        return " | ".join(lines)


class WebSocketHealthMonitor:
    """Monitors WebSocket connection health and provides status for UI.

    Thread-safe. Called from WebSocket callback and from UI.
    """

    def __init__(self, stale_threshold_sec: float = 10.0):
        """
        Args:
            stale_threshold_sec: Seconds without a tick before marking DELAYED.
        """
        self._stale_threshold = stale_threshold_sec
        self._lock = Lock()
        self._health = StreamHealth()
        self._connected_at: Optional[datetime] = None
        self._on_state_change: Optional[Callable] = None

    @property
    def health(self) -> StreamHealth:
        """Get current stream health."""
        with self._lock:
            self._update_derived()
            return self._health

    @property
    def state(self) -> StreamState:
        """Get current connection state."""
        with self._lock:
            self._update_derived()
            return self._health.state

    def set_state_change_callback(self, callback: Callable):
        """Set callback for state changes (for logging/alerting)."""
        self._on_state_change = callback

    def on_connected(self):
        """Called when WebSocket connects."""
        with self._lock:
            old_state = self._health.state
            self._health.state = StreamState.CONNECTED
            self._health.connected_at = datetime.now(timezone.utc)
            self._connected_at = self._health.connected_at
            self._health.last_error = ""
            if old_state != StreamState.CONNECTED:
                log.info(f"WebSocket connected (reconnect #{self._health.reconnect_count})")
                if self._on_state_change:
                    self._on_state_change(old_state, StreamState.CONNECTED)

    def on_message(self, latency_ms: Optional[float] = None):
        """Called when a valid message is received."""
        with self._lock:
            self._health.last_tick_at = datetime.now(timezone.utc)
            self._health.total_messages += 1
            if latency_ms is not None:
                # Exponential moving average for latency
                if self._health.latency_ms is None:
                    self._health.latency_ms = latency_ms
                else:
                    self._health.latency_ms = 0.8 * self._health.latency_ms + 0.2 * latency_ms
            if self._health.state == StreamState.DELAYED:
                self._health.state = StreamState.CONNECTED

    def on_error(self, error: str):
        """Called when a message error occurs."""
        with self._lock:
            self._health.failed_messages += 1
            self._health.last_error = error

    def on_disconnected(self, will_reconnect: bool = True):
        """Called when WebSocket disconnects."""
        with self._lock:
            old_state = self._health.state
            if will_reconnect:
                self._health.state = StreamState.RECONNECTING
                self._health.reconnect_count += 1
            else:
                self._health.state = StreamState.FAILED
            self._health.last_error = "Connection lost"
            if old_state != self._health.state:
                log.warning(f"WebSocket disconnected: {self._health.state.value}")
                if self._on_state_change:
                    self._on_state_change(old_state, self._health.state)

    def on_reconnect_attempt(self):
        """Called when a reconnection attempt starts."""
        with self._lock:
            self._health.state = StreamState.RECONNECTING

    def on_reconnect_failed(self):
        """Called when reconnection fails permanently."""
        with self._lock:
            self._health.state = StreamState.FAILED
            self._health.last_error = "Reconnection failed"

    def on_init_failed(self, error: str):
        """Called when WebSocket initialization fails."""
        with self._lock:
            self._health.state = StreamState.FAILED
            self._health.last_error = f"Init failed: {error}"
            log.error(f"WebSocket init failed: {error}")

    def reset(self):
        """Reset all health tracking."""
        with self._lock:
            self._health = StreamHealth()
            self._connected_at = None

    def _update_derived(self):
        """Update derived fields (called under lock)."""
        now = datetime.now(timezone.utc)

        # Update tick age
        if self._health.last_tick_at:
            self._health.last_tick_age_sec = (now - self._health.last_tick_at).total_seconds()

            # Check for staleness
            if self._health.state == StreamState.CONNECTED:
                if self._health.last_tick_age_sec > self._stale_threshold:
                    self._health.state = StreamState.DELAYED
                    log.warning(f"WebSocket stream stale: {self._health.last_tick_age_sec:.1f}s since last tick")
        else:
            self._health.last_tick_age_sec = None

        # Update connection age
        if self._connected_at:
            self._health.connection_age_sec = (now - self._connected_at).total_seconds()
        else:
            self._health.connection_age_sec = None


# Global WebSocket health monitor
ws_monitor = WebSocketHealthMonitor()
