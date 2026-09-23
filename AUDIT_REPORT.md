# COMPREHENSIVE FEASIBILITY & ARCHITECTURE AUDIT REPORT
## NIFTY 50 Market Intelligence — Factor Intelligence & Option-Selection Analysis Layer

**Date:** 2026-09-23  
**Scope:** Audit for extending existing NIFTY 50 Market Intelligence dashboard with (a) Factor Direction Intelligence and (b) Option Selection Intelligence  
**Status:** Implementation-ready specification  
**Constraints:** No implementation code; every methodology must be explainable; every data requirement must be verified; no arbitrary scoring systems

---

## SECTION 1: EXISTING APPLICATION AUDIT

### 1.1 Architecture Overview

The application is a single-file Streamlit dashboard (`app.py`, ~720 lines) with a modular internal architecture:

```
┌──────────────────────────────────────────────────────────────────┐
│                    app.py (Streamlit)                             │
│  - Page config: wide layout                                      │
│  - Starts SmartAPI WebSocket streaming                            │
│  - Fetches all providers → merges snapshots                       │
│  - Routes to Intraday or Weekly view                              │
│  - Renders candlestick chart, verdict header, components, evidence│
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Verdict Engines                                │
│  - intraday_verdict.py: 5 components (+1/0/-1 scoring)          │
│  - weekly_verdict.py: 5 components (+1/0/-1 scoring)            │
│  - Conflict detection on primary components only                  │
│  - Threshold-based direction/state mapping                        │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Provider Layer                                 │
│  AngelProvider → SmartAPI (spot, futures, candles, WebSocket)    │
│  NSEOptionsProvider → NSEIndiaApi (option chain, OI, IV, PCR)   │
│  WebSourceProvider → NSE web + YahooFinance (VIX, crude, FX)    │
│  MacroProvider → RBI, Yahoo, WorldBank (rates, inflation, GDP)  │
│  CapitalFlowsProvider → NSE/BSE/MoneyControl (FII/DII)          │
│  SectorProvider → NSE/BSE sector indices                         │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Support Layer                                  │
│  InstrumentManager, DiagnosticEngine, DataCache, SourceRegistry, │
│  StreamingManager, MarketHours, Merger, HistoryManager           │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Data Models                                    │
│  MarketSnapshot: canonical contract (~40 FieldMeta fields)       │
│  FieldMeta: value, timestamps, source, status, quality,          │
│             diagnostics, latency                                 │
│  Verdict: direction, state, raw_score, conflict, components      │
│  ComponentResult: score, label, reason, evidence                 │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Persistence Layer                              │
│  data/snapshots/YYYY-MM-DD.json  (90-day rotation)              │
│  data/verdicts/YYYY-MM-DD.json   (365-day rotation)             │
│  data/oi_history/YYYY-MM-DD.json (90-day rotation)              │
│  data/macro_cache.json           (last successful fetch)        │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow

1. App loads → start WebSocket streaming (SmartAPI)
2. For each registered provider: call `provider.fetch()` → returns `MarketSnapshot`
3. Merge all snapshots via `merge_snapshots()` — quality-based field selection per field
4. Compute verdict via `compute_verdict(snap)` from appropriate engine
5. Save snapshot + verdict via `history_manager`
6. Render UI: verdict header → components table → evidence → charts
7. Repeat on Streamlit auto-rerun (no manual refresh button)

### 1.3 Existing Verdict System

**Intraday components** (`engines/intraday_verdict.py`):
- Momentum: VWAP position + RSI direction
- Volume: Relative volume + price direction confirmation
- Futures: Futures price change + OI change interpretation
- Options: Call/Put OI changes + PCR + ATM IV
- Participation: A/D ratio + sector breadth

**Weekly components** (`engines/weekly_verdict.py`):
- Capital Flows: FII/DII 5-day + 20-day trends
- Macro: Brent crude, USD/INR, US 10Y, Fed rate, India policy rate (simple thresholds)
- Earnings: NIFTY earnings growth
- Participation: A/D ratio + sector breadth
- Derivatives: PCR + Futures OI change

**Scoring:** Each component → +1 (bullish), 0 (neutral/mixed), -1 (bearish). Sum = raw score.

**Thresholds (both engines):**
- +4/+5 → BULLISH SETUP
- +2/+3 → BULLISH BIAS
- -1 to +1 → MIXED / WAIT
- -3/-2 → BEARISH BIAS
- -5/-4 → BEARISH SETUP

**Conflict detection:** If primary components disagree (one bullish +1, one bearish -1), override verdict to MIXED/WAIT regardless of raw score.

**Evidence:** Each `ComponentResult` stores `evidence[]` list with human-readable reasons. Verdict stores `reasons[]` summary.

### 1.4 Existing Factor Usage

Factors currently exist ONLY as static thresholds inside the Weekly Macro component:
- Brent crude: <80 supportive, >90 headwind
- USD/INR: <83.5 strong INR, >84.5 weak INR
- US 10Y: <4.2 supportive, >4.6 headwind

These are NOT dynamically assessed. There is:
- NO factor direction persistence tracking
- NO acceleration/deceleration detection
- NO reversal detection
- NO multi-timeframe factor analysis
- NO factor history storage
- NO confidence/strength weighting

### 1.5 Existing Data Layers

**Providers:**
| Provider | Data | Source | Auth | Freshness | Reliability |
|----------|------|--------|------|-----------|-------------|
| AngelProvider | Spot, futures, 5-min candles, PCR, WebSocket | SmartAPI | Yes (API key + TOTP) | 5s | High |
| NSEOptionsProvider | Option chain (OI, IV, PCR, max pain), A/D, expiry dates | NSEIndiaApi (`nse` package) | No | 30s | Medium |
| WebSourceProvider | VIX, crude, USD/INR, US 10Y, Fed rate, sector indices | NSE web + Yahoo Finance | Session cookies / No | 5-300s | Medium |
| MacroProvider | RBI repo rate, inflation, GDP, PMI, Fed rate | RBI scrape + Yahoo + WorldBank | No | 7-90 days cached | Low-Medium |
| CapitalFlowsProvider | FII/DII flows | NSE web + BSE + MoneyControl | No | Daily EOD | Low |
| SectorProvider | NSE sector indices | NSE web + BSE | No | 300s | Medium |

**Known issues:**
- SmartAPI WebSocket: connection init failing ("Invalid initialization parameters")
- SmartAPI `optionGreek`: returns "Invalid expiry date" for format `29SEP26`
- SmartAPI `getOIData`: returns empty for index
- NSE web: may return 403 from some networks
- SmartAPI volume: returns 0 for index candles; falls back to nselib (daily data, not intraday)
- Hardcoded macro fallbacks: Old audit report documents hardcoded values (6.00%, 4.50%, 5.09%, 6.5%, 56.9). **Current `macro_provider.py` code no longer has these hardcoded fallbacks** — it returns UNAVAILABLE when sources fail. This is a positive change.

### 1.6 UI Structure

**Sidebar:**
- Navigation: Intraday / Weekly radio buttons
- Data health panel (from `utils/ui_production.py`)
- No market state indicator beyond freshness banner

**Main content (Intraday):**
- Data source banner
- Verdict header (color-coded)
- Component table
- Candlestick chart (Plotly, 5-min candles + VWAP + ATR bands)
- Evidence expanders
- Diagnostics expander

**Main content (Weekly):**
- Data source banner
- Verdict header
- Component table
- Capital Flows panel
- Macro panel
- Economy panel
- Earnings panel
- Market panel (sector performance)
- Diagnostics expander

### 1.7 History/Storage

**`HistoryManager`** (`providers/history_manager.py`) already exists:
- Snapshot history: daily JSON files, 90-day rotation
- Verdict history: daily JSON files, 365-day rotation, deduplicated by score
- OI history: daily JSON files, 90-day rotation
- Macro cache: persistent JSON

**Gaps:**
- NO factor-state history
- NO Greeks/option-analysis history
- NO historical factor-direction records
- NO time-series of factor values stored

### 1.8 Testing

Current test suite: 167 tests passing
- `test_verdict.py`: Verdict engine tests with deterministic fixtures
- `test_infrastructure.py`: Provider/cache/registry tests
- `test_production.py`: Production readiness tests
- `test_acceptance.py`: Acceptance criteria
- `test_new_features.py`: New feature tests

### 1.9 Reuse Assessment

**Can extend directly:**
- Verdict scoring framework (add factor components and option components using same +1/0/-1 pattern)
- `MarketSnapshot` contract (add new FieldMeta fields for factor states, Greeks, etc.)
- `HistoryManager` (add new storage categories)
- `DataCache` (cache factor states, Greeks)
- `DataQualityEngine` (already exists, works on any MarketSnapshot)
- UI helpers (new pages using same patterns)

**Must extend:**
- `config.py` (add thresholds for factor direction)
- Provider registry (register new providers)
- `app.py` (add new pages/views)

**Must implement new:**
- Factor direction engines (direction, acceleration, persistence, reversal)
- Greeks calculator (Black-Scholes)
- Expected move calculator
- Option suitability analyzer
- Factor history storage schema

---

## SECTION 2: FACTOR-DIRECTION ANALYSIS METHODOLOGY

### 2.1 Definition

For each factor, determine a directional assessment that is:
1. **Temporally bounded** (intraday / 5-day / 20-day)
2. **Change-aware** (rising/falling/flat vs. prior period)
3. **Persistence-aware** (sustained vs. noisy)
4. **Reversal-aware** (detects direction changes)

### 2.2 Per-Factor Direction Logic

**General formula for each factor at each timeframe:**

```
1. CURRENT STATE: Latest value
2. DIRECTION: Compare current to N-period average
   - Current > N-period avg + threshold → BULLISH (from factor perspective)
   - Current < N-period avg - threshold → BEARISH
   - Else → NEUTRAL
3. ACCELERATION: Rate of change vs. prior period
   - d1 (current day change) vs d5 (5-day avg daily change)
   - d1 > d5 * 1.5 → ACCELERATING
   - d1 < d5 * 0.5 → DECELERATING
   - Else → STEADY
4. PERSISTENCE: Consecutive days in same direction
   - >= 5 days → STRONG
   - 3-4 days → MODERATE
   - 1-2 days → WEAK
5. REVERSAL: Previous direction != current direction + confirmation
   - If direction flipped AND days_in_new_direction >= 2 → REVERSAL
   - If direction flipped AND days_in_new_direction == 1 → TEMPORARY_PULLBACK
```

**Factor-specific interpretations for NIFTY:**

| Factor | Bullish for NIFTY when | Bearish for NIFTY when | Relevance |
|--------|------------------------|------------------------|-----------|
| Brent crude | Falling or stable (lower input costs) | Rising sharply (inflation/profit pressure) | Medium |
| USD/INR | Falling (INR strengthening, export competitiveness) | Rising (INR weakening, capital outflows) | Medium-High |
| US 10Y yield | Falling or stable (EM capital flows positive) | Rising sharply (EM outflows) | Medium |
| FII flows | Net buying across 5d+20d | Net selling across 5d+20d | High |
| DII flows | Net buying (domestic support) | Net selling | Medium |
| RBI rate | Stable or cutting (dovish) | Rising (hawkish, cost pressure) | Medium |
| Inflation | Stable or falling | Rising (rate hike risk) | Medium |
| PMI | >55 (expansion) | <50 (contraction) | Low-Medium |
| GDP growth | Accelerating | Decelerating | Low |

### 2.3 Factor State History Storage

Add to `HistoryManager`:
```
data/factor_states/YYYY-MM-DD.json
  [
    {
      "timestamp": "...",
      "factor": "crude",
      "timeframe": "daily",
      "direction": "BULLISH|BEARISH|NEUTRAL",
      "value": 100.50,
      "previous_value": 98.20,
      "change_pct": 2.35,
      "acceleration": "ACCELERATING|STEADY|DECELERATING",
      "persistence": "STRONG|MODERATE|WEAK",
      "reversal": true/false,
      "nifty_relevance": "POSITIVE|NEGATIVE|NEUTRAL",
      "confidence": "HIGH|MEDIUM|LOW"
    },
    ...
  ]
```

### 2.4 Reversal Detection

```
Store per factor:
  - direction_history: list of (date, direction) tuples
  - days_in_current_direction: int
  - previous_direction: str

Detect reversal:
  IF current_direction != previous_direction
    AND days_in_new_direction >= 2
    THEN reversal = TRUE, strength = STRONG
  ELIF current_direction != previous_direction
    AND days_in_new_direction == 1
    THEN reversal = TRUE, strength = TEMPORARY
  ELSE reversal = FALSE
```

### 2.5 Feasibility Assessment

**Data availability:**
- All factor data already fetched by existing providers (MacroProvider, WebSourceProvider, CapitalFlowsProvider)
- Yahoo Finance provides history for crude, USD/INR, US 10Y via `yf.Ticker(ticker).history(period="1y")`
- FII/DII: daily data available; 5-day/20-day sums calculable
- Macro cached values: already stored in `macro_cache.json` and snapshots

**What's missing:**
- Historical factor-state records (not currently stored)
- Factor direction is NOT currently calculated — only static thresholds exist
- Acceleration/persistence/reversal logic needs new code

**Risk:**
- Low risk. Factor data sources are the same as existing providers. Logic is deterministic. Can be added as new component types to existing verdict engines.

---

## SECTION 3: MULTI-TIMEFRAME METHODOLOGY

### 3.1 Timeframe Hierarchy

```
PRIMARY: Weekly (structural)
SECONDARY: Intraday (tactical)
TERTIARY: 5-day (short-term context)
```

**Design rule:** Never treat timeframes as independent. Intraday verdict must be interpreted in weekly context.

### 3.2 Factor Assessment Across Timeframes

For each factor, calculate direction at each available timeframe:

| Factor | Intraday | 5-day | Weekly (20-day) |
|--------|----------|-------|-----------------|
| NIFTY spot | ✅ (current vs VWAP, RSI) | Calculable from daily candles | Calculable from daily candles |
| Crude | Yahoo delayed (~15 min) | Yahoo 5-day history | Yahoo 20-day history |
| USD/INR | Yahoo delayed | Yahoo 5-day history | Yahoo 20-day history |
| US 10Y | Yahoo delayed | Yahoo 5-day history | Yahoo 20-day history |
| FII/DII | N/A (EOD only) | 5-day sum | 20-day sum |
| VIX | NSE/Yahoo delayed | Calculable | Calculable |

### 3.3 Timeframe Conflict Handling

```
IF intraday_factors.bullish AND weekly_factors.bearish:
  → "Medium-term headwinds; intraday rally may be corrective"
  → Primary verdict = Weekly context; intraday = tactical view

IF intraday_factors.bearish AND weekly_factors.bullish:
  → "Pullback within uptrend; medium-term structure intact"
  → Primary verdict = Weekly context; intraday = entry opportunity

IF both aligned:
  → "Timeframes confirm; higher conviction"
```

### 3.4 Data Requirements

| Timeframe | NIFTY candles | Factor history | Availability |
|-----------|--------------|----------------|--------------|
| Intraday | 5-min (today) | Current value only | ✅ Available |
| 5-day | Daily (5 days) | Yahoo 5-day history | ✅ Available |
| 20-day | Daily (20 days) | Yahoo 20-day history | ✅ Available |

**Implementation:**
- Add `compute_factor_direction(factor_values, window)` function
- Store per-factor timeframe states in verdict components
- Add `timeframe_alignment` field to Verdict dataclass

---

## SECTION 4: OPTION SELECTION INTELLIGENCE METHODOLOGY

### 4.1 What "Option Selection" Means

The goal is structural suitability, not profit prediction:

**Question answered:** "Which option contract structurally suits my market view and holding period?"

**Trade-offs exposed:**
- Theta decay vs. expected move
- Delta vs. premium cost
- Liquidity vs. tight spread
- Expiry duration vs. time-decay acceleration

### 4.2 Greeks Calculation via Black-Scholes

**Why Black-Scholes:** No live broker API for reliable Greeks. NSE provides IV but not Greeks. Black-Scholes is the standard pricing model for European options (NIFTY options are European).

**Required inputs:**
| Input | Source | Availability |
|-------|--------|--------------|
| Spot price | SmartAPI `ltpData` | ✅ Real-time |
| Strike price | NSE option chain | ✅ Intraday |
| Days to expiry | Expiry date - today | ✅ Calculable |
| Implied volatility | NSE option chain `impliedVolatility` | ✅ Available |
| Risk-free rate | India repo rate (RBI) or US 10Y proxy | ⚠️ Cached/stale |

**Implementation approach:**
Use `scipy.stats.norm.cdf` for Black-Scholes:

```python
from scipy.stats import norm
import math

def black_scholes_greeks(spot, strike, dte, iv, rate=0.065):
    if dte <= 0:
        return None
    sqrt_t = math.sqrt(dte / 252)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv**2) * dte) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    
    delta = norm.cdf(d1)  # Call delta
    gamma = norm.pdf(d1) / (spot * iv * sqrt_t)
    theta = -(spot * norm.pdf(d1) * iv) / (2 * sqrt_t * 252) - rate * strike * math.exp(-rate * dte) * norm.cdf(d2)
    theta = theta / 252  # Daily theta
    vega = spot * norm.pdf(d1) * sqrt_t / 100  # For 1% IV change
    
    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,  # negative for long options
        "vega": vega,
    }
```

**Validation:**
- Compare against NSE option chain prices (implied premium should match within spread)
- Validate delta ≈ 0.5 for ATM options
- Document model assumptions (constant IV, European exercise, no dividends)

### 4.3 Theta Calculation & Interpretation

**Formula:** Theta = ∂C/∂t (partial derivative of option price w.r.t. time)

**Key characteristics:**
- Theta is NOT linear — accelerates exponentially as expiry approaches
- ATM options have highest theta
- OTM/ITM options have lower theta but also lower delta

**Interpretation:**

```
Daily theta = -₹X per day (for long option)

If you hold for N days with unchanged spot/IV:
  Premium loss = theta × N

Required spot move to break even:
  move_needed = |theta × N| / delta

Decision:
  IF expected_move > move_needed → structurally suitable
  IF expected_move < move_needed → theta too high for expected move
```

### 4.4 Expected Move Calculation

**Formula:**
```
1σ expected move = IV × √(DTE/252) × spot
2σ expected move (95% confidence) = 2 × IV × √(DTE/252) × spot
```

**Example:**
- NIFTY spot: 24,600
- IV: 18%
- DTE: 5 days
- 1σ expected move = 0.18 × √(5/252) × 24,600 ≈ ₹495
- 2σ expected move ≈ ₹990

**Compare to option decay:**
- If theta × 5 = ₹30, expected move ₹495 >> ₹30 → favorable
- If theta × 5 = ₹200, expected move ₹495 > ₹200 but margin thin

### 4.5 Expiry Selection Methodology

**Decision framework:**

| Expiry | Theta risk | Gamma risk | Vega risk | Best for |
|--------|-----------|-----------|----------|---------|
| 1-day | Very high | High | Low | High conviction, can monitor intraday |
| 3-5 day | High | Moderate | Moderate | Swing trades |
| 20-day | Moderate | Low | High | Structural view, lower daily cost |

**Logic:**
```
FOR each candidate expiry:
  expected_move = IV * sqrt(DTE/252) * spot
  theta_cost = abs(theta) * holding_days
  vega_risk = vega * potential_iv_change
  
  IF expected_move > theta_cost + vega_risk:
    expiry = CANDIDATE
  ELSE:
    expiry = POOR FIT
```

### 4.6 Strike Selection Methodology

| Strike | Delta | Gamma | Theta | Use case |
|--------|-------|-------|-------|----------|
| ITM (0.70-0.90) | High | Low | Low | High conviction, less time decay |
| ATM (0.40-0.60) | Moderate | Highest | Highest | Balanced risk/reward |
| OTM-1 (0.30-0.40) | Moderate | High | High | Leverage, higher risk |
| OTM-2+ (0.10-0.30) | Low | Moderate | High | Extreme leverage |

**Logic:**
```
IF high_conviction AND expect_large_move:
  ITM = BETTER (lower theta, higher intrinsic)
IF moderate_move_expected:
  ATM = BETTER (balanced)
IF expect_large_move AND want_leverage:
  OTM-1 = ACCEPTABLE (with risk warning)
IF liquidity_low:
  WARN user; next liquid strike may be better
```

### 4.7 Suitability Scoring

**5-dimension explainable score (0-100 each):**

| Dimension | What it measures | Formula concept |
|-----------|-----------------|-----------------|
| Direction fit | Does delta align with market view? | 100 if delta matches view direction, 0 if opposite |
| Theta risk | Daily decay burden | 100 if theta < 0.5% of premium, 0 if theta > 3% |
| Expected move | Can move overcome decay? | 100 if expected_move >> theta_cost, 0 if expected_move < theta_cost |
| Liquidity | Can enter/exit easily | 100 if OI > threshold and spread < threshold |
| Expiry fit | Does duration suit holding period? | 100 if DTE matches plan, 0 if too short/long |

```
overall = mean(direction_fit, theta_risk, expected_move, liquidity, expiry_fit)

CLASSIFICATION:
  80-100: Suitable
  60-79: Acceptable
  40-59: Caution
  0-39: Avoid
```

**Important constraint:** Score is NOT probability of profit. It is structural suitability given current conditions.

### 4.8 Feasibility Assessment

**Data availability:**
- All inputs for Black-Scholes are available from existing providers:
  - Spot: AngelProvider ✅
  - Strike + IV + premium: NSEOptionsProvider ✅
  - DTE: calculable from expiry date ✅
  - Risk-free rate: MacroProvider (India repo rate) ⚠️ cached/stale

**What needs to be built:**
1. GreeksCalculator module: Black-Scholes implementation
2. ExpectedMoveCalculator: IV-based + historical volatility
3. OptionSuitabilityAnalyzer: 5-dimension scoring
4. Expiry/strike comparison UI: table/matrix view

**Risk:**
- Medium risk. Black-Scholes assumptions violated for Indian options (dividends, early exercise). Must be disclosed.
- Risk-free rate is stale/cached — use approximate value with disclosure.
- NSE option chain data quality varies — validate against market prices.

---

## SECTION 5: DATA AUDIT FOR NEW SYSTEMS

### 5.1 Factor Direction Data Matrix

| Factor | Current | 5-day history | 20-day history | Source | Freshness | Reliability |
|--------|---------|--------------|----------------|--------|-----------|-------------|
| Brent crude | ✅ Yahoo | ✅ `yf.Ticker.history(period="1mo")` | ✅ Same | Yahoo Finance | Delayed | Medium |
| USD/INR | ✅ Yahoo | ✅ Same | ✅ Same | Yahoo Finance | Delayed | Medium |
| US 10Y | ✅ Yahoo | ✅ Same | ✅ Same | Yahoo Finance | Delayed | Medium |
| FII/DII | ✅ EOD | ✅ Calculable from daily snapshots | ✅ Calculable | CapitalFlowsProvider | Daily | Low-Medium |
| RBI rate | ✅ Cached | ❌ Not stored | ❌ Not stored | RBI scrape | Weekly | Low |
| Inflation | ✅ Cached | ❌ Not stored | ❌ Not stored | Trading Econ | Monthly | Low |
| GDP | ✅ Cached | ❌ Not stored | ❌ Not stored | World Bank | Quarterly | Low |
| PMI | ✅ Cached | ❌ Not stored | ❌ Not stored | Trading Econ | Monthly | Low |
| NIFTY | ✅ Live | ✅ SmartAPI candles | ✅ Calculable | SmartAPI | Real-time | High |
| VIX | ⚠️ Partial | ⚠️ Partial | ⚠️ Partial | NSE/Yahoo | Delayed | Medium |
| A/D ratio | ✅ Live | ✅ Calculable | ✅ Calculable | NSEOptionsProvider | Intraday | Medium |
| Sector perf | ✅ Live | ✅ Calculable | ✅ Calculable | SectorProvider | Intraday | Medium |

**Gap:** Macro cached values (RBI, inflation, GDP, PMI) are NOT stored with timestamps in a format that enables historical time-series. The `macro_cache.json` stores the latest value with a timestamp string, but there's no time-series.

**Recommendation:** Add factor-state storage to `HistoryManager`. For factors with cached values, append to a time-series file rather than overwriting.

### 5.2 Option Selection Data Matrix

| Data | Needed? | Available? | Source | Status |
|------|---------|-----------|--------|--------|
| Spot price | YES | YES | SmartAPI | ✅ |
| Strike price | YES | YES | NSE option chain | ✅ |
| DTE | YES | YES | Calculable | ✅ |
| IV (ATM) | YES | YES | NSE option chain | ✅ |
| IV (all strikes) | YES | YES | NSE option chain | ✅ |
| Option premium | YES | YES | NSE option chain | ✅ |
| Risk-free rate | YES | Partial | MacroProvider (cached) | ⚠️ |
| Open interest | YES | YES | NSE option chain | ✅ |
| OI change | YES | YES | NSE option chain | ✅ |
| Volume | YES | Partial | NSE option chain | ⚠️ |
| Bid/ask | YES | YES | NSE option chain | ✅ |

### 5.3 Historical Data Requirements

| Data | How far back | Frequency | For what purpose | Current storage |
|------|-------------|-----------|------------------|----------------|
| NIFTY candles | 1+ year | 5-min/daily | Factor direction, expected move | ❌ Only today's in cache |
| Factor values | 6+ months | Daily | Factor direction persistence, reversal detection | ❌ Not stored |
| Option chain | 3-6 months | Daily/EOD | Option suitability backtest | ❌ Not stored |
| Verdicts | 365 days | Per refresh | Accuracy measurement | ✅ `data/verdicts/` |
| OI observations | 90 days | Intraday | OI change tracking | ✅ `data/oi_history/` |

**Critical gap:** Candle data is NOT persistently stored. Only today's candles exist in memory cache. This means:
- Cannot compute multi-day factor direction from candles
- Cannot calculate 20-day factor trends
- Cannot backtest option suitability

**Recommendation:** Add daily candle persistence to `HistoryManager`. One file per day, rotation after 365 days.

### 5.4 Caching Strategy for New Systems

Extend existing `DataCache`:

| Cache key | Data | TTL | Purpose |
|-----------|------|-----|---------|
| `factor_direction_{factor}` | Factor direction state | 300s | Avoid recalculating on every rerun |
| `greek_{symbol}_{strike}_{expiry}` | Calculated Greeks | 30s | Performance |
| `option_suitability_{view}_{expiry}` | Suitability scores | 60s | Performance |
| `candles_{token}_{date}` | Daily candles | 86400s | Historical access |

---

## SECTION 6: OPEN-SOURCE & RESEARCH FINDINGS

### 6.1 Python Libraries for Options/Greeks

| Library | Purpose | Maintained | Reliability | Notes |
|---------|---------|-----------|-------------|-------|
| `scipy.stats.norm` | Black-Scholes normal CDF/PDF | Yes | High | Built into scipy; no extra dependency |
| `numpy` | Numerical computation | Yes | High | Already in requirements |
| `mibian` | Black-Scholes Greeks | Yes | Medium | Simple API but unmaintained-ish |
| `py_vollib` | Volatility + Greeks | Yes | High | More features, more complex |
| `pandas` | Data manipulation | Yes | High | Already in requirements |

**Recommendation:** Use `scipy.stats.norm` + manual Black-Scholes implementation. No extra dependency needed. Already uses numpy.

### 6.2 Black-Scholes Validation References

- Hull, "Options, Futures, and Other Derivatives" — standard textbook
- Wikipedia: Black-Scholes formula (verified)
- NSE option chain: use implied premium to validate calculated price
- If calculated price deviates >5% from market price, flag as model assumption violation

### 6.3 Factor-to-NIFTY Relationship Validation

**Verified relationships (from existing code + market knowledge):**

| Factor | Relationship | Evidence |
|--------|-------------|----------|
| Crude | Negative to NIFTY (input cost) | Used in weekly macro scoring with thresholds |
| USD/INR | Negative (INR weakening) to NIFTY | Used in weekly macro scoring |
| US 10Y | Negative (rising yields) to EM flows | Used in weekly macro scoring |
| FII flows | Positive to NIFTY | Primary component in weekly verdict |
| PMI | Positive to NIFTY | Used in weekly macro scoring |
| Inflation | Negative to NIFTY | Used in weekly macro scoring |

**Research needed for implementation:**
- Historical correlation coefficients (can be computed from stored data once history exists)
- Time-lag analysis (does factor change precede NIFTY change by N days?)
- Regime-dependence (does correlation change in bull vs bear markets?)

---

## SECTION 7: MULTI-SYSTEM ARCHITECTURE

### 7.1 Proposed Minimal Architecture

Extend the existing architecture WITHOUT rebuilding:

```
LAYER 1: DATA COLLECTION (existing, unchanged)
├── Providers: Angel, NSEOptions, WebSource, Macro, CapitalFlows, Sector
├── Caching (DataCache)
└── MarketSnapshot contract (extend with new fields)

LAYER 2: FACTOR ANALYSIS (NEW, extends existing)
├── factor_direction.py: compute_factor_direction() for each factor
├── factor_history.py: Factor state storage/retrieval
├── Extend MarketSnapshot with factor_state fields
└── Reuse HistoryManager for persistence

LAYER 3: NIFTY SYNTHESIS (existing, extended)
├── Extend verdict engines to include factor components
├── Add multi-timeframe context to Verdict dataclass
├── Conflict detection already exists — reuse
└── Evidence generation already exists — reuse

LAYER 4: OPTION ANALYSIS (NEW, major feature)
├── greeks_calculator.py: Black-Scholes implementation
├── expected_move.py: IV-based + HV-based expected move
├── option_suitability.py: 5-dimension scoring
├── Extend MarketSnapshot with option analysis fields
└── Reuse HistoryManager for option history

LAYER 5: UI & PRESENTATION (existing, extended)
├── New page: Factor Monitor
├── New page: Option Selection
├── Enhanced verdict header with factor context
└── New comparison tables
```

### 7.2 Component Reuse Map

| Existing Component | Factor Intelligence | Option Selection |
|-------------------|---------------------|------------------|
| MarketSnapshot | Extend with factor fields | Extend with Greeks fields |
| FieldMeta | Reuse as-is | Reuse as-is |
| Verdict | Extend with factor components | Add option suitability |
| ComponentResult | Reuse as-is | Reuse as-is |
| HistoryManager | Add factor_states storage | Add option_analysis storage |
| DataCache | Add factor_direction cache | Add Greeks cache |
| DataQualityEngine | Reuse as-is | Reuse as-is |
| Config | Add factor thresholds | Add Greeks parameters |
| UI helpers | New factor monitor page | New option comparison page |

### 7.3 Implementation Phases

**Phase A — Factor Direction Intelligence (Weeks 1-2)**
- `factor_direction.py`: compute direction, acceleration, persistence, reversal
- Extend `HistoryManager` with factor_states storage
- Add factor fields to `MarketSnapshot`
- New Factor Monitor page in UI
- Success: Factor direction displayed with confidence; reversals detected

**Phase B — NIFTY Synthesis with Factors (Week 3)**
- Add FactorDirectionComponent to weekly verdict engine
- Add multi-timeframe context fields to Verdict
- Enhanced evidence panel showing factor contribution
- Success: Verdict includes factor inputs; conflicts flagged

**Phase C — Greeks Calculation (Week 4)**
- `greeks_calculator.py`: Black-Scholes implementation
- Calculate for ATM ± 2 strikes, nearest 3 expiries
- Validate against NSE market prices
- Success: Greeks calculated within 5% of theoretical values

**Phase D — Theta & Expected Move (Week 5)**
- `expected_move.py`: IV-based and HV-based expected move
- Compare expected move to theta cost
- Success: Clear "move required to profit" presented

**Phase E — Option Suitability Scoring (Week 6)**
- `option_suitability.py`: 5-dimension scoring
- Recommend/Accept/Caution/Avoid classification
- Success: Score explainable; tested against historical data

**Phase F — Option Comparison UI (Weeks 7-8)**
- Expiry vs Strike matrix table
- Comparison view
- Success: User can compare contracts efficiently

---

## SECTION 8: RISKS & FAILURE MODES

### 8.1 Data Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|-----------|
| Yahoo Finance unreliable for Indian data | Factor data gaps | Medium | Already has fallbacks; add Alpha Vantage/FRED for key macro |
| NSE web blocking | Option chain gaps | High | Already has fallbacks; add caching |
| Macro scraping fragile | Factor gaps | Medium | Show UNAVAILABLE when fail; don't use stale |
| No persistent candle history | Cannot compute factor trends | High (current gap) | **Must add candle persistence before factor analysis** |
| Risk-free rate stale | Greeks inaccurate | Medium | Use approximate value with disclosure |

### 8.2 Methodology Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|-----------|
| Factor correlation unstable | Misleading factor direction | Medium | Test across regimes; add confidence levels |
| Black-Scholes assumptions violated | Greeks wrong | High | Document assumptions; validate against NSE data; disclose |
| Theta acceleration curve inaccurate | Decay estimate wrong | Medium | Validate against actual price changes |
| Expected move vs actual mismatch | Suitability inaccurate | Medium | Track prediction accuracy; adjust |
| Suitability score arbitrary | User distrust | Low | Make score fully explainable per dimension |

### 8.3 Operational Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|-----------|
| Greeks calculation slow | UI unresponsive | Medium | Cache results; limit to ATM ± 2 strikes |
| History storage large | Disk/performance | Low | Rolling rotation (already implemented) |
| Factor direction changes frequently | User confusion | Medium | Persist previous state; show change clearly |
| Multi-timeframe conflicts confusing | Poor UX | Medium | Clear hierarchy: Weekly > Intraday; explain in UI |

---

## SECTION 9: WHAT NOT TO IMPLEMENT

**Out of scope (not in Phases A-F):**

1. **Backtesting framework** — Add after model is stable
2. **Automated alerting** — Layer on after core works
3. **Portfolio-level risk** — Single-contract analysis first
4. **News sentiment analysis** — Too complex; structured data only
5. **Machine learning** — Deterministic system sufficient
6. **Options strategy builder** — Spreads, straddles later
7. **Multi-leg orders** — Single contracts only initially
8. **Custom factor definitions** — Fixed set first
9. **Regime change detection** — Add once factor reversal works
10. **TradingView charts** — Plotly sufficient

**Specifically avoid:**
- Arbitrary scoring weights (make every dimension explicit)
- Probability claims ("70% chance of profit") — only structural suitability
- ML-based predictions
- Auto-trading or order execution

---

## SECTION 10: SUCCESS CRITERIA

### 10.1 Phase A — Factor Direction
- [ ] Factor direction computed for all 8 tracked factors
- [ ] Direction shown with confidence level (HIGH/MEDIUM/LOW)
- [ ] Reversals detected and explained
- [ ] Factor state history persisted daily
- [ ] UI: Factor Monitor page showing all factors with direction + change + status

### 10.2 Phase B — NIFTY Synthesis
- [ ] Verdict engine includes factor components
- [ ] Multi-timeframe context in Verdict dataclass
- [ ] Conflict detection between NIFTY view and factor view
- [ ] Evidence panel shows factor contribution
- [ ] All 167 tests still pass

### 10.3 Phase C — Greeks
- [ ] Delta, Gamma, Theta, Vega calculated via Black-Scholes
- [ ] Calculated for ATM ± 2 strikes, 3 nearest expiries
- [ ] Within 5% of theoretical Black-Scholes values (self-consistent)
- [ ] Performance: < 1s for full chain calculation

### 10.4 Phase D — Theta & Expected Move
- [ ] Theta displayed as ₹/day with explanation
- [ ] Expected move from IV shown (1σ and 2σ)
- [ ] Comparison: "Required move: ₹X; Expected: ₹Y"
- [ ] Decision rule clearly stated

### 10.5 Phase E — Option Suitability
- [ ] 5-dimension score for each contract
- [ ] Classification: Recommend/Accept/Caution/Avoid
- [ ] Backtested: recommended contracts outperform avoided ones (historical)
- [ ] Score breakdown shown in UI

### 10.6 Phase F — UI
- [ ] Option comparison table (expiry × strike matrix)
- [ ] Trade-off explanations (theta vs vega, delta vs gamma)
- [ ] Compact, professional layout
- [ ] All 167+ tests pass

---

## APPENDIX A: DATA GAPS THAT MUST BE FILLED FIRST

**Before Phase A (Factor Direction):**
- Add daily candle persistence to `HistoryManager`
- Add factor-state storage schema to `HistoryManager`
- Ensure Yahoo Finance history API works for 20-day lookback

**Before Phase C (Greeks):**
- Add `scipy` to `requirements.txt`
- Validate Black-Scholes against NSE option chain prices
- Determine risk-free rate source (approximate 6.5% with disclosure)

**Before Phase E (Suitability):**
- Historical option chain snapshots (daily, 3-6 months minimum)
- Historical NIFTY realized moves (for backtest validation)

---

## APPENDIX B: EXISTING STRENGTHS TO PRESERVE

1. **Canonical MarketSnapshot** — Do not break; extend with new fields
2. **Provider isolation** — Each provider independently fetches; new providers follow same pattern
3. **FieldMeta provenance** — Every field carries source, timestamps, status, diagnostics
4. **Diagnostic engine** — 7-check workflow for nil responses
5. **Conflict detection** — Primary component disagreement flagging
6. **Graceful degradation** — No crashes on provider failure
7. **History manager** — Already exists, already has daily rotation; just extend
8. **Test fixtures** — Deterministic fixtures; add new ones for factor/option tests

---

## APPENDIX C: KEY DECISIONS REQUIRED FROM USER

| Decision | Options | Recommendation |
|----------|---------|---------------|
| Risk-free rate for Black-Scholes | RBI repo rate (cached) / 6.5% fixed / US 10Y proxy | Use 6.5% with explicit disclosure |
| Factor history depth | 30 days / 90 days / 365 days | 365 days (same as verdict history) |
| Greeks calculation scope | ATM only / ATM ± 1 / ATM ± 2 | ATM ± 2, 3 nearest expiries |
| Suitability score weights | Equal / customizable / fixed | Equal weights, each explicit |
| Backtest minimum history | 1 month / 3 months / 6 months | 3 months before claiming validation |
| Factor direction timeframes | Intraday + daily / daily + weekly | Daily + weekly; intraday factors unavailable for most |

---

**End of Audit Report**
