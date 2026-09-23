"""Live streaming for NIFTY spot via SmartAPI WebSocket."""

from __future__ import annotations

import threading
import time
import logging
from typing import Optional, Callable
from dataclasses import dataclass
from threading import Lock

from providers.ws_health import ws_monitor, StreamState

log = logging.getLogger(__name__)


@dataclass
class Tick:
    symbol: str
    ltp: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[str] = None


class StreamingManager:
    """Manages SmartAPI WebSocket streaming for NIFTY spot.

    Starts stream in background thread. Thread-safe access to latest tick.
    """

    def __init__(self, provider):
        self._provider = provider
        self._lock = Lock()
        self._latest_tick: Optional[Tick] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_tick: Optional[Callable[[Tick], None]] = None

    @property
    def latest_tick(self) -> Optional[Tick]:
        with self._lock:
            return self._latest_tick

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def on_tick(self, callback: Callable[[Tick], None]):
        self._on_tick = callback

    def start(self):
        if self.is_running:
            log.debug("Streaming already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()
        log.info("SmartAPI WebSocket stream started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        ws_monitor.on_disconnected(will_reconnect=False)

    def _stream_loop(self):
        """Background thread that maintains WebSocket connection."""
        # SmartAPI requires session stabilization time before WebSocket connection
        time.sleep(5)

        while self._running:
            try:
                ws_monitor.on_reconnect_attempt()
                self._run_websocket()
            except Exception as e:
                log.error(f"WebSocket error: {e}")
                ws_monitor.on_disconnected(will_reconnect=True)

            if self._running:
                time.sleep(min(2 ** ws_monitor.health.reconnect_count, 30))

    def _run_websocket(self):
        """Run a single WebSocket session."""
        try:
            ws = self._provider._client.get_websocket_connection()
        except ConnectionError as e:
            log.error(f"WebSocket init failed: {e}")
            ws_monitor.on_init_failed(str(e))
            time.sleep(5)
            return

        # Define callbacks
        def on_open(wsapp):
            ws_monitor.on_connected()
            log.info("WebSocket connection established")
            wsapp.subscribe(
                correlation_id="nifty_spot_ltp",
                mode=1,
                token_list=[{"exchangeType": 1, "tokens": ["99926000"]}]
            )

        def on_data(wsapp, message):
            t0 = time.monotonic()
            try:
                if not message or not isinstance(message, dict):
                    return

                ltp = message.get("last_traded_price")
                if ltp is None or ltp <= 0:
                    return

                tick = Tick(
                    symbol=message.get("token", "NIFTY"),
                    ltp=float(ltp) / 100.0,
                    open=float(message["open_price_of_the_day"]) / 100.0 if message.get("open_price_of_the_day") else None,
                    high=float(message["high_price_of_the_day"]) / 100.0 if message.get("high_price_of_the_day") else None,
                    low=float(message["low_price_of_the_day"]) / 100.0 if message.get("low_price_of_the_day") else None,
                    close=float(message["closed_price"]) / 100.0 if message.get("closed_price") else None,
                    volume=int(message["volume_trade_for_the_day"]) if message.get("volume_trade_for_the_day") else None,
                    timestamp=str(message.get("exchange_timestamp")) if message.get("exchange_timestamp") else None,
                )

                with self._lock:
                    self._latest_tick = tick

                latency = (time.monotonic() - t0) * 1000
                ws_monitor.on_message(latency_ms=latency)

                if self._on_tick:
                    self._on_tick(tick)

            except Exception as e:
                log.error(f"Error processing tick: {e}")
                ws_monitor.on_error(str(e))

        def on_error(wsapp, error):
            log.error(f"WebSocket error: {error}")
            ws_monitor.on_error(str(error))

        def on_close(wsapp, code, reason):
            log.warning(f"WebSocket closed: {code} - {reason}")
            ws_monitor.on_disconnected(will_reconnect=True)

        # Set callbacks
        ws.on_open = on_open
        ws.on_data = on_data
        ws.on_error = on_error
        ws.on_close = on_close

        # Connect and run
        try:
            ws.connect()
        except Exception as e:
            log.error(f"WebSocket connect failed: {e}")
            ws_monitor.on_init_failed(str(e))
            time.sleep(5)
            return

        # Keep alive until disconnect
        while self._running and ws_monitor.state == StreamState.CONNECTED:
            time.sleep(1)


# Global streaming manager (singleton)
_streaming_manager: Optional[StreamingManager] = None


def get_streaming_manager(provider=None) -> Optional[StreamingManager]:
    global _streaming_manager
    if _streaming_manager is None and provider is not None:
        _streaming_manager = StreamingManager(provider)
    return _streaming_manager
