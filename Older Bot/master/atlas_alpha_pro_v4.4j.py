import logging
import time
import random
from ib_insync import *
import pandas as pd
import schedule
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt
import os

# Try ADX library
try:
    from ta.trend import ADXIndicator
except ImportError:
    ADXIndicator = None

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
ib = IB()
ib.connect("127.0.0.1", 7497, clientId=random.randint(1000, 9999))

# Symbols and cache
SYMBOLS = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','USDCHF','NZDUSD','EURGBP']
ema_cache = {}

# CONFIG
SAFETY_MARGIN_PCT = 0.10
EMA_REFRESH_INTERVAL = 6  # minutes
BUFFER_MULTIPLIER = 0.1    # % of ATR
MAX_DAILY_DRAWDOWN_PCT = 0.10

daily_pnl_threshold = None
trading_paused = False

# Google Sheets helper
def get_google_sheet(tab_name):
    scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("gspread_credentials.json", scope)
    client = gspread.authorize(creds)
    return client.open("naitiks_trade_log").worksheet(tab_name)

# Ensure IB connection
def ensure_connected():
    if not ib.isConnected():
        try:
            ib.disconnect()
            ib.connect("127.0.0.1", 7497, clientId=random.randint(1000, 9999))
            logging.info("🔌 Reconnected to IB Gateway.")
        except Exception as e:
            logging.error(f"Reconnection failed: {e}")

# PnL history log
def log_pnl_history():
    ensure_connected()
    try:
        sheet = get_google_sheet("PnLHistory")
        summary = {x.tag: x for x in ib.accountSummary()}
        row = [
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            summary["NetLiquidation"].value,
            summary["TotalCashValue"].value,
            summary["BuyingPower"].value,
            summary["UnrealizedPnL"].value,
            summary["RealizedPnL"].value,
            summary["Currency"].value
        ]
        sheet.append_row(row)
        logging.info("✅ PnL history logged.")
    except Exception as e:
        logging.error(f"PnL log failed: {e}")

# Live portfolio update
def update_live_portfolio():
    ensure_connected()
    try:
        sheet = get_google_sheet("LivePortfolio")
        summary = {x.tag: x for x in ib.accountSummary()}
        sheet.clear()
        sheet.append_row(["Metric","Value"])
        sheet.append_row(["Last Updated", datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        for key in ["NetLiquidation","TotalCashValue","BuyingPower","UnrealizedPnL","RealizedPnL","Currency"]:
            sheet.append_row([key, summary[key].value])
    except Exception as e:
        logging.error(f"LivePortfolio update failed: {e}")

# Fetch EMA trends
def fetch_ema_trend(symbol):
    try:
        df1 = util.df(ib.reqHistoricalData(Forex(symbol), '', '3 D', '1 hour', 'MIDPOINT', False))
        df4 = util.df(ib.reqHistoricalData(Forex(symbol), '', '7 D', '4 hours', 'MIDPOINT', False))
        if len(df1) < 20 or len(df4) < 20:
            raise Exception("Not enough data")
        return {
            'price': df1['close'].iloc[-1],
            'ema1': df1['close'].ewm(span=50).mean().iloc[-1],
            'ema4': df4['close'].ewm(span=50).mean().iloc[-1]
        }
    except:
        return None

def refresh_ema_trends():
    for s in SYMBOLS:
        t = fetch_ema_trend(s)
        if not t:
            time.sleep(5)
            t = fetch_ema_trend(s)
        ema_cache[s] = t

# Dynamic ADX thresholds
def get_dynamic_adx_thresholds(symbol, lookback_days=7):
    bars = ib.reqHistoricalData(Forex(symbol), '', f'{lookback_days} D', '1 hour', 'MIDPOINT', False)
    df = util.df(bars)
    if len(df) < 20 or ADXIndicator is None:
        return 25, 20
    adx_series = ADXIndicator(df['high'], df['low'], df['close'], window=14).adx()
    return float(adx_series.quantile(0.75)), float(adx_series.quantile(0.25))

# Position sizing
def calculate_position_size(symbol, atr, buying_power, net_liq, confidence_factor):
    if buying_power < float(net_liq) * SAFETY_MARGIN_PCT:
        return 0
    mult = 15 if 'JPY' in symbol else 10
    a_str, a_weak = get_dynamic_adx_thresholds(symbol)
    summary = {x.tag: x for x in ib.accountSummary()}
    adx = summary.get('ADX', type('x', (), {'value': 20})).value
    if adx > a_str:
        mult = 15
    elif adx < a_weak:
        mult = 8
    risk = 0.03 if atr < 0.0015 else 0.02 if atr < 0.0025 else 0.01
    risk *= confidence_factor
    cap = buying_power * risk
    size = cap / (atr * mult)
    return min(size, 95000)

# Bracket orders
def place_bracket_order(symbol, qty, entry, atr):
    if qty <= 0:
        return
    c = Forex(symbol)
    parent = MarketOrder('BUY', qty, transmit=True, tif='GTC')
    parent.orderId = ib.client.getReqId()
    tp1 = LimitOrder('SELL', int(qty*0.4), round(entry+atr*1.5, 5), parentId=parent.orderId, transmit=True, tif='GTC')
    tp2 = LimitOrder('SELL', int(qty*0.4), round(entry+atr*2.5, 5), parentId=parent.orderId, transmit=True, tif='GTC')
    tp3 = LimitOrder('SELL', qty-int(qty*0.8), round(entry+atr*3.0, 5), parentId=parent.orderId, transmit=True, tif='GTC')
    sl = StopOrder('SELL', qty, round(entry-atr*1.5, 5), parentId=parent.orderId, transmit=True, tif='GTC')
    grp = f"OCA_{symbol}_{int(time.time())}"
    for o in (tp1, tp2, tp3):
        o.ocaGroup, o.ocaType = grp, 2
    for o in (parent, tp1, tp2, tp3, sl):
        ib.placeOrder(c, o)

# Readiness charts omitted for brevity…

class AtlasAlphaPro:
    def run_cycle(self):
        update_live_portfolio(); log_pnl_history(); ensure_connected()
        tlist = [(s, ema_cache[s]) for s in SYMBOLS if ema_cache.get(s)]
        summary = {x.tag: x for x in ib.accountSummary()}
        net_liq = summary['NetLiquidation'].value
        bp = summary['BuyingPower'].value
        for symbol, trend in tlist:
            price, ema1, ema4 = trend['price'], trend['ema1'], trend['ema4']
            data = fetch_ema_trend(symbol)  # reuse EMA+ATR fetch for entry atr
            atr = ATR = data.get('atr', None) if data else None
            if not atr:
                continue
            buffer = BUFFER_MULTIPLIER * atr
            if price <= ema1 - buffer:
                continue
            # --- Confidence Flag ---
            if price > ema4:
                confidence_label = "High"
                confidence_factor = 1.0
            else:
                confidence_label = "Normal"
                confidence_factor = 0.5
            qty = calculate_position_size(symbol, atr, bp, net_liq, confidence_factor)
            logging.info(f"{symbol} | Confidence: {confidence_label} | Qty: {qty}")
            place_bracket_order(symbol, qty, price, atr)

    def run(self):
        refresh_ema_trends(); self.run_cycle()
        schedule.every(EMA_REFRESH_INTERVAL).minutes.do(refresh_ema_trends)
        schedule.every(2).minutes.do(self.run_cycle)
        while True:
            schedule.run_pending(); time.sleep(1)

if __name__ == '__main__':
    AtlasAlphaPro().run()
