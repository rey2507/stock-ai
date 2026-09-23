"""Test NSE API with proper session + retry."""
import requests, time

# NSE needs a proper session with cookies from homepage
s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
})

# Step 1: Get cookies from homepage
print("Getting NSE cookies...")
try:
    r = s.get("https://www.nseindia.com", timeout=15)
    print(f"Homepage: {r.status_code}, cookies: {dict(s.cookies)}")
except Exception as e:
    print(f"Homepage error: {e}")

time.sleep(1)

# Step 2: Try VIX
print("\n=== VIX ===")
try:
    r = s.get("https://www.nseindia.com/api/allIndices", timeout=15)
    print(f"Status: {r.status_code}, Content-Type: {r.headers.get('Content-Type')}")
    if r.status_code == 200 and 'json' in r.headers.get('Content-Type', ''):
        data = r.json()
        for idx in data.get("data", []):
            if "INDIA VIX" in idx.get("name", "").upper():
                print(f"  VIX: {idx.get('last')}, Change: {idx.get('percentChange')}")
                break
    else:
        print(f"  Response: {r.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# Step 3: Advances/Declines
print("\n=== A/D ===")
try:
    r = s.get("https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050", timeout=15)
    if r.status_code == 200:
        data = r.json()
        stocks = data.get("data", [])
        adv = sum(1 for s in stocks if s.get("pChange", 0) > 0)
        dec = sum(1 for s in stocks if s.get("pChange", 0) < 0)
        unch = sum(1 for s in stocks if s.get("pChange", 0) == 0)
        print(f"  A/D/U: {adv}/{dec}/{unch}")
except Exception as e:
    print(f"  Error: {e}")

# Step 4: Option Chain
print("\n=== Option Chain ===")
try:
    r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=15)
    if r.status_code == 200:
        data = r.json()
        records = data.get("records", {})
        spot = records.get("underlyingValue", 0)
        expiry = records.get("expiryDates", [None])[0]
        print(f"  Spot: {spot}, Expiry: {expiry}")
        
        ce_all = records.get("CE", [])
        pe_all = records.get("PE", [])
        
        # Filter by nearest expiry
        ce = [o for o in ce_all if o.get("expiryDate") == expiry]
        pe = [o for o in pe_all if o.get("expiryDate") == expiry]
        
        if ce and pe:
            strikes = sorted(set(o.get("strikePrice") for o in ce))
            atm = min(strikes, key=lambda s: abs(s - spot)) if strikes else 0
            
            # ATM IV
            atm_ce = [o for o in ce if o.get("strikePrice") == atm]
            atm_pe = [o for o in pe if o.get("strikePrice") == atm]
            
            if atm_ce:
                print(f"  ATM CE IV: {atm_ce[0].get('impliedVolatility')}, OI: {atm_ce[0].get('openInterest')}")
            if atm_pe:
                print(f"  ATM PE IV: {atm_pe[0].get('impliedVolatility')}, OI: {atm_pe[0].get('openInterest')}")
            
            # Total OI for PCR
            total_ce = sum(o.get("openInterest", 0) for o in ce)
            total_pe = sum(o.get("openInterest", 0) for o in pe)
            print(f"  Total CE OI: {total_ce:,}, PE OI: {total_pe:,}")
            print(f"  PCR (OI): {total_pe/total_ce:.2f}" if total_ce else "  PCR: N/A")
            
            # Max Pain
            all_strikes = sorted(set(o.get("strikePrice") for o in ce + pe))
            min_pain = float('inf')
            max_pain_strike = 0
            for strike in all_strikes:
                pain = 0
                for o in ce:
                    if o.get("strikePrice") < strike:
                        pain += (strike - o.get("strikePrice", 0)) * o.get("openInterest", 0)
                for o in pe:
                    if o.get("strikePrice") > strike:
                        pain += (o.get("strikePrice", 0) - strike) * o.get("openInterest", 0)
                if pain < min_pain:
                    min_pain = pain
                    max_pain_strike = strike
            print(f"  Max Pain: {max_pain_strike}")
    else:
        print(f"  Status: {r.status_code}")
except Exception as e:
    print(f"  Error: {e}")
