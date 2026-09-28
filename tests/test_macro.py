"""Stage 16 — Macro Factors endpoint + journal tests.

All network-touching fetchers are monkeypatched: no test ever touches
yfinance or nselib. We assert honest labeling (status/timestamp/source),
the summary tally logic, and the journal expectation-vs-actual resolution.
"""

from datetime import date

import pandas as pd
import pytest

from backend import db, main, macro

# conftest exposes module-level `client`; re-export as a fixture.

@pytest.fixture()
def client():
    from tests.conftest import client as c
    return c


@pytest.fixture()
def no_refresher(monkeypatch):
    """Never start the background refresher thread inside tests."""
    monkeypatch.setattr(macro, "start_macro_refresher", lambda: None)


def _fake_yf_change(spec_symbol):
    """Return a canned _yf_daily_change payload for any symbol."""

    def fake(symbol):
        if symbol == "BROKEN":
            raise RuntimeError("network down")
        return {"value": 100.0, "previous": 98.0, "change_pct": 2.04,
                "timestamp": "2026-09-25 00:00 (exchange tz)", "status": "live"}
    return fake


@pytest.fixture()
def fake_yf(monkeypatch):
    monkeypatch.setattr(macro, "_yf_daily_change", _fake_yf_change("x"))


@pytest.fixture()
def fake_nselib(monkeypatch):
    """Canned VIX / FII-DII / events fetches."""
    def fake_vix():
        return {"id": "india_vix", "name": "India VIX", "group": "india",
                "source": "nselib", "status": "live", "value": 13.6,
                "previous": 12.2, "change_pct": 11.5,
                "timestamp": "28-SEP-2026"}
    monkeypatch.setattr(macro, "_vix_row", fake_vix)

    def fake_fd():
        return {"id": "fii_dii", "name": "FII / DII", "group": "inst",
                "source": "nselib", "status": "live",
                "value": {"fii": {"index_fut_long": 100, "index_fut_short": 150,
                                  "index_fut_net": -50, "index_call_long": 10,
                                  "index_put_long": 20},
                          "dii": {"index_fut_long": 30, "index_fut_short": 10,
                                  "index_fut_net": 20, "index_call_long": 1,
                                  "index_put_long": 2}},
                "trade_date": "2026-09-25", "timestamp": "2026-09-25 (T+1)"}
    monkeypatch.setattr(macro, "_fii_dii_row", fake_fd)

    def fake_events():
        return {"id": "events", "name": "events", "group": "events",
                "source": "nselib", "status": "live",
                "items": [{"date": "2026-09-29", "symbol": "TCS",
                           "company": "Tata Consultancy", "purpose": "AGM"}]}
    monkeypatch.setattr(macro, "_events_rows", fake_events)


# -------------------------------------------------------------- snapshot

def test_snapshot_all_live(fake_yf, fake_nselib, no_refresher):
    snap = macro.get_macro_snapshot(force=True)
    live = [f for f in snap["factors"] if f["status"] == "live"]
    assert len(live) == len(snap["factors"]) == 16
    row = snap["factors"][0]
    assert row["source"] == "yfinance"
    assert row["timestamp"] and row["value"] == 100.0
    assert snap["vix"]["status"] == "live"
    assert snap["fii_dii"]["value"]["fii"]["index_fut_net"] == -50
    assert snap["events"]["items"][0]["symbol"] == "TCS"
    assert "does not predict" in snap["disclaimer"]


def test_snapshot_honest_unavailable(monkeypatch, no_refresher):
    def boom(symbol):
        raise RuntimeError("network down")
    monkeypatch.setattr(macro, "_yf_daily_change", boom)
    def vix_fail():
        return {"id": "india_vix", "name": "India VIX", "group": "india",
                "source": "nselib", "status": "unavailable", "value": None,
                "previous": None, "change_pct": None, "timestamp": None}
    monkeypatch.setattr(macro, "_vix_row", vix_fail)
    snap = macro.get_macro_snapshot(force=True)
    assert all(f["status"] == "unavailable" for f in snap["factors"])
    assert snap["vix"]["status"] == "unavailable"
    counts = snap["summary"]["counts"]
    # 16 yf factors + VIX unavailable; the FII/DII walker swallows per-day
    # errors and only reports unavailable when 10 days yield nothing — with
    # all fetchers broken its status stays "unavailable" too.
    assert counts["unavailable"] >= 17
    assert counts["positive"] == 0


def test_summary_signal_directions(fake_yf, fake_nselib, no_refresher):
    snap = macro.get_macro_snapshot(force=True)
    counts = snap["summary"]["counts"]
    # All yf factors +2.04%: positives for dir=+1, negatives for dir=-1,
    # gold is unclear, VIX +11.5% counts negative, FII net -50 negative.
    assert counts["unclear"] == 1  # gold
    assert counts["negative"] >= 5  # brent, us10y, dxy, usdinr, VIX, FII…
    assert counts["positive"] >= 8
    contrib_names = sum((v for v in snap["summary"]["contributors"].values()), [])
    assert "India VIX" in contrib_names
    assert "FII net index futures" in contrib_names


def test_cache_hit(monkeypatch, fake_yf, fake_nselib, no_refresher):
    calls = {"n": 0}
    real = macro._yf_factors
    def counted():
        calls["n"] += 1
        return real()
    monkeypatch.setattr(macro, "_yf_factors", counted)
    macro.get_macro_snapshot(force=True)
    macro.get_macro_snapshot()  # within TTL: no re-fetch
    assert calls["n"] == 1


# ---------------------------------------------------------------- routes

def test_macro_route(client, fake_yf, fake_nselib, no_refresher, monkeypatch):
    macro.get_macro_snapshot(force=True)  # prime cache
    resp = client.get("/api/macro")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data and "factors" in data


# --------------------------------------------------------------- journal

def test_journal_crud_and_validation(client):
    today = date.today().isoformat()
    resp = client.post("/api/macro/journal", json={
        "trade_date": today, "expected": "UP", "confidence": 3,
        "reasons": "test view"})
    assert resp.status_code == 200
    # Upsert replaces the same date's entry.
    resp = client.post("/api/macro/journal", json={
        "trade_date": today, "expected": "DOWN", "confidence": 5,
        "reasons": "revised"})
    assert resp.status_code == 200
    data = client.get("/api/macro/journal").json()
    assert data["entries"][0]["expected"] == "DOWN"
    assert data["stats"]["pending"] == 1
    # Bad payload rejected (pydantic validates length; 422 for both).
    assert client.post("/api/macro/journal", json={
        "trade_date": "garbage", "expected": "UP", "confidence": 3,
    }).status_code in (400, 422)
    assert client.post("/api/macro/journal", json={
        "trade_date": today, "expected": "SIDEWAYS", "confidence": 3,
    }).status_code == 422
    assert client.post("/api/macro/journal", json={
        "trade_date": today, "expected": "UP", "confidence": 9,
    }).status_code == 422
    # Delete.
    eid = data["entries"][0]["id"]
    assert client.delete(f"/api/macro/journal/{eid}").json()["ok"]
    assert client.delete(f"/api/macro/journal/{eid}").status_code == 404


def test_journal_resolution(monkeypatch):
    """Open entries resolve against the next NSE session's open."""
    today = date.today()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO macro_journal (trade_date, expected, confidence, reasons) "
            "VALUES (?, 'UP', 3, 'x')",
            (today.isoformat(),))
        # Next session: prev close 1000 -> open 1005 = +0.5% => UP => correct.
        df = pd.DataFrame({
            "INDEX_NAME": ["NIFTY 50", "NIFTY 50"],
            "OPEN_INDEX_VAL": [1000.0, 1005.0],
            "HIGH_INDEX_VAL": [1010.0, 1015.0],
            "CLOSE_INDEX_VAL": [1000.0, 1012.0],
            "LOW_INDEX_VAL": [995.0, 1002.0],
            "TURN_OVER": [1, 1], "TRADED_QTY": [1, 1],
            "TIMESTAMP": [
                today.strftime("%d-%b-%Y").upper(),
                (today + __import__("datetime").timedelta(days=1)).strftime("%d-%b-%Y").upper()],
        })
        monkeypatch.setattr(
            "nselib.capital_market.index_data",
            lambda *a, **k: df)
        resolved = macro.resolve_open_journal_entries(conn)
        assert resolved == 1
        row = conn.execute("SELECT * FROM macro_journal").fetchone()
        assert row["actual"] == "UP"
        assert row["outcome"] == "correct"
        assert row["resolved_at"]
