# Paper-Trader (Stage 4)

A paper-trading platform for Indian markets (NSE F&O). Stage 4 adds a
**provider-based market data layer**: live NSE data via `nselib` with
automatic fallback to the Stage 3 simulation.

Trading features (from Stages 2–3):

- **Options** — Black-Scholes pricing with full Greeks (Δ, Γ, Ν, Θ, ρ);
  live chains carry real OI/IV/volume, the simulated surface adds skew
- **Futures** — index + stock futures, realistic NSE lot sizes,
  SPAN-like % margin (live spots; prices derived via cost-of-carry)
- **Order types** — MARKET, LIMIT, STOP-LOSS (auto-trigger), and
  BRACKET (entry + target + stop-loss legs with cascade close)
- **SPA frontend** — watchlist → option chain → trade modal → positions →
  account, token-based design system, light/dark themes
- **70 pytest tests** — Greeks math, provider fallback/caching/normalization,
  lot/margin rules, SL triggers, bracket cascades, watchlist CRUD

## Market data (Stage 4)

Providers are tried in priority order; failures cool a provider down for
30s and the next one takes over — the mock provider is always last:

| # | Provider | Serves | Status label |
| --- | --- | --- | --- |
| 1 | `nselib` (live NSE) | index spots + change %, option chains (real OI/IV/volume), expiries | `live` |
| — | nselib (derived) | futures prices from live spot via cost-of-carry | `derived` |
| 2 | `mock` (simulation) | everything: full F&O surface, deterministic buckets | `simulated` |

Every market-data response carries `source`, `status`, and `timestamp` so
the UI/tests can tell what they are looking at. Gaps fall through **without**
punishing the provider (`ProviderMiss`, e.g. stock spots have no live source
yet); hard failures cool it down (`ProviderError`). Live calls run in a
worker pool with an 8s timeout so a hung NSE request can't block a route.

`GET /api/market-status` shows the provider stack health, cooldowns, and
last errors.

NSE rate limits (~1 req/s) are handled by a TTL cache: quotes/chains 5s,
expiries 1h.

> nselib quirk handled internally: `expiry_dates_option_index()` returns
> `%d-%b-%Y` dates while `nse_live_option_chain()` parses `%d-%m-%Y` — the
> provider converts between the two formats.

No external market data? Delete `nselib` from requirements (or uninstall it)
and the app runs fully simulated, exactly as Stage 3.

## Requirements

- Python 3.10+ (developed on 3.13)
- `nselib` + `pandas` for live NSE data (optional but in requirements.txt;
  the app degrades gracefully to the simulation without them)
- Greeks use only `math` (erf-based normal CDF ≈ `scipy.stats.norm.cdf`)

## Setup (first time)

From the project root (`paper-trader/`):

```bash
python -m venv .venv
```

Activate the virtual environment:

| Shell                | Command                            |
| -------------------- | ---------------------------------- |
| PowerShell (Windows) | `.venv\Scripts\Activate.ps1`       |
| Git Bash (Windows)   | `source .venv/Scripts/activate`    |
| macOS / Linux        | `source .venv/bin/activate`        |

Install dependencies:

```bash
pip install -r requirements.txt
```

## Initialise the database

`data/trader.db` is created automatically on server startup, but you can
create/refresh it explicitly:

```bash
python -c "from backend.db import init_db; init_db()"
```

The schema is **versioned** (`meta.schema_version = 3`). A pre-v3 database
(from Stage 2) is detected and **reset** on first run — paper positions do
not survive the upgrade. Delete `data/trader.db` any time to reset the
simulation to ₹10,00,000.

## Run the server

```bash
uvicorn backend.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000>.

## Run the tests

From the project root:

```bash
pytest tests/
```

## API overview

| Method & path | Purpose |
| --- | --- |
| `GET /api/health` | liveness probe |
| `GET /api/market-status` | provider stack diagnostics (health, cooldowns, errors) |
| `GET /api/watchlist` · `POST /api/watchlist` · `DELETE /api/watchlist/{symbol}` | watchlist CRUD with live quotes (`source`/`status` per row) |
| `GET /api/instruments?kind=index\|stock` | F&O underlyings (lot size, margin %) |
| `GET /api/expiries/{symbol}` | expiry ladder + futures curve (live or simulated) |
| `GET /api/option-chain/{symbol}?expiry=YYYY-MM-DD` | strike grid: CE/PE LTP, IV, OI, Greeks |
| `POST /api/orders` | place MARKET / LIMIT / SL / BRACKET (EQ, FUT, CE, PE) |
| `GET /api/orders` · `DELETE /api/orders/{id}` | order book / cancel (brackets cascade) |
| `GET /api/positions` | open positions marked to market, per-position Greeks |
| `GET /api/account` | cash, available funds, margin used, equity, portfolio Greeks |

### Try the trading loop

```bash
# ATM option chain for the near expiry
curl http://127.0.0.1:8000/api/option-chain/NIFTY

# Buy 1 lot (75) of a NIFTY future — margin is held, not debited
curl -X POST http://127.0.0.1:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol":"NIFTY","instrument_type":"FUT","side":"BUY","quantity":75}'

# Bracket order on equity: entry + target + stop-loss legs
curl -X POST http://127.0.0.1:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol":"SBIN","side":"BUY","quantity":100,"order_type":"BRACKET",
       "target_price":810,"stoploss_price":780}'

# Positions with Greeks, and the account with portfolio Greeks
curl http://127.0.0.1:8000/api/positions
curl http://127.0.0.1:8000/api/account
```

Quantity rules: F&O orders must be whole lots (NIFTY 75, BANKNIFTY 30,
RELIANCE 250, …); equity stays unit-based. Options need `expiry` +
`strike`; futures default to the near-month contract.

## Project structure

```
paper-trader/
├── backend/
│   ├── main.py                 # FastAPI routes + static mount
│   ├── db.py                   # SQLite schema v3, init, query helpers
│   ├── market_data_source.py   # MarketDataSource ABC + ProviderError/Miss
│   ├── market_data_manager.py  # fallback, TTL cache, cooldowns, timeouts
│   ├── models.py               # normalized Quote/OptionQuote/ChainRow/…
│   ├── market_data.py          # MockMarketData (simulation engine)
│   ├── greeks.py               # Black-Scholes, IV smile, expiry calendar
│   ├── trading_engine.py       # validation, margin, fills, SL/bracket logic
│   ├── nse_data.py             # static NSE metadata (lots, margins, index spots)
│   └── providers/
│       ├── mock_provider.py    # wraps MockMarketData as a provider
│       └── nselib_provider.py  # live NSE adapter (chains, indices, expiries)
├── data/
│   └── trader.db          # SQLite (schema-versioned; reset to upgrade)
├── web/                   # SPA (unchanged in Stage 4)
├── tests/
│   ├── conftest.py        # throwaway DB + mock-only manager per test
│   ├── test_health.py
│   ├── test_options.py    # Black-Scholes, boundaries, IV surface, expiries
│   ├── test_futures.py    # lot sizes, margin math, contango curve
│   ├── test_orders.py     # fills, funds, SL triggers, bracket cascades
│   ├── test_watchlist.py  # CRUD, quotes, portfolio Greeks
│   └── test_market_data.py# fallback, cooldown, cache, nselib normalization
├── requirements.txt
├── README.md
└── ARCHITECTURE.md        # design notes and Stage 5+ roadmap
```

## What is NOT in Stage 4

Live stock/stock-futures prices (indices only for now) · WebSocket push
(3s polling) · real historical data / backtesting · charges/brokerage ·
risk limits · multi-account · charts/analytics · Flutter/mobile.
See `ARCHITECTURE.md` for the Stage 5+ roadmap.
