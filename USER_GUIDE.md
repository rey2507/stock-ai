# User Guide

## Dashboard Overview

The NIFTY 50 Market Intelligence Dashboard provides real-time and contextual market data for NIFTY 50 index trading. It aggregates data from multiple sources (Angel One SmartAPI, NSE, Yahoo Finance, etc.) and produces a structured verdict on market direction and confidence.

## Pages

### 1. Intraday (Default)

**Purpose**: Real-time market state and intraday trading context

**Sections**:
- **Market**: NIFTY spot, futures, change%, India VIX
- **Price Action**: Candlestick chart with VWAP and ATR bands
- **Dashboard State**: Verdict (direction + state), component scores, evidence
- **Verdict History**: Last 10 verdict changes with deltas
- **Futures**: Futures OI, OI change, price/OI relationship
- **Options**: Call/Put OI, OI change, PCR, ATM IV, Max Pain
- **Volume**: NIFTY volume (EOD), options volume (intraday), relative volume
- **Participation**: Advances, Declines, A/D ratio, sector performance
- **Momentum**: VWAP, RSI, volume vs average, price above VWAP

**How to interpret**:
- Green verdict = bullish conditions
- Red verdict = bearish conditions
- Orange verdict = mixed/wait
- Score range: -5 (strong bearish) to +5 (strong bullish)
- Conflict flag = primary components disagree

### 2. Weekly

**Purpose**: Structural/weekly market context

**Sections**:
- **Capital Flows**: FII/DII flows (1d, 5d, 20d, monthly)
- **Macro**: Brent crude, USD/INR, US 10Y, Fed rate, India policy rate
- **Economy**: CPI inflation, GDP growth, PMI
- **Earnings**: NIFTY earnings growth
- **Volume**: NIFTY volume (EOD), options volume (intraday)
- **Dashboard State**: Weekly verdict components
- **Market**: A/D ratio, advances, declines, sector performance
- **Derivatives**: PCR, futures OI change, ATM IV

**How to interpret**:
- Weekly verdict uses longer-term data (capital flows, macro, earnings)
- Macro component is sensitive to data availability — may show PARTIAL
- Capital flows show institutional money movement

### 3. Expiry

**Purpose**: Option chain analysis for current expiry

**Sections**:
- **Expiry Overview**: ATM strike, PCR, Max Pain, ATM IV
- **Option Chain Heatmap**: Strike-by-strike OI and IV with arrows for OI changes
- **OI Changes**: Call/Put OI change totals
- **Options Volume**: Total CE + PE volume from option chain

**How to interpret**:
- ◆ marks ATM strike
- ↑ = OI increasing (new positions)
- ↓ = OI decreasing (unwinding)
- Max Pain = strike where most options expire worthless
- PCR > 1.2 = put writing (bullish), PCR < 0.8 = call writing (bearish)

## Understanding Data Freshness

Every metric shows its data age:

| Indicator | Meaning |
|-----------|---------|
| `[LIVE]` | Data < 10 seconds old |
| `[LIVE — 3s]` | Data 3 seconds old |
| `[DELAYED — 85s]` | Data 85 seconds old |
| `[STALE]` | Data from cache (age varies) |
| `UNAVAILABLE` | No data available |

**Why it matters**:
- Stale data can mislead trading decisions
- Macro data is often STALE (cached for days/weeks)
- Options volume is LIVE (from option chain)
- NIFTY volume is EOD (from nselib)

## Understanding Verdict Scores

Each component scores +1 (bullish), 0 (neutral), or -1 (bearish).

**Intraday Components**:
1. **Momentum** (primary): VWAP position + RSI direction
2. **Futures** (primary): Price change + OI change relationship
3. **Options**: Call/Put OI change + PCR + IV
4. **Volume**: Relative volume + price direction confirmation
5. **Participation** (primary): A/D ratio + sector breadth

**Weekly Components**:
1. **Capital Flows** (primary): FII/DII trend across timeframes
2. **Macro** (primary): Crude, USD/INR, US yields, rates
3. **Earnings** (primary): NIFTY earnings growth
4. **Participation**: A/D ratio + sector performance
5. **Derivatives**: PCR + futures OI change

**Conflict Detection**:
When primary components disagree (e.g., Momentum bullish but Futures bearish), the verdict is overridden to MIXED/WAIT regardless of raw score.

## Sidebar Information

- **Market**: Current session (OPEN/CLOSED/PRE-MARKET/CLOSING) with time to next event
- **Data Health**: Overall quality (Good/Partial/Poor) with provider count
- **Stream**: WebSocket status (CONNECTED/DISCONNECTED/RECONNECTING)
- **Data Sources**: Which sources provided each field (NSE, Yahoo, BSE, etc.)
- **Navigation**: Switch between Intraday, Weekly, Expiry

## Common Scenarios

### "No live data available"
- Check Market status in sidebar
- Verify Angel One credentials in `.env`
- Check if market is open (holidays show CLOSED)
- Check Data Health panel for provider failures

### "WebSocket unavailable; using polling"
- SmartAPI WebSocket initialization is being retried
- App continues with REST API polling as fallback
- Data freshness may be slightly reduced

### "NSE website blocked from your location"
- NSE web requests are failing
- App is using fallback sources (BSE, MoneyControl, Yahoo)
- Check Data Sources panel for actual source used

### Macro fields showing "STALE"
- External source (RBI, Trading Economics, World Bank) is unreachable
- App is showing last cached value with age indicator
- This is transparent — you know the data is not current

### Volume shows "EOD"
- NIFTY volume comes from nselib (daily data)
- This is not intraday volume — it's yesterday's close
- Options volume is intraday (from option chain)

## Tips

1. **Refresh manually**: Click browser refresh or Ctrl+R to force data update
2. **Check diagnostics**: Expand "Diagnostics" in each page for pipeline status
3. **Monitor conflicts**: Conflict flag means primary components disagree — wait for confirmation
4. **Trust freshness**: A STALE macro field should not drive trading decisions
5. **Use verdict history**: Shows how market state evolved during the session

## Limitations

- This is a **research tool only**, not financial advice
- Data accuracy depends on external sources (Angel One, NSE, Yahoo Finance)
- Macro data may be delayed or stale
- Futures OI is EOD-only (not real-time)
- India VIX may be delayed when sourced from Yahoo Finance
- Not all SmartAPI endpoints are functional (optionGreek, getOIData)
