"""Test NSE APIs with proper session handling."""
import requests

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# First visit main page to get cookies
try:
    r = session.get("https://www.nseindia.com", headers=headers, timeout=10)
    print(f"NSE home: {r.status_code}, cookies: {len(session.cookies)}")
except Exception as e:
    print(f"NSE home error: {e}")

# Now try APIs
print("\n=== India VIX ===")
try:
    r = session.get("https://www.nseindia.com/api/allIndices", headers=headers, timeout=10)
    data = r.json()
    for idx in data.get("data", []):
        if "INDIA VIX" in idx.get("name", "").upper():
            print(f"  VIX: {idx.get('last')}, Change: {idx.get('percentChange')}")
            break
except Exception as e:
    print(f"  VIX error: {e}")

print("\n=== NSE Advances/Declines ===")
try:
    r = session.get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050", headers=headers, timeout=10)
    data = r.json()
    stocks = data.get("data", [])
    advances = sum(1 for s in stocks if s.get("pChange", 0) > 0)
    declines = sum(1 for s in stocks if s.get("pChange", 0) < 0)
    unchanged = sum(1 for s in stocks if s.get("pChange", 0) == 0)
    print(f"  A/D: {advances}/{declines}/{unchanged}")
    # Also get individual stock data for sector performance
    for s in stocks[:3]:
        print(f"  {s.get('symbol')}: {s.get('pChange')}%")
except Exception as e:
    print(f"  A/D error: {e}")

print("\n=== NSE Option Chain ===")
try:
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", headers=headers, timeout=10)
    data = r.json()
    records = data.get("records", {})
    expiry = records.get("expiryDates", [None])[0]
    print(f"  Nearest expiry: {expiry}")
    
    ce_options = [o for o in records.get("CE", []) if o.get("expiryDate") == expiry]
    pe_options = [o for o in records.get("PE", []) if o.get("expiryDate") == expiry]
    
    # Get ATM strike
    spot = records.get("underlyingValue", 0)
    print(f"  Spot: {spot}")
    
    strikes = sorted(set(o.get("strikePrice") for o in ce_options))
    if strikes:
        atm = min(strikes, key=lambda s: abs(s - spot))
        print(f"  ATM: {atm}")
        
        # Get ATM CE/PE data
        atm_ce = [o for o in ce_options if o.get("strikePrice") == atm][0] if ce_options else {}
        atm_pe = [o for o in pe_options if o.get("strikePrice") == atm][0] if pe_options else {}
        
        print(f"  ATM CE OI: {atm_ce.get('openInterest')}, IV: {atm_ce.get('impliedVolatility')}")
        print(f"  ATM PE OI: {atm_pe.get('openInterest')}, IV: {atm_pe.get('impliedVolatility')}")
        
        # Total OI
        total_ce_oi = sum(o.get("openInterest", 0) for o in ce_options)
        total_pe_oi = sum(o.get("openInterest", 0) for o in pe_options)
        print(f"  Total CE OI: {total_ce_oi}")
        print(f"  Total PE OI: {total_pe_oi}")
        print(f"  PCR (OI): {total_pe_oi / total_ce_oi if total_ce_oi else 0:.2f}")
except Exception as e:
    print(f"  Option chain error: {e}")

print("\n=== FII/DII ===")
try:
    r = session.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=headers, timeout=10)
    data = r.json()
    print(f"  FII/DII data: {json.dumps(data[:3] if isinstance(data, list) else data, indent=2)[:500]}")
except Exception as e:
    print(f"  FII/DII error: {e}")

import json
