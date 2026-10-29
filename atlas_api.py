"""
atlas_api.py
============
Flask blueprint that serves the ATLAS front end (atlas/index.html) and its
JSON API. Registered by bot.py (bot + website in one process) and by
atlas_server.py (website only, no Fyers login needed).

Routes
------
  GET /                         ATLAS front end
  GET /classic                  the original bot dashboard (dashboard.html)
  GET /api/health               what is configured / reachable
  GET /api/quotes               every ATLAS asset (Yahoo, + Fyers LIVE overlay for bot symbols)
  GET /api/history?s=&r=        chart series (r = 1D 5D 1M 6M YTD 1Y 5Y MAX)
  GET /api/asset?s=             stats + technicals (features.py) + ML model view for NSE stocks
  GET /api/fundamentals?s=A,B   fundamentals (one or many, max 60)
  GET /api/india                sectors, VIX, NIFTY 50 breadth/movers, FII/DII flows
  GET /api/commodities          CommodityPriceAPI if keyed, else Yahoo futures
  GET /api/news[?s=SYM]         headlines (market-wide or per symbol)
  GET /api/bot/state            trading bot: live state, positions, today's stats
  GET /api/bot/sim              model replay on real out-of-sample history (SIM mode)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Blueprint, jsonify, request, send_from_directory

import market_data as md

# macOS allows only 256 open files per process by default; Yahoo downloads +
# Flask threads can exhaust that ("Too many open files"). Raise the limit.
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _want = 10240 if _hard == resource.RLIM_INFINITY else min(10240, _hard)
    if _soft < _want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_want, _hard))
except Exception:
    pass

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "atlas"
MODEL_DIR = BASE_DIR / "models"

bp = Blueprint("atlas", __name__)

# bot.py plugs these in; atlas_server.py leaves them as None
HOOKS = {"bot_state": None, "live_prices": None}

SYM_RE = re.compile(r"^[A-Z0-9&\-\.\^=]{1,24}$")


def _sym(raw) -> str | None:
    s = (raw or "").strip().upper()
    return s if SYM_RE.match(s) else None


def _err(msg, code=503):
    return jsonify({"error": msg}), code


@bp.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
@bp.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@bp.route("/live.js")
def live_js():
    return send_from_directory(WEB_DIR, "live.js", mimetype="application/javascript")


@bp.route("/classic")
def classic():
    return send_from_directory(BASE_DIR, "dashboard.html")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
@bp.route("/api/health")
def health():
    import os
    meta = {}
    try:
        meta = json.loads((MODEL_DIR / "metadata.json").read_text())
    except Exception:
        pass
    bot = HOOKS["bot_state"]() if HOOKS["bot_state"] else None
    return jsonify({
        "ok": True,
        "yfinance": md.yf is not None,
        "commodityKey": bool(os.environ.get("COMMODITY_API_KEY")),
        "finnhubKey": bool(os.environ.get("FINNHUB_API_KEY")),
        "model": meta.get("model_name"), "modelTrained": meta.get("trained_at"),
        "bot": None if bot is None else {"running": True, "token": bot.get("token_status")},
    })


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------
@bp.route("/api/quotes")
def quotes():
    try:
        q = md.get_quotes()
    except Exception as e:
        return _err(f"Market data unavailable: {str(e)[:120]}")
    live = HOOKS["live_prices"]() if HOOKS["live_prices"] else {}
    if live:
        items = []
        for i in q["items"]:
            lp = live.get(i["s"])
            if lp and lp.get("price") and time.time() - lp.get("t", 0) < 90:
                p = float(lp["price"]); prev = i.get("prev")
                ch = p - prev if prev else i["ch"]
                i = dict(i, p=p, ch=ch, pc=(ch / prev * 100) if prev else i["pc"],
                         src="Fyers", status="LIVE",
                         ts=dt.datetime.fromtimestamp(lp["t"], dt.timezone.utc).isoformat())
            items.append(i)
        q = dict(q, items=items)
    return jsonify(q)


@bp.route("/api/history")
def history():
    s, r = _sym(request.args.get("s")), (request.args.get("r") or "1D").upper()
    if not s or r not in md.RANGES:
        return _err("bad symbol or range", 400)
    try:
        return jsonify(md.get_history(s, r))
    except Exception as e:
        return _err(f"History unavailable: {str(e)[:120]}")


def _f(x, nd=4):
    try:
        x = float(x)
        return round(x, nd) if np.isfinite(x) else None
    except Exception:
        return None


@bp.route("/api/asset")
def asset():
    """Stats + technicals computed by features.py from 1-2 years of daily bars,
    plus the ML model's view for NSE stocks."""
    from features import add_indicators
    s = _sym(request.args.get("s"))
    if not s:
        return _err("bad symbol", 400)
    try:
        d = md.daily_frame(s, "2y")
    except Exception as e:
        return _err(f"History unavailable: {str(e)[:120]}")
    d = d.dropna(subset=["close"]).reset_index(drop=True)
    if len(d) < 30:
        return _err("not enough history", 404)
    x = add_indicators(d)
    last, prev = x.iloc[-1], x.iloc[-2]
    yr = x.tail(252)
    out = {
        "s": s, "src": "Yahoo Finance", "asOf": str(pd.Timestamp(last["date"]).date()),
        "stats": {
            "open": _f(last["open"]), "prevClose": _f(prev["close"]), "high": _f(last["high"]),
            "low": _f(last["low"]), "hi52": _f(yr["high"].max()), "lo52": _f(yr["low"].min()),
            "volume": _f(last["volume"], 0), "avgVol20": _f(x["volume"].tail(20).mean(), 0),
        },
        "tech": {k: _f(last.get(k)) for k in (
            "close", "sma20", "sma50", "sma200", "ema20", "ema9", "ema21", "rsi", "rsi2", "macd",
            "macd_signal", "macd_hist", "bb_upper", "bb_mid", "bb_lower", "vwap", "stoch", "adx",
            "di_diff", "cci", "williams_r", "atr", "atr_pct", "obv_roc", "volatility_10",
            "pct_from_52w_high", "pct_from_52w_low")},
    }
    meta = md.UNIVERSE.get(s)
    is_nse_stock = (meta is None) or (meta[2] == "Stocks" and meta[3] == "IN")
    if is_nse_stock:
        try:
            from ml_signals import score_symbol_frame
            out["model"] = score_symbol_frame(s, d)
        except Exception as e:
            out["model"] = {"error": str(e)[:160]}
    return jsonify(out)


@bp.route("/api/fundamentals")
def fundamentals():
    raw = (request.args.get("s") or "").upper()
    syms = [x for x in (_sym(p) for p in raw.split(",")) if x][:60]
    if not syms:
        return _err("bad symbol", 400)
    try:
        if len(syms) == 1:
            return jsonify(md.get_fundamentals(syms[0]))
        return jsonify({"items": md.get_fundamentals_many(syms)})
    except Exception as e:
        return _err(f"Fundamentals unavailable: {str(e)[:120]}")


@bp.route("/api/india")
def india():
    try:
        return jsonify(md.get_india())
    except Exception as e:
        return _err(f"India data unavailable: {str(e)[:120]}")


@bp.route("/api/commodities")
def commodities():
    try:
        return jsonify(md.get_commodities())
    except Exception as e:
        return _err(f"Market data unavailable: {str(e)[:120]}")


@bp.route("/api/news")
def news():
    s = request.args.get("s")
    try:
        if s:
            sym = _sym(s)
            if not sym:
                return _err("bad symbol", 400)
            return jsonify(md.get_symbol_news(sym))
        return jsonify(md.get_news())
    except Exception as e:
        return _err(f"News unavailable: {str(e)[:120]}")


# ---------------------------------------------------------------------------
# trading bot
# ---------------------------------------------------------------------------
@bp.route("/api/bot/state")
def bot_state():
    if not HOOKS["bot_state"]:
        return jsonify({"running": False, "reason": "Bot not running (started via atlas_server.py). "
                                                     "Run start_today.command to start the bot."})
    return jsonify(dict(HOOKS["bot_state"](), running=True))


@bp.route("/api/bot/sim")
def bot_sim():
    """Replay of the model's real out-of-sample trades (walk-forward test
    written by train_model.py). ?scope=bot (default, the bot's symbols) or all."""
    path = MODEL_DIR / "oos_trades.csv"
    if not path.exists():
        return _err("No replay yet: run train_model.py", 404)
    t = pd.read_csv(path)
    scope = request.args.get("scope", "bot")
    meta = {}
    try:
        meta = json.loads((MODEL_DIR / "metadata.json").read_text())
    except Exception:
        pass
    if scope == "bot":
        t = t[t["symbol"].isin(meta.get("bot_symbols", []))]
    t = t.sort_values("entry_date")
    # real daily closes for each trade's holding period (drives the live-trading simulation)
    from universe import HIST_DIR
    hist = {}
    paths = []
    for _, r in t.iterrows():
        sym = r["symbol"]
        if sym not in hist:
            try:
                hist[sym] = pd.read_csv(HIST_DIR / f"{sym}.csv", usecols=["date", "close"]).set_index("date")["close"]
            except Exception:
                hist[sym] = pd.Series(dtype=float)
        h = hist[sym]
        seg = h[(h.index >= r["entry_date"]) & (h.index <= r["exit_date"])]
        paths.append([[d, round(float(c), 2)] for d, c in seg.items()])
    t = t.assign(path=paths)
    wins = int((t["net_ret"] > 0).sum())
    gp = float(t.loc[t["net_ret"] > 0, "net_ret"].sum())
    gl = float(-t.loc[t["net_ret"] < 0, "net_ret"].sum())
    return jsonify({
        "scope": scope, "trades": t.to_dict(orient="records"),
        "summary": {
            "n": int(len(t)), "wins": wins, "winRate": wins / len(t) if len(t) else None,
            "avgRet": float(t["net_ret"].mean()) if len(t) else None,
            "profitFactor": gp / gl if gl > 0 else None,
            "from": t["entry_date"].min() if len(t) else None, "to": t["exit_date"].max() if len(t) else None,
        },
        "rules": meta.get("strategy"), "threshold": meta.get("threshold"),
        "positionValue": meta.get("position_value"),
    })


# ---------------------------------------------------------------------------
# 24/7 demo: LIVE SIM market replay (see replay.py)
# ---------------------------------------------------------------------------
@bp.route("/api/bot/replay")
def bot_replay():
    import replay
    return jsonify(replay.engine().state())


@bp.route("/api/bot/replay/speed")
def bot_replay_speed():
    import replay
    try:
        x = int(request.args.get("x", 1))
    except ValueError:
        x = 1
    e = replay.engine()
    e.speed = x if x in (1, 2, 4, 15) else 1
    return jsonify({"speed": e.speed})


@bp.record_once
def _start_replay(state):
    try:
        import replay
        replay.engine()
    except Exception as e:
        print("replay not started:", e)


# ---------------------------------------------------------------------------
# LIVE SIM: simulated market + bot trading it in real time (see sim_market.py)
# ---------------------------------------------------------------------------
@bp.route("/api/bot/livesim")
def bot_livesim():
    import sim_market
    return jsonify(sim_market.engine().state())


@bp.route("/api/bot/livesim/speed")
def bot_livesim_speed():
    import sim_market
    try:
        x = int(request.args.get("x", 1))
    except ValueError:
        x = 1
    e = sim_market.engine()
    e.speed = x if x in (1, 2, 4, 10) else 1
    return jsonify({"speed": e.speed})


@bp.record_once
def _start_livesim(state):
    try:
        import sim_market
        sim_market.engine()
    except Exception as e:
        print("live sim not started:", e)
