"""
strategy.py
===========
The swing-trade rules the ML model is trained to filter, shared by
train_model.py, backtest.py and the live bot so all three agree.

THE TRADE (long only, daily bars)
---------------------------------
1. After the close of day t, the model looks at that day's completed bar
   and estimates P(win) for "buy tomorrow at the open".
2. If P(win) >= the threshold saved in models/metadata.json, buy at the
   next session's open (the live bot buys on its first scan after 9:15).
3. Exit at whichever happens first:
     - take-profit : price reaches entry * (1 + TP)       -> sell
     - stop-loss   : price falls to entry * (1 - SL)      -> sell
     - time stop   : still open at the close of day MAX_HOLD -> sell at close
   Live, TP/SL are checked on every 10-second quote. In the backtest they are
   checked on each day's high/low, and if a day touched BOTH levels we
   assume the stop was hit first (the pessimistic choice).
4. A trade "wins" if it makes money AFTER all charges (see costs below).

Why these exits produce a high win rate, and the catch
--------------------------------------------------------
TP is smaller than SL, so most trades that work out hit TP quickly, while
the losers are fewer but bigger. A high win rate on its own says nothing
about profit: what matters is win-rate x average win vs loss-rate x average
loss. backtest.py reports both (profit factor, expectancy), and the model's
threshold is chosen so that expectancy AFTER costs stays positive.
"""
import numpy as np
import pandas as pd

# Exit rules, as multiples of the stock's ATR% on the signal day (adapts to
# each stock's volatility). These defaults are overwritten by whatever
# train_model.py selects and saves in models/metadata.json.
TP_ATR = 0.75
SL_ATR = 4.0
MAX_HOLD = 15            # trading days
# Clamp so a very quiet / very wild stock can't produce silly levels.
TP_MIN, TP_MAX = 0.008, 0.06
SL_MIN, SL_MAX = 0.02, 0.15

# ---------------------------------------------------------------------------
# Fyers charges (https://fyers.in/charges-list.md, checked 2026-10-23)
#   brokerage  : min(Rs 20, 0.3%) per executed order, + 18% GST
#   STT        : 0.1% on buy and on sell (delivery)
#   exchange   : 0.0030699% per side, + 18% GST
#   stamp duty : 0.015% on buy
#   DP charge  : Rs 12.5 + GST per scrip on the sell day
# ---------------------------------------------------------------------------
SLIPPAGE = 0.0005        # 0.05% per side, assumed


def round_trip_cost(position_value: float) -> float:
    """Total round-trip cost as a FRACTION of position value (delivery/CNC)."""
    v = float(position_value)
    if v <= 0:
        return 0.0
    brokerage = 2 * min(20.0, 0.003 * v) * 1.18
    stt = 0.001 * v * 2
    exch = 0.000030699 * v * 2 * 1.18
    stamp = 0.00015 * v
    dp = 12.5 * 1.18
    return (brokerage + stt + exch + stamp + dp) / v + 2 * SLIPPAGE


def exit_levels(entry: float, atr_pct: float, tp_atr=None, sl_atr=None):
    """Returns (take_profit_price, stop_loss_price) for a long entry."""
    tp_atr = TP_ATR if tp_atr is None else tp_atr
    sl_atr = SL_ATR if sl_atr is None else sl_atr
    a = float(atr_pct) / 100.0
    tp = float(np.clip(tp_atr * a, TP_MIN, TP_MAX))
    sl = float(np.clip(sl_atr * a, SL_MIN, SL_MAX))
    return entry * (1 + tp), entry * (1 - sl)


def simulate_outcomes(df: pd.DataFrame, tp_atr: float, sl_atr: float, max_hold: int):
    """
    For every row t of ONE symbol's frame (needs open/high/low/close/atr_pct),
    simulate buying at open[t+1] with the exit rules above.
    Returns (gross_return, holding_days, exit_reason) arrays; NaN where the
    trade can't be fully simulated (end of data).
    exit_reason: 1 = take-profit, -1 = stop-loss, 0 = time stop.
    """
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a = df["atr_pct"].to_numpy(float) / 100.0
    n = len(df)
    ret = np.full(n, np.nan)
    days = np.full(n, np.nan)
    why = np.full(n, np.nan)
    tp_f = np.clip(tp_atr * a, TP_MIN, TP_MAX)
    sl_f = np.clip(sl_atr * a, SL_MIN, SL_MAX)
    for t in range(n - 1):
        if not np.isfinite(a[t]):
            continue
        e = o[t + 1]
        if not np.isfinite(e) or e <= 0:
            continue
        tp, sl = e * (1 + tp_f[t]), e * (1 - sl_f[t])
        done = False
        for k in range(1, max_hold + 1):
            j = t + k
            if j >= n:
                break
            if k > 1 and o[j] <= sl:            # gapped through the stop
                ret[t], days[t], why[t] = o[j] / e - 1, k, -1; done = True; break
            if k > 1 and o[j] >= tp:            # gapped through the target
                ret[t], days[t], why[t] = o[j] / e - 1, k, 1; done = True; break
            if l[j] <= sl:                      # pessimistic: stop before target
                ret[t], days[t], why[t] = sl / e - 1, k, -1; done = True; break
            if h[j] >= tp:
                ret[t], days[t], why[t] = tp / e - 1, k, 1; done = True; break
            if k == max_hold:
                ret[t], days[t], why[t] = c[j] / e - 1, k, 0; done = True; break
        if not done:
            ret[t] = np.nan
    return ret, days, why


# ---------------------------------------------------------------------------
# Trade gate (v2.1). The model is only trained on, and only allowed to buy,
# setups that pass these plain-English rules. Walk-forward analysis showed
# the model's losing trades were concentrated in exactly these situations:
#   * stock in a long-term DOWNtrend (200-day average falling, or price
#     more than 10% below it)      -> "falling knife", 73-76% win rate
#   * India VIX above 30 (panic)   -> 58% win rate
#   * stock moving > 4% a day (ATR) -> 73% win rate
# ---------------------------------------------------------------------------
GATE = {"sma200_slope_min": 0.0, "dist_sma200_min": -10.0, "vix_max": 30.0, "atr_pct_max": 4.0}


def passes_gate(df):
    """Boolean Series/array: rows the strategy is allowed to trade."""
    vix_ok = ~(df["vix"] > GATE["vix_max"])          # missing VIX (pre-2014) is allowed
    return ((df["sma200_slope"] > GATE["sma200_slope_min"]) & (df["dist_sma200"] > GATE["dist_sma200_min"])
            & vix_ok & (df["atr_pct"] < GATE["atr_pct_max"]))
