
# --- Insight Logger with Trade Readiness Charts (Fixed v1.1) ---
import logging
import time
import random
from ib_insync import *
import pandas as pd
from datetime import datetime
from pytz import timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
ib = IB()

# Connect with random client ID to avoid collision
client_id = random.randint(100000, 999999)
logging.info(f"⬭ Using clientId: {client_id}")
ib.connect("127.0.0.1", 7497, clientId=client_id)

# Google Sheets setup
def get_google_sheet(tab_name):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("gspread_credentials.json", scope)
    client = gspread.authorize(creds)
    return client.open("naitiks_trade_log").worksheet(tab_name)

# Symbols to monitor
SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'USDCHF', 'NZDUSD', 'EURGBP']

# Utility to fetch price and indicators
def get_trend_metrics(symbol):
    try:
        bars_1h = ib.reqHistoricalData(Forex(symbol), endDateTime='', durationStr='1 D', barSizeSetting='1 hour', whatToShow='MIDPOINT', useRTH=False)
        bars_4h = ib.reqHistoricalData(Forex(symbol), endDateTime='', durationStr='2 D', barSizeSetting='4 hours', whatToShow='MIDPOINT', useRTH=False)
        df1 = util.df(bars_1h)
        df4 = util.df(bars_4h)
        if len(df1) < 50 or len(df4) < 50:
            logging.warning(f"⚠️ Skipped {symbol} — insufficient data: 1H={len(df1)}, 4H={len(df4)}")
            return None
        price = df1['close'].iloc[-1]
        ema1 = df1['close'].ewm(span=50).mean().iloc[-1]
        ema4 = df4['close'].ewm(span=50).mean().iloc[-1]
        atr = df1['high'].sub(df1['low']).rolling(window=14).mean().iloc[-1]
        slope = df1['close'].rolling(window=10).apply(lambda x: (x[-1] - x[0]) / len(x)).iloc[-1]
        trend_bias = 'Bullish' if price > ema1 and price > ema4 else 'Flat'
        spread_impact = round(df1['close'].iloc[-1] - df1['open'].iloc[-1], 5)
        alpha_score = round((price - ema1) + (ema1 - ema4) + slope, 4)
        return price, ema1, ema4, atr, slope, trend_bias, spread_impact, alpha_score
    except Exception as e:
        logging.warning(f"⚠️ Skipped {symbol} — no data returned: {e}")
        return None

# Chart generation
def update_trade_readiness_charts():
    try:
        sheet = get_google_sheet("TradeReadiness")
        sheet.clear()
        sheet.append_row(["Symbol", "Price", "EMA50_1H", "EMA50_4H", "ATR", "Slope", "TrendBias", "SpreadImpact", "AlphaScore"])
        metrics_list = []

        for symbol in SYMBOLS:
            metrics = get_trend_metrics(symbol)
            if metrics:
                row = [symbol] + list(metrics)
                sheet.append_row(row)
                metrics_list.append((symbol, metrics))
            else:
                logging.info(f"ℹ️ No metrics for {symbol}, skipping row append.")

        for panel_num, start in enumerate([0, 4]):
            fig, axs = plt.subplots(2, 2, figsize=(12, 8))
            for i in range(4):
                idx = start + i
                if idx >= len(metrics_list):
                    continue
                symbol, data = metrics_list[idx]
                price, ema1, ema4, atr, slope, trend_bias, spread, alpha = data

                ax = axs[i // 2][i % 2]
                ax.set_title(f"{symbol} | {trend_bias} | Slope: {slope:.1f}")
                ax.plot([0, 1, 2, 3], [ema4, ema1, price, price], linestyle='--', marker='o')
                ax.set_xticks([0, 1, 2, 3])
                ax.set_xticklabels(["EMA50 (4H)", "EMA50 (1H)", "Current Price", "Signal Zone"])
                ax.text(0.5, price, f"Alpha: {alpha:.2f}\nSpread: {spread:.2f}\nATR: {atr:.4f}", fontsize=9)
                ax.grid(True)
            plt.tight_layout()
            filename = f"TradeChart_Panel_{panel_num + 1}.png"
            plt.savefig(filename)
            logging.info(f"✅ Chart panel saved: {filename}")
            plt.close()
    except Exception as e:
        logging.error(f"❌ update_trade_readiness_charts failed: {e}")

# Main loop
if __name__ == '__main__':
    while True:
        try:
            update_trade_readiness_charts()
            time.sleep(120)
        except Exception as e:
            logging.error(f"Logger cycle failed: {e}")
            time.sleep(30)
