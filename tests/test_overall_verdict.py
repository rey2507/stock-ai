"""Stage 18 — Overall NIFTY 50 verdict combiner tests.

No network: technical and macro pieces are canned dicts shaped exactly like
the existing /api/verdict and /api/macro responses.
"""

import pytest

from backend import main, overall_verdict as OV


@pytest.fixture()
def client():
    from tests.conftest import client as c
    return c


def _tech(verdict="bullish", conf=60, checks=None):
    return {
        "status": "live", "source": "nselib", "verdict": verdict,
        "confidence": conf, "candles_used": 64,
        "checks": checks or [
            {"name": "Price vs SMA 50", "signal": "bullish", "detail": "above"},
            {"name": "MACD", "signal": "bearish", "detail": "below"},
            {"name": "RSI 14", "signal": "bullish", "detail": "55"},
        ],
        "price_action": {}, "one_liner": "line", "disclaimer": "d",
    }


def _macro(pos=5, neg=3, neutral=1, unclear=1, unavailable=0):
    return {
        "factors": [{"status": "live"}] * (pos + neg),
        "summary": {
            "counts": {"positive": pos, "negative": neg, "neutral": neutral,
                       "unclear": unclear, "unavailable": unavailable},
            "contributors": {"positive": ["S&P 500"], "negative": ["Brent"]},
        },
    }


def test_combine_bullish_majority():
    r = OV.combine(_tech(), _macro(pos=5, neg=3))
    # tech: 2 bull - 1 bear = +1 (3 decided); macro: +2 (8 decided)
    assert r["technical"]["score"] == 1
    assert r["macro"]["score"] == 2
    assert r["verdict"] == "bullish"
    assert r["confidence"] > 0
    assert "technicals bullish" in r["one_liner"]
    assert "not a prediction" in r["disclaimer"].lower()


def test_combine_bearish_majority():
    checks = [{"name": "x", "signal": "bearish", "detail": "d"}] * 3
    r = OV.combine(_tech(verdict="bearish", checks=checks), _macro(pos=1, neg=6))
    assert r["verdict"] == "bearish"


def test_combine_neutral_when_balanced():
    checks = [{"name": "x", "signal": "bullish", "detail": "d"},
              {"name": "y", "signal": "bearish", "detail": "d"}]
    r = OV.combine(_tech(checks=checks), _macro(pos=3, neg=3))
    assert r["verdict"] == "neutral"


def test_combine_technical_unavailable():
    tech = _tech()
    tech["status"] = "unavailable"
    tech["checks"] = []
    r = OV.combine(tech, _macro(pos=4, neg=2))
    assert r["technical"]["verdict"] == "unclear"
    assert r["macro"]["verdict"] == "bullish"
    assert r["verdict"] == "bullish"


def test_combine_all_unclear():
    tech = _tech()
    tech["status"] = "unavailable"
    tech["checks"] = []
    macro = _macro(pos=0, neg=0)
    r = OV.combine(tech, macro)
    assert r["verdict"] == "unclear"
    assert r["confidence"] == 0
    assert "no decided signals" in r["one_liner"]


def test_route(client, monkeypatch):
    main._OVERALL_CACHE.clear()
    monkeypatch.setattr(main, "get_verdict", lambda symbol: _tech())
    monkeypatch.setattr(main.macro_mod, "get_macro_snapshot",
                        lambda force=False: _macro())
    resp = client.get("/api/overall-verdict/NIFTY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NIFTY"
    assert data["verdict"] == "bullish"
    assert data["status"] == "live"


def test_route_honest_when_both_sides_down(client, monkeypatch):
    main._OVERALL_CACHE.clear()
    down = _tech()
    down.update({"status": "unavailable", "verdict": "unclear",
                 "confidence": 0, "checks": []})
    monkeypatch.setattr(main, "get_verdict", lambda symbol: down)
    monkeypatch.setattr(main.macro_mod, "get_macro_snapshot",
                        lambda force=False: _macro(pos=0, neg=0))
    data = client.get("/api/overall-verdict/NIFTY").json()
    assert data["verdict"] == "unclear"
    assert data["status"] == "unavailable"
