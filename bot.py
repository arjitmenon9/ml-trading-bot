from flask import Flask, jsonify
from flask_cors import CORS
import threading
import time
import datetime

from ml_signals import MLSignalEngine
from config import get_fyers_client
import fyers_execution as execution

# Credentials now live in fyers_secrets.json (see config.py) instead of being
# hard-coded here. Run auth.py then get_token.py each trading day to refresh
# the access token (Fyers tokens expire daily - this is a Fyers platform
# rule, not something we can work around).
fyers = get_fyers_client()

# ---------------------------------------------------------------------------
# ML SWITCH: True = trade on the trained model's signal (this project's goal).
# False = fall back to the original hand-tuned scoring rule below, e.g. if
# you haven't trained a model yet, or want to A/B compare the two live.
# Either way both signals are computed and shown on the dashboard so you can
# always see them side by side.
# ---------------------------------------------------------------------------
USE_ML = True

try:
    ml_engine = MLSignalEngine()
    print(f"ML model loaded: {ml_engine.metadata.get('model_name')} "
          f"(test accuracy {ml_engine.metadata.get('test_accuracy')})")
    if str(ml_engine.metadata.get("data_source", "")).startswith("demo"):
        print("!" * 70)
        print("! WARNING: this model was trained on SYNTHETIC demo data.")
        print("! Its signals are for learning/testing the pipeline only.")
        print("! Run fetch_data.py + train_model.py with real data before")
        print("! trusting this bot's decisions with a real account.")
        print("!" * 70)
except FileNotFoundError as e:
    print(f"! {e}")
    print("! Falling back to rule-based signals only. Run train_model.py to enable ML.")
    ml_engine = None
    USE_ML = False

SYMBOLS = [
    "NSE:HDFCBANK-EQ",
    "NSE:AUBANK-EQ",
    "NSE:BAJAJFINSV-EQ",
    "NSE:RELIANCE-EQ"
]

NEEDED = 10

state = {
    "symbols": SYMBOLS,
    "use_ml": USE_ML,
    "stocks": {
        sym: {
            "price": 0, "ema9": 0, "ema21": 0, "rsi": 0,
            "macd": 0, "macd_signal": 0, "bb_upper": 0,
            "bb_lower": 0, "bb_mid": 0, "vwap": 0, "score": 0,
            "signal": "WAITING", "signal_reason": "",
            "ml_signal": "WAITING", "ml_confidence": 0,
            "price_history": [], "volume_history": [],
            "pnl": 0, "orders": []
        }
        for sym in SYMBOLS
    },
    "total_pnl": 0,
    "total_orders": 0
}

positions = {}
app = Flask(__name__)
CORS(app)

@app.route("/state")
def get_state():
    return jsonify(state)

def get_quote_single(symbol):
    try:
        resp = fyers.quotes({"symbols": symbol})
        # Try all known response formats
        if 'd' in resp and resp['d']:
            v = resp['d'][0]['v']
        elif 'data' in resp and resp['data']:
            v = resp['data'][0]['v']
        else:
            print(f"  Unknown response for {symbol}: {resp}")
            return 0, 0
        return v.get('lp', 0), v.get('volume', 1000)
    except Exception as e:
        print(f"  Quote error [{symbol}]: {e}")
        return 0, 0

def calculate_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def calculate_rsi(prices, period=14):
    # NOTE (fixed): the original version always read prices[0:period+1], i.e.
    # the OLDEST ticks collected, so RSI silently froze after the first
    # (period+1) ticks and never updated again. It now uses the most recent
    # window, like a real rolling RSI.
    if len(prices) < period + 1:
        return None
    window = prices[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(window)):
        change = window[i] - window[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None, None
    macd_values = []
    for i in range(slow - 1, len(prices)):
        ef = calculate_ema(prices[:i+1], fast)
        es = calculate_ema(prices[:i+1], slow)
        if ef and es:
            macd_values.append(ef - es)
    if len(macd_values) < signal:
        return None, None, None
    macd_line = macd_values[-1]
    signal_line = calculate_ema(macd_values, signal)
    return macd_line, signal_line, macd_line - signal_line if signal_line else None

def calculate_bollinger_bands(prices, period=20, std_dev=2):
    if len(prices) < period:
        return None, None, None
    recent = prices[-period:]
    mid = sum(recent) / period
    std = (sum((p - mid) ** 2 for p in recent) / period) ** 0.5
    return mid + std_dev * std, mid, mid - std_dev * std

def calculate_vwap(prices, volumes):
    if len(prices) < 2:
        return None
    total_v = sum(volumes)
    if total_v == 0:
        return None
    return sum(p * v for p, v in zip(prices, volumes)) / total_v

def calculate_stochastic(prices, period=14):
    if len(prices) < period:
        return None
    recent = prices[-period:]
    lowest, highest = min(recent), max(recent)
    if highest == lowest:
        return 50
    return ((prices[-1] - lowest) / (highest - lowest)) * 100

def calculate_score(price, prices, volumes):
    score = 0
    reasons = []
    ema9  = calculate_ema(prices, 9)
    ema21 = calculate_ema(prices, 21)
    rsi   = calculate_rsi(prices, 14)
    macd, macd_sig, _ = calculate_macd(prices)
    bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(prices)
    vwap  = calculate_vwap(prices, volumes)
    stoch = calculate_stochastic(prices)

    if ema9 and ema21:
        if ema9 > ema21: score += 1; reasons.append("EMA9>EMA21 ✅")
        else:            score -= 1; reasons.append("EMA9<EMA21 ❌")
    if rsi:
        if rsi < 40:    score += 2; reasons.append(f"RSI oversold({rsi:.0f}) ✅✅")
        elif rsi < 60:  score += 1; reasons.append(f"RSI bullish({rsi:.0f}) ✅")
        elif rsi > 75:  score -= 2; reasons.append(f"RSI overbought({rsi:.0f}) ❌❌")
        elif rsi > 60:  score -= 1; reasons.append(f"RSI bearish({rsi:.0f}) ❌")
    if macd and macd_sig:
        if macd > macd_sig: score += 1; reasons.append("MACD bullish ✅")
        else:               score -= 1; reasons.append("MACD bearish ❌")
    if bb_upper and bb_lower:
        if price < bb_lower:   score += 2; reasons.append("Below BB lower ✅✅")
        elif price > bb_upper: score -= 2; reasons.append("Above BB upper ❌❌")
        elif price < bb_mid:   score += 1; reasons.append("Below BB mid ✅")
        else:                  score -= 1; reasons.append("Above BB mid ❌")
    if vwap:
        if price > vwap: score += 1; reasons.append("Above VWAP ✅")
        else:            score -= 1; reasons.append("Below VWAP ❌")
    if stoch:
        if stoch < 20:   score += 2; reasons.append(f"Stoch oversold({stoch:.0f}) ✅✅")
        elif stoch > 80: score -= 2; reasons.append(f"Stoch overbought({stoch:.0f}) ❌❌")

    return score, reasons, ema9, ema21, rsi, macd, macd_sig, bb_upper, bb_mid, bb_lower, vwap

def paper_buy(symbol, price, score, reasons):
    short = symbol.replace("NSE:","").replace("-EQ","")
    qty, order_result = execution.place_order(symbol, "BUY", price)
    tag = "LIVE" if execution.LIVE_TRADING else "PAPER"
    print(f"\n  BUY[{tag}] [{short}] {qty}x Rs.{price} Score:{score} | {', '.join(reasons[:2])}")
    positions[symbol] = {"price": price, "qty": qty}
    state["stocks"][symbol]["orders"].append({
        "type": "BUY", "price": price, "qty": qty, "score": score, "live": execution.LIVE_TRADING,
        "time": datetime.datetime.now().strftime('%H:%M:%S')
    })
    state["stocks"][symbol]["signal"] = "BUY"
    state["stocks"][symbol]["signal_reason"] = " | ".join(reasons[:2])
    state["total_orders"] += 1

def paper_sell(symbol, price, score, reasons):
    short = symbol.replace("NSE:","").replace("-EQ","")
    pnl = 0
    qty = 1
    if symbol in positions:
        qty = positions[symbol].get("qty", 1)
        pnl = round((price - positions[symbol]["price"]) * qty, 2)
        state["stocks"][symbol]["pnl"] = round(state["stocks"][symbol]["pnl"] + pnl, 2)
        state["total_pnl"] = round(sum(s["pnl"] for s in state["stocks"].values()), 2)
    _, order_result = execution.place_order(symbol, "SELL", price)
    execution.risk_manager.record_trade(pnl)
    tag = "LIVE" if execution.LIVE_TRADING else "PAPER"
    print(f"\n  SELL[{tag}] [{short}] {qty}x Rs.{price} P&L:Rs.{pnl} Score:{score}")
    positions.pop(symbol, None)
    state["stocks"][symbol]["orders"].append({
        "type": "SELL", "price": price, "qty": qty, "pnl": pnl, "score": score,
        "live": execution.LIVE_TRADING, "time": datetime.datetime.now().strftime('%H:%M:%S')
    })
    state["stocks"][symbol]["signal"] = "SELL"
    state["stocks"][symbol]["signal_reason"] = " | ".join(reasons[:2])
    state["total_orders"] += 1

def process_symbol(symbol, price, volume):
    s = state["stocks"][symbol]
    s["price"] = price
    s["price_history"].append(price)
    s["volume_history"].append(volume if volume else 1000)
    prices  = s["price_history"]
    volumes = s["volume_history"]
    short = symbol.replace("NSE:","").replace("-EQ","")

    # --- ML signal: always computed (independent of the tick-count gate
    # below), since it keeps its own daily-bar history internally. ---
    ml_signal, ml_confidence, _ = (ml_engine.predict(symbol, price, volume)
                                    if ml_engine else ("DISABLED", 0.0, None))
    s["ml_signal"] = ml_signal
    s["ml_confidence"] = round(ml_confidence, 3)

    if len(prices) < NEEDED:
        s["signal"] = "COLLECTING"
        print(f"  [{short}] Rs.{price} | Collecting ({len(prices)}/{NEEDED}) | ML:{ml_signal}")
        return

    score, reasons, ema9, ema21, rsi, macd, macd_sig, bb_upper, bb_mid, bb_lower, vwap = \
        calculate_score(price, prices, volumes)

    s["score"]       = score
    s["ema9"]        = round(ema9, 2) if ema9 else 0
    s["ema21"]       = round(ema21, 2) if ema21 else 0
    s["rsi"]         = round(rsi, 2) if rsi else 0
    s["macd"]        = round(macd, 2) if macd else 0
    s["macd_signal"] = round(macd_sig, 2) if macd_sig else 0
    s["bb_upper"]    = round(bb_upper, 2) if bb_upper else 0
    s["bb_lower"]    = round(bb_lower, 2) if bb_lower else 0
    s["bb_mid"]      = round(bb_mid, 2) if bb_mid else 0
    s["vwap"]        = round(vwap, 2) if vwap else 0

    # NOTE (fixed): the original crashed here once len(prices) passed NEEDED
    # (10) but was still below what calculate_rsi/calculate_macd need
    # internally (15 / 35 points) - rsi/macd come back as None in that
    # window, and formatting None with `:.1f` raises. Guarded now.
    rsi_str = f"{rsi:.1f}" if rsi is not None else "n/a"
    print(f"  [{short}] Rs.{price} | Score:{score:+d} | RSI:{rsi_str} | ML:{ml_signal}({ml_confidence:.2f}) | ", end="")

    # --- Trading decision: ML signal if enabled & ready, else the original
    # rule-based score. Both are always visible in `state` for comparison. ---
    if USE_ML and ml_signal != "COLLECTING":
        decision = ml_signal
    else:
        if score >= 3:
            decision = "BUY"
        elif score <= -3:
            decision = "SELL"
        elif score < 0:
            decision = "EXIT"  # treated as a sell-to-flatten below
        else:
            decision = "HOLD"

    if decision == "BUY" and symbol not in positions:
        print("BUY 🟢")
        paper_buy(symbol, price, score, reasons)
    elif decision in ("SELL", "EXIT") and symbol in positions:
        print("SELL 🔴")
        paper_sell(symbol, price, score, reasons)
    else:
        s["signal"] = "HOLD"
        s["signal_reason"] = " | ".join(reasons[:2])
        print("HOLD ⚪")

def run_bot():
    mode_tag = "LIVE TRADING (real orders, real money)" if execution.LIVE_TRADING else "PAPER TRADING (simulated, no real orders)"
    print("\n" + "="*70)
    print(f"  BOT STARTED — {mode_tag} — 4 STOCKS")
    for sym in SYMBOLS:
        print(f"    {sym}")
    mode = "ML MODEL" if USE_ML else "RULE-BASED SCORE"
    print(f"  Decision mode: {mode} (score/ML both tracked either way)")
    if execution.LIVE_TRADING:
        print(f"  Risk limits: max Rs.{execution.MAX_POSITION_VALUE}/trade, "
              f"daily loss limit Rs.{execution.DAILY_LOSS_LIMIT}, "
              f"max {execution.MAX_TRADES_PER_DAY} trades/day")
    print("="*70 + "\n")

    while True:
        now = datetime.datetime.now()

        if now.hour < 9 or (now.hour == 9 and now.minute < 15):
            print("Waiting for market to open (9:15 AM)...")
            time.sleep(60)
            continue
        if now.hour > 15 or (now.hour == 15 and now.minute >= 30):
            print(f"\nMarket closed. Total P&L: Rs.{state['total_pnl']}")
            break

        if execution.risk_manager.should_halt():
            print(f"\n*** KILL-SWITCH TRIPPED: {execution.risk_manager.halt_reason} ***")
            print("*** No new positions will be opened for the rest of today. ***")
            print("*** Existing open positions are left as-is - manage them manually if needed. ***")
            time.sleep(60)
            continue

        try:
            print(f"\n[{now.strftime('%H:%M:%S')}] ---- Scanning 4 stocks ----")
            for symbol in SYMBOLS:
                price, volume = get_quote_single(symbol)
                if price > 0:
                    process_symbol(symbol, price, volume)
                else:
                    print(f"  Skipping {symbol} — no price received")
                time.sleep(0.5)

            print(f"  Total P&L: Rs.{state['total_pnl']} | Positions: {list(positions.keys())}")

        except Exception as e:
            import traceback
            traceback.print_exc()

        time.sleep(10)

bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

print("Flask server starting at http://127.0.0.1:5000")
app.run(host="127.0.0.1", port=5000)
