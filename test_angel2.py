"""Test Angel Broking - search for Nifty futures and options."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import pyotp
from SmartApi.smartConnect import SmartConnect
from dotenv import load_dotenv
import os

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

# Search for Nifty futures
print('\n--- NIFTY FUT ---')
search = obj.searchScrip('NFO', 'NIFTY FUT')
if search.get('data'):
    for item in search['data'][:10]:
        print(f'  {item["tradingsymbol"]} token={item["symboltoken"]}')

# Search for NIFTY options
print('\n--- NIFTY CE/PE ---')
search = obj.searchScrip('NFO', 'NIFTY')
if search.get('data'):
    # Show first 20
    for item in search['data'][:20]:
        print(f'  {item["tradingsymbol"]} token={item["symboltoken"]}')
    print(f'  ... total {len(search["data"])} results')

# Get Nifty spot with full market data
print('\n--- NIFTY SPOT (LTP) ---')
ltp = obj.ltpData('NSE', 'NIFTY', '99926000')
print(f'  LTP: {ltp["data"]}')

# Try to get Nifty futures LTP
print('\n--- NIFTY FUT LTP ---')
if search.get('data'):
    # Find a futures contract
    fut_items = [i for i in search['data'] if 'FUT' in i['tradingsymbol']]
    if fut_items:
        fut = fut_items[0]
        print(f'  Using: {fut["tradingsymbol"]}')
        ltp_fut = obj.ltpData('NFO', fut['tradingsymbol'], fut['symboltoken'])
        print(f'  LTP: {ltp_fut["data"]}')

# Get candle data for VWAP/RSI calculation
print('\n--- CANDLE DATA (15min) ---')
from datetime import datetime, timedelta
end = datetime.now()
start = end - timedelta(days=5)
candle_params = {
    'exchange': 'NSE',
    'symboltoken': '99926000',
    'interval': 'FIFTEEN_MINUTE',
    'fromdate': start.strftime('%Y-%m-%d 09:15'),
    'todate': end.strftime('%Y-%m-%d 15:30'),
}
candles = obj.getCandleData(candle_params)
if candles.get('status') and candles.get('data'):
    print(f'  Got {len(candles["data"])} candles')
    print(f'  Latest 3: {candles["data"][-3:]}')
else:
    print(f'  Error: {candles}')

# Gain/Loss data
print('\n--- GAINERS/LOSERS ---')
gl = obj.gainersLosers({'type': 'gainers', 'exchange': 'NSE'})
if gl.get('status') and gl.get('data'):
    print(f'  Top 5 gainers:')
    for item in gl['data'][:5]:
        print(f'    {item.get("tradingSymbol", "N/A")}: {item.get("percentChange", "N/A")}%')
else:
    print(f'  Response: {gl}')
