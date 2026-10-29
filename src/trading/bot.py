"""
bot.py  (v2)
============
The trading bot + the ATLAS website, in one process.

    python src/trading/bot.py        then open http://127.0.0.1:5000

What it does each trading day (all times IST)
---------------------------------------------
09:15+  first scan with a valid Fyers token:
          1. tops up data/history from Fyers (yesterday's completed bars)
          2. scores every stock in the universe with the ML model
          3. buys the highest-confidence setups (P(win) >= threshold),
             at most CAP_PER_DAY new trades and MAX_OPEN open positions
             (entries allowed until ENTRY_CUTOFF)
every ~10 s: live quotes for the focus stocks + every open position;
             sells when price reaches the take-profit or the stop-loss
15:20+  time stop: positions that reached MAX_HOLD trading days are sold

Positions and closed trades are saved to paper_trades.json, so a restart
(or the daily start_today.command) no longer forgets open positions.

Fyers tokens expire every day. The bot no longer crashes or freezes on an
expired token: it shows "FYERS TOKEN EXPIRED" on the dashboard and picks
up the new token by itself as soon as get_token.py saves it.
"""
from __future__ import annotations

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)

import datetime as dt
import json
import socket
import sys

# print() lines show up immediately even when piped through `tee`
sys.stdout.reconfigure(line_buffering=True)
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

socket.setdefaulttimeout(10)

from flask import Flask, jsonify
from flask_cors import CORS

import atlas_api
import fyers_execution as execution
import ml_signals
import update_data
from config import SECRETS_PATH
from universe import FOCUS_SYMBOLS, fyers_symbol, short_symbol

from paths import ROOT as BASE_DIR  # noqa: E402
JOURNAL = BASE_DIR / "paper_trades.json"
SCAN_SECONDS = 10
ENTRY_CUTOFF = (11, 0)       # no new entries after 11:00 (model assumes an open-ish entry)
PREP_FROM = (8, 30)          # start the daily data update + scoring this early (before the 9:15 open)
TIME_STOP_AT = (15, 20)      # sell positions that reached MAX_HOLD days

# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
try:
    MODEL = ml_signals.get_model()
    RULES = MODEL.rules
    print(f"ML model loaded: {MODEL.meta.get('model_name')} | today's threshold P(win) >= {MODEL.threshold:.3f} "
          f"(top {(1-(MODEL.q or 0))*100:.1f}% of recent scores) | "
          f"holdout win rate {MODEL.meta.get('holdout', {}).get('win_rate', 0):.1%}")
except Exception as e:
    print(f"! Model not available ({e}). The website runs; the bot will not trade until train_model.py is run.")
    MODEL, RULES = None, {"cap_per_day": 3, "max_open": 10, "max_hold": 15}

# ---------------------------------------------------------------------------
# Fyers client with daily-token hot reload
# ---------------------------------------------------------------------------
_tl = threading.local()
_token = {"status": "missing", "msg": "not checked yet", "mtime": None}
_pool = ThreadPoolExecutor(max_workers=4)


def _secrets_mtime():
    try:
        return SECRETS_PATH.stat().st_mtime
    except FileNotFoundError:
        return None


def fyers():
    """This thread's Fyers client, recreated whenever fyers_secrets.json
    changes (i.e. after get_token.py saves a new token). None if no valid
    token for today."""
    from config import get_fyers_client
    mt = _secrets_mtime()
    if getattr(_tl, "mtime", "x") != mt or getattr(_tl, "client", None) is None:
        try:
            _tl.client = get_fyers_client()
            _token.update(status="ok", msg="valid for today")
        except Exception as e:
            _tl.client = None
            _token.update(status="expired", msg=str(e)[:160])
        _tl.mtime = mt
    return _tl.client if _token["status"] == "ok" else None


def quotes(fy_symbols: list[str]) -> dict:
    """{short_symbol: {price, prev, volume}} for up to 50 symbols per call."""
    out = {}
    cli = fyers()
    if not cli or not fy_symbols:
        return out
    for i in range(0, len(fy_symbols), 50):
        chunk = fy_symbols[i:i + 50]
        try:
            resp = _pool.submit(cli.quotes, {"symbols": ",".join(chunk)}).result(timeout=8)
        except FutTimeout:
            print("  Quote request timed out (8s); skipping this cycle")
            continue
        except Exception as e:
            print(f"  Quote error: {e}")
            continue
        if resp.get("s") != "ok":
            msg = str(resp.get("message", resp))
            if resp.get("code") in (-15, -16, -17) or "token" in msg.lower():
                _token.update(status="expired", msg="Fyers rejected the token: " + msg[:100])
                _tl.client = None
            else:
                print(f"  Quote response: {msg[:160]}")
            continue
        for q in resp.get("d", []):
            v = q.get("v", {})
            if v.get("lp"):
                out[short_symbol(q.get("n", v.get("symbol", "")))] = {
                    "price": float(v["lp"]), "prev": float(v.get("prev_close_price") or 0) or None,
                    "volume": v.get("volume"), "t": time.time()}
    return out


# ---------------------------------------------------------------------------
# journal (persistent paper positions + closed trades)
# ---------------------------------------------------------------------------
_lock = threading.RLock()


def _load_journal():
    if JOURNAL.exists():
        try:
            return json.loads(JOURNAL.read_text())
        except Exception:
            print("! paper_trades.json unreadable; starting a fresh journal (old file kept as .bad)")
            JOURNAL.rename(JOURNAL.with_suffix(".json.bad"))
    return {"positions": {}, "closed": [], "orders": [], "signals": {}}


J = _load_journal()


def _save():
    tmp = JOURNAL.with_suffix(".tmp")
    tmp.write_text(json.dumps(J, indent=1, default=str))
    tmp.replace(JOURNAL)


# ---------------------------------------------------------------------------
# live state for the dashboard
# ---------------------------------------------------------------------------
LIVE = {}            # short symbol -> latest quote
S = {"last_scan": None, "market": "CLOSED", "signals_date": None, "signals": [], "all_scores": {}, "reason": "",
     "prep": None,  # None | "running" | date (done)
     "data_update": None}


def trading_days_between(d0: str, d1: dt.date) -> int:
    import numpy as np
    return int(np.busday_count(dt.date.fromisoformat(d0), d1))


def market_phase(now=None):
    now = now or dt.datetime.now()
    if now.weekday() >= 5:
        return "CLOSED"
    t = (now.hour, now.minute)
    if t < (9, 15):
        return "PRE-OPEN"
    if t >= (15, 30):
        return "CLOSED"
    return "OPEN"


def bot_state() -> dict:
    with _lock:
        today = dt.date.today().isoformat()
        pos = []
        unreal = 0.0
        for sym, p in J["positions"].items():
            px = LIVE.get(sym, {}).get("price") or p["entry"]
            pnl = (px - p["entry"]) * p["qty"]
            unreal += pnl
            pos.append({**p, "symbol": sym, "price": px, "pnl": round(pnl, 2),
                        "pnl_pct": round((px / p["entry"] - 1) * 100, 2),
                        "days_held": trading_days_between(p["entry_date"], dt.date.today()),
                        "max_hold": RULES.get("max_hold", 15)})
        closed_today = [c for c in J["closed"] if c["exit_date"] == today]
        realized = sum(c["pnl"] for c in closed_today)
        allc = J["closed"]
        wins = sum(1 for c in allc if c["pnl"] > 0)
        orders_today = [o for o in J["orders"] if o["date"] == today]
        sig_map = dict(S["all_scores"])
        focus = []
        for s in FOCUS_SYMBOLS:
            q = LIVE.get(s, {})
            focus.append({"symbol": s, "price": q.get("price"),
                          "pc": ((q["price"] / q["prev"] - 1) * 100) if q.get("prev") else None, "p": sig_map.get(s)})
        meta = MODEL.meta if MODEL else {}
        return {
            "mode": "LIVE" if execution.LIVE_TRADING else "PAPER",
            "token_status": _token["status"], "token_msg": _token["msg"],
            "market": S["market"], "last_scan": S["last_scan"], "reason": S["reason"],
            "universe_size": len(meta.get("symbols", [])), "signals_date": S["signals_date"],
            "signals": [{"symbol": r["symbol"], "p": r["p"], "action": r.get("action", "")} for r in S["signals"][:15]],
            "focus": focus, "positions": pos, "orders": J["orders"][-60:],
            "today": {"orders": len(orders_today), "realized_pnl": round(realized, 2),
                      "unrealized_pnl": round(unreal, 2), "total_pnl": round(realized + unreal, 2)},
            "alltime": {"trades": len(allc), "wins": wins, "win_rate": wins / len(allc) if allc else None,
                        "realized_pnl": round(sum(c["pnl"] for c in allc), 2)},
            "model": {"name": meta.get("model_name"), "threshold": meta.get("threshold"), "rules": RULES if MODEL else None,
                      "oos_win_rate": meta.get("holdout", {}).get("win_rate"), "oos_trades": meta.get("holdout", {}).get("n"),
                      "trained_at": meta.get("trained_at")},
            "data_update": S["data_update"],
        }


def legacy_state() -> dict:
    """Old /state shape, so the classic dashboard.html still works."""
    b = bot_state()
    stocks = {}
    for f in b["focus"]:
        sym = fyers_symbol(f["symbol"])
        held = next((p for p in b["positions"] if p["symbol"] == f["symbol"]), None)
        stocks[sym] = {"price": f["price"] or 0, "signal": "HOLDING" if held else "WATCHING",
                       "ml_signal": "BUY" if (f["p"] or 0) >= (b["model"]["threshold"] or 1) else "NO TRADE",
                       "ml_confidence": f["p"] or 0, "pnl": held["pnl"] if held else 0,
                       "orders": [o for o in b["orders"] if o["symbol"] == f["symbol"]],
                       "price_history": [], "volume_history": [], "score": 0, "signal_reason": ""}
    return {"symbols": list(stocks), "use_ml": True, "stocks": stocks,
            "total_pnl": b["today"]["total_pnl"], "total_orders": b["today"]["orders"]}


# ---------------------------------------------------------------------------
# trading
# ---------------------------------------------------------------------------
def _order(side, sym, price, qty, reason, pnl=None):
    _, resp = execution.place_order(fyers_symbol(sym), side, price, qty=qty)
    now = dt.datetime.now()
    o = {"date": now.date().isoformat(), "time": now.strftime("%H:%M:%S"), "type": side, "symbol": sym,
         "qty": qty, "price": round(price, 2), "reason": reason, "live": execution.LIVE_TRADING}
    if pnl is not None:
        o["pnl"] = round(pnl, 2)
    if resp is not None:
        o["broker"] = str(resp)[:200]
    J["orders"].append(o)
    J["orders"] = J["orders"][-500:]
    tag = "LIVE" if execution.LIVE_TRADING else "PAPER"
    print(f"\n  {side}[{tag}] {sym} {qty}x Rs.{price:.2f} | {reason}" + (f" | P&L Rs.{pnl:.2f}" if pnl is not None else ""))


def open_position(r: dict, price: float):
    from strategy import exit_levels
    qty = execution.position_size(price)
    tp, sl = exit_levels(price, r["atr_pct"], RULES["tp_atr"], RULES["sl_atr"])
    J["positions"][r["symbol"]] = {"qty": qty, "entry": round(price, 2), "entry_date": dt.date.today().isoformat(),
                                   "tp": round(tp, 2), "sl": round(sl, 2), "p": r["p"], "signal_date": r["asOf"]}
    _order("BUY", r["symbol"], price, qty, f"model P(win) {r['p']:.1%} >= {MODEL.threshold:.1%}")
    execution.risk_manager.record_trade(0)


def close_position(sym: str, price: float, why: str):
    p = J["positions"].pop(sym)
    from strategy import round_trip_cost
    gross = (price - p["entry"]) * p["qty"]
    charges = round_trip_cost(p["entry"] * p["qty"]) * p["entry"] * p["qty"]
    pnl = gross - charges
    J["closed"].append({"symbol": sym, "entry_date": p["entry_date"], "exit_date": dt.date.today().isoformat(),
                        "entry": p["entry"], "exit": round(price, 2), "qty": p["qty"], "gross": round(gross, 2),
                        "charges": round(charges, 2), "pnl": round(pnl, 2), "why": why, "p": p.get("p")})
    _order("SELL", sym, price, p["qty"], why, pnl)
    execution.risk_manager.record_trade(pnl)


def morning_routine(today: dt.date):
    """Once per day: refresh history, score the universe, open new positions."""
    cli = fyers()
    if cli is None:
        S["reason"] = "Waiting for a valid Fyers token (run start_today.command or get_token.py)."
        return False
    print("\n[morning] updating daily history from Fyers...")
    t0 = time.time()
    res = update_data.update_from_fyers(cli, days=20, log=print)
    S["data_update"] = {"at": dt.datetime.now().isoformat(timespec="seconds"), "ok": res["ok"],
                        "failed": len(res["failed"]), "source": "Fyers"}
    print(f"[morning] {res['ok']} files updated, {res.get('skipped', 0)} already current, "
          f"{len(res['failed'])} failed ({time.time()-t0:.0f}s)")
    for sym, why in res["failed"][:5]:
        print(f"   ! {sym}: {why}")
    if res["ok"] == 0:
        S["reason"] = "Daily data update failed; not trading today until it succeeds."
        return False
    if MODEL is None:
        S["reason"] = "No v2 model; run train_model.py."
        return True
    sig = ml_signals.daily_candidates(today=today)
    rows = sig["rows"]
    S["all_scores"] = {r["symbol"]: r["p"] for r in sig["all"]}
    MODEL.log_scores(sig["asOf"], sig["all"]) if sig["asOf"] else None
    if sig["stale"]:
        print(f"[morning] {len(sig['stale'])} symbols skipped: history not current ({', '.join(sig['stale'][:8])}...)")
    S["signals"], S["signals_date"] = rows, sig["asOf"]
    J["signals"][today.isoformat()] = [{"symbol": r["symbol"], "p": r["p"]} for r in rows[:20]]
    picks = [r for r in rows if r["p"] >= MODEL.threshold]
    print(f"[morning] scored {len(sig['all'])} stocks from {sig['asOf']} close, {len(rows)} pass the trend/volatility gate; "
          f"{len(picks)} above today's threshold "
          f"{MODEL.threshold:.3f}. Top 5: " + ", ".join(f"{r['symbol']} {r['p']:.3f}" for r in rows[:5]))
    for r in rows:
        r["action"] = "BUY" if r["p"] >= MODEL.threshold else "—"
    return True


def try_entries(now: dt.datetime):
    if MODEL is None or (now.hour, now.minute) >= ENTRY_CUTOFF or execution.risk_manager.should_halt():
        return
    today = now.date().isoformat()
    bought_today = sum(1 for o in J["orders"] if o["date"] == today and o["type"] == "BUY")
    picks = [r for r in S["signals"] if r["p"] >= MODEL.threshold and r["symbol"] not in J["positions"]]
    if not picks:
        return
    want = [r for r in picks][: max(0, RULES["cap_per_day"] - bought_today)]
    if not want:
        return
    q = quotes([fyers_symbol(r["symbol"]) for r in want])
    LIVE.update(q)
    for r in want:
        if len(J["positions"]) >= RULES["max_open"]:
            break
        px = q.get(r["symbol"], {}).get("price")
        if px:
            r["action"] = "BOUGHT"
            open_position(r, px)
    _save()


def check_exits(now: dt.datetime):
    today = now.date()
    for sym in list(J["positions"]):
        p = J["positions"][sym]
        px = LIVE.get(sym, {}).get("price")
        if not px or time.time() - LIVE[sym].get("t", 0) > 60:
            continue
        if p["entry_date"] == today.isoformat() and px < p["tp"] and px > p["sl"]:
            continue
        if px >= p["tp"]:
            close_position(sym, px, f"take-profit (target Rs.{p['tp']})")
        elif px <= p["sl"]:
            close_position(sym, px, f"stop-loss (stop Rs.{p['sl']})")
        elif (now.hour, now.minute) >= TIME_STOP_AT and trading_days_between(p["entry_date"], today) >= RULES["max_hold"] - 1:
            close_position(sym, px, f"time stop ({RULES['max_hold']} trading days)")
    _save()


def run_bot():
    mode_tag = "LIVE TRADING (real orders, real money)" if execution.LIVE_TRADING else "PAPER TRADING (simulated, no real orders)"
    print("\n" + "=" * 70)
    print(f"  BOT STARTED - {mode_tag}")
    if MODEL:
        print(f"  Universe: {len(MODEL.meta.get('symbols', []))} NSE stocks | focus: {', '.join(FOCUS_SYMBOLS)}")
        print(f"  Rules: buy at P(win) >= {MODEL.threshold:.3f}; TP {RULES['tp_atr']}xATR, SL {RULES['sl_atr']}xATR, "
              f"max {RULES['max_hold']} days; <= {RULES['cap_per_day']} new/day, <= {RULES['max_open']} open")
    print(f"  Open positions carried over: {list(J['positions']) or 'none'}")
    print("  Website: http://127.0.0.1:5000")
    print("=" * 70 + "\n")
    last_wait_msg = 0

    last_prep_try = [0.0]

    def prep(day):
        S["prep"] = "running"
        last_prep_try[0] = time.time()
        try:
            S["prep"] = day if morning_routine(day) else None
        except Exception:
            traceback.print_exc()
            S["prep"] = None

    while True:
        now = dt.datetime.now()
        S["market"] = market_phase(now)
        # daily data update + scoring, in the background so exit checks keep running.
        # Starts from 08:30 so it is ready by the 09:15 open (Fyers allows 50 history calls/min).
        if (now.weekday() < 5 and (now.hour, now.minute) >= PREP_FROM and (now.hour, now.minute) < (15, 30)
                and S["prep"] not in ("running", now.date()) and time.time() - last_prep_try[0] > 300
                and fyers() is not None):
            threading.Thread(target=prep, args=(now.date(),), daemon=True).start()
        try:
            if S["market"] != "OPEN":
                if time.time() - last_wait_msg > 900:
                    print(f"[{now:%H:%M}] Market {S['market'].lower()}. Positions: {list(J['positions']) or 'none'}")
                    last_wait_msg = time.time()
                if S["market"] == "PRE-OPEN" and fyers() is None:
                    S["reason"] = "Fyers token expired - run start_today.command (the bot will pick it up automatically)."
                time.sleep(30)
                continue
            if fyers() is None:
                S["reason"] = "Fyers token expired - run start_today.command or get_token.py; the bot reloads it automatically."
                if time.time() - last_wait_msg > 300:
                    print(f"[{now:%H:%M}] {S['reason']}")
                    last_wait_msg = time.time()
                time.sleep(15)
                continue
            S["reason"] = "" if S["prep"] == now.date() else "Updating today's data from Fyers and scoring stocks (a few minutes)..."
            if S["prep"] == now.date():
                try_entries(now)
            syms = sorted(set(FOCUS_SYMBOLS) | set(J["positions"]))
            q = quotes([fyers_symbol(s) for s in syms])
            LIVE.update(q)
            S["last_scan"] = dt.datetime.now().isoformat(timespec="seconds")
            with _lock:
                check_exits(now)
            line = " | ".join(f"{s} {LIVE[s]['price']:.2f}" for s in syms if s in q)
            print(f"[{now:%H:%M:%S}] {line} | open: {len(J['positions'])}")
        except Exception:
            traceback.print_exc()
        time.sleep(SCAN_SECONDS)


# ---------------------------------------------------------------------------
# web server
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)
atlas_api.HOOKS["bot_state"] = bot_state
atlas_api.HOOKS["live_prices"] = lambda: dict(LIVE)
app.register_blueprint(atlas_api.bp)


@app.route("/state")
def get_state():
    return jsonify(legacy_state())


if __name__ == "__main__":
    fyers()
    print(f"Fyers token: {_token['status']} ({_token['msg']})")
    threading.Thread(target=run_bot, daemon=True).start()
    print("Website + API at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, threaded=True)
