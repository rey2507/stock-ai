# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-09-22

### Added

- **Persistent JSON history** (`providers/history_manager.py`)
  - Snapshot history: daily files in `data/snapshots/`
  - Verdict history: daily files in `data/verdicts/` (only stores when score changes)
  - OI observation history: daily files in `data/oi_history/`
  - Macro cache: `data/macro_cache.json` for last successful macro fetch
  - 90-day rotation for snapshots/OI, 365-day rotation for verdicts

- **Verdict history panel** (`utils/history_ui.py`)
  - Shows last 10 verdict changes in Intraday view
  - Color-coded by direction (green=bullish, red=bearish, orange=mixed)
  - Displays delta from previous score

- **"What Changed" summary** (`utils/history_ui.py`)
  - Shows component score changes when verdict changes
  - Displays evidence for current verdict
  - Auto-shows only when score differs from previous

- **Per-field freshness indicators** (`utils/ui.py`)
  - All metrics now show `[LIVE — Xs]`, `[STALE]`, or `[DELAYED — Xs]`
  - Color-coded by freshness state
  - UNAVAILABLE fields clearly marked

- **Expiry page** (`utils/expiry_ui.py`)
  - New sidebar navigation option: "Expiry"
  - ATM Strike, PCR, Max Pain, ATM IV overview
  - Option chain heatmap: ATM ± 4 strikes with OI/IV
  - OI change arrows (↑/↓) per strike
  - Total options volume display

- **Candlestick charting** (`app.py`)
  - Interactive Plotly candlestick chart with VWAP line
  - ATR bands (upper/lower) as dotted lines
  - Volume bars below price action
  - Hover tooltips with OHLCV data
  - 500px height, responsive width

- **Volume metrics clarification** (`app.py`)
  - NIFTY Volume (EOD) — sourced from nselib daily data
  - Options Volume (Intraday) — sourced from NSE option chain
  - Relative Volume — from candle data
  - Each labeled with its data source

- **NSE blocking diagnostics** (`providers/capital_flows_provider.py`, `providers/web_source.py`)
  - Logs each attempted source before calling
  - Logs success/failure with HTTP status
  - Records attempted sources in SourceRegistry
  - Sidebar shows actual data sources used for each field

### Fixed

- **WebSocket streaming initialization** (`providers/smartapi_client.py`, `providers/streaming.py`)
  - Stores JWT token from session response (was using feed_token for auth_token)
  - Added validation before WebSocket creation
  - Added 5-second stabilization delay before first connection
  - Added explicit error handling with diagnostic output
  - Sidebar shows: 🟢 CONNECTED, 🟡 POLLING, or 🔴 FAILED

- **optionGreek expiry format** (`providers/smartapi_client.py`)
  - Tries multiple expiry formats: `29SEP26`, `29-SEP-26`, `2026-09-29`, `29SEP26`
  - Logs which format succeeds
  - Falls back to NSE `impliedVolatility` if SmartAPI Greeks fail

- **Hardcoded macro fallbacks** (`providers/macro_provider.py`)
  - Removed all hardcoded values: 6.00%, 4.50%, 5.09%, 6.5%, 56.9
  - When source fails: returns UNAVAILABLE with diagnostic message
  - When cache exists: returns STALE with age indicator
  - Last successful fetch persisted to `data/macro_cache.json`
  - Weekly verdict excludes UNAVAILABLE/STALE fields from Macro scoring
  - Flags PARTIAL if > 2 macro inputs missing

- **Persistent history** (`providers/history_manager.py`)
  - Snapshots saved every refresh
  - Verdicts saved only when score changes
  - OI observations saved with timestamp
  - Auto-rotation: 90 days for snapshots/OI, 365 days for verdicts

### Changed

- **Weekly verdict Macro scoring** (`engines/weekly_verdict.py`)
  - Now handles partial data gracefully
  - Excludes UNAVAILABLE fields from scoring
  - Flags PARTIAL when > 2 inputs missing

- **Volume calculation** (`providers/nse_options_provider.py`, `app.py`)
  - Added `total_option_volume` to MarketSnapshot
  - Computed from option chain last prices (CE + PE)
  - Displayed with clear source labels

- **Source tracking** (`providers/web_source.py`, `providers/capital_flows_provider.py`)
  - Each field now carries its actual source (NSE, BSE, YahooFinance, etc.)
  - Fallback chain explicitly logged
  - Sidebar shows per-provider health and last fetch time

### Deprecated

- Hardcoded fallback values in `macro_provider.py` (removed)
- SmartAPI `getOIData` for futures OI (moved to nsefin)
- SmartAPI `nseIntraday` for advances/declines (moved to NSEOptionsProvider)

### Security

- No changes to authentication or credential handling
- JWT token now properly stored for WebSocket auth (was previously using feed_token incorrectly)

## [1.0.0] - 2026-09-01

### Added

- Initial NIFTY 50 Market Intelligence Dashboard
- Angel One SmartAPI integration
- Multi-provider architecture (Angel, NSE, WebSource, Macro, CapitalFlows, Sector)
- Canonical MarketSnapshot data contract
- Intraday and Weekly verdict engines
- Conflict detection for primary components
- Diagnostic engine for nil SmartAPI responses
- WebSocket streaming for NIFTY spot
- Market-hours awareness
- Source provenance tracking
