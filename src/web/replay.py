"""
replay.py
=========
24/7 DEMO MODE ("LIVE SIM"): the trading bot trading REAL historical NSE
prices in real time, whether or not the market is open.

How it works
------------
* The trades are the model's real walk-forward (out-of-sample) trades from
  models/oos_trades.csv: same entries, same exits, same P&L after charges.
  Nothing is invented and no result is tuned for the demo.
* Each historical trading day is played as one simulated session lasting
  DAY_SECONDS of wall-clock time (default 45 s). During it prices tick every
  ~1.5 s along a path that follows that day's real open, high, low and close
  (data/history). Only days on which the bot holds or opens a position are
  played; idle days are skipped.
* BUY orders fire at the real open of the entry day. SELL orders fire when
  the ticking price reaches the trade's real exit level (take-profit or
  stop-loss) or at the close of the time-stop day.
* It starts at the 2022 holdout (years the model never saw while its
  settings were chosen), runs to October 2026, then continues with 2018-2021
  and loops. Progress is saved to replay_state.json, so it resumes after a
  restart.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from paths import ROOT as BASE  # noqa: E402
STATE_FILE = BASE / "data" / "replay_state.json"
TICKS = 30
DAY_SECONDS = 45.0


class Replay:
    def __init__(self):
        self.lock = threading.RLock()
        self.speed = 1
        self.ok = False
        self.error = None
        try:
            self._load()
            self.ok = True
        except Exception as e:  # no model yet etc.
            self.error = str(e)

    # ------------------------------------------------------------------ data
    def _load(self):
        t = pd.read_csv(BASE / "models" / "oos_trades.csv")
        meta = json.loads((BASE / "models" / "metadata.json").read_text())
        self.pv = meta.get("position_value", 50000)
        hold = t[t["entry_date"] >= "2022-01-01"].sort_values("entry_date")
        cal = t[t["entry_date"] < "2022-01-01"].sort_values("entry_date")
        self.trades = pd.concat([hold, cal], ignore_index=True)
        self.trades["tid"] = range(len(self.trades))
        hist = {}
        for s in self.trades["symbol"].unique():
            h = pd.read_csv(BASE / "data" / "history" / f"{s}.csv", usecols=["date", "open", "high", "low", "close"])
            hist[s] = h.set_index("date")
        self.hist = hist
        # schedule: ordered list of (date, segment) for days with activity, holdout segment first
        sched = []
        for seg, part in (("holdout", hold), ("calibration", cal)):
            days = set()
            for _, r in part.iterrows():
                idx = self.hist[r["symbol"]].index
                days.update(idx[(idx >= r["entry_date"]) & (idx <= r["exit_date"])])
            sched += [(d, seg) for d in sorted(days)]
        self.sched = sched
        self.by_entry = {}
        self.by_exit = {}
        for _, r in self.trades.iterrows():
            key_seg = "holdout" if r["entry_date"] >= "2022-01-01" else "calibration"
            self.by_entry.setdefault((r["entry_date"], key_seg), []).append(r)
            self.by_exit.setdefault(r["exit_date"], []).append(r)
        self.st = self._fresh()
        if STATE_FILE.exists():
            try:
                saved = json.loads(STATE_FILE.read_text())
                if saved.get("n_sched") == len(self.sched):
                    self.st = saved
            except Exception:
                pass

    def _fresh(self):
        return {"day_i": 0, "tick": 0, "cycle": 1, "orders": 0, "closed": 0, "wins": 0, "realized": 0.0,
                "open": {}, "log": [], "n_sched": len(self.sched), "started": time.strftime("%Y-%m-%d %H:%M")}

    # ------------------------------------------------------------------ paths
    def _path(self, sym, date, exit_px=None, exit_kind=None):
        o, h, l, c = (float(x) for x in self.hist[sym].loc[date, ["open", "high", "low", "close"]])
        rng = np.random.default_rng(abs(hash((sym, date))) % (2 ** 32))
        if exit_kind == "target":
            first, second = h, l
        elif exit_kind == "stop":
            first, second = l, h
        else:
            first, second = (h, l) if rng.random() < 0.5 else (l, h)
        k1, k2 = int(TICKS * rng.uniform(.2, .4)), int(TICKS * rng.uniform(.55, .8))
        xs, ys = [0, k1, k2, TICKS - 1], [o, first, second, c]
        p = np.interp(np.arange(TICKS), xs, ys)
        p = p * (1 + rng.normal(0, 0.0012, TICKS))
        p[0], p[k1], p[k2], p[-1] = o, first, second, c
        return np.clip(p, l, h)

    def _today_paths(self, date):
        paths = {}
        for tid, pos in self.st["open"].items():
            r = self.trades.iloc[int(tid)]
            kind = r["exit_reason"] if r["exit_date"] == date else None
            paths[tid] = self._path(r["symbol"], date, exit_kind=kind if kind in ("target", "stop") else None)
        return paths

    # ------------------------------------------------------------------ engine
    def _log(self, d, side, sym, px, why):
        self.st["log"].insert(0, {"date": d, "time": self._clock(), "type": side, "symbol": sym, "price": round(px, 2), "why": why})
        del self.st["log"][60:]

    def _clock(self):
        m = 555 + int(self.st["tick"] / (TICKS - 1) * 375)   # 09:15 .. 15:30
        return f"{m // 60:02d}:{m % 60:02d}"

    def step(self):
        with self.lock:
            st = self.st
            if st["day_i"] >= len(self.sched):
                st.update(day_i=0, tick=0, cycle=st["cycle"] + 1, open={})
            date, seg = self.sched[st["day_i"]]
            if st["tick"] == 0:
                for r in self.by_entry.get((date, seg), []):
                    q = max(1, int(self.pv // r["entry_px"]))
                    st["open"][str(int(r["tid"]))] = {"symbol": r["symbol"], "qty": q, "entry": float(r["entry_px"]),
                                                      "price": float(r["entry_px"]), "p": float(r["p"]), "entry_date": date}
                    st["orders"] += 1
                    self._log(date, "BUY", r["symbol"], r["entry_px"], f"model P(win) {r['p']*100:.1f}% (top 0.3% + gate)")
                self._paths = self._today_paths(date)
            if not hasattr(self, "_paths") or self._paths is None:
                self._paths = self._today_paths(date)
            for tid in list(st["open"]):
                pos = st["open"][tid]
                if tid not in self._paths:
                    self._paths[tid] = self._today_paths(date).get(tid)
                path = self._paths[tid]
                if path is None:
                    continue
                px = float(path[min(st["tick"], TICKS - 1)])
                pos["price"] = px
                r = self.trades.iloc[int(tid)]
                if r["exit_date"] != date:
                    continue
                exit_px = float(r["entry_px"] * (1 + r["gross_ret"]))
                kind = r["exit_reason"]
                hit = (kind == "target" and px >= exit_px) or (kind == "stop" and px <= exit_px) or st["tick"] >= TICKS - 1
                if hit:
                    pos["price"] = exit_px
                    pnl = float(r["net_ret"]) * self.pv
                    st["realized"] += pnl
                    st["closed"] += 1
                    st["wins"] += pnl > 0
                    st["orders"] += 1
                    label = {"target": "take-profit", "stop": "stop-loss", "time": "time stop"}.get(kind, kind)
                    self._log(date, "SELL", r["symbol"], exit_px, f"{label} | P&L {'+' if pnl >= 0 else '-'}Rs.{abs(pnl):,.0f} after charges")
                    del st["open"][tid]
            st["tick"] += 1
            if st["tick"] >= TICKS:
                st["tick"] = 0
                st["day_i"] += 1
                self._paths = None
                try:
                    STATE_FILE.write_text(json.dumps(st))
                except Exception:
                    pass

    def run(self):
        while True:
            if self.ok:
                try:
                    self.step()
                except Exception as e:
                    self.error = str(e)
            time.sleep(DAY_SECONDS / TICKS / max(1, self.speed))

    # ------------------------------------------------------------------ view
    def state(self):
        if not self.ok:
            return {"ok": False, "error": self.error}
        with self.lock:
            st = self.st
            date, seg = self.sched[min(st["day_i"], len(self.sched) - 1)]
            unreal = sum((p["price"] - p["entry"]) * p["qty"] for p in st["open"].values())
            pos = [{"symbol": p["symbol"], "qty": p["qty"], "entry": p["entry"], "price": round(p["price"], 2),
                    "pnl": round((p["price"] - p["entry"]) * p["qty"], 2), "pnl_pct": round((p["price"] / p["entry"] - 1) * 100, 2),
                    "p": p["p"], "entry_date": p["entry_date"]} for p in st["open"].values()]
            return {"ok": True, "sim_date": date, "sim_time": self._clock(), "segment": seg, "cycle": st["cycle"],
                    "speed": self.speed, "orders": st["orders"], "closed": st["closed"], "wins": st["wins"],
                    "win_rate": st["wins"] / st["closed"] if st["closed"] else None,
                    "realized": round(st["realized"], 2), "unrealized": round(unreal, 2),
                    "total_pnl": round(st["realized"] + unreal, 2), "positions": pos, "log": st["log"][:40],
                    "position_value": self.pv, "started": st["started"],
                    "progress": f"{st['day_i'] + 1}/{len(self.sched)} trading days"}

    def reset(self):
        with self.lock:
            self.st = self._fresh()
            self._paths = None


_R = None
_lock = threading.Lock()


def engine() -> Replay:
    global _R
    with _lock:
        if _R is None:
            _R = Replay()
            threading.Thread(target=_R.run, daemon=True).start()
    return _R
