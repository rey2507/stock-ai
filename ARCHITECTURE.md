# Architecture — Paper-Trader (Stage 4)

## Overview

A single FastAPI process serves the JSON API and the SPA frontend. State
lives in a schema-versioned SQLite database (`data/trader.db`). Market data
flows through a **provider layer** (Stage 4): `nselib` live NSE data first,
the deterministic Black-Scholes simulation last — with caching, cooldowns,
and honest `source`/`status` metadata on every response.

## Request flow

```
Browser (SPA)
  │
  ├─ GET /            → static index.html (mounted from web/)
  ├─ /css/* /js/*     → static assets
  │
  ├─ GET  /api/watchlist      ─┐
  ├─ GET  /api/option-chain/N  │
  ├─ GET  /api/expiries/N      ├─→ MarketManager ─→ [nselib → mock]
  ├─ POST /api/orders         │     (TTL cache,     │
  ├─ GET  /api/positions      │      cooldowns,     │
  └─ GET  /api/account       ─┘      timeouts)     mock engine
        ▲                                        (BS Greeks, IV smile,
        └── app.js polls every 3s                 theta clock)
```

The **same manager** backs trading and display: positions mark against
whichever provider is serving, so chains, quotes, and P&L never disagree.
The mock provider is the terminal fallback — the trading loop works with
or without live NSE access.

## Provider layer

| Piece | Responsibility |
| --- | --- |
| `market_data_source.MarketDataSource` | ABC covering the full engine surface (spots, futures, option quotes, chains, expiries) |
| `providers/mock_provider.py` | wraps the Stage 3 `MockMarketData` (which itself implements the ABC) |
| `providers/nselib_provider.py` | live adapter: index spots from `live_index_performances()`, chains from `nse_live_option_chain()`, expiries from `expiry_dates_option_index()`; Greeks computed locally from NSE IV (smile fallback) |
| `market_data_manager.MarketManager` | priority selection, 5s/1h TTL cache, 30s error cooldown, 8s live-call timeout, `ProviderMiss` vs `ProviderError` semantics, `source`/`status`/`timestamp` metadata |
| `models.py` | normalized dataclasses (`Quote`, `OptionQuote`, `ChainRow`, `OptionChain`, `FutureQuote`, `ExpiryList`) with dict-style read access so the engine consumes models unchanged |

**Failure semantics**

- `ProviderError` (network down, schema drift, empty data) → fall through
  **and** cool the provider down 30s.
- `ProviderMiss` (per-symbol gap, e.g. stock spots) → fall through without
  cooling down — nselib simply doesn't cover it yet.
- Timeout → treated as `ProviderError` (worker thread finishes in the
  background; result discarded).

**Known nselib quirks handled**: expiry listing is `%d-%b-%Y` but
`nse_live_option_chain` parses `%d-%m-%Y` (converted); messy cell values
(`"-"`, `"1,23,456"`) normalized; missing LTP/IV fall back to Black-Scholes
pricing on the smile IV.

## Key design decisions (Stages 3–4)

- **Pure-math Greeks** — erf-based normal CDF; no scipy dependency.
- **Deterministic mock** — quotes seeded by `(key, 2s bucket)`; tests pin
  `volatility=0` and swap in a mock-only manager via conftest.
- **Theta clock** — mock premiums re-derive from an effective T shrinking
  per 45s tick (~240× compression); the manager advances it.
- **Two cash models** — prepaid instruments (EQ, long options) debit cash on
  open; margin instruments (FUT, short options) hold % margin and settle
  realized P&L. Equity adds prepaid market value **or** margin P&L per
  instrument — never both.
- **Signed positions + bracket orders** — `positions.side` ∈ {LONG, SHORT};
  brackets are parent + ENTRY/TARGET/STOPLOSS legs linked by `parent_id`,
  processed lazily on the next state read.
- **Schema versioning** — `meta.schema_version`; pre-v3 DBs reset on startup.

## Module map

| Path | Purpose |
| --- | --- |
| `backend/main.py` | FastAPI routes + static mount |
| `backend/market_data_manager.py` | provider selection, cache, cooldown, timeout |
| `backend/market_data_source.py` | provider ABC + error taxonomy |
| `backend/models.py` | normalized market data models |
| `backend/providers/` | nselib (live) + mock (simulation) |
| `backend/db.py` | SQLite schema v3, init, query helpers |
| `backend/trading_engine.py` | validation, margin, fills, SL/bracket cascade |
| `backend/greeks.py` | Black-Scholes + IV surface + expiry calendar |
| `backend/nse_data.py` | static lot sizes, margins, index metadata |

## Not in Stage 4 (explicit non-goals)

Live stock & stock-futures prices (indices only) · WebSocket push ·
historical data/backtesting · charges/brokerage · risk limits ·
multi-account · charts/analytics · Flutter.

## Stage 5+ roadmap (stubs)

- **Complete live coverage** — stock spots via `price_volume_and_deliverable_position_data`
  or a market-watch endpoint; live futures prices (derivative_data bhav);
  WebSocket push to replace 3s polling.
- **Charges & realism** — brokerage/STT/exchange fees on F&O fills;
  expiry-day ITM settlement; physical delivery warnings.
- **Analytics** — P&L attribution (spot/IV/theta), payoff charts, T+0 line.
- **Risk** — portfolio-level SPAN approximation, max-loss guardrails,
  margin-shortfall alerts.
