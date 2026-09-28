"""Paper-Trader — Macro Factors module (Stage 16).

Free, already-available data sources only:

- **Global / commodities / rates / FX** — yfinance daily closes for the
  tracked ticker list below. Current value = latest close; previous =
  the prior session's close; % change derived. These are DAILY closes,
  so during market hours the "current" value may be the previous
  session's — the timestamp is always shown so staleness is visible.
- **India VIX, NIFTY 50** — nselib `india_vix_data` / live index feed.
- **FII/DII activity** — nselib `participant_wise_trading_volume`
  (T+1 published; we walk back up to 10 calendar days for the latest
  trading day and report it honestly with its trade date).
- **Events** — nselib `event_calendar_for_equity` for today/next few days
  (corporate actions + scheduled items).

Nothing is ever fabricated: a source that fails or returns nothing yields
`{"status": "unavailable"}` rows, and timestamps are passed through so the
UI can show exactly how old each figure is. This module is descriptive
only — it computes no predictions and does not touch trading logic.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("papertrader.macro")

# ---------------------------------------------------------------------------
# Tracked tickers (yfinance). (symbol, display name, group, kind, signal note)
# kind: "level" (raw value) or "pct" (signal from % change)
# signal direction: +1 = rising is bullish for NIFTY opening, -1 = inverse
# ---------------------------------------------------------------------------

GLOBAL_TT = "+05:30"  # Indian tz suffix for display strings

YF_FACTORS: list[dict[str, Any]] = [
    # group: global | us_fut | asia | commodity | rate
    {"yf": "^GSPC",    "id": "sp500",      "name": "S&P 500",            "group": "global", "dir": +1},
    {"yf": "^DJI",     "id": "dow",        "name": "Dow Jones",          "group": "global", "dir": +1},
    {"yf": "^IXIC",    "id": "nasdaq",     "name": "Nasdaq",             "group": "global", "dir": +1},
    {"yf": "ES=F",     "id": "es_fut",     "name": "S&P 500 Fut (ES)",   "group": "us_fut", "dir": +1},
    {"yf": "NQ=F",     "id": "nq_fut",     "name": "Nasdaq Fut (NQ)",    "group": "us_fut", "dir": +1},
    {"yf": "YM=F",     "id": "ym_fut",     "name": "Dow Fut (YM)",       "group": "us_fut", "dir": +1},
    {"yf": "NKD=F",    "id": "nikkei_fut", "name": "Nikkei Fut (NKD)",   "group": "asia",   "dir": +1},
    {"yf": "^N225",    "id": "nikkei",     "name": "Nikkei 225",         "group": "asia",   "dir": +1},
    {"yf": "^HSI",     "id": "hangseng",   "name": "Hang Seng",          "group": "asia",   "dir": +1},
    {"yf": "^KS11",    "id": "kospi",      "name": "KOSPI",              "group": "asia",   "dir": +1},
    {"yf": "000001.SS","id": "shanghai",   "name": "Shanghai Composite", "group": "asia",   "dir": +1},
    {"yf": "BZ=F",     "id": "brent",      "name": "Brent Crude",        "group": "commodity", "dir": -1},
    {"yf": "GC=F",     "id": "gold",       "name": "Gold",               "group": "commodity", "dir": 0},
    {"yf": "^TNX",     "id": "us10y",      "name": "US 10Y Yield (%)",   "group": "rate",   "dir": -1},
    {"yf": "DX-Y.NYB", "id": "dxy",        "name": "Dollar Index (DXY)", "group": "rate",   "dir": -1},
    {"yf": "INR=X",    "id": "usdinr",     "name": "USD/INR",            "group": "rate",   "dir": -1},
]

_GROUP_LABELS = {
    "global": "Global Markets",
    "us_fut": "US Futures",
    "asia": "Asian Markets",
    "india": "India",
    "commodity": "Commodities",
    "rate": "Rates & Dollar",
}

# Staleness: quotes older than this are flagged stale in the UI.
STALE_SECONDS = 8 * 3600

# ---------------------------------------------------------------------------
# Cache — one shared snapshot refreshed by a daemon thread, like spot refresh.
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"data": None, "ts": 0.0}
CACHE_TTL = 300  # 5 minutes


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime(
        "%Y-%m-%d %H:%M:%S IST")


def _fmt_ts(ts: Any) -> Optional[str]:
    """Human-readable IST timestamp from a yfinance/nat timestamp."""
    try:
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts is None:
            return None
        naive = ts.replace(tzinfo=None)
        # yfinance returns local-to-exchange timestamps; we display the
        # exchange-local date/time (clearly the source's own clock).
        return naive.strftime("%Y-%m-%d %H:%M") + " (exchange tz)"
    except Exception:  # noqa: BLE001
        return str(ts) if ts else None


def _yf_daily_change(symbol: str) -> Optional[dict[str, Any]]:
    """Latest close vs previous close from yfinance daily history."""
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period="5d", interval="1d")
    if hist is None or len(hist) < 2:
        return None
    last = float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2])
    if prev == 0:
        return None
    return {
        "value": round(last, 4),
        "previous": round(prev, 4),
        "change_pct": round((last / prev - 1) * 100, 2),
        "timestamp": _fmt_ts(hist.index[-1]),
        "status": "live",
    }


def _yf_factors() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for spec in YF_FACTORS:
        row: dict[str, Any] = {
            "id": spec["id"], "name": spec["name"], "group": spec["group"],
            "source": "yfinance", "status": "unavailable",
            "value": None, "previous": None, "change_pct": None,
            "timestamp": None,
        }
        try:
            got = _yf_daily_change(spec["yf"])
            if got:
                row.update(got)
        except Exception as exc:  # noqa: BLE001 — network/parse failures are expected
            log.debug("macro yf %s failed: %s", spec["yf"], exc)
        out.append(row)
    return out


def _vix_row() -> dict[str, Any]:
    """India VIX from nselib (EOD series; latest row is today's close)."""
    row: dict[str, Any] = {
        "id": "india_vix", "name": "India VIX", "group": "india",
        "source": "nselib", "status": "unavailable",
        "value": None, "previous": None, "change_pct": None, "timestamp": None,
    }
    try:
        from nselib import capital_market as cm

        df = cm.india_vix_data(period="1W")
        if df is not None and len(df) >= 2:
            cols = {c.strip().upper(): c for c in df.columns}
            last = df.iloc[-1]
            prev = df.iloc[-2]
            row.update({
                "value": float(last[cols["CLOSE_INDEX_VAL"]]),
                "previous": float(prev[cols["CLOSE_INDEX_VAL"]]),
                "change_pct": float(last[cols["VIX_PERC_CHG"]]),
                "timestamp": str(last[cols["TIMESTAMP"]]),
                "status": "live",
                "note": "EOD series — updates after market close",
            })
    except Exception as exc:  # noqa: BLE001
        log.debug("macro india_vix failed: %s", exc)
    return row


def _fii_dii_row() -> dict[str, Any]:
    """Latest FII/DII participant activity (T+1) from nselib.

    Signal: net index-futures stance (long minus short contracts) for FII
    and DII — the standard proxy used for institutional activity. Reported
    with its actual trade date; weekly-holiday days are skipped honestly.
    """
    row: dict[str, Any] = {
        "id": "fii_dii", "name": "FII / DII Activity (F&O participants)",
        "group": "inst", "source": "nselib", "status": "unavailable",
        "value": None, "timestamp": None,
    }
    try:
        from nselib import derivatives as nd

        last_err: Exception | None = None
        for back in range(1, 11):  # walk back over weekends/holidays
            dt = date.today() - timedelta(days=back)
            try:
                df = nd.participant_wise_trading_volume(
                    trade_date=dt.strftime("%d-%m-%Y"))
            except Exception as exc:  # noqa: BLE001 — non-trading day or fetch issue
                last_err = exc
                continue
            if df is None or df.empty:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            # nselib dates before the file's start can still return STALE
            # cached rows — verify the returned row actually matches the
            # requested trade date via the report's date column if present.
            date_col = next((c for c in df.columns
                             if c.upper().startswith("DATE")), None)
            if date_col is not None:
                got = str(df.iloc[0][date_col])
                if dt.strftime("%d-%b-%Y").upper() not in got.upper():
                    last_err = RuntimeError(f"stale report rows for {dt}")
                    continue
            by_type = {str(r["Client Type"]).strip().upper(): r
                       for _, r in df.iterrows()}
            participants: dict[str, Any] = {}
            for ptype in ("FII", "DII"):
                r = by_type.get(ptype)
                if r is None:
                    participants[ptype.lower()] = None
                    continue
                col_fi = next((c for c in df.columns
                               if c.upper().startswith("FUTURE INDEX LONG")), None)
                col_fs = next((c for c in df.columns
                               if c.upper().startswith("FUTURE INDEX SHORT")), None)
                if col_fi is None or col_fs is None:
                    participants[ptype.lower()] = None
                    continue
                fi_l, fi_s = float(r[col_fi]), float(r[col_fs])
                opt_call_l = _num_or_none(df, r, "OPTION INDEX CALL LONG")
                opt_put_l = _num_or_none(df, r, "OPTION INDEX PUT LONG")
                participants[ptype.lower()] = {
                    "index_fut_long": int(fi_l),
                    "index_fut_short": int(fi_s),
                    "index_fut_net": int(fi_l - fi_s),
                    "index_call_long": opt_call_l,
                    "index_put_long": opt_put_l,
                }
            row.update({
                "value": participants,
                "trade_date": dt.strftime("%Y-%m-%d"),
                "timestamp": dt.strftime("%Y-%m-%d") + " (T+1 publication)",
                "status": "live",
                "note": "Published next trading day; net = index futures long − short contracts",
            })
            return row
        log.debug("macro fii_dii: no data in last 10 days (%s)", last_err)
    except Exception as exc:  # noqa: BLE001
        log.debug("macro fii_dii failed: %s", exc)
    return row


def _num_or_none(df, row, col_prefix: str) -> Optional[int]:
    col = next((c for c in df.columns
                if c.upper().startswith(col_prefix.upper())), None)
    if col is None:
        return None
    try:
        return int(float(row[col]))
    except Exception:  # noqa: BLE001
        return None


def _events_rows() -> dict[str, Any]:
    """Upcoming corporate/market events (next 7 days) from nselib."""
    out: dict[str, Any] = {
        "id": "events", "name": "Upcoming Events (next 7 days)",
        "group": "events", "source": "nselib", "status": "unavailable",
        "items": [],
    }
    try:
        from nselib import capital_market as cm

        d1 = date.today().strftime("%d-%m-%Y")
        d2 = (date.today() + timedelta(days=7)).strftime("%d-%m-%Y")
        df = cm.event_calendar_for_equity(from_date=d1, to_date=d2)
        if df is not None and not df.empty:
            items = []
            for _, r in df.head(50).iterrows():
                items.append({
                    "date": str(r.get("date", ""))[:10],
                    "symbol": str(r.get("symbol", "")).strip(),
                    "company": str(r.get("company", "")).strip(),
                    "purpose": str(r.get("purpose", "")).strip(),
                })
            items.sort(key=lambda x: x["date"])
            out.update({"items": items, "status": "live"})
    except Exception as exc:  # noqa: BLE001
        log.debug("macro events failed: %s", exc)
    return out


# ---------------------------------------------------------------------------
# Signal classification — descriptive reading, NOT a prediction.
# ---------------------------------------------------------------------------

def _signal(row: dict[str, Any]) -> str:
    """positive / negative / neutral / unclear / unavailable.

    'unclear' is used where a factor's meaning is genuinely ambiguous for
    an Indian opening (gold, and rows with tiny/meaningless moves).
    """
    if row.get("status") != "live" or row.get("change_pct") is None:
        return "unavailable"
    pct = float(row["change_pct"])
    spec = next((s for s in YF_FACTORS if s["id"] == row.get("id")), None)
    direction = spec["dir"] if spec else 0
    if row.get("id") == "gold" or direction == 0:
        return "unclear"
    if abs(pct) < 0.05:
        return "neutral"
    score = pct if direction > 0 else -pct
    return "positive" if score > 0 else "negative"


def _fii_dii_signal(fii_dii: dict[str, Any]) -> str:
    val = fii_dii.get("value")
    if fii_dii.get("status") != "live" or not isinstance(val, dict):
        return "unavailable"
    fii = val.get("fii") or {}
    net = fii.get("index_fut_net")
    if net is None:
        return "unclear"
    if net > 0:
        return "positive"
    if net < 0:
        return "negative"
    return "neutral"


def _summary(factors: list[dict[str, Any]], fii_dii: dict[str, Any],
             vix: dict[str, Any]) -> dict[str, Any]:
    """Count positive / negative / neutral factors for the pre-market view.

    This is a tally of how tracked global indicators closed, NOT a
    prediction of how the Indian market will open.
    """
    counts = {"positive": 0, "negative": 0, "neutral": 0,
              "unclear": 0, "unavailable": 0}
    contributors: dict[str, list[str]] = {k: [] for k in counts}
    for f in factors:
        s = _signal(f)
        counts[s] += 1
        contributors[s].append(f["name"])

    fd_sig = _fii_dii_signal(fii_dii)
    counts[fd_sig] += 1
    contributors[fd_sig].append("FII net index futures")

    # India VIX: rising VIX is usually read as risk-off (negative for
    # opening sentiment), falling VIX as calm/positive.
    vix_sig = "unavailable"
    if vix.get("status") == "live" and vix.get("change_pct") is not None:
        v = float(vix["change_pct"])
        vix_sig = "neutral" if abs(v) < 2.0 else ("negative" if v > 0 else "positive")
    counts[vix_sig] += 1
    if vix_sig != "unavailable":
        contributors[vix_sig].append("India VIX")

    return {"counts": counts, "contributors": contributors,
            "note": "Tally of how tracked factors closed — a reading, "
                    "not a prediction."}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_macro_snapshot(force: bool = False) -> dict[str, Any]:
    """Return the cached macro snapshot, refreshing if older than TTL."""
    with _CACHE_LOCK:
        age = time.time() - _CACHE["ts"]
        if not force and _CACHE["data"] is not None and age < CACHE_TTL:
            data = dict(_CACHE["data"])
            data["cache_age_seconds"] = int(age)
            return data

    fetched_at = _now_iso()
    factors = _yf_factors()
    vix = _vix_row()
    fii_dii = _fii_dii_row()
    events = _events_rows()

    all_rows = factors + [vix, fii_dii]
    summary = _summary([f for f in factors], fii_dii, vix)

    data = {
        "generated_at": fetched_at,
        "cache_ttl_seconds": CACHE_TTL,
        "groups": [_GROUP_LABELS[g] for g in
                   ("global", "us_fut", "asia", "india", "commodity", "rate")],
        "factors": factors,
        "vix": vix,
        "fii_dii": fii_dii,
        "events": events,
        "summary": summary,
        "disclaimer": "Macro data is descriptive only. It does not predict "
                      "market direction and nothing here is guaranteed.",
    }

    with _CACHE_LOCK:
        _CACHE["data"] = data
        _CACHE["ts"] = time.time()
    return data


def start_macro_refresher() -> None:
    """Daemon thread refreshing the snapshot every CACHE_TTL seconds."""

    def _loop() -> None:
        while True:
            try:
                get_macro_snapshot(force=True)
            except Exception as exc:  # noqa: BLE001 — never kill the loop
                log.warning("macro refresher error: %s", exc)
            time.sleep(CACHE_TTL)

    threading.Thread(target=_loop, daemon=True, name="macro-refresher").start()


# ---------------------------------------------------------------------------
# Pre-market journal (user expectations vs actual outcome)
# ---------------------------------------------------------------------------

JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL,
    expected        TEXT NOT NULL CHECK (expected IN ('UP', 'DOWN', 'FLAT')),
    confidence      INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    reasons         TEXT NOT NULL DEFAULT '',
    actual          TEXT,
    outcome         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT,
    UNIQUE (trade_date)
);
"""


def _classify_move(open_: float, prev_close: float) -> str:
    """Descriptive classification of the actual opening move."""
    if prev_close == 0:
        return "UNCLEAR"
    chg = (open_ / prev_close - 1) * 100
    if chg > 0.15:
        return "UP"
    if chg < -0.15:
        return "DOWN"
    return "FLAT"


def _outcome_for(expected: str, actual: str) -> str:
    if expected == actual:
        return "correct"
    if actual in ("UNCLEAR", ""):
        return "unclear"
    return "wrong"


def resolve_open_journal_entries(conn) -> int:
    """Fill `actual` for open journal rows once NSE publishes data.

    Uses nselib `index_data` for NIFTY 50: the first session after the
    journal's trade_date gives the previous close and the open. Returns
    the number of rows resolved. Descriptive only — no predictions.
    """
    rows = conn.execute(
        "SELECT id, trade_date, expected FROM macro_journal "
        "WHERE actual IS NULL ORDER BY trade_date LIMIT 10").fetchall()
    if not rows:
        return 0
    resolved = 0
    try:
        from nselib import capital_market as cm
    except Exception:  # noqa: BLE001
        return 0
    for r in rows:
        try:
            d = datetime.strptime(r["trade_date"], "%Y-%m-%d").date()
            # Fetch from the trade date itself; the next row in the series
            # is the next trading session (skips weekends/holidays). Actual
            # = that session's OPEN vs the trade date's CLOSE.
            d1 = d.strftime("%d-%m-%Y")
            d2 = (d + timedelta(days=5)).strftime("%d-%m-%Y")
            df = cm.index_data("NIFTY 50", from_date=d1, to_date=d2)
            if df is None or len(df) < 2:
                continue
            cols = {c.strip().upper(): c for c in df.columns}
            df = df.sort_values(cols["TIMESTAMP"])
            base = df.iloc[0]    # trade date (or next session if holiday)
            nxt = df.iloc[1]     # the session whose open we judge
            prev_close = float(base[cols["CLOSE_INDEX_VAL"]])
            open_ = float(nxt[cols["OPEN_INDEX_VAL"]])
            actual = _classify_move(open_, prev_close)
            outcome = _outcome_for(r["expected"], actual)
            conn.execute(
                "UPDATE macro_journal SET actual=?, outcome=?, resolved_at=? "
                "WHERE id=?",
                (actual, outcome, _now_iso(), r["id"]))
            resolved += 1
        except Exception as exc:  # noqa: BLE001 — keep resolving other rows
            log.debug("journal resolve %s failed: %s", r["trade_date"], exc)
    return resolved
