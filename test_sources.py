"""Test free data sources for fields SmartAPI cannot provide."""
import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# 1. India VIX from NSE
print("=== India VIX ===")
try:
    r = requests.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=10)
    data = r.json()
    for idx in data.get("data", []):
        if "INDIA VIX" in idx.get("name", "").upper():
            print(f"  VIX: {idx.get('last')}, Change: {idx.get('percentChange')}")
            break
except Exception as e:
    print(f"  NSE VIX error: {e}")

# 2. FII/DII from NSDL
print("\n=== FII/DII Flows ===")
try:
    r = requests.get("https://www.nsdl.co.in/Services/Investors/iiacs/Fortnightly", headers=headers, timeout=10)
    print(f"  NSDL status: {r.status_code}")
except Exception as e:
    print(f"  NSDL error: {e}")

# Try alternative FII/DII source
try:
    r = requests.get("https://trendlyne.com/equity/fii-dii/", headers=headers, timeout=10)
    print(f"  Trendlyne status: {r.status_code}, len: {len(r.text)}")
except Exception as e:
    print(f"  Trendlyne error: {e}")

# 3. Crude oil from yfinance
print("\n=== Crude Oil (yfinance) ===")
try:
    import yfinance as yf
    crude = yf.Ticker("CL=F")
    hist = crude.history(period="1d")
    if not hist.empty:
        print(f"  Crude: {hist['Close'].iloc[-1]:.2f}")
    else:
        print("  No crude data")
except Exception as e:
    print(f"  Crude error: {e}")

# 4. USD/INR from yfinance
print("\n=== USD/INR (yfinance) ===")
try:
    usd = yf.Ticker("USDINR=X")
    hist = usd.history(period="1d")
    if not hist.empty:
        print(f"  USDINR: {hist['Close'].iloc[-1]:.2f}")
    else:
        print("  No USDINR data")
except Exception as e:
    print(f"  USDINR error: {e}")

# 5. US 10Y yield from yfinance
print("\n=== US 10Y Yield (yfinance) ===")
try:
    us10y = yf.Ticker("^TNX")
    hist = us10y.history(period="1d")
    if not hist.empty:
        print(f"  US10Y: {hist['Close'].iloc[-1]:.3f}")
    else:
        print("  No US10Y data")
except Exception as e:
    print(f"  US10Y error: {e}")

# 6. NSE advances/declines
print("\n=== NSE Advances/Declines ===")
try:
    r = requests.get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050", headers=headers, timeout=10)
    data = r.json()
    stocks = data.get("data", [])
    advances = sum(1 for s in stocks if s.get("pChange", 0) > 0)
    declines = sum(1 for s in stocks if s.get("pChange", 0) < 0)
    unchanged = sum(1 for s in stocks if s.get("pChange", 0) == 0)
    print(f"  A/D: {advances}/{declines}/{unchanged}")
except Exception as e:
    print(f"  NSE A/D error: {e}")

# 7. NSE Option Chain OI
print("\n=== NSE Option Chain ===")
try:
    r = requests.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=headers, timeout=10)
    data = r.json()
    records = data.get("records", {})
    ce_oi = sum(o.get("openInterest", 0) for o in records.get("CE", []) if o.get("expiryDate") == records.get("expiryDates", [None])[0])
    pe_oi = sum(o.get("openInterest", 0) for o in records.get("PE", []) if o.get("expiryDate") == records.get("expiryDates", [None])[0])
    print(f"  CE OI: {ce_oi}, PE OI: {pe_oi}, PCR: {pe_oi/ce_oi if ce_oi else 0:.2f}")
    # ATM IV
    ltp = records.get("underlyingValue", 0)
    print(f"  Spot: {ltp}")
except Exception as e:
    print(f"  NSE Option Chain error: {e}")
