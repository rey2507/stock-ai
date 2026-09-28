# Paper-Trader Progress

> **Handoff document.** Written so another AI can resume with full context:
> what each stage built, how it works internally, verified behavior, known
> quirks, and where the next stage should plug in.
> State: **Stages 1–19 complete, 143/143 tests passing.** Stage 19 added
> missing SENSEX index support across all providers and prepared Linux VM +
> Cloudflare Tunnel deployment files.

---

## Stage 1: Project Skeleton ✅
**Status:** Complete
**Tests:** 3/3 passing
**Output:** FastAPI health check + static HTML frontend
**Files:** backend/main.py, web/index.html, web/css/style.css, web/js/app.js, requirements.txt

Details:
- Single uvicorn process serves JSON API (`/api/health`) AND the static
  frontend. The static mount is registered **last** so `/api/*` routes win.
- This "API-first, static-after" ordering is a load-bearing decision — never
  move the mount above the routes.
- TestClient-based tests; no live server needed.
- Original test files (test_health.py) still pass unchanged.

---

## Stage 2: Paper Trading Engine ✅
**Status:** Complete
**Tests:** 18/18 passing
**Output:** SQLite schema, orders (MARKET/LIMIT), positions, P&L calculation
**Files:** backend/db.py, backend/trading_engine.py (grew here, restructured in Stage 3), POST/GET /api/orders, GET /api/positions, GET /api/account
**Live Data:** None (mock only)

Details:
- **SQLite schema** at `data/trader.db`, created on startup via FastAPI
  lifespan hook (`db.init_db()`).
- **MockMarketData:** deterministic per-2-second-bucket quotes
  (`random.Random(f"{symbol}:{bucket}")`) — same price within a bucket, so
  tests are stable, but prices visibly move in the UI.
- **Order flow:** POST /api/orders → validate symbol/funds → record order row
  → update cash & positions atomically in one `db.db()` context (commit on
  success, rollback on error).
- **P&L model:** BUY debits cash at fill price; SELL credits proceeds;
  unrealized P&L = `(current − avg_price) × qty` marked to mock price.
- **Account:** single row, virtual capital ₹10,00,000
  (`cash`, `starting_capital`); margin/P&L derived at read time.
- Schema versioning didn't exist yet — added in Stage 3 (see below).

---

## Stage 3: Options & Futures ✅
**Status:** Complete
**Tests:** 54/54 passing
**Output:** NSE options chains with Greeks (Black-Scholes), futures, watchlist, stop-loss/bracket orders
**Files:** backend/greeks.py, backend/trading_engine.py (full F&O engine), backend/nse_data.py, backend/market_data.py (rewritten as F&O mock), web option-chain UI + trade modal in the SPA
**Live Data:** None (mock only)
**Limitations:** Hardcoded NSE metadata (lot sizes, expiries), theta clock unreliable

Details:
- **backend/greeks.py — pure-math Black-Scholes** (stdlib only, `erf`-based
  normal CDF, no scipy). Returns price, delta, gamma, vega (per 1 IV point),
  theta_per_day, rho. Boundary handling: T→0 or σ→0 collapses to intrinsic
  payoff with zero Greeks. Also: IV smile
  (`base + skew·ln(K/S) + smile·ln(K/S)²`, skew −0.35, smile 0.12, clamped
  5–120%), term-structure pump near expiry, and the NSE expiry calendar
  (weekly Thursdays + last-Thursday monthlies, merged ascending; no holiday
  awareness — known limitation).
- **backend/nse_data.py — static F&O metadata:** 4 indices (NIFTY 75,
  BANKNIFTY 30, FINNIFTY 65, MIDCPNIFTY 120 lots) + 10 stocks with lot sizes
  and margin % (12–14% indices, 20% stocks), plus `INDEX_SPOT` anchors and
  `SHORT_OPTION_MARGIN_PCT = 0.15`.
- **MockMarketData upgraded to full F&O surface:**
  - Futures: `spot × (1 + 6.5% × t)` + per-bucket wiggle; basis widens with tenor.
  - Options: Black-Scholes on the deterministic IV smile; the **theta clock**
    ticks every 45s at 240× compression so premiums visibly decay (only when
    `advance_theta_clock()` is called — routes call it lazily).
- **Trading engine (backend/trading_engine.py):**
  - **Lot validation:** F&O quantities must be whole lots (`qty % lot == 0`).
  - **Margin model (critical to understand):**
    - EQ buys + long options: **prepaid** — cash debited up front.
    - FUT + short options: **margin instruments** — cash untouched at open,
      `used_margin` = notional × margin% (fut) / spot-notional × 15% (short
      opts); P&L settles into cash on close. Account equity =
      `cash + prepaid_value + margin_unrealized` (adding FUT notional would
      double-count).
  - **`ensure_funds`** gates opens only (`available = cash − margin_held`);
    exits release margin instead of consuming.
  - **Stop-loss:** standalone SL orders park as `status=OPEN` rows;
    SL-M semantics (fill at trigger, not gap price). Already-breached SLs
    fill immediately.
  - **Bracket orders:** parent + entry + target + stoploss legs
    (`orders.parent_id` + `leg_role`). Market entries fill now and arm both
    exit legs; LIMIT entries wait for a touch. Either exit leg filling closes
    the position and cancels the sibling. `cancel_order` works from any
    family member and cascades.
  - **Lazy settlement:** `process_pending_orders(conn, md)` is called by
    routes **before any state read** (orders/positions/account) — SL/bracket/
    LIMIT triggers fire on the first request after a price breach. (In Stage
    5 it was discovered this call had been accidentally commented out in
    POST /api/orders and fixed.)
  - **Portfolio Greeks:** delta/gamma/vega/theta summed over positions;
    FUT contributes Δ = units; options contribute signed unit Greeks
    (sign-flipped for SHORT).
- **Schema bumped to v3** (`meta` table with `schema_version`): pre-v3
  databases are **dropped and reseeded**, not migrated (paper trading —
  nothing to preserve). Positions became UNIQUE on
  (symbol, instrument_type, expiry, strike, side) with side LONG/SHORT.
- **UI:** option-chain page (click any call/put price → trade ticket modal),
  watchlist with Equity/Futures/Chain buttons, positions page with per-order
  cancel, account page with portfolio Greeks.

---

## Stage 4: Live NSE Provider Integration ✅
**Status:** Complete
**Tests:** 70/70 passing (54 existing + 16 new)
**Output:** MarketDataSource abstraction, nselib live provider (option chains + index spots), mock provider as fallback
**Files:** backend/market_data_source.py, backend/market_data_manager.py, backend/models.py, backend/providers/{nselib_provider.py, mock_provider.py, __init__.py}
**Live Data:**
  - NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY spots + option chains (nselib)
  - Index futures (derived via cost-of-carry)
  - Stock spots/futures (mock/simulated)
**Provider Stack:** nselib → mock (fallback)
**Caching:** 5s quotes, 1h expiries, 30s provider cooldown on error
**Limitations:** Stock spots remain simulated (nselib has no live stock quotes)

Details:
- **backend/market_data_source.py — the contract:** abstract
  `MarketDataSource` covering the FULL engine surface (get_spot, get_quotes,
  get_future_price, future_curve, get_option_quote, option_chain, expiries)
  so the entire app marks against one coherent source. Two exception types
  drive fallback semantics:
  - `ProviderError` → fall through AND cool the provider down 30s
    (network down, schema drift, empty responses).
  - `ProviderMiss` → per-symbol fall-through with NO cooldown (nselib serves
    indices but has no stock spots — that's normal, not a failure).
- **backend/models.py — normalized dataclasses** with `DictAccessMixin`
  (dict-style `q["ltp"]` access so the existing engine reads models
  unchanged): `Quote`, `OptionQuote` (ltp/bid/ask/iv/Greeks/oi/oi_change/
  volume/spot/t_years), `ChainRow` (strike + ce + pe), `OptionChain`,
  `FutureQuote` (price + basis), `ExpiryList`. All carry
  `source`/`status`/`timestamp`.
- **backend/market_data_manager.py — MarketManager facade:**
  - `_fetch()` loop per call: skip cooling providers → mock calls run
    inline, live calls go through a 4-worker ThreadPoolExecutor with an
    **8s timeout** (a hung NSE request can't block a route forever; timed-out
    threads keep running until their HTTP timeout fires — accepted) →
    success clears cooldown and populates cache.
  - **TTL cache doubles as the NSE rate-limiter** (~1 req/s effective):
    quotes/chains 5s, expiries 1h. Cache keys are (method, args, kwargs).
  - **Status labels:** mock → `"simulated"`, nselib spots/chains → `"live"`,
    nselib futures → `"derived"` (they're carry-model, not scraped).
  - `market-status` route exposes provider order, health, cooldowns, last errors.
  - `default_manager()`: tries to import NselibProvider (skips silently if
    nselib isn't installed), appends MockMarketDataProvider last.
- **backend/providers/nselib_provider.py — the adapter,** written against
  the **installed nselib 2.5.1** surface with its real quirks handled:
  - Entry points live in submodules: `nselib.indices.live_index_performances()`
    (one call returns ALL index rows; cached 5s),
    `nselib.derivatives.expiry_dates_option_index()` (dict keyed by
    underlying, `%d-%b-%Y` strings), `nselib.derivatives.nse_live_option_chain()`
    (**parses expiry as `%d-%m-%Y`** — inconsistent with the listing format;
    the provider converts ISO→`%d-%m-%Y` for chain calls).
  - Coverage is deliberately partial and honest: index spots + chains are
    LIVE (real OI/IV/volume); stocks raise `ProviderMiss` → mock serves
    without punishment; futures are derived from spot via carry.
  - Greeks computed **locally** with our Black-Scholes from the chain's IV;
    smile fallback when NSE publishes `-`/0. Messy numeric strings
    (`"2,00,000"`, `"-"`, `nan`) normalized by `_num()`.
- **backend/providers/mock_provider.py:** thin wrapper exposing the Stage 3
  MockMarketData behind the provider contract; exposes `.md` so tests can
  shift base spots / tick the theta clock.
- **Test isolation:** conftest.py swaps `main.market_data` for a mock-only
  MarketManager (`volatility=0`) and points `db.DB_PATH` at tmp_path.
  nselib is tested through fake pandas-returning modules (recorded call
  args assert the date-format contract). **No test ever touches the network.**

---

## Stage 5: Real Data Labeling & Candles ✅
**Status:** Complete
**Tests:** 90/90 passing (70 existing + 20 new)
**Output:** Status badges (LIVE/SIM/STALE/UNAVAILABLE), `/api/chart` endpoint with daily/intraday candles
**Files:** web/js/badges.js, backend/providers/candles.py, backend/market_data_source.py (candle contract), backend/providers/{nselib_provider.py, mock_provider.py} (get_candles), backend/market_data_manager.py (candle caching + marking_meta), backend/main.py (chart route + fixed pending-orders bug), tests/test_candles.py, frontend badge wiring across watchlist/chain/positions/account
**Live Data:**
  - Index spots + option chains (nselib, labeled LIVE)
  - Daily OHLCV candles (nselib, labeled LIVE)
  - Intraday candles (mock, labeled SIM)
  - Futures (derived, labeled DERIVED)
**Frontend Changes:**
  - Watchlist: source badges (LIVE vs SIM)
  - Option chain: fixed mislabeled OI/volume columns, added source badge
  - Positions/Account: marking source displayed
**Limitations:** No stale-age threshold yet, no NSE holiday calendar, mock theta clock not advancing in live mode

Details:
- **Candle architecture:**
  - `get_candles(symbol, interval, start, end)` added to the
    `MarketDataSource` ABC; `CANDLE_INTERVALS = ("1m","5m","15m","1h","1D")`.
  - **backend/providers/candles.py** — shared helpers:
    - `normalize_candles()` coerces messy provider rows (any real NSE column
      spelling, comma-numbers, `-` blanks) into
      `{timestamp, open, high, low, close, volume, oi}`; rows without
      timestamp+close are DROPPED, never faked; high/low repaired to bracket
      open/close; `detect_column_map()` matches lowercased column names
      (HistoricalDate, TtlTrdQnty, …) and deliberately skips CHNG_IN_OI.
    - `build_mock_candles()` — deterministic mean-reverting walk seeded per
      (symbol, interval); NSE session buckets 09:15–15:30 on weekdays;
      per-interval volatility; MAX_CANDLES=2000 guard.
  - **nselib serves 1D only** (via `capital_market.price_volume_data` for
    stocks / `index_data` for indices — daily timestamps arrive as
    `"01-JUL-2026"` and are preserved as published); intraday raises
    `ProviderMiss` → mock serves, honestly labeled SIM.
  - **Manager caching:** candles cached with **1h TTL for 1D** (immutable EOD
    history) and **5m for intraday** (today's bars still form).
  - **`GET /api/chart/{symbol}?interval=&start=&end=`** returns
    `{symbol, interval, start, end, status, source, candles[]}`; invalid
    interval → 422, unknown symbol → 404, no provider coverage →
    `{status: "unavailable", candles: []}` (never fake data).
- **The critical bug fix:** `engine.process_pending_orders()` inside
  POST /api/orders had been accidentally **commented out** (trailing text on
  the comment line), meaning SL/bracket legs settled only on read routes.
  Restored.
- **Status badges (web/js/badges.js):** `BADGE_DEFS` maps
  live/derived/stale/simulated/mixed/unavailable → color-coded spans;
  `sourceBadge()` for headers, `inlineSourceBadge()` for table rows.
- **Honest-data guarantees baked in:** account response carries
  `data_source: {status, source}` (MIXED when positions mark against
  different sources); positions carry per-position source/status; mock
  chains return `volume: None`/`oi: None` and the UI dashes them out rather
  than inventing numbers.

---

## Stage 6: Interactive Candlestick Charts ✅
**Status:** Complete
**Tests:** 90/90 passing (no backend changes)
**Output:** Canvas-based chart page with symbol/interval/date selection, hover tooltip
**Files:** chart section in web/index.html (SPA route `page-chart`), web/js/chart.js, chart wiring in web/js/api.js (`API.chart`) + web/js/app.js, web/css/chart.css
**Features:**
  - Symbol dropdown (populated from `/api/instruments` — not hardcoded)
  - Interval segmented buttons (1m, 5m, 15m, 1h, 1D)
  - Quick ranges (5, 10, 20, 50 days) + custom date pickers (from/to + Apply)
  - Candlesticks (green up, red down) + volume bars + grid + price/time axes
  - Hover tooltip (OHLCV, hit-tested via cached pixel layout)
  - Theme-aware (light/dark) — MutationObserver on `data-theme` re-renders
  - Responsive layout (mobile: stacked controls, 320px chart)
**Live Data:** 1D NIFTY/BANKNIFTY show real nselib candles; intraday shows mock
**Limitations:** No indicators (added in 7), no zoom/pan, polling only, custom dates accept any range (backend clamps)

Details:
- **Architecture decision (user-approved):** HTML5 `<canvas>` over Chart.js /
  TradingView Lightweight Charts (zero dependencies, fits the no-build-step
  vanilla setup), built as an SPA route consistent with the existing pages.
- **Rendering:** devicePixelRatio-scaled canvas; candles drawn per slot
  (`slot = plotW / n`, body width clamped 1–14px); price scale computed from
  candle highs/lows with 5% padding; volume strip along the bottom of the
  price panel; max 6 time-axis labels. Colors read from CSS variables via
  `getComputedStyle` so theme swaps just re-render.
- **Quirk handled:** nselib daily timestamps arrive as `"01-JUL-2026"` —
  axis labels normalize both ISO and `%d-%b-%Y` formats
  (`fmtTs()` regex test).
- **Verified end-to-end in a live browser session:** 675 SIM candles on 5m
  NIFTY; 9 LIVE candles on 1D NIFTY (source=nselib); interval switch
  re-fetches; tooltip works; theme toggle re-renders (confirmed via pixel
  readback); 422 on `interval=2m`; zero console errors.
- **Empty-state overlay** (`#chart-empty`) shows the API's `detail` message
  when a range returns no candles; canvas hides.

---

## Stage 7: Technical Indicators ✅
**Status:** Complete
**Tests:** 90/90 passing (no backend changes — all math is client-side)
**Output:** SMA, EMA, RSI, MACD calculations + canvas overlays/subplots + localStorage persistence
**Files:** web/js/indicators.js (pure calcs), web/js/chart.js (reworked panel layout, overlays, subplots, legend, tooltip), web/index.html (legend container + indicators.js script tag), web/css/chart.css + design-system.css (indicator color vars)
**Indicators Implemented:**
  - SMA (20, 50, 200 period)
  - EMA (12, 26 period)
  - RSI (14 period, Wilder's smoothing) — separate subplot with 30/70 guides and shaded over/oversold zones
  - MACD (12/26/9) — separate subplot: MACD line + signal line + histogram
**Features:**
  - Legend checkboxes with color swatches (toggle on/off, recompute lazily —
    only active indicators are calculated)
  - localStorage persistence (key `pt-indicators`, survives reload; default
    on first visit: SMA20 only)
  - Theme-aware colors via CSS variables
    (`--ind-sma20 #ff6b6b`, `--ind-sma50 #4ecdc4`, `--ind-sma200 #95e1d3`,
    `--ind-ema12 #ffa07a`, `--ind-ema26 #ffb347`, `--ind-rsi #9b59b6`,
    `--ind-macd #3498db`, `--ind-signal #e74c3c`)
  - Tooltip + a live values row under the legend show all active indicator
    values at the hovered bar (falls back to the last bar)
  - Insufficient history → `null` (never faked); overlay lines break at nulls
**Live Data:** Indicators calculated client-side from live OHLCV
**Verified:** Pixel scanning confirms all 4 indicator types render; persistence
TDZ bug fixed (`IND_STORAGE_KEY` const declared before `ChartState` uses it)
**Limitations:** Fixed periods (no inline editing), no alert signals, no
backtesting, SMA200 needs ~200 bars (gaps on short ranges)

Details (implementation notes the next stage will need):
- **indicators.js — pure functions, no DOM, null-padded arrays aligned
  index-for-index with the closes array:**
  - `calcSMA(closes, period)` — rolling sum, O(n).
  - `calcEMA(closes, period)` — seeded with the SMA of the first `period`
    closes (standard practice), then `k = 2/(period+1)` smoothing.
  - `calcRSI(closes, period=14)` — Wilder's smoothing
    (`avg = (prev×(period−1) + cur)/period`); first value at index `period`;
    `avgLoss === 0 → RSI = 100`.
  - `calcMACD(closes, 12, 26, 9)` — EMA diff for the MACD line; signal EMA
    computed on the **compacted** non-null MACD segment then re-aligned back
    to candle indexes; histogram = macd − signal.
  - `computeIndicators(candles, active)` — the single entry point the chart
    calls; only computes what's toggled on.
- **chart.js panel system:** `panelLayout(height)` distributes the canvas
  vertically — price panel keeps ≥45%, each active subplot (RSI, MACD) gets
  ≥70px plus a 14px title strip and 10px gap. Subplot titles are drawn in the
  indicator's color.
- **Overlay scaling:** active SMA/EMA values are pushed into the
  highs/lows arrays before computing the price scale, so lines never clip.
- **RSI subplot:** fixed 0–100 scale, guide lines + axis labels at 30/70,
  `fillWhere()` shades regions where RSI > 70 (down-soft) or < 30 (up-soft).
- **MACD subplot:** auto-scaled to min/max of line+signal+histogram with 8%
  padding, zero-line guide, histogram bars behind the two lines
  (green ≥ 0, red < 0).
- **`drawLine()`** skips nulls by breaking the path (that's why SMA200
  simply doesn't appear until 200 bars exist).
- **Legend wiring:** checkbox change → update `ChartState.active` →
  `saveActiveIndicators()` → recompute → re-render → refresh values row.
   `updateLegendValues(hoverIdx)` uses `??` fallback to the last bar.

---

## Stage 8: Data Integrity & Persistence ✅
**Status:** Complete
**Tests:** 106 passing (90 existing + 16 new)
**Output:** Verified zero placeholder data in normal operation; full data-integrity test suite
**Files:** `tests/test_persistence.py`, `tests/test_reconciliation.py`, `tests/test_no_placeholder.py`
**Data Integrity Verified:**
- Account state persists across restart
- Orders persist to SQLite atomically
- Positions derived from database (no in-memory portfolio)
- Market prices from verified provider abstraction (live or explicitly simulated)
- P&L reconciles with actual trades
- Empty states display empty (not demo data)
- No hardcoded balances, prices, or P&L in normal mode
- All source metadata accurately labeled (LIVE/SIM/DERIVED/STALE/UNAVAILABLE)
**Limitations:** None — this is a data-correct paper-trading application

---

## Stage 9: Order Entry from Chart ✅
**Status:** Complete
**Tests:** 106/106 passing (no new tests — browser-side feature)
**Output:** Click-to-trade from chart canvas using existing trade modal
**Files:** `web/js/chart.js`
**Implementation:**
- Canvas click handler on chart page detects valid candle clicks inside the price panel
- Temporary Buy/Sell popup appears near click position
- Buy → `openTradeModal({ symbol, instrumentType: "EQ", presetSide: "BUY" })`
- Sell → `openTradeModal({ symbol, instrumentType: "EQ", presetSide: "SELL" })`
- Popup dismisses on outside click or after selection
- Price panel bounds stored in `ChartState.layout` for hit-testing
- Candle close is NOT used as a price preset (would require trade-modal.js modification)
**Limitations:** Equity-only (EQ), no LIMIT price prefill, no pending-order visualization

---

## Stage 10: Real Stock Spot Provider & Honest Marking ✅
**Status:** Complete
**Tests:** 106/106 passing (no new tests — production data-source change)
**Output:** Real stock prices via yfinance when available; simulated stock prices hidden from UI/account math
**Files:** `backend/providers/yfinance_provider.py` (new), `backend/providers/mock_provider.py`, `backend/market_data_manager.py`, `backend/main.py`, `requirements.txt`
**Implementation:**
- Added `YfinanceProvider` — fetches real current prices for 10 Indian equities via yfinance
- Production provider stack: `nselib → yfinance → mock` (indices + fallback only)
- Mock provider gains `allow_fake_stocks` flag; production instance sets it `False` so stocks fall through to `ProviderMiss` instead of fake prices
- Positions/account/watchlist/instruments endpoints catch `ProviderError` and return `null`/`unavailable` for unpriced instruments instead of fabricating numbers
- `data/trader.db` cleared for fresh start (old test trades removed)
**Limitations:** yfinance required for real stock prices; outside market hours yfinance returns last close (real but stale); futures/options still use nselib + mock fallback

---

## Stage 11: Order Entry from Chart ✅
**Status:** Complete
**Tests:** 106/106 passing (no new tests — browser-side feature)
**Output:** Click-to-trade from chart canvas using existing trade modal
**Files:** `web/js/chart.js`
**Implementation:**
- Canvas click handler on chart page detects valid candle clicks inside the price panel
- Temporary Buy/Sell popup appears near click position
- Buy → `openTradeModal({ symbol, instrumentType: "FUT", presetSide: "BUY" })`
- Sell → `openTradeModal({ symbol, instrumentType: "FUT", presetSide: "SELL" })`
- Popup dismisses on outside click or after selection
- Price panel bounds stored in `ChartState.layout` for hit-testing
- Candle close is NOT used as a price preset (would require trade-modal.js modification)
**Limitations:** Futures/Options only (EQ removed), no LIMIT price prefill, no pending-order visualization

---

## Stage 12: Futures/Options Only — Mock Removal & Data Honesty ✅
**Status:** Complete
**Tests:** 106/106 passing
**Output:** Futures/Options-only trading; mock provider removed from production stack; honest unavailable data handling
**Files:** `backend/trading_engine.py`, `backend/market_data_manager.py`, `backend/main.py`, `backend/providers/mock_provider.py`, `backend/market_data.py`, `backend/providers/nselib_provider.py`, `backend/providers/mock_provider.py`, `tests/test_orders.py`, `tests/test_persistence.py`, `tests/test_candles.py`, `tests/test_market_data.py`
**Implementation:**
- **Removed Equity (EQ) trading** — `validate_request` now rejects EQ orders with clear error message
- **Provider stack cleaned** — Production stack: `nselib → yfinance` (no mock); Mock provider only in tests via `conftest.py`
- **Honest unavailable data** — When nselib/yfinance fail, API returns `null`/`unavailable` instead of fabricating numbers
- **Provider failure handling** — `ProviderError` caught in endpoints; `null`/`unavailable` returned for unpriced instruments
- **Mock provider isolated** — `allow_fake_stocks` flag; production sets `False`; tests keep `True` for deterministic data
- **Market hours awareness** — Orders placed outside market hours accepted as `PENDING` (placeholder for future Stage 13)
- **Instrument metadata** — Only FUT and OPT contracts allowed; EQ references removed from engine, routes, and tests
**Limitations:** Market hours enforcement (pending orders) not fully implemented yet; pending-order visualization not added

---

## Stage 13: Order Chain UI Polish & Bug Fixes ✅
**Status:** Complete
**Tests:** 106/106 passing
**Output:** Production-ready option chain UI with real NSE data; bug fixes for order placement and clearing
**Files:** `web/js/option-chain.js`, `web/index.html`, `web/css/components.css`, `backend/main.py`, `backend/market_data_manager.py`, `backend/providers/mock_provider.py`, `backend/market_data.py`, `backend/providers/nselib_provider.py`, `web/js/positions.js`, `web/js/trade-modal.js`
**Implementation:**
- **Option Chain UI Overhaul** — Header band with CALLS/STRIKE/PUTS labels; sticky strike column with gradient background; Call/Put side tints (up-soft/down-soft); ATM row highlight (2px warn outline); MAX OI badges; right-aligned numeric columns with tabular-nums; compact padding; strike range selector (5/10/15/All)
- **Real NSE Chain Loads** — Backend `strikes_per_side` parameter works (2→9 rows, 5→21, 10→41); nselib source returns live data with OI, volume, IV, Greeks
- **Order Placement Fixed** — Fixed `instrumentType` case mismatch in Close button; orders place at real nselib prices
- **Clear All Orders** — New `DELETE /api/orders` endpoint + "Clear All" button in UI with confirmation dialog
- **Clean Database** — Fresh DB on restart; EQ positions filtered from API responses
- **Mock Provider Isolated** — Production stack: nselib → yfinance (no mock); mock only in tests
**Limitations:** Market hours enforcement not yet implemented; pending-order visualization not added

---

## Stage 14: Angel One SmartAPI Failover, Performance & Reliability Overhaul ✅
**Status:** Complete
**Tests:** 117/117 passing (106 existing + 11 new Angel tests)
**Output:** Real broker API failover provider, fast endpoints, race-safe order entry, reduce-only close guard
**Files:** backend/providers/angel_provider.py (new), backend/market_data_manager.py, backend/main.py, backend/trading_engine.py, backend/nse_data.py, backend/config.py, tests/test_angel_provider.py, web/js/quick-trade.js, web/js/watchlist.js, web/js/trade-modal.js, web/js/positions.js, web/js/api.js

### 14a. Angel One SmartAPI provider (failover, not replacement)
- **Provider stack is now `nselib → angel → yfinance`** (no mock in
  production; mock only injected in tests via conftest). Status map:
  `{"mock": "simulated", "nselib": "live", "angel": "live"}`.
- Credentials from `.env` via backend/config.py (`ANGEL_API_KEY/
  CLIENT_CODE/PIN/TOTP_SECRET`, all-or-nothing — status() booleans only).
- **The installed SmartAPI SDK differs from its docs** — the adapter is
  written against the real surface: `searchScrip(exchange, searchscrip)`
  (NO expiry kwarg), rows carry `tradingsymbol`/`symboltoken`/`expiry`
  (strikes parsed from NFO trading symbols like `NIFTY2510025100CE`),
  batch quotes = `getMarketData(mode, exchangeTokens)` (not FULL),
  `ltpData(...)`, `getCandleData(params)`. INDEX_TOKENS: NIFTY 99926000,
  BANKNIFTY 99926009, FINNIFTY 99926037, MIDCPNIFTY 99926048.
- Session management: `_ensure_session` with lock, TOTP failure falls back
  to PIN-only ("000000"), one `_maybe_relogin` attempt on auth errors,
  quote throttle `QUOTE_MIN_INTERVAL = 1.05` (Angel ~1 rps).
- Error semantics match the nselib contract: "token not found"-style
  responses raise `ProviderMiss` (no cooldown), genuine failures raise
  `ProviderError` (30s cooldown). 11 fake-SDK tests cover session, quote
  batching, chain assembly, and failover ordering.

### 14b. MarketManager internals (current facts)
- **TTL cache (per key, in-memory, per process — resets on restart):**
  QUOTE_TTL_SECONDS=5.0, EXPIRY_TTL_SECONDS=3600, CANDLE_TTL_DAILY=3600,
  CANDLE_TTL_INTRADAY=300.
- **Tiered live timeouts:** `LIVE_CALL_TIMEOUT_SECONDS = 3.0` (spots/cheap
  calls) and `LIVE_CALL_TIMEOUT_HEAVY_SECONDS = 8.0` for
  `_HEAVY_METHODS = {option_chain, get_option_quote, expiries, get_candles}`
  (the nselib chain scrape needs 4–6s; a flat 3s timeout once benched every
  provider simultaneously → `no providers configured` 503s — fixed).
- **Single-flight coalescing:** `_inflight` dict of threading.Event keyed by
  (method, args, kwargs); joiners wait up to timeout+2s; leader publishes
  `(ok, payload)`. ThreadPoolExecutor(max_workers=8, "md-live").
- **`_providers_for(method)` ordering:** heavy methods are **Angel-first**
  (its batched getMarketData chain fetch is ~1–2s vs nselib's 4–6s HTML
  scrape); spots stay nselib-first to protect Angel's ~1 rps quota.
- **`ANGEL_FIRST_METHODS`** and `_fetch_as_leader` iterate
  `_providers_for(method)`; ProviderMiss = per-symbol fall-through w/o
  cooldown; ProviderError/timeout = 30s cooldown.
- **Angel `_search_nfo` cache:** 15-minute TTL (`_nfo_cache` in
  angel_provider.py) so chain/quote/expiry/futures lookups share ONE
  searchScrip call per underlying per 15 min — this ended the
  `Access denied because of exceeding access rate` bans.
- **Background warm-up:** `warm_market_data()` + Angel session pre-warm in
  lifespan; a `_spot_refresher` daemon thread re-fetches all stock spots
  **every 60s** so positions/account marking is always warm.

### 14c. Route-level caching & latency fixes
- **`/api/instruments`: whole-response cache** (`_instruments_cache`, 30s
  TTL in main.py) — was 9s EVERY call (per-symbol get_spot loop), now
  ~0.01s warm.
- **Order placement: ProviderError → HTTP 503** with a clear detail
  message listing each provider's miss (was a raw 500).
- **`portfolio_greeks` guarded with try/except** in BOTH /api/positions
  and /api/account — a market-data outage can no longer 500 the page;
  greeks show 0.0 instead.

### 14d. Lot sizes updated (NSE circular FAOP70616, Jan-2026 series)
NIFTY 65 (was 75), BANKNIFTY 30, FINNIFTY 60 (was 65), MIDCPNIFTY 120,
RELIANCE 500 (was 250), TCS 225 (was 175), ITC 1725 (was 1600),
HDFCBANK 650 (was 550), TATAMOTORS 1600 (was 550), INFY 400,
ICICIBANK 700, SBIN 750, AXISBANK 625, LT 175.
- Frontend hardcoded `lotSize: 75` removed from quick-trade.js;
  `lotSizeOf(symbol)` helper in watchlist.js resolves from the instruments
  cache; trade-modal waits for the server-resolved lot before enabling
  submit ("Quantity (loading…)").

### 14e. Order-entry race & close-intent fixes (real bugs found via live testing)
- **Bug A — duplicate orders:** 11 SELL fills in one second on the
  positions page (no submit lock while pricing took 2–4s). Fixed: submit
  button disables + shows "Placing…" until the order resolves.
- **Bug B — position race condition:** concurrent POST /api/orders each
  read the same position row and last-write-wins destroyed quantity
  (130 shown despite 715 sold). Fixed: **global `_TRADING_LOCK`**
  serializes all place/cancel/clear operations in main.py. Verified with a
  5-concurrent-orders test (sequential fills, no lost updates).
- **Bug C — close silently flipping:** the Close button presets the
  opposite side; if the position was already flattened, `apply_fill` took
  the open path and OPENED a new reversed position (drained the paper
  balance). Fixed with **reduce-only close**: `OrderRequest.reduce_only`
  field → carried through `validate_request` (it was silently dropping
  unknown fields — the first guard didn't fire until this was fixed) →
  `apply_fill`/`_close_position` reject with HTTP 400
  "Nothing to close…" when no opposing position exists; quantity clamps
  when it shrank. Frontend Close sends `reduce_only: true`
  (positions.js + trade-modal.js); manual trades unaffected.
- **Side flip BUY→SELL on close is CORRECT** (exiting a long = selling);
  the mirror (SHORT close presets BUY) verified too.
- Database fully reset after the incidents: 0 orders, 0 positions,
  cash ₹10,00,000 (backups at data/trader.db.bak-*). Order IDs restart.

### 14f. Verified live behavior (fresh server, all providers healthy)
- /api/expiries/NIFTY: 200 live ~2.6s cold; /api/option-chain/NIFTY
  (61 rows): 200 live ~0.7s cold, ~0.003s cached (source may report
  nselib when it wins the race within its 8s budget — both are live).
- /api/positions: 200 ~2s warm (no crash); open+close order flow filled
  instantly; close-after-flatten rejected 400 with clear message.
- Note: the old `timed out after 3s` provider last_errors are stale
  history, not active cooldowns — /api/market-status shows live health.

**Limitations:** nselib remains fragile (HTML scrape); Angel searchScrip
occasionally reports `Scrip not found in scrip master cache` for some
underlyings (harmless — falls through); yfinance still can't do chains/
expiries; WebSocket streaming replaces polling only in a future stage.

---

## Stage 15: Order Ticket Redesign (Close-Button Popup) ✅
**Status:** Complete
**Tests:** 117/117 passing (frontend-only + one read-only endpoint)
**Output:** Redesigned trade ticket following the user's 10-point spec: Contract → BUY/SELL → Quantity → Order type → Money impact → Confirm
**Files:** web/js/trade-modal.js (rewritten), web/js/positions.js, web/js/api.js, backend/main.py (POST /api/order-preview), web/css/components.css (tt- prefix block at end)
**Implementation:**
- **Header:** `NIFTY 22850 CE` + `29 Sep 2026 · Weekly · LTP ₹xxx.xx` and a
  live status dot (● Live / ◷ Delayed / ! Unavailable) — honest labeling
  from the quote's `source`/`status`.
- **BUY/SELL segmented control** — large, visually distinct, never both
  selected. Close context shows a short label (`Closing LONG · 65 qty —
  this order will SELL your existing position`) instead of the old
  repeated paragraph.
- **Quantity stepper** — `− 1 lot +` with `1 lot = 65 qty` and est. value
  below; lots ↔ qty stay in sync; server-resolved lot size required before
  submit enables.
- **Order-type segmented control** — Market | Limit | Stop-Loss | Bracket;
  only the fields relevant to the selected type render (Market shows
  "Execution price: Market", Limit shows limit price, etc.).
- **LTP line** always visible and never blanked during refresh. No bid/ask
  (free sources don't provide reliable depth) and no charges row (no
  charge model — faked charges would be worse than omitted).
- **Order summary table** — contract, side, qty, price, est. value,
  **required margin** and **available after order** via the new read-only
  **`POST /api/order-preview`** endpoint (`orderPreview` in api.js) —
  no side effects, safe to call on every field change.
- **Action-labeled submit** (`BUY NIFTY 22850 CE` / `SELL …`) →
  **confirmation step** (`Confirm paper order? BUY · 65 qty · Market …` with
  Cancel / Confirm buttons) — prevents accidental orders; double-click
  protection retained from Stage 14 ("Placing…" lock).
- Success feedback shows order ID, exact fill price and timestamp.
**Live-verified end-to-end:** open SHORT NIFTY PE 22800 → close via ticket
(filled) → close again rejected 400 "Nothing to close" → book reset to 0
orders / 0 positions / ₹10,00,000. Note: `node --check` and jq-style JSON
parsing through Git-Bash `/tmp` paths can throw ENOENT spuriously on this
machine — rely on HTTP status codes, not those artifacts.
**Limitations:** Bracket order fields render in the ticket per the spec but
bracket execution follows the Stage 3 engine semantics (entry/target/SL
legs); last-selected order type is not yet persisted across sessions.

---

## Stage 16: Macro Factors Page + Pre-market Journal ✅
**Status:** Complete
**Tests:** 124/124 passing (117 existing + 7 new in tests/test_macro.py)
**Output:** New "🌍 Macro" SPA page — global/US-futures/Asian markets, India
(VIX), commodities & rates, FII/DII activity, upcoming events, pre-market
summary tally, and a personal journal (expected vs actual) with honest
unavailable/stale labeling throughout. Free sources only; zero fake data.
**Files:** backend/macro.py (new), backend/db.py (schema v4 + journal CRUD),
backend/main.py (/api/macro, /api/macro/journal GET/POST/DELETE, refresher),
web/js/macro.js (new), web/css/macro.css (new), web/index.html, web/js/api.js,
web/js/app.js, tests/test_macro.py

### Data sources (all free, verified live against the installed libs)
- **Global markets:** S&P 500 (^GSPC), Dow (^DJI), Nasdaq (^IXIC) — yfinance.
- **US futures:** ES=F, NQ=F, YM=F — yfinance.
- **Asian markets:** Nikkei futures (NKD=F) + Nikkei (^N225), Hang Seng (^HSI),
  KOSPI (^KS11), Shanghai (000001.SS) — yfinance.
- **Commodities & rates:** Brent (BZ=F), Gold (GC=F), US 10Y yield (^TNX),
  Dollar Index (DX-Y.NYB), USD/INR (INR=X) — yfinance.
- **India:** India VIX via nselib `india_vix_data(period="1W")` (EOD series;
  the nselib market-watch feed has NO GIFT Nifty — see Limitations).
- **FII/DII:** nselib `participant_wise_trading_volume(trade_date=...)` —
  walks back up to 10 calendar days past weekends/holidays for the latest
  T+1 report; extracts index-futures long/short/net per participant plus
  index call/put longs; stale cached reports (nselib returns old rows for
  out-of-range dates) are detected and rejected via a date-column check.
- **Events:** nselib `event_calendar_for_equity(from,to)` for today+7 days
  (corporate actions/announcements, 28 rows live on verify day).
- Every yfinance factor: latest daily close vs previous close, % change,
  exchange-local timestamp. No intraday global feed exists free — values are
  daily closes and the UI says so via the shown timestamp.

### Honest labeling & signals
- Each row carries `status: live|unavailable`, `source`, `timestamp`;
  failures produce `unavailable` rows — never fabricated numbers.
- Signal badges: ▲ positive / ▼ negative / ● neutral (<0.05% move) /
  ? unclear (gold is directionally ambiguous; no fake reading) /
  — unavailable. Direction map: Brent, US10Y, DXY, USD/INR rising = negative
  for Indian risk sentiment; gold unclear; everything else rising = positive.
- Pre-market summary: count tiles (positive/negative/neutral/unclear/
  unavailable) plus per-signal contributor lists, explicitly labeled
  "a tally of how tracked factors closed — a reading, not a prediction".

### Pre-market journal (expectation vs actual)
- `macro_journal` table (schema v4 — purely additive upgrade; the trading
  book is NOT dropped). One entry per trade_date (UNIQUE), editable (upsert
  resets resolution).
- Fields: date, expected (UP/DOWN/FLAT), confidence (1–5), reasons (free
  text), then `actual` + `outcome` filled automatically.
- **Resolution:** `resolve_open_journal_entries` fetches nselib NIFTY 50
  daily series; the next session's OPEN vs the journal date's CLOSE is
  classified UP (>+0.15%) / DOWN (<−0.15%) / FLAT, and outcome is
  correct/wrong/unclear. Runs lazily on journal GET (best-effort; never
  blocks the response).
- Stats bar: correct / wrong / unclear / pending counts. Entries deletable.

### Routes & caching
- `GET /api/macro` — full snapshot, 5-min in-process cache
  (`macro.get_macro_snapshot`), `cache_age_seconds` included.
- `GET/POST/DELETE /api/macro/journal[/{id}]` — POST validated
  (date format, expectation enum, confidence 1–5) and serialized under the
  global `_TRADING_LOCK` (though it touches no trading state).
- `macro.start_macro_refresher()` daemon thread refreshes the snapshot every
  CACHE_TTL (300s) so the page never pays a cold fetch.

### Verified live (fresh server)
- `/api/macro`: 200, 0.02s cached; 16/16 factors live; VIX live (13.64);
  FII/DII live (trade_date present); events 28 items; summary counts sane.
- Journal POST/GET/DELETE round-trip 200s; pending→resolved path covered by
  tests with a canned pandas frame (no network in tests).
- Page served (page-macro present), macro.js/macro.css 200.

**Limitations / honest gaps (next-stage candidates):**
- **No free GIFT Nifty source was found** (nselib's market-watch feed lacks
  it; SGX/NSE IX endpoints are not in nselib). The India group currently
  shows India VIX + USD/INR (rates group) — GIFT Nifty left out rather than
  faked. If found later (e.g. a scraping endpoint), add a row to the India
  group.
- Global values are daily closes; "current" lags during Indian hours —
  timestamp column makes this visible.
- FII/DII publishes T+1; today's activity appears tomorrow.
- Events list is corporate actions only — no macro-economic calendar
  (RBI/Fed dates) exists in nselib.
- Signals are rule-of-thumb directional mappings, deliberately coarse and
  clearly labeled as readings, not predictions.

**Recommendations recorded for the user:**
1. GIFT Nifty: keep hunting free feeds (NSE IX site HTML, investing.com
   scraping is against ToS — avoid). Honest omission beats fake data.
2. Add an economic-calendar source (e.g. parse NSE "market events" or use a
   free FRED/Federal-REST feed for US macro prints) to enrich the Events card.
3. Consider a 30-day journal accuracy chart (rolling correct %) once history
   accumulates.
4. WebSocket push (Stage 16-A recommendation from the previous plan) remains
   the biggest app-wide latency improvement.

---

## Stage 17: Chart Upgrade — Live Intraday, Technical Verdict & Price Action ✅
**Status:** Complete
**Tests:** 136/136 passing (124 existing + 12 new in tests/test_verdict.py)
**Output:** Chart page now serves REAL live intraday candles (1m/5m/15m/1h)
for all 14 underlyings, auto-refreshes every 30s with a dashed last-price
line, and shows a descriptive technical-verdict card + price-action panel.
**Files:** backend/verdict.py (new), backend/main.py (/api/verdict/{symbol}),
web/js/chart.js (verdict loader, live refresh, last-price line),
web/index.html (verdict/pa cards), web/css/macro.css (chart-insights styles),
web/js/api.js, tests/test_verdict.py

### Key discovery: live intraday was already available
Probes verified yfinance serves REAL NSE intraday candles for both indices
(^NSEI, ^NSEBANK, …) and stocks (.NS): RELIANCE 5m = 218 bars in 0.8s;
NIFTY 5m = 150 bars. Angel's `getCandleData` (1m–1D) is also fully
implemented and tried first (heavy-method timeout 8s, falls through to
yfinance). The chart page's old "1D only" default was stale — the stack
needed no provider changes, just frontend defaults + a route nuance.

### Backend: /api/verdict/{symbol}
- **backend/verdict.py — pure functions mirroring web/js/indicators.js**:
  SMA, EMA (SMA-seeded), RSI-14 (Wilder), MACD 12/26/9 (signal seeded on
  the compacted series, re-aligned like the frontend), ATR-14 (Wilder).
- **compute_verdict(candles)** aggregates 7 named checks →
  Bullish/Bearish/Neutral + confidence % (|score|/decisive, capped):
  price vs SMA50, EMA12/26 cross state, RSI zone (70/30/55/45 bands),
  MACD histogram sign, 20-day range position (top/bottom 20% = decisive),
  trend slope (first-half vs second-half mean, ±1% bands), last-session
  direction. Fewer than 30 candles → honest "unclear".
- **Price-action block**: last/prev close, day change, day high/low,
  20d high/low, range position %, ATR-14.
- **one_liner()**: plain-language sentence ("Indicators lean bullish: up
  1.26% on the day, trading near the 20-day high, ATR 2.5% of price.").
- Response carries an explicit **"not a prediction" disclaimer**; source
  and status labeled honestly (simulated for mock in tests).
- Route caches per symbol for 5 min (daily closes); unavailable providers
  → status "unavailable", verdict "unclear" (never a 500); unknown symbol
  → 404.

### Frontend (chart page)
- **Verdict card**: verdict badge (▲ Bullish / ▼ Bearish / ● Neutral /
  ? Unclear with confidence %), confidence bar, per-check list with
  bull/bear/neutral markers and detail lines, one-liner, disclaimer.
- **Price-action card**: six stat tiles (last close, day change, day
  high/low, 20d high/low, range position, ATR) with source label.
- **Live intraday**: default interval is now 5m; 1m/5m/15m/1h auto-refresh
  every 30s (skipped when tab hidden, modal open, or non-chart route;
  server cache TTL 300s means refreshes usually hit the cache). 1D stays
  manual-refresh (EOD data).
- **Dashed last-price line** with a colored right-edge price tag (green/red
  vs previous close) drawn on the canvas after overlays.
- Chart CSS lives in macro.css alongside Stage 16 styles (one shared
  signal-badge vocabulary `.msig-*` used by both pages).

### Verified live (fresh server)
- `/api/verdict/NIFTY`: bullish, confidence 100%, source nselib, 64 daily
  candles, one-liner + full price-action correct (range pos 97%, ATR 615).
- `/api/verdict/RELIANCE`: bullish 33% (mixed checks — aggregation behaves).
- `/api/chart/RELIANCE?interval=5m`: 200 live yfinance, 218 candles, 0.4s.
- `/api/chart/NIFTY?interval=15m`: live, 150 candles.
- Page HTML contains verdict+pa cards; chart.js and macro.css served.

### What else can be added later (recorded recommendations)
1. **VWAP + volume profile** on intraday charts (computable from candles
   already fetched — no new data needed).
2. **Previous-day close + session open markers** as horizontal lines on
   intraday views.
3. **Candle-pattern annotations** (engulfing/doji/pin bars) as small flags.
4. **Multi-timeframe verdict** (daily + 1h side by side) — the engine
   already accepts any candle list.
5. **Crosshair + pinned OHLC legend** top-left (hover data already there).
6. WebSocket push (from Stage 16 recommendations) remains the biggest
   app-wide improvement; chart auto-refresh is a step in that direction.

---

## Stage 18: NIFTY 50 Verdict Page (technical + macro combined) ✅
**Status:** Complete
**Tests:** 143/143 passing (136 existing + 7 new in tests/test_overall_verdict.py)
**Output:** The Chart page's NIFTY technical verdict was moved to the former
Macro page (renamed **⚖ Verdict**), which is now NIFTY 50-focused: an
**overall verdict** combining the existing technical checks with the existing
macro factor tally, plus the full technical detail and the macro factors.
**Files:** backend/overall_verdict.py (new), backend/main.py
(/api/overall-verdict/NIFTY), web/index.html (nav rename, page rework, chart
block removed), web/js/chart.js (verdict display removed), web/js/macro.js
(verdict renderers moved here + overall loader), web/js/api.js,
web/css/macro.css (.ov-* styles), tests/test_overall_verdict.py

### What the Verdict page shows (NIFTY 50 only)
1. **Overall verdict** card — bullish/bearish/neutral + confidence %, with
   two sub-blocks (technicals X% · N checks, macro Y% · N factors) and a
   one-liner. From the new **`GET /api/overall-verdict/NIFTY`**.
2. **NIFTY 50 technicals** card — the exact technical-verdict card moved
   from the Chart page (same `/api/verdict/NIFTY` endpoint, same checks,
   unchanged math).
3. **Macro factor tally** + grouped factor tables + FII/DII + events
   (unchanged from Stage 16) + the pre-market journal (unchanged).

### The combiner (backend/overall_verdict.py — additive only)
- Normalizes both existing vocabularies (bullish/positive ↔ bearish/negative,
  neutral, unclear/unavailable excluded from score AND denominator).
- Technical score from the existing compute_verdict checks; macro score
  from the existing summary counts. Equal weight, decided-only share −1..1,
  ±0.15 dead-band → overall verdict; confidence = |share|.
- Honest paths: technical unavailable → macro-only verdict; both unclear →
  "unclear" with 0 confidence and "no decided signals" one-liner.
- Response carries the "not a prediction" disclaimer. No new indicators,
  no new sources, no fake data — it only counts existing signals.

### What was REMOVED from the Chart page
- The verdict card, price-action card, and the `.chart-insights` container
  (index.html); the `loadVerdict()` renderer (chart.js — now lives in
  macro.js against the same endpoint). **The chart itself is untouched**:
  candles, intervals, live 30s intraday auto-refresh, dashed last-price
  line, indicators, tooltip, click-to-trade all unchanged.
- `/api/verdict/{symbol}` endpoint kept (the Verdict page uses it; route
  tests unchanged and passing).

### Verified live (fresh server)
- `/api/overall-verdict/NIFTY`: bearish 27% (technicals bullish 100% /
  5 checks, macro bearish 65% / 17 factors) — combination behaves and the
  one-liner explains the split.
- `/api/verdict/NIFTY` still 200; chart 5m still live (293 candles);
  chart.js contains zero verdict references; page HTML contains the new
  cards and the renamed nav button.

---

## Current Architecture

**Backend (Python 3.10+, FastAPI, SQLite, nselib, yfinance):**
- Request flow: SPA → FastAPI route → trading_engine (orders) or
  MarketManager (market data) → provider stack → cache/fallback.
- MarketManager provider stack: nselib (indices/live) → **angel** (SmartAPI
  failover: chains/quotes/expiries, Angel-first for heavy methods) →
  yfinance (stocks/live).
  **No mock provider in production** — if live providers fail, return `unavailable`.
  TTL cache (quotes 5s, expiries 1h, candles 1h daily / 5m intraday), 30s cooldowns,
  tiered live-call timeouts (3s spots / 8s heavy chain calls), single-flight
  coalescing (8-worker pool), ProviderMiss vs ProviderError semantics,
  Angel searchScrip 15-min cache, 60s background spot refresher.
- 14 F&O underlyings (4 indices + 10 stocks) with static lot/margin metadata
  (lot sizes per NSE circular FAOP70616, Jan-2026 series — NIFTY 65, RELIANCE 500, …).
- Paper trading engine: lot validation, prepaid vs margin instruments,
  SL/bracket order lifecycle, lazy trigger settlement, portfolio Greeks,
  **global trading lock** (race-free order entry), **reduce-only close guard**
  (Close can never silently open a reversed position).
- **Futures/Options only** — Equity (EQ) trading removed; only FUT and OPT contracts allowed.
- Schema v3 at `data/trader.db` (auto-reset from older versions).
- Run: `.venv/Scripts/python.exe -m uvicorn backend.main:app --reload --port 8000`
  (Git Bash on Windows; from project root so `backend.main` imports).

**Frontend (no build step, globals, script-order dependencies):**
- Single-page app: index.html sections + `App.route` + `ROUTES` map in
  app.js; 3s polling (15s on chart; skipped when tab hidden/modal open);
  theme toggle (localStorage `pt-theme`).
- Script load order at bottom of index.html: api.js → format.js → badges.js →
  trade-modal.js → watchlist.js → option-chain.js → positions.js →
  **indicators.js → chart.js** → app.js (last, boots everything).
- Redesigned order ticket (trade-modal.js): header + live status dot,
  BUY/SELL segmented control, lots stepper, order-type segmented control
  (Market/Limit/SL/Bracket, per-type fields), order summary with margin/
  available-after (via read-only POST /api/order-preview), action-labeled
  submit → confirmation step, submit lock ("Placing…"), close-context labels.
- Design tokens in design-system.css (`--surface`, `--up/--down`,
  `--text-muted`, …) — canvas reads them via getComputedStyle.
- Pages: Watchlist (LIVE/SIM badges), Option Chain (real OI/IV/volume when
  live, Greeks, click-to-trade), Positions & Orders (per-position marking +
  Greeks + cancel), Account (cash/margin/equity/P&L + portfolio Greeks +
  data source), Chart (candles + volume + SMA/EMA overlays + RSI/MACD
  subplots + legend + tooltip + click-to-trade Buy/Sell popup).

**Data:**
- **LIVE:** index spots (nselib), option chains + option/future quotes
  (nselib scrape or Angel batched getMarketData — both real), stock spots
  (yfinance/angel/derive), daily candles (nselib/yfinance)
- **DERIVED:** futures on live spots (cost-of-carry at 6.5%)
- **SIM:** intraday candles, mock-option Greeks; stock spots show `unavailable` outside yfinance reach
- Nothing is ever labeled live unless it came from nselib/yfinance; uncovered requests
  return `unavailable`, never faked numbers.
- Unpriced instruments return `null` for price/P&L in API responses instead of fake simulated values.

**Tests:** 143 passing, zero network access (conftest swaps in a mock-only
manager with volatility=0 and a tmp DB; nselib exercised via fake modules,
Angel via FakeSmartApi in tests/test_angel_provider.py, macro fetchers
monkeypatched in tests/test_macro.py, verdict tested on synthetic candles
in tests/test_verdict.py, overall combiner on canned inputs in
tests/test_overall_verdict.py).

---

## Stage 19: SENSEX Index Support + Cloudflare Tunnel Deployment Prep ✅
**Status:** Complete
**Tests:** 143/143 passing
**Output:** All 5 NSE indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX) work end-to-end; Linux VM + Cloudflare Tunnel deployment ready
**Files:** backend/nse_data.py, backend/market_data.py, backend/providers/nselib_provider.py, backend/providers/angel_provider.py, backend/providers/yfinance_provider.py, backend/main.py, deploy/paper-trader.service, deploy/cloudflared-config.yml, deploy/paper-trader.env, deploy/deploy.sh, deploy/DEPLOYMENT.md

### 19a. SENSEX index added across entire provider stack
- **backend/nse_data.py:** Added to `INDEX_META` (lot=20, margin=12%) and `INDEX_SPOT` (74,300)
- **backend/market_data.py:** Added to `BASE_IV` (0.14) and `get_spot()` index check
- **backend/providers/nselib_provider.py:** Added to `INDEX_NAME_MAP` ("S&P BSE SENSEX")
- **backend/providers/angel_provider.py:** Added to `INDEX_TOKENS` (BSE, 99919000)
- **backend/providers/yfinance_provider.py:** Added to `_INDEX_TICKER_MAP` (^BSESN)
- Lot size validation works correctly for all 5 indices: NIFTY=65, BANKNIFTY=30, FINNIFTY=60, MIDCPNIFTY=120, SENSEX=20

### 19b. Cloudflare Tunnel deployment preparation (no code rewrites)
- **backend/main.py:** Added `ProxyHeadersMiddleware` (from uvicorn) + `TrustedHostMiddleware` for Cloudflare Tunnel — handles `X-Forwarded-*` headers and validates `Host` header behind the tunnel
- **deploy/paper-trader.service:** systemd service for FastAPI (runs as `paper-trader` user, secure hardening, env file from `/etc/paper-trader.env`)
- **deploy/cloudflared-config.yml:** Cloudflare Tunnel config template (ingress rules, timeouts, HTTP→VM connection)
- **deploy/paper-trader.env:** Environment variables template (Angel One credentials, no secrets committed)
- **deploy/deploy.sh:** Automated VM setup script (user, venv, deps, DB init, systemd install)
- **deploy/DEPLOYMENT.md:** Complete step-by-step deployment guide

### Verified
- All 143 tests pass
- SENSEX option chain loads live data (via nselib/Angel/yfinance)
- Order placement works for SENSEX (lot validation, margin, Greeks)
- Frontend correctly fetches lot sizes via `lotSizeOf()` → `/api/instruments`
- App loads without errors

---

## What's NOT Built (Yet)

- ❌ Alert/signal system (indicators don't trigger orders)
- ❌ Backtesting engine
- ❌ Strategy builder
- ❌ Indicator optimization
- ❌ WebSocket / SSE streaming (still 3s polling — the single biggest
  remaining latency lever)
- ❌ Incremental frontend updates (each poll re-renders whole tables)
- ❌ Stale-while-revalidate serving on the frontend
- ❌ Authentication
- ❌ Flutter mobile app
- ❌ NSE holiday calendar / trading-hours enforcement
- ❌ Stale-data threshold (STALE badge exists but nothing sets it yet)
- ❌ Order history analytics
- ❌ Chart zoom/pan; indicator period editing
- ❌ Chart pending-order visualization
- ❌ LIMIT price prefill from chart click
- ❌ F&O order entry from chart (CE/PE/FUT)
- ❌ yfinance fallback when network is down (mock removed; shows `unavailable` for stocks)
- ❌ Persist last-selected order type / quantity across sessions

## Natural Next Stages (Stage 17+)

**Option A: WebSocket/SSE push (recommended — the last big perf lever)**
- Replace 3s polling with a server push channel; incremental table updates.
- Eliminates the remaining per-tick load and gives sub-second quote updates.

**Option B: Trade Journal & Analytics**
- Trades history view with entry/exit prices; P&L per trade, win rate,
  max drawdown; CSV export.
- Integration points: `orders` table already records fills with
  `filled_price`/`filled_at`/`leg_role`/`parent_id`; a read-only `/api/journal`
  route + a new SPA page would follow the watchlist pattern.

**Option C: Market Hours & Stale-Data Enforcement**
- IST trading-hours detection (9:15–15:30, weekdays), STALE badge wiring
  (renderer exists in badges.js, nothing sets it yet), outside-hours orders
  park as PENDING.**Option D: Persist ticket preferences + bracket leg UX**
- localStorage for last order type/quantity; visualize bracket target/SL
  legs on the positions page and chart.

---

**Recommendation:** Start with **Option A (WebSocket/SSE push)** — polling
is now the dominant source of perceived slowness; everything else is polish.

**Next stage?**
