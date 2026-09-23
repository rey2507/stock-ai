# Data Sources Reference

This document describes all data sources used by the dashboard, their reliability, freshness, and fallback chains.

## Primary Data Sources

### Angel One SmartAPI
**Status**: ✅ Connected  
**Reliability**: High  
**Auth Required**: Yes (API key, client code, PIN, TOTP)  
**Rate Limit**: 5 RPS  
**Data Provided**:
- NIFTY spot LTP, open, high, low, close, change%
- NIFTY futures LTP, change%
- 5-minute OHLCV candles (volume returns 0 for index)
- Instrument discovery (futures, options tokens)
- PCR for all indices (fallback)
- WebSocket streaming for live spot updates

**Limitations**:
- `optionGreek` returns "Invalid expiry date" — format issue being investigated
- `getOIData` returns empty for index instruments
- `nseIntraday` returns empty
- Does not provide: VIX, FII/DII, macro data, sector indices

**Fallback**: None for SmartAPI-specific fields. External providers used for unsupported data.

---

### NSEIndiaApi (`nse` package)
**Status**: ⚠️ May be blocked from some networks  
**Reliability**: Medium  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- NIFTY option chain (CE/PE OI, IV, last price)
- Advances/Declines for NIFTY 50
- Expiry dates

**Limitations**:
- NSE website may block certain IP ranges
- Session cookies required for some endpoints
- Data availability varies by network

**Fallback**: Yahoo Finance for VIX/macro; BSE/MoneyControl for FII/DII

---

### Yahoo Finance (`yfinance`)
**Status**: ✅ Working  
**Reliability**: Medium  
**Auth Required**: No  
**Rate Limit**: ~2000/day (unpublished)  
**Data Provided**:
- India VIX (`^INDIAVIX`) — delayed
- Brent crude (`BZ=F`) — delayed
- WTI crude (`CL=F`) — delayed
- USD/INR (`USDINR=X`) — delayed
- US 10Y yield (`^TNX`) — delayed
- Fed rate proxy (`^IRX` 13-week T-bill) — delayed

**Limitations**:
- 5-day lookback only for historical data
- Delayed data (15-20 minutes for Indian markets)
- Ticker symbols may change

**Fallback**: NSE web for India VIX; NSE for advances/declines

---

### NSE Web Scraping (`nseindia.com`)
**Status**: ⚠️ Intermittent  
**Reliability**: Low-Medium  
**Auth Required**: Session cookies  
**Rate Limit**: Unknown  
**Data Provided**:
- India VIX (from allIndices API)
- Advances/Declines (from equity-stockIndices API)
- Option chain (from option-chain-indices API)
- Sector indices (from allIndices API)

**Limitations**:
- Requires session cookies from homepage visit
- May return 403 from some networks
- HTML structure changes break scrapers

**Fallback**: Yahoo Finance for VIX/macro; BSE/MoneyControl for FII/DII

---

### RBI Website Scraping
**Status**: ⚠️ Fragile  
**Reliability**: Low  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- RBI repo rate (from press releases)

**Limitations**:
- Regex-based HTML scraping
- Breaks if website layout changes
- No structured API

**Fallback**: Cache of last successful fetch (marked STALE)

---

### Trading Economics Scraping
**Status**: ⚠️ Fragile  
**Reliability**: Low  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- India CPI inflation
- India Manufacturing PMI

**Limitations**:
- Regex-based HTML scraping
- Breaks if website layout changes
- May require paid subscription for API

**Fallback**: Cache of last successful fetch (marked STALE)

---

### World Bank API
**Status**: ✅ Working  
**Reliability**: Medium  
**Auth Required**: No  
**Rate Limit**: Per-IP  
**Data Provided**:
- India GDP growth (quarterly, annual %)

**Limitations**:
- Quarterly frequency only
- Data lags by ~1 quarter

**Fallback**: Cache of last successful fetch (marked STALE)

---

### BSE API
**Status**: ⚠️ May return HTML  
**Reliability**: Low  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- FII/DII flows (fallback)

**Limitations**:
- May return HTML instead of JSON
- Less reliable than NSE

**Fallback**: MoneyControl API

---

### MoneyControl API
**Status**: ⚠️ May return HTML  
**Reliability**: Low  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- FII/DII flows (fallback)

**Limitations**:
- May return HTML instead of JSON
- Unofficial endpoint

**Fallback**: None (last resort)

---

### nselib
**Status**: ✅ Working  
**Reliability**: Medium  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- NIFTY 50 index data (daily OHLCV)
- Used for volume fallback

**Limitations**:
- Daily data only
- Library dependency not in requirements.txt

---

### nsefin
**Status**: ⚠️ EOD only  
**Reliability**: Low  
**Auth Required**: No  
**Rate Limit**: Unknown  
**Data Provided**:
- F&O bhavcopy (EOD)
- Futures OI (EOD only)

**Limitations**:
- End-of-day only
- Not suitable for intraday tracking
- Requires nsefin package

---

## Data Freshness Matrix

| Data | Source | Freshness | Notes |
|------|--------|-----------|-------|
| NIFTY spot | SmartAPI | 5s | Real-time via WebSocket |
| NIFTY futures | SmartAPI | 5s | Real-time |
| 5-min candles | SmartAPI | 30s | Volume returns 0 for index |
| Option chain | NSEIndiaApi | 30s | Cached |
| A/D ratio | NSEIndiaApi | 120s | Cached |
| India VIX | NSE/Yahoo | 120s | Delayed if Yahoo |
| Crude | Yahoo | 300s | Delayed |
| USD/INR | Yahoo | 300s | Delayed |
| US 10Y | Yahoo | 300s | Delayed |
| FII/DII | NSE/BSE/MC | 86400s | Daily, EOD |
| RBI rate | RBI scrape | 604800s | Weekly, cached |
| Fed rate | Yahoo/^IRX | 172800s | Cached |
| Inflation | Trading Econ | 2592000s | Monthly, cached |
| GDP | World Bank | 7776000s | Quarterly, cached |
| PMI | Trading Econ | 2592000s | Monthly, cached |
| Sector perf | NSE/BSE | 300s | Intraday |
| Futures OI | nsefin | 86400s | EOD only |

## Fallback Chains

### India VIX
1. NSE web (`/api/allIndices`) → 2. Yahoo Finance (`^INDIAVIX`)

### FII/DII Flows
1. NSE web (`/api/fiidiiTradeReact`) → 2. BSE API → 3. MoneyControl API

### Macro Data
- Each macro field has its own fallback chain
- Last successful fetch cached in `data/macro_cache.json`
- Shown as STALE when cache is used

### Advances/Declines
1. NSEIndiaApi (`advanceDecline`) → 2. NSE web (`equity-stockIndices`)

### Futures OI
1. nsefin bhavcopy (EOD only) → No intraday fallback

### Option Chain
1. NSEIndiaApi (`optionChain`) → 2. SmartAPI `getMarketData` (unreliable) → 3. NSE web scraping

## Known Issues

1. **SmartAPI WebSocket**: Connection initialization failing — under investigation
2. **optionGreek**: Expiry format mismatch — multiple formats being tried
3. **NSE blocking**: Some networks cannot access nseindia.com — fallbacks in place
4. **SmartAPI volume**: Returns 0 for index candles — using nselib fallback
5. **Futures OI**: EOD only from nsefin — no intraday source available
6. **Macro scraping**: RBI, Trading Economics scrapers are fragile to layout changes
