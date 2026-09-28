"""Stage 17 — Technical verdict tests.

Pure-function tests on synthetic candles (no network); route tests use the
conftest client with the manager's get_candles monkeypatched.
"""

import pytest

from backend import main, verdict as V


@pytest.fixture()
def client():
    from tests.conftest import client as c
    return c


def _mk_candles(closes, spread=0.002):
    """Build candle dicts from a close series."""
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        h = max(o, c) * (1 + spread)
        low = min(o, c) * (1 - spread)
        out.append({"timestamp": str(i), "open": o, "high": h,
                    "low": low, "close": c, "volume": 1000})
    return out


# --------------------------------------------------------- primitives

def test_sma():
    assert V.sma([1, 2, 3, 4, 5], 5) == 3.0
    assert V.sma([1, 2], 5) is None


def test_ema_seeded_with_sma():
    series = V.ema_series([10, 10, 10, 10, 10], 3)
    assert series[2] == 10.0  # seed = SMA(3)
    assert all(v == 10.0 for v in series[2:])


def test_rsi_extremes():
    up = [100 + i for i in range(20)]          # monotonic gains → RSI 100
    assert V.rsi(up, 14) == 100.0
    down = [100 - i for i in range(20)]        # monotonic losses → RSI ~0
    assert V.rsi(down, 14) == pytest.approx(0.0, abs=0.01)


def test_macd_shape():
    up = [100 * (1.01 ** i) for i in range(60)]
    m = V.macd(up)
    assert m is not None
    assert m["macd"] > 0           # uptrend: fast EMA above slow
    assert m["histogram"] == pytest.approx(m["macd"] - m["signal"])


def test_atr_positive():
    candles = _mk_candles([100 + (i % 5) for i in range(30)])
    a = V.atr(candles, 14)
    assert a is not None and a > 0


# ------------------------------------------------------------ verdict

def test_verdict_bullish_uptrend():
    closes = [100 * (1.004 ** i) for i in range(80)]
    v = V.compute_verdict(_mk_candles(closes))
    assert v["verdict"] == "bullish"
    assert v["confidence"] > 0
    names = [c["name"] for c in v["checks"]]
    assert "Price vs SMA 50" in names and "MACD 12/26/9" in names
    pa = v["price_action"]
    assert pa["range_position_pct"] >= 80   # relentless uptrend rides the highs
    assert pa["atr_14"] > 0
    assert "not a prediction" in v["disclaimer"]


def test_verdict_bearish_downtrend():
    closes = [200 * (0.996 ** i) for i in range(80)]
    v = V.compute_verdict(_mk_candles(closes))
    assert v["verdict"] == "bearish"
    assert v["price_action"]["range_position_pct"] <= 20


def test_verdict_unclear_short_history():
    v = V.compute_verdict(_mk_candles([100 + i for i in range(10)]))
    assert v["verdict"] == "unclear"
    assert v["confidence"] == 0
    assert "need 30+" in v["checks"][0]["detail"]


def test_one_liner_mentions_verdict():
    closes = [100 * (1.004 ** i) for i in range(80)]
    v = V.compute_verdict(_mk_candles(closes))
    line = V.one_liner(v)
    assert line.startswith("Indicators lean")
    assert "20-day" in line or "ATR" in line


# -------------------------------------------------------------- route

def test_verdict_route(client, monkeypatch):
    main._VERDICT_CACHE.clear()
    closes = [100 * (1.003 ** i) for i in range(90)]
    candles = _mk_candles(closes)
    monkeypatch.setattr(main.market_data, "get_candles",
                        lambda *a, **k: (candles, "mock"))
    resp = client.get("/api/verdict/NIFTY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "simulated"   # mock source is labeled honestly
    assert data["verdict"] == "bullish"
    assert data["source"] == "mock"
    assert data["candles_used"] == 90


def test_verdict_route_unavailable(client, monkeypatch):
    main._VERDICT_CACHE.clear()

    def boom(*a, **k):
        raise RuntimeError("all providers down")
    monkeypatch.setattr(main.market_data, "get_candles", boom)
    resp = client.get("/api/verdict/NIFTY")
    assert resp.status_code == 200   # honest unavailable, not a 500
    data = resp.json()
    assert data["status"] == "unavailable"
    assert data["verdict"] == "unclear"
    assert "not a prediction" in data["disclaimer"]


def test_verdict_route_unknown_symbol(client):
    assert client.get("/api/verdict/NOTAREALSYMBOL").status_code == 404
