#!/usr/bin/env python3
import time
import random
import logging
import pytz
from datetime import datetime, timezone
from ib_insync import IB, Forex, LimitOrder, util
from ta.volatility import AverageTrueRange
from ta.trend import ADXIndicator

# ─── CONFIG ───────────────────────────────────────────────────────────────

SYMBOLS = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','USDCHF','NZDUSD','EURGBP','USDCAD']
EMA_PERIOD = 50
ATR_PERIOD = 14
ADX_PERIOD = 14
ADX_LOOKBACK = {
    'EURUSD': 7, 'USDJPY': 7, 'GBPUSD': 7,
    'AUDUSD': 14, 'NZDUSD': 14, 'USDCHF': 14, 'EURGBP': 14, 'USDCAD': 14
}

# Per-pair ADX thresholds for entry and high-strength tier

ENTRY_ADX_THRESH = {
    'EURUSD': 18,
    'GBPUSD': 18,
    'EURGBP': 18,
    'USDJPY': 15,
    'EURJPY': 15,
    'AUDUSD': 18,
    'NZDUSD': 18,
    'USDCHF': 18,
    'USDCAD': 18
}
HIGH_ADX_THRESH = {
    'EURUSD': 30, 'GBPUSD': 32, 'EURGBP': 32,
    'USDJPY': 28, 'EURJPY': 28, 'AUDUSD': 30,
    'NZDUSD': 30, 'USDCHF': 30, 'USDCAD': 30
}

BUFFER_FACTOR   = 0.25   # keep 25% of equity in reserve
RISK_HIGH       = 0.025  # 2.5% risk when ADX is very strong
RISK_MED        = 0.01   # 1.0% risk when ADX is moderate
SL_ATR_MULT     = 1.3    # stop set at entry - ATR * multiplier
TP_ATR_MULT     = 2.2    # take-profit at entry + ATR * multiplier
MIN_UNITS       = 1000   # IBKR micro-lot minimum
RUN_INTERVAL    = 300    # seconds (5 minutes)
COOLDOWN_HOURS  = 1      # per-symbol cooldown after fill

# ─── TIME-WINDOW FILTER ────────────────────────────────────────────────────

ALLOWED_HOURS = {
    # full active windows for these four pairs
    'NZDUSD': [(19, 24), (0, 4), (8, 12)],
    'EURJPY': [(3, 6),     (8, 12)],
    'USDCAD': [(8, 12)],
    'GBPUSD': [(3, 6)],
    # most-profitable-only windows for the rest
    'EURUSD': [(8, 12)],
    'USDJPY': [(19, 24)],
    'AUDUSD': [(0, 4)],
    'EURGBP': [(3, 6)],
    'USDCHF': [(8, 12)],
}

# ─── STATE ─────────────────────────────────────────────────────────────────

last_entry_time = {sym: None for sym in SYMBOLS}

# ─── SETUP ─────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
ib = IB()
ib.connect('127.0.0.1', 7497, clientId=random.randint(1, 9999))
logging.info(f"✅ Connected to IBKR — running every {RUN_INTERVAL}s")

# ─── HELPERS ────────────────────────────────────────────────────────────────

def fetch_df(symbol, duration='3 D', barSize='1 hour'):
    try:
        bars = ib.reqHistoricalData(
            Forex(symbol), '',
            durationStr=duration,
            barSizeSetting=barSize,
            whatToShow='MIDPOINT', useRTH=False
        )
    except ConnectionError:
        ib.disconnect()
        ib.connect('127.0.0.1', 7497, clientId=random.randint(1, 9999))
        bars = ib.reqHistoricalData(
            Forex(symbol), '',
            durationStr=duration,
            barSizeSetting=barSize,
            whatToShow='MIDPOINT', useRTH=False
        )
    return util.df(bars)

# ─── ADDED HELPERS START ────────────────────────────────────────────────────

def compute_hourly_adx(symbol, lookback_days=7):
    df_h1 = fetch_df(symbol, duration=f'{lookback_days} D', barSize='1 hour')
    adx_series = ADXIndicator(df_h1.high, df_h1.low, df_h1.close, window=ADX_PERIOD).adx()
    return adx_series.iloc[-1]

def compute_adr(symbol, window_days=14):
    df_d = fetch_df(symbol, duration=f'{window_days} D', barSize='1 day')
    daily_ranges = df_d.high - df_d.low
    return daily_ranges.rolling(window=window_days).mean().iloc[-1]

def today_range(symbol):
    tz = pytz.timezone('America/New_York')
    now_et = datetime.now(tz)
    since = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    hours = (now_et - since).seconds // 3600 + 1
    secs = hours * 3600
    df_5m = fetch_df(symbol, duration=f'{secs} S', barSize='5 mins')
    if df_5m is None or df_5m.empty:
        return 0
    return df_5m.high.max() - df_5m.low.min()

# ─── ADDED HELPERS END ──────────────────────────────────────────────────────

# ─── ORDER ENTRY ────────────────────────────────────────────────────────────

def place_limit_bracket(symbol, entry, risk_pct):
    acct = {v.tag: float(v.value) for v in ib.accountSummary()}
    equity = acct['NetLiquidation']
    buying_power = acct.get('AvailableFunds', acct.get('BuyingPower', equity))
    usable = equity * (1 - BUFFER_FACTOR)
    risk_amt = usable * risk_pct

    df = fetch_df(symbol)
    atr = AverageTrueRange(df.high, df.low, df.close, window=ATR_PERIOD)
    atr_val = atr.average_true_range().iloc[-1]

    sl = round(entry - SL_ATR_MULT * atr_val, 5)
    tp = round(entry + TP_ATR_MULT * atr_val, 5)
    pip = 0.0001 if 'JPY' not in symbol else 0.01
    sl_pips = abs(entry - sl) / pip
    units = max(int((risk_amt / sl_pips) / pip), MIN_UNITS)
    notional = entry * units

    if notional > buying_power:
        logging.warning(f"{symbol}: notional {notional:.0f} > buying power {buying_power:.0f}, skipping")
        return False

    logging.info(f"{symbol}: BUY {units}@{entry}  SL@{sl}  TP@{tp}")
    c = Forex(symbol)
    grp = f"OCA_{symbol}_{int(time.time())}"
    orders = [
        LimitOrder('BUY', units, entry, tif='GTC'),
        LimitOrder('SELL', units, sl,   ocaGroup=grp, ocaType=2, tif='GTC', orderType='STP'),
        LimitOrder('SELL', units, tp,   ocaGroup=grp, ocaType=2, tif='GTC')
    ]
    for o in orders:
        try:
            ib.placeOrder(c, o)
        except Exception as e:
            logging.error(f"{symbol}: order failed ({o.action}@{o.lmtPrice}): {e}")
            return False
    return True

# ─── PER-SYMBOL PROCESSING ──────────────────────────────────────────────────

def process_symbol(symbol):
    now_et = datetime.now(pytz.timezone('America/New_York'))
    current_hour = now_et.hour
    windows = ALLOWED_HOURS.get(symbol, [])
    if not any(
        (start < end and start <= current_hour < end) or
        (start >= end and (current_hour >= start or current_hour < end))
        for start, end in windows
    ):
        return

    now = datetime.now(timezone.utc)
    prev = last_entry_time[symbol]
    if prev and (now - prev).total_seconds() < COOLDOWN_HOURS * 3600:
        return

    # ─── ADDED SCRIPT BEGIN ────────────────────────────────────────────────────
    h1_adx = compute_hourly_adx(symbol, lookback_days=7)
    if h1_adx < ENTRY_ADX_THRESH[symbol]:
        return  # no clear trend

    adr = compute_adr(symbol, window_days=14)
    todays_range = today_range(symbol)
    if todays_range < 0.5 * adr:
        return  # too little volatility today

    df_5m = fetch_df(symbol, duration='4 D', barSize='5 mins')
    if len(df_5m) < EMA_PERIOD + 1:
        logging.warning(f"{symbol}: insufficient 5m data")
        return

    price5m, prev_p5m = df_5m.close.iloc[-1], df_5m.close.iloc[-2]
    ema50_5m = df_5m.close.ewm(span=EMA_PERIOD).mean().iloc[-1]

    if not (prev_p5m <= ema50_5m < price5m):
        return
    # ─── ADDED SCRIPT END ──────────────────────────────────────────────────────

    df = fetch_df(symbol)
    if len(df) < EMA_PERIOD + 1:
        logging.warning(f"{symbol}: insufficient data")
        return

    price, prev_p = df.close.iloc[-1], df.close.iloc[-2]
    ema50 = df.close.ewm(span=EMA_PERIOD).mean().iloc[-1]
    if not (prev_p <= ema50 < price):
        return

    lookback = ADX_LOOKBACK.get(symbol, 7)
    adx_df = fetch_df(symbol, duration=f'{lookback} D')
    adx_val = ADXIndicator(adx_df.high, adx_df.low, adx_df.close, window=ADX_PERIOD).adx().iloc[-1]

    if adx_val >= HIGH_ADX_THRESH[symbol]:
        pct = RISK_HIGH
    elif adx_val >= ENTRY_ADX_THRESH[symbol]:
        pct = RISK_MED
    else:
        return

    if place_limit_bracket(symbol, price, pct):
        last_entry_time[symbol] = now

# ─── MAIN LOOP ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.info("🤖 Atlas Alpha Pro v4.4j – per-pair ADX entry thresholds loaded.")
    while True:
        logging.info("🕒 Cycle start")
        for sym in SYMBOLS:
            try:
                process_symbol(sym)
            except Exception:
                logging.exception(f"Error on {sym}, continuing.")
        logging.info(f"💤 Sleeping for {RUN_INTERVAL}s")
        time.sleep(RUN_INTERVAL)