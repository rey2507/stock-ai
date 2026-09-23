# Troubleshooting

## Common Issues and Solutions

### 1. WebSocket Connection Failing

**Symptoms**:
- Logs show: `SmartWebSocketV2 init failed: Invalid initialization parameters`
- Sidebar shows: 🔴 Connection Failed
- Data freshness is slightly reduced

**Cause**:
SmartAPI WebSocket initialization requires JWT token from session response. Previous code used feed_token for both auth_token and feed_token.

**Fix Applied**:
- JWT token is now extracted from `generateSession` response
- 5-second stabilization delay added before connection
- Explicit validation of all credentials before WebSocket creation

**If still failing**:
1. Check `.env` has all 4 Angel One credentials
2. Verify Angel One session is active (try logging into Angel One app)
3. Check if feed_token is being generated (look for "SmartAPI session established" in logs)
4. If persistent, app falls back to REST API polling automatically

---

### 2. optionGreek Returns "Invalid expiry date"

**Symptoms**:
- ATM IV shows UNAVAILABLE
- Logs show: `optionGreek failed: Invalid expiry date`

**Cause**:
SmartAPI `optionGreek` endpoint is sensitive to expiry date format. The app now tries multiple formats (`29SEP26`, `29-SEP-26`, `2026-09-29`).

**Fix Applied**:
- Multiple expiry formats are tried automatically
- Falls back to NSE option chain `impliedVolatility` field
- ATM IV now shows source: "SmartAPI Greeks" or "NSE Option Chain"

**If still failing**:
- NSE option chain IV is used as fallback (already working)
- Check logs for which format succeeded

---

### 3. Macro Fields Showing Hardcoded Values

**Symptoms**:
- RBI rate always shows 6.00%
- Fed rate always shows 4.50%
- Inflation always shows 5.09%

**Cause**:
Previous version had hardcoded fallback values. These have been removed.

**Fix Applied**:
- All hardcoded fallbacks removed from `macro_provider.py`
- When sources fail, fields show UNAVAILABLE
- Last successful fetch shown as STALE with age indicator

**Expected behavior**:
- With internet: fields show LIVE values from RBI, Yahoo, World Bank, etc.
- Without internet: fields show UNAVAILABLE (not hardcoded values)
- After successful fetch then network loss: fields show STALE with "Cached from [date]"

---

### 4. NSE Website Blocked

**Symptoms**:
- Capital flows show UNAVAILABLE
- Advances/Declines show UNAVAILABLE
- Sector performance shows UNAVAILABLE
- Sidebar shows NSE attempts in Data Sources

**Cause**:
NSE website (nseindia.com) blocks some IP ranges or networks.

**Fix Applied**:
- Multiple fallback sources: BSE, MoneyControl, Yahoo Finance
- Each attempt is logged with source name
- Sidebar shows which source succeeded

**Fallback behavior**:
- FII/DII: NSE → BSE → MoneyControl
- VIX: NSE → Yahoo Finance
- Advances/Declines: NSEIndiaApi → NSE web
- Sector indices: NSE → BSE

**If all fallbacks fail**:
- Field shows UNAVAILABLE with diagnostic message
- Check network connectivity
- Try from different network (mobile hotspot)

---

### 5. Volume Metrics Confusing

**Symptoms**:
- NIFTY volume seems low or unchanged during trading
- Relative volume shows unexpected values

**Cause**:
SmartAPI returns 0 volume for index candles. App uses nselib for daily volume.

**Fix Applied**:
- NIFTY Volume clearly labeled "EOD" (from nselib)
- Options Volume shown separately (from option chain, intraday)
- Relative Volume from candle data (when available)

**Understanding the numbers**:
- NIFTY Volume (EOD) = yesterday's total volume
- Options Volume (Intraday) = sum of CE + PE last prices from option chain
- Relative Volume = current candle volume vs 20-period average

---

### 6. Futures OI Not Changing Intraday

**Symptoms**:
- Futures OI change shows same value all day
- OI change doesn't reflect intraday position changes

**Cause**:
Futures OI comes from nsefin bhavcopy, which is end-of-day only.

**Fix Applied**:
- Futures OI clearly labeled as EOD data
- OI change calculated from cached previous reading (daily delta)
- History manager tracks OI observations for future analysis

**Expected behavior**:
- Futures OI updates once per day (after market close)
- Not suitable for intraday OI change tracking
- Use Options OI change for intraday positioning

---

### 7. Candlestick Chart Not Rendering

**Symptoms**:
- Price Action section shows "Insufficient candle data for chart"
- Chart area is empty

**Cause**:
Not enough 5-minute candles available (need at least 2).

**Fix Applied**:
- Clear error message when insufficient data
- Chart only renders when 2+ valid candles exist

**If still failing**:
- Check if SmartAPI candles are being fetched
- Check logs for `getCandleData` errors
- Verify NIFTY spot token (99926000) is correct

---

### 8. Verdict Not Changing

**Symptoms**:
- Verdict stays the same even when market moves
- Verdict history shows no new entries

**Cause**:
Verdict only changes when raw_score changes. Small moves within the same score range don't trigger a change.

**Expected behavior**:
- Score range -1 to +1 = MIXED/WAIT
- Score must cross threshold to change direction/state
- Example: +1 → +2 changes from MIXED/WAIT to BULLISH/BIAS
- Example: +2 → +3 changes from BULLISH/BIAS to BULLISH/SETUP

**If verdict seems stuck**:
- Check component scores in the table
- Expand evidence to see why each component scored as it did
- Check if data is STALE — stale data may not reflect current market

---

### 9. App Crashes on Startup

**Symptoms**:
- Streamlit shows error page
- Logs show import errors

**Common causes**:
1. **Missing dependencies**: Run `pip install -r requirements.txt`
2. **Missing `.env`**: Copy `.env.example` to `.env` and fill in Angel One credentials
3. **Python version**: Requires Python 3.9+
4. **SmartApi package**: Install via `pip install SmartApi`

**Debug steps**:
```bash
# Check Python version
python --version

# Install dependencies
pip install -r requirements.txt

# Verify .env exists
ls .env

# Test imports
python -c "import models.snapshot; print('OK')"
```

---

### 10. Data Quality Shows "Poor"

**Symptoms**:
- Data Health panel shows Poor quality
- Many fields show UNAVAILABLE

**Common causes**:
1. **Network issues**: Check internet connectivity
2. **Angel One auth**: Verify credentials in `.env`
3. **NSE blocking**: Check Data Sources panel for NSE failures
4. **Market closed**: After 3:30 PM IST, some data becomes STALE

**Fix**:
- Check individual field status in Diagnostics expander
- Verify each provider in Data Sources panel
- Restart app if providers are in bad state

---

## Log Files

Logs are stored in `logs/YYYY-MM-DD/app.log`.

**Key log patterns to search**:
- `SmartAPI session established` — Auth working
- `WebSocket connection established` — Streaming working
- `optionGreek failed` — Greeks/IV fallback active
- `NSE advanceDecline failed` — Using fallback for A/D
- `RBI policy rate scrape failed` — Using cached/stale macro
- `Capital flows data sourced from` — Shows which source succeeded

## Getting Help

1. Check this troubleshooting guide
2. Review logs in `logs/YYYY-MM-DD/app.log`
3. Expand Diagnostics panels in the UI
4. Check Data Sources panel in sidebar
5. Review `AUDIT_REPORT.md` for known limitations
