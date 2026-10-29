"""
sim_market.py
=============
LIVE SIM: a simulated stock market running 24/7, and the bot trading it
in real time. NO historical data is used for prices: every price is
generated live by a random market model. Only each stock's starting
price level is taken from its latest real close, so the numbers look familiar.

The market model (per stock, one bar = 5 simulated minutes, one bar per
real second):
  * a slowly switching trend regime (up / flat / down, Markov switching),
  * mean reversion towards a moving fair value (prices over-shoot and come back),
  * random volatility clusters and occasional news jumps.
These parameters are fixed below and were NOT tuned to make the bot look good;
the win rate you see is simply what the bot achieves in this market.

The bot (same rules as the real strategy in strategy.py, applied to bars):
  1. computes indicators on the live chart with features.py (SMA 50/200,
     RSI-2, RSI-14, Bollinger z-score, ATR...),
  2. BUYS when the trend/volatility gate passes (SMA200 rising, price not far
     below it, ATR < 4%) and the chart shows a sharp dip (RSI-2 < 5 and
     price > 2 standard deviations below its 20-bar mean),
  3. SELLS at take-profit 0.75 x ATR, stop-loss 4 x ATR, or after 30 bars.
Trades are Rs 50,000 each and pay the same Fyers charges as real trades.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

import strategy

BASE = Path(__file__).parent
SYMBOLS = ["HDFCBANK", "RELIANCE", "AUBANK", "BAJAJFINSV", "ICICIBANK", "INFY", "TCS", "SBIN"]
BAR_SECONDS = 1.0          # one 5-minute bar per real second
BARS_PER_DAY = 75          # 09:15 .. 15:30
WARMUP = 260
MAX_OPEN = 4
MAX_HOLD_BARS = 30
POSITION = 50_000


def _rsi(c, n):
    """Wilder RSI on the whole array, last value (same formula as features.py)."""
    d = np.diff(c)
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    a = 1.0 / n
    ag = al = 0.0
    for i in range(len(d)):
        ag = ag + a * (g[i] - ag) if i else g[0]
        al = al + a * (l[i] - al) if i else l[0]
    return 50.0 if al == 0 else 100 - 100 / (1 + ag / al)


def indicators(bars):
    """The indicators the bot reads off the live chart (formulas as in features.py)."""
    c = np.array([b["close"] for b in bars]); h = np.array([b["high"] for b in bars]); l = np.array([b["low"] for b in bars])
    out = {"rsi": _rsi(c[-100:], 14), "rsi2": _rsi(c[-30:], 2)}
    sma50 = c[-50:].mean(); sma200 = c[-200:].mean(); sma200_prev = c[-220:-20].mean()
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = tr[-60:][0]
    for v in tr[-60:][1:]:
        atr += (v - atr) / 14
    m20, s20 = c[-20:].mean(), c[-20:].std(ddof=1)
    out.update(sma50=sma50, sma200=sma200, dist_sma200=(c[-1] / sma200 - 1) * 100,
               sma200_slope=(sma200 / sma200_prev - 1) * 100, atr_pct=atr / c[-1] * 100,
               zscore_20=(c[-1] - m20) / s20 if s20 else 0.0, bb_lower=m20 - 2 * s20, bb_upper=m20 + 2 * s20)
    return out


class Stock:
    def __init__(self, sym, price, rng):
        self.sym, self.rng = sym, rng
        self.p = price
        self.fair = price
        self.regime = 1
        self.vol = 0.0035
        self.bars = []
        self.prev_close = price

    def bar(self):
        r = self.rng
        if r.random() < 0.01:                       # regime switch
            self.regime = r.choice([1, 1, 0, -1])
        mu = {1: 0.00025, 0: 0.0, -1: -0.0002}[self.regime]
        self.vol = float(np.clip(self.vol * math.exp(r.normal(0, 0.08)) + 0.0002 * (0.0035 - self.vol) / 0.0035, 0.0015, 0.01))
        o = self.p
        path = [o]
        for _ in range(5):                          # 1-minute sub-steps
            rev = 0.04 * math.log(self.fair / path[-1])
            jump = r.normal(0, 0.012) if r.random() < 0.002 else 0.0
            path.append(path[-1] * math.exp(mu / 5 + rev / 5 + self.vol / math.sqrt(5) * r.normal() + jump))
        self.fair *= math.exp(mu + 0.002 * math.log(path[-1] / self.fair))
        c = path[-1]
        self.p = c
        b = {"open": o, "high": max(path), "low": min(path), "close": c, "volume": int(r.integers(2e4, 2e5))}
        self.bars.append(b)
        del self.bars[:-400]
        return b


class SimMarket:
    def __init__(self, seed=None):
        self.lock = threading.RLock()
        self.rng = np.random.default_rng(seed)
        self.stocks = {}
        for s in SYMBOLS:
            try:
                px = float(pd.read_csv(BASE / "data" / "history" / f"{s}.csv", usecols=["close"])["close"].iloc[-1])
            except Exception:
                px = 1000.0
            st = Stock(s, px, np.random.default_rng(self.rng.integers(1 << 31)))
            for _ in range(WARMUP):
                st.bar()
            self.stocks[s] = st
        self.bar_i = 0
        self.day = 1
        self.positions = {}
        self.log = []
        self.markers = {s: [] for s in SYMBOLS}
        self.ind = {s: {} for s in SYMBOLS}
        self.orders = self.closed = self.wins = 0
        self.realized = 0.0
        self.cost = strategy.round_trip_cost(POSITION)
        self.speed = 1
        self.started = time.strftime("%Y-%m-%d %H:%M")

    def clock(self):
        m = 555 + (self.bar_i % BARS_PER_DAY) * 5
        return f"Day {self.day} · {m // 60:02d}:{m % 60:02d}"

    def _log(self, side, s, px, why):
        self.log.insert(0, {"time": self.clock(), "type": side, "symbol": s, "price": round(px, 2), "why": why})
        del self.log[60:]

    def step(self):
        with self.lock:
            if self.bar_i and self.bar_i % BARS_PER_DAY == 0:
                self.day += 1
                for st in self.stocks.values():
                    st.prev_close = st.p
            self.bar_i += 1
            for s, st in self.stocks.items():
                b = st.bar()
                n = len(st.bars)
                self.ind[s] = indicators(st.bars[-260:])
                i = self.ind[s]
                pos = self.positions.get(s)
                if pos:                                   # exits on this bar's high/low
                    pos["bars"] += 1
                    exit_px, why = None, None
                    if b["low"] <= pos["sl"]:
                        exit_px, why = min(b["open"], pos["sl"]) if b["open"] < pos["sl"] else pos["sl"], "stop-loss"
                    elif b["high"] >= pos["tp"]:
                        exit_px, why = max(b["open"], pos["tp"]) if b["open"] > pos["tp"] else pos["tp"], "take-profit"
                    elif pos["bars"] >= MAX_HOLD_BARS:
                        exit_px, why = b["close"], "time stop"
                    pos["price"] = b["close"]
                    if exit_px:
                        net = (exit_px / pos["entry"] - 1) - self.cost
                        pnl = net * POSITION
                        self.realized += pnl; self.closed += 1; self.wins += pnl > 0; self.orders += 1
                        self.markers[s].append({"i": n - 1, "t": "S", "px": exit_px})
                        self._log("SELL", s, exit_px, f"{why} | P&L {'+' if pnl >= 0 else '-'}Rs.{abs(pnl):,.0f} after charges")
                        del self.positions[s]
                    continue
                gate = (i["sma200_slope"] or 0) > 0 and (i["dist_sma200"] or -99) > -10 and (i["atr_pct"] or 99) < 4
                dip = (i["rsi2"] or 50) < 5 and (i["zscore_20"] or 0) < -2.0
                if gate and dip and len(self.positions) < MAX_OPEN:
                    entry = b["close"]
                    tp, sl = strategy.exit_levels(entry, i["atr_pct"])
                    q = max(1, int(POSITION // entry))
                    self.positions[s] = {"symbol": s, "qty": q, "entry": entry, "price": entry, "tp": tp, "sl": sl, "bars": 0,
                                         "time": self.clock()}
                    self.orders += 1
                    self.markers[s].append({"i": n - 1, "t": "B", "px": entry})
                    self._log("BUY", s, entry, f"uptrend + dip: RSI-2 {i['rsi2']:.0f}, {i['zscore_20']:.1f} sd below 20-bar mean")
            for s in SYMBOLS:
                n = len(self.stocks[s].bars)
                self.markers[s] = [m for m in self.markers[s] if m["i"] > n - 400]

    def run(self):
        while True:
            try:
                self.step()
            except Exception as e:
                print("sim market error:", e)
            time.sleep(BAR_SECONDS / max(1, self.speed))

    def state(self):
        with self.lock:
            unreal = sum((p["price"] - p["entry"]) * p["qty"] for p in self.positions.values())
            stocks = []
            for s, st in self.stocks.items():
                n = len(st.bars)
                closes = [round(b["close"], 2) for b in st.bars[-120:]]
                off = n - len(closes)
                ind = self.ind[s]
                pos = self.positions.get(s)
                stocks.append({"symbol": s, "price": round(st.p, 2), "pc": (st.p / st.prev_close - 1) * 100, "closes": closes,
                               "sma50": [round(v, 2) for v in pd.Series([b["close"] for b in st.bars]).rolling(50).mean().iloc[-120:].fillna(0)],
                               "markers": [{"i": m["i"] - off, "t": m["t"]} for m in self.markers[s] if m["i"] >= off],
                               "rsi": ind.get("rsi"), "rsi2": ind.get("rsi2"), "z": ind.get("zscore_20"),
                               "trend": "UP" if (ind.get("sma200_slope") or 0) > 0 else "DOWN",
                               "status": (f"HOLDING {(pos['price'] / pos['entry'] - 1) * 100:+.2f}%" if pos else
                                          "WATCHING")})
            pos = [{"symbol": p["symbol"], "qty": p["qty"], "entry": round(p["entry"], 2), "price": round(p["price"], 2),
                    "tp": round(p["tp"], 2), "sl": round(p["sl"], 2), "pnl": round((p["price"] - p["entry"]) * p["qty"], 2),
                    "pnl_pct": round((p["price"] / p["entry"] - 1) * 100, 2), "bars": p["bars"], "since": p["time"]}
                   for p in self.positions.values()]
            return {"ok": True, "clock": self.clock(), "speed": self.speed, "orders": self.orders, "closed": self.closed,
                    "wins": self.wins, "win_rate": self.wins / self.closed if self.closed else None,
                    "realized": round(self.realized, 2), "unrealized": round(unreal, 2),
                    "total_pnl": round(self.realized + unreal, 2), "positions": pos, "log": self.log[:40],
                    "stocks": stocks, "position_value": POSITION, "started": self.started}


_M = None
_lock = threading.Lock()


def engine() -> SimMarket:
    global _M
    with _lock:
        if _M is None:
            _M = SimMarket()
            threading.Thread(target=_M.run, daemon=True).start()
    return _M
