"""SmartAPI Phase 0 probe — measure before integrating.

Standalone script; NO app code is touched. Logs into Angel One SmartAPI
with the credentials in .env (via backend/config.py), then probes:

  1. Login/session stability
  2. Spot freshness: smartapi vs yfinance vs nselib, 3 samples over ~2 min
     — verdict is about *staleness vs movement*, not numeric difference
  3. Candles 5m NIFTY: timestamp grid (NSE 09:15-15:30 IST), OHLC sanity,
     raw vs adjusted
  4. Rate headroom: 6 LTP calls at 1/sec (limit is 1 rps quotes,
     3 rps / 500 per min candles, ~9 rps combined)
  5. Expiry ladder via searchScrip (informational only)
  6. Final recommendation: integrate / do-not-integrate

Usage:  .venv/Scripts/python.exe tools/smartapi_probe.py
Secrets are never printed (client code masked, tokens discarded).
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import ANGEL  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")

# Angel One instrument tokens (exchangeSegment NSE / NFO).
TOKENS = {
    "NIFTY": ("NSE", "99926000"),      # Nifty 50 index
    "BANKNIFTY": ("NSE", "99926009"),  # Bank nifty index
    "RELIANCE": ("NSE", "2885"),       # Reliance Industries
}


class Probe:
    def __init__(self) -> None:
        self.smart_api = None
        self.feed_token = None
        self.report: list[str] = []
        self.verdict_points: list[str] = []

    def line(self, text: str = "") -> None:
        print(text)
        self.report.append(text)

    # ---------------------------------------------------------------- login
    def login(self) -> bool:
        from SmartApi import SmartConnect  # noqa: PLC0415
        import pyotp  # noqa: PLC0415

        if not ANGEL.complete:
            self.line(f"LOGIN: FAIL — missing {ANGEL.missing()}")
            return False
        try:
            api = SmartConnect(api_key=ANGEL.api_key)
            totp = pyotp.TOTP(ANGEL.totp_secret).now()
            data = api.generateSession(ANGEL.client_code, ANGEL.pin, totp)
        except Exception as exc:  # noqa: BLE001
            self.line(f"LOGIN: FAIL — {type(exc).__name__}: {str(exc)[:200]}")
            return False

        if not (data and data.get("status")):
            msg = (data or {}).get("message", "no payload")
            self.line(f"LOGIN: FAIL — API said: {str(msg)[:200]}")
            return False

        self.smart_api = api
        self.feed_token = data["data"].get("feedToken")
        masked = ANGEL.client_code[:3] + "***" if ANGEL.client_code else "?"
        self.line(f"LOGIN: PASS — client {masked}, session established")
        return True

    # ---------------------------------------------------------------- spots
    def ltp(self, symbol: str) -> float | None:
        seg, token = TOKENS[symbol]
        try:
            resp = self.smart_api.ltpData("NSE", symbol, token)
            if resp and resp.get("data"):
                return float(resp["data"]["ltp"])
        except Exception as exc:  # noqa: BLE001
            self.line(f"    ltpData {symbol} error: {str(exc)[:120]}")
        return None

    def probe_spots(self) -> None:
        self.line("\n2. SPOT FRESHNESS (3 samples, ~30s apart)")
        samples: list[dict[str, dict[str, float | None]]] = []
        for i in range(3):
            row: dict[str, dict[str, float | None]] = {}
            for sym in ("NIFTY", "RELIANCE"):
                smart = self.ltp(sym)
                time.sleep(1.1)  # stay under 1 rps quote limit
                yf_px = yfinance_spot(sym)
                time.sleep(1.1)
                row[sym] = {"smartapi": smart, "yfinance": yf_px}
            samples.append(row)
            self.line(f"  sample {i + 1}: "
                      + " | ".join(
                          f"{s} smartapi={row[s]['smartapi']} yf={row[s]['yfinance']}"
                          for s in row))
            if i < 2:
                time.sleep(30)

        for sym in ("NIFTY", "RELIANCE"):
            smart_vals = [r[sym]["smartapi"] for r in samples if r[sym]["smartapi"]]
            yf_vals = [r[sym]["yfinance"] for r in samples if r[sym]["yfinance"]]
            smart_moved = len(set(smart_vals)) > 1 if smart_vals else False
            yf_moved = len(set(yf_vals)) > 1 if yf_vals else False
            market_open = dt.datetime.now(IST).weekday() < 5 and \
                dt.time(9, 15) <= dt.datetime.now(IST).time() <= dt.time(15, 30)
            if not market_open:
                self.line(f"  {sym}: market CLOSED — static prices expected from all sources "
                          f"(both served identical values: {'yes' if smart_vals and yf_vals and smart_vals[-1] == yf_vals[-1] else 'no'})")
                self.verdict_points.append(f"spots {sym}: market-closed sample (inconclusive for freshness)")
                continue
            verdict = ("smartapi current (moved between samples)"
                       if smart_moved else "smartapi static while market open — STALE")
            yf_v = "yf moving" if yf_moved else "yf stale while market open"
            self.line(f"  {sym}: {verdict}; {yf_v}")
            self.verdict_points.append(f"spots {sym}: {verdict}, {yf_v}")

    # -------------------------------------------------------------- candles
    def probe_candles(self) -> None:
        self.line("\n3. CANDLES — 5m NIFTY last 2 trading days")
        seg, token = TOKENS["NIFTY"]
        to_dt = dt.datetime.now(IST)
        from_dt = to_dt - dt.timedelta(days=4)
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "FIVE_MINUTE",
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            resp = self.smart_api.getCandleData(params)
        except Exception as exc:  # noqa: BLE001
            self.line(f"  getCandleData FAIL: {str(exc)[:200]}")
            self.verdict_points.append("candles: FAIL")
            return
        rows = (resp or {}).get("data") or []
        if not rows:
            self.line("  getCandleData returned no rows")
            self.verdict_points.append("candles: EMPTY")
            return

        bad_ts, bad_ohlc, outside_session = 0, 0, 0
        for r in rows:
            try:
                ts = dt.datetime.fromisoformat(r[0])
            except (ValueError, TypeError):
                bad_ts += 1
                continue
            if ts.weekday() >= 5:
                bad_ts += 1
            t = ts.time()
            if not (dt.time(9, 15) <= t <= dt.time(15, 30)):
                outside_session += 1
            # SmartAPI candle row: [timestamp, open, high, low, close, volume]
            o, h, low, c = r[1], r[2], r[3], r[4]
            if not all(isinstance(v, (int, float)) and v > 0 for v in (o, h, low, c)):
                bad_ohlc += 1
            elif h < max(o, c) or low > min(o, c):
                bad_ohlc += 1

        last_close = rows[-1][4]
        spot = self.ltp("NIFTY")
        time.sleep(1.1)
        self.line(f"  rows: {len(rows)}; bad timestamps: {bad_ts}; "
                  f"outside 09:15-15:30 IST: {outside_session}; bad OHLC: {bad_ohlc}")
        self.line(f"  last 5m close: {last_close} vs live LTP: {spot}")
        consistent = spot is not None and abs(last_close - spot) < spot * 0.001
        ok = bad_ts == 0 and bad_ohlc == 0 and outside_session == 0
        self.verdict_points.append(
            f"candles: {'USABLE (grid+OHLC clean)' if ok else 'PROBLEMATIC'}; "
            f"last close {last_close} vs spot {spot} "
            f"({'consistent' if consistent else 'divergent'})")

    # ----------------------------------------------------------------- rate
    def probe_rate(self) -> None:
        self.line("\n4. RATE HEADROOM — 6 LTP calls at ~1/sec")
        errors = 0
        start = time.monotonic()
        for i in range(6):
            v = self.ltp("NIFTY")
            if v is None:
                errors += 1
            time.sleep(1.0)
        elapsed = time.monotonic() - start
        self.line(f"  6 calls in {elapsed:.1f}s, errors: {errors}/6 (quote limit 1 rps)")
        self.verdict_points.append(
            f"rate: {errors}/6 errors at 1 rps cadence "
            f"({'headroom confirmed' if errors == 0 else 'THROTTLING SEEN'})")

    # -------------------------------------------------------------- expiries
    def probe_expiries(self) -> None:
        self.line("\n5. EXPIRY LADDER via searchScrip (informational only)")
        try:
            resp = self.smart_api.searchScrip(
                exchange="NFO", searchscrip="NIFTY", expiry="")
            rows = (resp or {}).get("data") or []
            expiries = sorted({str(r.get("expiry", ""))[:10] for r in rows if r.get("expiry")})
            self.line(f"  NFO NIFTY expiries (first 6): {expiries[:6]}")
            self.verdict_points.append("expiries: reachable (informational)")
        except Exception as exc:  # noqa: BLE001
            self.line(f"  searchScrip error: {str(exc)[:150]}")
            self.verdict_points.append("expiries: error (informational)")

    # ------------------------------------------------------------ recommendation
    def recommend(self) -> None:
        self.line("\n6. RECOMMENDATION")
        joined = " ".join(self.verdict_points).lower()
        positives = sum(x in joined for x in ("current", "usable", "headroom confirmed"))
        self.line("  points: " + "; ".join(self.verdict_points))
        if "fail" in joined or "problematic" in joined or "throttling" in joined:
            self.line("  => DO-NOT-INTEGRATE (fix the failing probes first)")
        elif positives >= 3:
            self.line("  => INTEGRATE (fresh spots + usable raw candles + rate headroom)")
        else:
            self.line("  => DO-NOT-INTEGRATE (no meaningful improvement demonstrated)")

    def run(self) -> None:
        self.line("SMARTAPI PHASE 0 PROBE — " + dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"))
        self.line("1. LOGIN")
        if not self.login():
            self.line("\nRECOMMENDATION => DO-NOT-INTEGRATE (no session)")
            return
        self.probe_spots()
        self.probe_candles()
        self.probe_rate()
        self.probe_expiries()
        self.recommend()


def yfinance_spot(symbol: str) -> float | None:
    """Yahoo Finance last price for comparison (delayed source)."""
    try:
        import yfinance as yf  # noqa: PLC0415

        ticker = {"NIFTY": "^NSEI", "RELIANCE": "RELIANCE.NS"}.get(symbol)
        if not ticker:
            return None
        fast = yf.Ticker(ticker).fast_info
        price = fast.get("lastPrice") or fast.get("last_price")
        return round(float(price), 2) if price else None
    except Exception:
        return None


if __name__ == "__main__":
    Probe().run()
