# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Streamlit App (app.py)                      │
│  - Page config: wide layout                                        │
│  - WebSocket streaming on load                                      │
│  - Fetches all providers → merges snapshots                         │
│  - Routes to Intraday / Weekly / Expiry view                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Verdict Engines                                │
│  - intraday_verdict.py: 5 components (+1/0/-1 scoring)             │
│  - weekly_verdict.py: 5 components (+1/0/-1 scoring)               │
│  - Conflict detection for primary components                        │
│  - Threshold-based direction/state mapping                          │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Provider Layer                                 │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────────────┐     │
│  │ AngelProvider │  │ NSEOptionsProv│  │ WebSourceProvider    │     │
│  │ - SmartAPI    │  │ - NSEIndiaApi │  │ - NSE web scraping   │     │
│  │ - Spot/Futures│  │ - Option chain│  │ - Yahoo Finance      │     │
│  │ - Candles     │  │ - OI/IV/PCR   │  │ - VIX, crude, FX     │     │
│  │ - WebSocket   │  │ - A/D ratio   │  │ - US 10Y yield       │     │
│  └──────────────┘  └───────────────┘  └─────────────────────┘     │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────────────┐     │
│  │ MacroProvider│  │ CapitalFlowsPr│  │ SectorProvider       │     │
│  │ - RBI rate   │  │ - NSE FII/DII │  │ - NSE sector indices │     │
│  │ - Fed proxy  │  │ - BSE fallback│  │ - BSE fallback       │     │
│  │ - Inflation  │  │ - MoneyControl│  │                      │     │
│  │ - GDP (WB)   │  │               │  │                      │     │
│  │ - PMI        │  │               │  │                      │     │
│  └──────────────┘  └───────────────┘  └─────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Support Layer                                    │
│                                                                     │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────┐    │
│  │ InstrumentMgr  │  │ DiagnosticEng  │  │ StreamingManager   │    │
│  │ - Token discov │  │ - 7-check nil │  │ - WebSocket thread │    │
│  │ - ATM detect   │  │   diagnostic  │  │ - Tick buffer      │    │
│  │ - Option chain │  │ - Retry logic │  │ - Health monitor   │    │
│  └────────────────┘  └────────────────┘  └───────────────────┘    │
│                                                                     │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────────┐    │
│  │ DataCache      │  │ SourceRegistry │  │ HistoryManager     │    │
│  │ - In-memory    │  │ - Provider    │  │ - Snapshot JSON    │    │
│  │ - Freshness    │  │   stats       │  │ - Verdict JSON     │    │
│  │ - Windowed TTL │  │ - Provenance  │  │ - OI history JSON  │    │
│  └────────────────┘  └────────────────┘  └───────────────────┘    │
│                                                                     │
│  ┌────────────────┐  ┌────────────────┐                            │
│  │ MarketHours    │  │ Merger         │                            │
│  │ - NSE sessions │  │ - Multi-snap   │                            │
│  │ - Holidays     │  │   merge        │                            │
│  │ - Freshness adj│  │ - Quality pick │                            │
│  └────────────────┘  └────────────────┘                            │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Data Models                                    │
│                                                                     │
│  MarketSnapshot: Canonical contract (38 fields, all FieldMeta)     │
│  FieldMeta: value, timestamps, source, status, quality, diagnostics│
│  Verdict: direction, state, raw_score, conflict, quality           │
│  ComponentResult: score, label, reason, evidence                   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Persistence Layer                              │
│                                                                     │
│  data/snapshots/YYYY-MM-DD.json   (90-day rotation)               │
│  data/verdicts/YYYY-MM-DD.json    (365-day rotation)              │
│  data/oi_history/YYYY-MM-DD.json  (90-day rotation)               │
│  data/macro_cache.json            (last successful macro fetch)    │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### Intraday Refresh Cycle

```
1. App loads → Start WebSocket stream (5s delay)
2. Fetch all providers in parallel:
   - AngelProvider → SmartAPI (spot, futures, candles)
   - NSEOptionsProvider → NSEIndiaApi (option chain, A/D)
   - WebSourceProvider → YahooFinance + NSE web (VIX, crude, etc.)
   - MacroProvider → RBI, Yahoo, WorldBank (macro)
   - CapitalFlowsProvider → NSE/BSE/MoneyControl (FII/DII)
   - SectorProvider → NSE/BSE (sector indices)
3. Merge snapshots → pick best quality per field
4. Compute verdict → Intraday engine (5 components)
5. Save snapshot + verdict to disk
6. Render UI with freshness indicators
7. Repeat on Streamlit rerun (~5-30s interval)
```

### Field Provenance

Every field in MarketSnapshot carries:
- `value`: The actual data
- `source`: Provider name (AngelBroking, NSEOptions, WebSource, etc.)
- `source_endpoint`: Specific API method used
- `observed_at`: When data was observed at source
- `fetched_at`: When we fetched it
- `freshness_seconds`: Age of data
- `status`: LIVE | DELAYED | STALE | UNAVAILABLE | HISTORICAL
- `quality`: GOOD | PARTIAL | INVALID
- `diagnostic_reason`: Why field has this status
- `diagnostic_message`: Human-readable explanation
- `provider_latency_ms`: Time for provider to respond
- `retry_count`: Number of retries attempted

## Key Design Decisions

### 1. Canonical MarketSnapshot Contract
All providers produce the same MarketSnapshot structure. Consumers (UI, verdict engines) never talk to providers directly. This enables:
- Easy addition of new providers
- Consistent field access patterns
- Centralized provenance tracking

### 2. Provider Isolation
Each provider independently fetches, parses, and normalizes. Failures are contained within the provider. The merger picks the best available value per field.

### 3. Graceful Degradation
No provider failure crashes the system. Fields are marked UNAVAILABLE with diagnostic reasons. The verdict engine handles missing data by scoring 0 for that component.

### 4. No Hardcoded Values
Macro provider no longer uses hardcoded fallbacks. When sources fail, fields show UNAVAILABLE. Last successful values are cached and shown as STALE with age indicator.

### 5. File-Based Persistence
Simple JSON files for history. No database required. Daily rotation keeps file sizes manageable. Thread-safe writes via lock.

### 6. Diagnostic-First Approach
When SmartAPI returns nil, the diagnostic engine investigates WHY before marking unavailable. Never silently fail.

## Directory Structure

```
Nifty 50 verdict/
├── app.py                          # Streamlit entry point
├── config.py                       # Constants, thresholds
├── metric_registry.py              # Metric definitions
├── requirements.txt                # Python dependencies
├── .env                            # Credentials (git-ignored)
├── .env.example                    # Credential template
├── AUDIT_REPORT.md                 # Pre-implementation audit
├── CHANGELOG.md                    # Version history
├── ARCHITECTURE.md                 # This file
├── DATA_SOURCES.md                 # Data source reference
├── USER_GUIDE.md                   # End-user documentation
├── TROUBLESHOOTING.md              # Common issues
├── opt-expiry.json                 # Hardcoded expiry (legacy)
├── data/                           # Persistent storage
│   ├── snapshots/                  # Daily snapshot JSON files
│   ├── verdicts/                   # Daily verdict JSON files
│   ├── oi_history/                 # Daily OI observation files
│   └── macro_cache.json            # Last successful macro fetch
├── logs/                           # Application logs
│   └── YYYY-MM-DD/
│       └── app.log
├── models/                         # Data contracts
│   ├── snapshot.py                 # FieldMeta, MarketSnapshot
│   ├── verdict.py                  # Verdict, ComponentResult
│   ├── field_status.py             # FieldStatus, DiagnosticReason
│   ├── source_policy.py            # Field-level policies
│   └── __init__.py
├── providers/                      # Data providers
│   ├── base.py                     # BaseProvider ABC
│   ├── registry.py                 # Provider registry
│   ├── merger.py                   # Snapshot merger
│   ├── angel_provider.py           # SmartAPI primary provider
│   ├── smartapi_client.py          # Low-level SmartAPI wrapper
│   ├── nse_options_provider.py     # NSE option chain
│   ├── web_source.py               # NSE web + YahooFinance
│   ├── macro_provider.py           # Macro indicators
│   ├── capital_flows_provider.py   # FII/DII flows
│   ├── sector_provider.py          # Sector indices
│   ├── instrument_manager.py       # Token discovery
│   ├── cache.py                    # In-memory cache
│   ├── source_registry.py          # Provider stats/provenance
│   ├── history_manager.py          # Persistent history (NEW)
│   ├── streaming.py                # WebSocket manager
│   ├── ws_health.py                # WebSocket health monitor
│   ├── diagnostic_engine.py        # 7-check nil diagnostic
│   ├── conflict_detector.py        # Conflict detection
│   ├── data_quality.py             # Data quality assessment
│   ├── oi_model.py                 # OI timeseries + aggregation
│   └── __init__.py
├── engines/                        # Verdict engines
│   ├── intraday_verdict.py         # 5-component intraday scoring
│   ├── weekly_verdict.py           # 5-component weekly scoring
│   └── __init__.py
├── utils/                          # UI utilities
│   ├── ui.py                       # Shared UI helpers
│   ├── ui_production.py            # Production sidebar
│   ├── history_ui.py               # Verdict history panel (NEW)
│   ├── expiry_ui.py                # Expiry page (NEW)
│   ├── market_hours.py             # NSE session tracking
│   └── __init__.py
├── tests/                          # Test suite
│   ├── fixtures.py                 # Deterministic test data
│   ├── test_verdict.py             # Verdict engine tests
│   ├── test_infrastructure.py      # Infrastructure tests
│   ├── test_production.py          # Production readiness tests
│   ├── test_acceptance.py          # Acceptance criteria tests
│   ├── test_new_features.py        # New feature tests (NEW)
│   └── __init__.py
├── __pycache__/
├── .pytest_cache/
└── .kilo/
```

## Threading Model

- **WebSocket thread**: daemon thread for SmartAPI streaming
- **Streamlit reruns**: Each user interaction triggers a full rerun
- **Provider fetch**: Sequential in app.py (parallelism could be added)
- **Cache/SourceRegistry**: Thread-safe with locks
- **HistoryManager**: Thread-safe with locks for file writes

## Caching Strategy

| Layer | Cache | TTL | Purpose |
|-------|-------|-----|---------|
| DataCache | In-memory | 5-120s | Reduce API calls |
| history_manager | JSON files | 90-365 days | Persistence |
| macro_cache | JSON file | 7-90 days | Last known values |
| instrument_option_chain | In-memory | 30s | Reduce token lookups |

## Extension Points

1. **New provider**: Inherit `BaseProvider`, implement `fetch() → MarketSnapshot`
2. **New metric**: Add to `metric_registry.py`, update provider fetch
3. **New verdict component**: Add scoring function in engine
4. **New UI page**: Add sidebar option, create render function
5. **New data source**: Create provider or extend existing one
