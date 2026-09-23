"""Test alternative data sources when NSE blocks."""
import yfinance as yf
import requests, json

# 1. India VIX from Yahoo Finance
print("=== India VIX (Yahoo) ===")
try:
    vix = yf.Ticker("^INDIAVIX")
    hist = vix.history(period="5d")
    if not hist.empty:
        print(f"  Current: {hist['Close'].iloc[-1]:.2f}")
        if len(hist) > 1:
            print(f"  Prev: {hist['Close'].iloc[-2]:.2f}")
    else:
        print("  No data")
except Exception as e:
    print(f"  Error: {e}")

# 2. Try NIFTY from Yahoo (for OI proxy via volume)
print("\n=== NIFTY Volume (Yahoo) ===")
try:
    nifty = yf.Ticker("^NSEI")
    hist = nifty.history(period="5d")
    if not hist.empty:
        for i, row in hist.iterrows():
            print(f"  {i.date()}: Close={row['Close']:.2f}, Vol={row['Volume']:,.0f}")
except Exception as e:
    print(f"  Error: {e}")

# 3. FII/DII from MoneyControl
print("\n=== FII/DII ===")
try:
    r = requests.get(
        "https://trendlyne.com/equity/fii-dii/",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10
    )
    print(f"  Trendlyne status: {r.status_code}")
except Exception as e:
    print(f"  Error: {e}")

# Try economic times
try:
    r = requests.get(
        "https://economictimes.indiatimes.com/markets/fii-dii",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10
    )
    print(f"  ET status: {r.status_code}, len: {len(r.text)}")
except Exception as e:
    print(f"  ET error: {e}")

# 4. Macro data from World Bank / RBI
print("\n=== Macro (RBI) ===")
try:
    r = requests.get(
        "https://api.worldbank.org/v2/country/IND/indicator/FP.CPI.TOTL.ZG?format=json&date=2024",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10
    )
    if r.status_code == 200:
        data = r.json()
        if len(data) > 1:
            for item in data[1][:3]:
                print(f"  {item.get('indicator', {}).get('value')}: {item.get('value')}")
except Exception as e:
    print(f"  Macro error: {e}")

# 5. Alternative advances/declines from NSE via different approach
print("\n=== NIFTY Bank (proxy for market breadth) ===")
try:
    bank = yf.Ticker("^NSEBANK")
    hist = bank.history(period="1d")
    if not hist.empty:
        print(f"  Bank Nifty: {hist['Close'].iloc[-1]:.2f}")
except Exception as e:
    print(f"  Error: {e}")

# 6. Crude from MCX (via Yahoo - Brent)
print("\n=== Brent Crude (Yahoo) ===")
try:
    brent = yf.Ticker("BZ=F")
    hist = brent.history(period="1d")
    if not hist.empty:
        print(f"  Brent: {hist['Close'].iloc[-1]:.2f}")
except Exception as e:
    print(f"  Brent error: {e}")

# 7. Gold from Yahoo
print("\n=== Gold (Yahoo) ===")
try:
    gold = yf.Ticker("GC=F")
    hist = gold.history(period="1d")
    if not hist.empty:
        print(f"  Gold: {hist['Close'].iloc[-1]:.2f}")
except Exception as e:
    print(f"  Gold error: {e}")
