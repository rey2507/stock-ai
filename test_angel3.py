"""Test Angel Broking - deeper API exploration."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import pyotp
from SmartApi.smartConnect import SmartConnect
from dotenv import load_dotenv
import os
import json

load_dotenv(r'C:\Users\reyya\dashboardforoptions\Nifty 50 verdict\.env')

api_key = os.getenv('ANGEL_API_KEY')
client_code = os.getenv('ANGEL_CLIENT_CODE')
pin = os.getenv('ANGEL_PIN')
totp_secret = os.getenv('ANGEL_TOTP_SECRET')

totp = pyotp.TOTP(totp_secret)
obj = SmartConnect(api_key=api_key)
data = obj.generateSession(client_code, pin, totp.now())

if not data.get('status'):
    print(f'Login failed: {data}')
    exit()

print('=== Login OK ===')

# Try NSE search for NIFTY
print('\n--- NSE search NIFTY ---')
search = obj.searchScrip('NSE', 'NIFTY')
if search.get('status') and search.get('data'):
    for item in search['data'][:10]:
        print(f'  {item["exchange"]}: {item["tradingsymbol"]} token={item["symboltoken"]}')
else:
    print(f'  Error: {search.get("message")}')

# Try NFO search with different terms
print('\n--- NFO search: NIFTY25SEPFUT ---')
try:
    search2 = obj.searchScrip('NFO', 'NIFTY25SEPFUT')
    if search2.get('status') and search2.get('data'):
        for item in search2['data'][:5]:
            print(f'  {item["tradingsymbol"]} token={item["symboltoken"]}')
    else:
        print(f'  {search2.get("message")}')
except Exception as e:
    print(f'  Error: {e}')

# Try NFO search: BANKNIFTY
print('\n--- NFO search: BANKNIFTY ---')
try:
    search3 = obj.searchScrip('NFO', 'BANKNIFTY')
    if search3.get('status') and search3.get('data'):
        for item in search3['data'][:10]:
            print(f'  {item["tradingsymbol"]} token={item["symboltoken"]}')
    else:
        print(f'  {search3.get("message")}')
except Exception as e:
    print(f'  Error: {e}')

# Try to get NSE gainers/losers for advance/decline
print('\n--- GAINERS ---')
try:
    gl = obj.gainersLosers({'type': 'gainers', 'exchange': 'NSE'})
    if gl.get('status') and gl.get('data'):
        print(f'  Got {len(gl["data"])} gainers')
        for item in gl['data'][:3]:
            print(f'    {item}')
    else:
        print(f'  {gl}')
except Exception as e:
    print(f'  Error: {e}')

print('\n--- LOSERS ---')
try:
    gl2 = obj.gainersLosers({'type': 'losers', 'exchange': 'NSE'})
    if gl2.get('status') and gl2.get('data'):
        print(f'  Got {len(gl2["data"])} losers')
        for item in gl2['data'][:3]:
            print(f'    {item}')
    else:
        print(f'  {gl2}')
except Exception as e:
    print(f'  Error: {e}')

# Try putCallRatio
print('\n--- PUT CALL RATIO ---')
try:
    pcr = obj.putCallRatio()
    print(f'  PCR response: {json.dumps(pcr, indent=2)[:1000]}')
except Exception as e:
    print(f'  Error: {e}')

# Try getOIData
print('\n--- OI DATA ---')
try:
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=1)
    oi_params = {
        'exchange': 'NSE',
        'symboltoken': '99926000',
        'fromdate': start.strftime('%Y-%m-%d 09:15'),
        'todate': end.strftime('%Y-%m-%d 15:30'),
    }
    oi = obj.getOIData(oi_params)
    if oi.get('status') and oi.get('data'):
        print(f'  Got OI data: {len(oi["data"])} records')
        for item in oi['data'][:3]:
            print(f'    {item}')
    else:
        print(f'  {oi}')
except Exception as e:
    print(f'  Error: {e}')
