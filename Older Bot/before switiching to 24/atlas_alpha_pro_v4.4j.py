# --- Atlas Alpha Pro v4.7 + Stability Patch ---
# HTF Confirmation + Tiered Risk + Dynamic BuyingPower + Margin Protection
# Now includes: Reconnection logic, timezone-aware session checking, debug session logs

import logging
import time
import random
from ib_insync import *
import pandas as pd
import schedule
from datetime import datetime
from pytz import timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
ib = IB()
ib.connect("127.0.0.1", 7497, clientId=random.randint(1000, 9999))

# Constants
VOLATILITY_THRESHOLD = 0.0025
SAFETY_MARGIN_PCT = 0.10  # Bot pauses if BuyingPower < 10% of account

# Ensure reconnection if dropped
def ensure_connected():
    if not ib.isConnected():
        try:
            ib.disconnect()
            ib.connect("127.0.0.1", 7497, clientId=random.randint(1000, 9999))
            logging.info("🔌 Reconnected to IB Gateway.")
        except Exception as e:
            logging.error(f"❌ Reconnection failed: {e}")

# Google Sheets
def get_google_sheet(tab_name):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("gspread_credentials.json", scope)
    client = gspread.authorize(creds)
    return client.open("naitiks_trade_log").worksheet(tab_name)

def log_pnl_history():
    ensure_connected()
    if not ib.isConnected():
        logging.error("❌ Skipping PnL logging: Still not connected.")
        return
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
            summary["Currency"].value,
        ]
        sheet.append_row(row)
        logging.info("✅ PnL history logged.")
    except Exception as e:
        logging.error(f"PnL log failed: {e}")

def update_live_portfolio():
    ensure_connected()
    if not ib.isConnected():
        logging.error("❌ Skipping LivePortfolio update: Still not connected.")
        return
    try:
        sheet = get_google_sheet("LivePortfolio")
        summary = {x.tag: x for x in ib.accountSummary()}
        sheet.clear()
        sheet.append_row(["Metric", "Value"])
        sheet.append_row(["Last Updated", datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        sheet.append_row(["NetLiquidation", summary["NetLiquidation"].value])
        sheet.append_row(["CashValue", summary["TotalCashValue"].value])
        sheet.append_row(["BuyingPower", summary["BuyingPower"].value])
        sheet.append_row(["UnrealizedPnL", summary["UnrealizedPnL"].value])
        sheet.append_row(["RealizedPnL", summary["RealizedPnL"].value])
        sheet.append_row(["Currency", summary["Currency"].value])
    except Exception as e:
        logging.error(f"LivePortfolio update failed: {e}")

# Trend windows and pairs
SESSION_WINDOWS = {"LondonNY": (8, 12), "NewYork": (14, 17)}
SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'USDCHF', 'NZDUSD', 'EURGBP']

def is_optimal_session():
    now = datetime.now(timezone('Canada/Eastern'))
    hour = now.hour
    logging.info(f"🕒 Session Check | Ontario Hour: {hour} | Full Time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    return any(start <= hour < end for start, end in SESSION_WINDOWS.values())

def get_ema_trends(symbol):
    try:
        df1 = util.df(ib.reqHistoricalData(Forex(symbol), '', '2 D', '1 hour', 'MIDPOINT', False))
        df4 = util.df(ib.reqHistoricalData(Forex(symbol), '', '5 D', '4 hours', 'MIDPOINT', False))
        if len(df1) < 50 or len(df4) < 50:
            return None, None, None
        ema1 = df1['close'].ewm(span=50).mean().iloc[-1]
        ema4 = df4['close'].ewm(span=50).mean().iloc[-1]
        price = df1['close'].iloc[-1]
        return price, ema1, ema4
    except:
        return None, None, None

def calculate_position_size(atr, buying_power, net_liquidation, confidence):
    if buying_power < float(net_liquidation) * SAFETY_MARGIN_PCT:
        logging.warning("⛔️ Capital too low — pausing trading.")
        return 0
    risk_pct = 0.02 if confidence == "high" else 0.01
    capital = float(buying_power) * risk_pct
    return round((capital / (atr * 10)), 2)

def place_bracket_order(symbol, quantity, entry, atr):
    if quantity <= 0:
        return
    contract = Forex(symbol)
    parent = MarketOrder('BUY', quantity, transmit=False)
    parent.orderId = ib.client.getReqId()
    tp1 = LimitOrder('SELL', int(quantity * 0.4), round(entry + atr * 1.5, 5), parentId=parent.orderId, transmit=False)
    tp2 = LimitOrder('SELL', int(quantity * 0.4), round(entry + atr * 2.5, 5), parentId=parent.orderId, transmit=False)
    tp3 = LimitOrder('SELL', quantity - int(quantity * 0.8), round(entry + atr * 3.0, 5), parentId=parent.orderId, transmit=False)
    sl = StopOrder('SELL', quantity, round(entry - atr * 1.5, 5), parentId=parent.orderId, transmit=True)
    oca_group = f"OCA_{symbol}_{int(time.time())}"
    for tp in [tp1, tp2, tp3]:
        tp.ocaGroup, tp.ocaType = oca_group, 2
    for order in [parent, tp1, tp2, tp3, sl]:
        ib.placeOrder(contract, order)
    logging.info(f"ORDER PLACED: {symbol} | Entry: {entry} | Qty: {quantity}")

class AtlasAlphaPro:
    def run_cycle(self):
        update_live_portfolio()
        log_pnl_history()
        ensure_connected()
        if not ib.isConnected():
            logging.error("❌ Skipping run cycle: Still not connected.")
            return
        if not is_optimal_session():
            for s in SYMBOLS:
                logging.info(f"{s}: ⏳ Skipped — outside session")
            return
        summary = {x.tag: x for x in ib.accountSummary()}
        net_liq = summary["NetLiquidation"].value
        buying_power = summary["BuyingPower"].value
        for symbol in SYMBOLS:
            price, ema1, ema4 = get_ema_trends(symbol)
            if not price or not ema1 or not ema4 or price <= ema1:
                logging.info(f"{symbol}: ❌ Skipped — trend filter failed")
                continue
            confidence = "high" if price > ema4 else "normal"
            atr = 0.0018
            quantity = calculate_position_size(atr, float(buying_power), float(net_liq), confidence)
            place_bracket_order(symbol, quantity, price, atr)

    def run(self):
        schedule.every(2).minutes.do(self.run_cycle)
        while True:
            schedule.run_pending()
            time.sleep(1)

if __name__ == '__main__':
    bot = AtlasAlphaPro()
    bot.run()
