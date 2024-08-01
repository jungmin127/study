import pandas as pd
from dash import Dash, html, dash_table
import pyupbit
import requests
import json
import numpy as np

def define_crypto(crypto, to_date, count):
    df = pyupbit.get_ohlcv(crypto, interval="day", to=to_date, count=count, period=0.1)
    df = df.reset_index()
    df = df.rename(columns={'index': 'date'})
    df['date'] = df['date'].dt.to_period(freq='D')
    df = df.set_index('date')
    return df

url = "https://api.upbit.com/v1/market/all?isDetails=true"
headers = {"accept": "application/json"}

res = requests.get(url, headers=headers)
en_name = [content['market'] for content in res.json()]
kr_name = [content['korean_name'] for content in res.json()]

name = pd.DataFrame({'en': en_name,'kr': kr_name})
name['krw'] = name['en'].str.contains('KRW')
kr_name = name[name['krw'] == 1].reset_index(drop=True)
kr_name = kr_name.drop(columns=['krw'])

cryptos = kr_name['en'].tolist()[0:5]
to_date = '20240726'
count = 2000

data_frames = {}

for crypto in cryptos:
  df = define_crypto(crypto, to_date, count)
  data_frames[crypto] = df

for crypto in cryptos:
  crypto_name = crypto.replace('KRW-',"").lower()
  globals()[crypto_name] = data_frames.get(crypto)