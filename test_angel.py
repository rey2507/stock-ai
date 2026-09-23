"""Test Angel Broking connection."""
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

print(f'API Key: {api_key[:4]}...')
print(f'Client: {client_code}')

totp = pyotp.TOTP(totp_secret)
current_totp = totp.now()
print(f'TOTP generated: {current_totp}')

obj = SmartConnect(api_key=api_key)
data = obj.generateSession(client_code, pin, current_totp)
print(f'Login status: {data.get("status")}')

if data.get('status'):
    feed_token = data['data']['feedToken']
    print(f'Feed token received')

    # Test: Get Nifty 50 LTP
    result = obj.ltpData('NSE', 'NIFTY', '99926000')
    print(f'Nifty LTP response: {result}')

    # Test: Search for NIFTY
    search = obj.searchScrip('NSE', 'NIFTY')
    if search.get('status') and search.get('data'):
        for item in search['data'][:5]:
            print(f'  Found: {item["tradingsymbol"]} token={item["symboltoken"]}')

    # Test: Get market data for Nifty
    market_data = obj.getMarketData('FULL', {'NSE': ['99926000']})
    print(f'Market data status: {market_data.get("status")}')
    if market_data.get('status') and market_data.get('data'):
        for k, v in market_data['data'].items():
            print(f'  {k}: {v}')
else:
    print(f'Login failed: {data}')
