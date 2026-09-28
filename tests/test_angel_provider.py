"""Paper-Trader — AngelProvider unit tests (fake SmartAPI, no network).

Covers: skip-when-unconfigured, login/session handling, spot + quotes,
expiries parsing, option-chain normalization + Greeks, failover wiring in
``default_manager`` ordering, and manager cooldown → angel takeover.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from backend.market_data_manager import MarketManager
from backend.market_data_source import ProviderError, ProviderMiss
from backend.providers.angel_provider import AngelProvider


class FakeSmartApi:
    """Minimal in-memory SmartConnect double with the surface we call."""

    def __init__(self, fail_auth_once: bool = False):
        self.login_calls = 0
        self.fail_auth_once = fail_auth_once
        self.auth_broken = False
        self.ltp_calls = 0

    def generateSession(self, client, pin, totp):
        self.login_calls += 1
        if self.fail_auth_once and self.login_calls == 1:
            return {"status": False, "message": "Invalid TOTP"}
        return {"status": True, "data": {"feedToken": "feed", "jwtToken": "jwt"}}

    def ltpData(self, exchange, symbol, token):
        self.ltp_calls += 1
        if self.auth_broken:
            self.auth_broken = False
            return {"status": False, "message": "session expired, please login again"}
        if token == "99926000":
            return {"status": True, "data": {"ltp": "25,113.45"}}
        if token == "2885":
            return {"status": True, "data": {"ltp": 2891.5}}
        if token == "999999":                          # fake NIFTY FUT
            return {"status": True, "data": {"ltp": 25130.0}}
        return {"status": False, "message": "token not found"}

    def searchScrip(self, exchange, searchscrip):
        today = date.today()
        exp1 = (today + timedelta(days=5)).strftime("%d%b%Y")     # 05Oct2026 style
        exp2 = (today + timedelta(days=33)).strftime("%d%b%Y")
        if exchange == "NSE":
            known = {"NIFTY": "99926000", "RELIANCE": "2885"}
            if searchscrip in known:
                return {"status": True, "data": [
                    {"tradingsymbol": searchscrip, "exchange": "NSE",
                     "symboltoken": known[searchscrip]},
                ]}
            return {"status": True, "data": []}   # unknown → resolution miss
        # NFO rows for the chain (SDK rows: tradingsymbol/expiry/symboltoken;
        # strike parsed from the trading symbol).
        rows = []
        step = 50
        spot = 25100
        for i in range(-2, 3):
            strike = spot + i * step
            rows.append({"tradingsymbol": f"NIFTY{strike}CE",
                         "symboltoken": str(100000 + strike), "expiry": exp1})
            rows.append({"tradingsymbol": f"NIFTY{strike}PE",
                         "symboltoken": str(200000 + strike), "expiry": exp1})
        rows.append({"tradingsymbol": "NIFTY" + exp1.replace("-", "") + "FUT",
                     "symboltoken": "999999", "expiry": exp1})
        rows.append({"tradingsymbol": "NIFTY" + exp2.replace("-", "") + "FUT",
                     "symboltoken": "999998", "expiry": exp2})
        return {"status": True, "data": rows}

    def getMarketData(self, mode, exchangeTokens):
        fetched = []
        for token in exchangeTokens["NFO"]:
            fetched.append({
                "symbolToken": str(token),
                "ltp": "120.50",
                "iv": "14.5",
                "oi": "123456",
                "volume": "98765",
                "depth": {"buy": [{"price": "120.40"}], "sell": [{"price": "120.60"}]},
            })
        return {"status": True, "data": {"fetched": fetched}}

    def getCandleData(self, params):
        return {"status": True, "data": [
            ["2026-09-25T09:15:00", 25000, 25100, 24950, 25050, 1234],
            ["2026-09-25T09:20:00", 25050, 25150, 25000, 25100, 1555],
        ]}


class FakeCreds:
    api_key = "k"
    client_code = "c"
    pin = "p"
    totp_secret = "s"

    @property
    def complete(self):
        return True


@pytest.fixture
def provider():
    api = FakeSmartApi()
    return AngelProvider(api=api, creds=FakeCreds()), api


# --------------------------------------------------------------------- setup

def test_skips_without_credentials(monkeypatch):
    from backend.providers import angel_provider as mod

    class NoCreds:
        complete = False

    # Not installed → skip cleanly.
    monkeypatch.setattr(mod, "_SmartConnect", None)
    with pytest.raises(ProviderError):
        AngelProvider(api=None, creds=NoCreds())


def test_login_flow(provider):
    p, api = provider
    assert p.get_spot("NIFTY") == 25113.45
    assert api.login_calls == 1
    assert p.get_spot("RELIANCE") == 2891.5
    assert api.login_calls == 1  # session reused, no re-login


def test_relogin_on_session_expiry(provider):
    p, api = provider
    api.auth_broken = True
    assert p.get_spot("NIFTY") == 25113.45  # transparently re-logged in
    assert api.login_calls == 2


def test_unknown_symbol_is_provider_miss(provider):
    p, _ = provider
    with pytest.raises(ProviderMiss):
        p.get_spot("NOSUCH")


# ------------------------------------------------------------------ chain

def test_option_chain_normalization(provider):
    p, _ = provider
    expiry = (date.today() + timedelta(days=5)).isoformat()
    rows = p.option_chain("NIFTY", expiry, strikes_per_side=2)
    assert len(rows) == 5
    atm_row = min(rows, key=lambda r: abs(r.strike - 25100))
    ce = atm_row.ce
    assert ce.ltp == 120.5
    assert ce.iv == pytest.approx(0.145)
    assert ce.delta == pytest.approx(0.55, abs=0.45)
    assert ce.oi == 123456
    assert ce.bid == 120.4 and ce.ask == 120.6
    assert atm_row.pe.option_type == "PE"


def test_expiries_parse_and_filter(provider):
    p, _ = provider
    expiries = p.expiries(6, symbol="NIFTY")
    assert expiries
    today = date.today().isoformat()
    assert all(e >= today for e in expiries)


def test_future_price_real_contract(provider):
    p, _ = provider
    expiry = (date.today() + timedelta(days=5)).isoformat()
    price = p.get_future_price("NIFTY", expiry)
    assert price == 25130.0


def test_candles_normalized(provider):
    p, _ = provider
    candles = p.get_candles("NIFTY", "5m", "2026-09-24", "2026-09-25")
    assert len(candles) == 2
    assert candles[0]["close"] == 25050.0
    assert candles[0]["volume"] == 1234


# ------------------------------------------------- manager wiring/failover

class _AngelishProvider:
    """Stand-in with the same name so manager fallback ordering is tested."""

    name = "angel"

    def __init__(self):
        self.calls = 0

    def get_spot(self, symbol):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("angel: session down")
        return 25000.0

    def get_quotes(self):
        return {}

    def get_future_price(self, symbol, expiry):
        raise ProviderMiss("not covered")

    def future_curve(self, symbol):
        raise ProviderMiss("not covered")

    def get_option_quote(self, *a):
        raise ProviderMiss("not covered")

    def option_chain(self, *a, **k):
        raise ProviderMiss("not covered")

    def expiries(self, count=6, symbol=None):
        raise ProviderMiss("not covered")

    def health_check(self):
        return True


def test_default_manager_orders_nselib_angel_yfinance():
    from backend.market_data_manager import default_manager

    try:
        mgr = default_manager()
    except Exception:
        pytest.skip("no providers installed in this environment")
    names = [p.name for p in mgr.providers]
    if "angel" in names:
        assert names.index("nselib") < names.index("angel") < names.index("yfinance")


def _make_manager(*providers, quote_ttl=0.0):
    ticks = {"t": 0.0}

    def now_fn():
        return ticks["t"]

    mgr = MarketManager(providers=list(providers), quote_ttl=quote_ttl,
                        now_fn=now_fn)
    return mgr, ticks


def test_manager_falls_through_to_next_provider():
    class Down:
        name = "nselib"

        def get_spot(self, symbol):
            raise ProviderError("scrape down")

        health_check = lambda self: False

    angel = _AngelishProvider()
    mgr, ticks = _make_manager(Down(), angel)
    # nselib raises → cooldown; angel's first call raises → angel cooldown;
    # manager then raises (all providers cooling). After cooldown expiry the
    # next call lands on angel and succeeds.
    with pytest.raises(ProviderError):
        mgr.get_spot("NIFTY")
    assert angel.calls == 1

    ticks["t"] += 31
    assert mgr.get_spot("NIFTY") == 25000.0
    assert angel.calls == 2


def test_symbol_gap_does_not_cool_provider():
    class GapThenOk:
        name = "angel"
        calls = 0

        def get_spot(self, symbol):
            GapThenOk.calls += 1
            if symbol == "NOSUCH":
                raise ProviderMiss("angel: no token")
            return 100.0

    mgr, _ = _make_manager(GapThenOk())
    with pytest.raises(ProviderError):
        mgr.get_spot("NOSUCH")          # all providers missed
    assert mgr.provider_status()[0]["cooling_down"] is False  # miss ≠ failure
    assert mgr.get_spot("NIFTY") == 100.0            # provider still trusted
