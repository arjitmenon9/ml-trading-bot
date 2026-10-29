"""
ml_signals.py  (v2)
===================
The bridge between the trained model (train_model.py) and everything that
uses it live: the bot (bot.py), the ATLAS asset pages (atlas_api.py) and
predict_trend.py.

Why v1's live signal was noisy
------------------------------
v1 re-ran the model every 10 seconds on a half-formed "today" bar, so the
signal flipped BUY/SELL/HOLD all day long (BUY 981x, SELL 1053x, HOLD 1427x
in one session's log) even though the model was trained on COMPLETED daily
bars. v2 scores only completed bars: once per day, right after the open,
using the previous session's close. That is exactly what the model was
trained and back-tested on (signal at close of day t, buy at the open of
day t+1), so there is no train/serve skew.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import strategy
from features import CROSS_FEATURES, MODEL_FEATURES, add_cross_section, build_features, market_frame
from universe import BASE_DIR, all_symbols, last_segment, load_history, load_index

MODEL_DIR = BASE_DIR / "models"
STALE_DAYS = 5   # a "latest bar" older than this (calendar days) is never traded
SCORE_LOG = BASE_DIR / "data" / "score_history.csv"


class Model:
    def __init__(self, model_dir: Path = MODEL_DIR):
        p = model_dir / "model.pkl"
        if not p.exists():
            raise FileNotFoundError(f"No trained model at {p}. Run train_model.py first.")
        self.model = joblib.load(p)
        self.meta = json.loads((model_dir / "metadata.json").read_text())
        if self.meta.get("model_version", 1) < 2:
            raise RuntimeError("models/ holds the old v1 model. Run train_model.py to train v2.")
        self.rules = self.meta["strategy"]
        self.features = self.meta.get("feature_columns", MODEL_FEATURES)
        self.q = self.meta.get("quantile")
        self.threshold = self.current_threshold()

    def proba(self, rows: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(rows[self.features])[:, 1]

    # ---- rolling threshold: top (1-q) of the last WINDOW trading days' scores
    def _scores(self) -> pd.DataFrame:
        parts = []
        for p in (MODEL_DIR / "recent_scores.csv", SCORE_LOG):
            if p.exists():
                parts.append(pd.read_csv(p, usecols=["date", "symbol", "p"]))
        if not parts:
            return pd.DataFrame(columns=["date", "symbol", "p"])
        d = pd.concat(parts).drop_duplicates(["date", "symbol"], keep="last")
        keep = sorted(d["date"].unique())[-int(self.meta.get("window", 250)):]
        return d[d["date"].isin(keep)]

    def current_threshold(self) -> float:
        if self.meta.get("threshold_mode") != "rolling_quantile" or self.q is None:
            return float(self.meta["threshold"])
        s = self._scores()
        return float(np.quantile(s["p"], self.q)) if len(s) > 500 else float(self.meta["threshold"])

    def log_scores(self, day: str, rows: list[dict]):
        """Append today's universe scores (truly out-of-sample for this model)
        so tomorrow's threshold reflects them."""
        if not rows:
            return
        new = pd.DataFrame([{"date": day, "symbol": r["symbol"], "p": r["p"]} for r in rows])
        if SCORE_LOG.exists():
            old = pd.read_csv(SCORE_LOG)
            new = pd.concat([old[old["date"] != day], new])
        new.to_csv(SCORE_LOG, index=False)

    def plan(self, last_row: pd.Series, p: float, entry_ref: float | None = None) -> dict:
        """Trade plan for one scored row (entry reference = last close unless given)."""
        entry = float(entry_ref if entry_ref else last_row["close"])
        tp, sl = strategy.exit_levels(entry, last_row["atr_pct"], self.rules["tp_atr"], self.rules["sl_atr"])
        return {"p_win": round(float(p), 4), "threshold": self.threshold,
                "signal": "BUY" if p >= self.threshold else "NO TRADE",
                "entry_ref": round(entry, 2), "tp": round(tp, 2), "sl": round(sl, 2),
                "tp_pct": round((tp / entry - 1) * 100, 2), "sl_pct": round((1 - sl / entry) * 100, 2),
                "atr_pct": round(float(last_row["atr_pct"]), 3), "max_hold": self.rules["max_hold"]}


_MODEL: Model | None = None


def get_model() -> Model:
    global _MODEL
    if _MODEL is None:
        _MODEL = Model()
    return _MODEL


def reload_model():
    global _MODEL
    _MODEL = None
    return get_model()


def local_market() -> pd.DataFrame:
    return market_frame(load_index("NIFTY50"), load_index("INDIAVIX"))


def _oos_summary(m: Model) -> dict | None:
    h = m.meta.get("holdout") or {}
    if not h.get("n"):
        return None
    return {"n": h["n"], "win_rate": h["win_rate"], "period": "2022 → " + m.meta.get("data_through", "")[:4] + ", walk-forward"}


# ---------------------------------------------------------------------------
# ATLAS asset page: score any NSE stock from a daily OHLCV frame
# ---------------------------------------------------------------------------
def _market_with_yahoo() -> pd.DataFrame:
    """Local NIFTY/VIX history, topped up with Yahoo bars if the local files
    are behind (the website can run without the bot's daily Fyers update)."""
    n, v = load_index("NIFTY50"), load_index("INDIAVIX")
    try:
        import market_data as md
        for sym, tick, frame in (("NIFTY50", "^NSEI", "n"), ("INDIAVIX", "^INDIAVIX", "v")):
            y = md.CACHE.get(("idx", sym), 900, lambda t=tick: _yahoo_daily(t))[0]
            base = n if frame == "n" else v
            if len(y) and (base.empty or y["date"].max() > base["date"].max()):
                merged = pd.concat([base, y[y["date"] > (base["date"].max() if len(base) else pd.Timestamp(0))]])
                if frame == "n":
                    n = merged
                else:
                    v = merged
    except Exception:
        pass
    return market_frame(n, v)


def _yahoo_daily(ticker: str) -> pd.DataFrame:
    import market_data as md
    h = md.yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=False)
    h = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    idx = h.index.tz_localize(None) if getattr(h.index, "tz", None) is not None else h.index
    h.index = pd.to_datetime(idx).normalize()
    return h.rename_axis("date").reset_index()


def completed_bars(d: pd.DataFrame, now: dt.datetime | None = None) -> pd.DataFrame:
    """Drop today's still-forming bar during market hours (IST)."""
    now = now or dt.datetime.now()
    if len(d) and pd.Timestamp(d["date"].iloc[-1]).date() == now.date() and (now.hour, now.minute) < (15, 30):
        return d.iloc[:-1]
    return d


def _latest_rows(symbols, mkt, today, stale_days=STALE_DAYS):
    rows, stale = [], []
    mkt_last = mkt["date"].max() if len(mkt) else None
    for s in symbols:
        try:
            d = completed_bars(last_segment(load_history(s)))
        except Exception:
            continue
        if len(d) < 230:
            continue
        last_date = pd.Timestamp(d["date"].iloc[-1])
        if (pd.Timestamp(today) - last_date).days > stale_days or (mkt_last is not None and last_date > mkt_last):
            stale.append(s)
            continue
        x = build_features(d.tail(400).reset_index(drop=True), mkt)
        r = x.iloc[-1].copy()
        r["symbol"] = s
        rows.append(r)
    return pd.DataFrame(rows), stale


def daily_candidates(symbols: list[str] | None = None, today: dt.date | None = None, stale_days: int = STALE_DAYS) -> dict:
    """Score every symbol's latest COMPLETED daily bar, the same way the
    model was trained: features -> cross-sectional ranks across the whole
    universe that day -> trade gate -> model. Returns
    {asOf, threshold, rows (gated, sorted by p), all (every scored symbol), stale}."""
    import strategy
    m = get_model()
    today = today or dt.date.today()
    universe = [s for s in all_symbols() if s in set(m.meta.get("symbols", []))]
    df, stale = _latest_rows(universe, local_market(), today, stale_days)
    if df.empty:
        return {"asOf": None, "rows": [], "all": [], "stale": stale, "threshold": m.threshold}
    as_of = df["date"].max()
    df = df[df["date"] == as_of].reset_index(drop=True)   # one common session for the ranks
    df = add_cross_section(df)
    gate = strategy.passes_gate(df).to_numpy()
    df["p"] = m.proba(df)
    m.threshold = m.current_threshold()
    out_all, rows = [], []
    for g, (_, r) in zip(gate, df.iterrows()):
        rec = m.plan(r, float(r["p"]))
        rec.update({"symbol": r["symbol"], "p": round(float(r["p"]), 4), "asOf": str(as_of.date()),
                    "close": float(r["close"]), "gate": bool(g)})
        if not g:
            rec["signal"] = "NO TRADE"
        out_all.append(rec)
        if g:
            rows.append(rec)
    rows.sort(key=lambda r: r["p"], reverse=True)
    if symbols:
        want = set(symbols)
        rows = [r for r in rows if r["symbol"] in want]
        out_all = [r for r in out_all if r["symbol"] in want]
    return {"asOf": str(as_of.date()), "rows": rows, "all": out_all, "stale": stale,
            "threshold": m.threshold, "frame": df}


_SNAP = {"day": None, "res": None}


def universe_snapshot() -> dict:
    """daily_candidates() cached for the day (used by the website)."""
    key = (dt.date.today(), (BASE_DIR / "data" / "index" / "NIFTY50.csv").stat().st_mtime)
    if _SNAP["day"] != key:
        # display only: show the latest available bars even if a few days old (labelled with asOf)
        _SNAP["res"], _SNAP["day"] = daily_candidates(stale_days=3650), key
    return _SNAP["res"]


def score_symbol_frame(sym: str, d: pd.DataFrame) -> dict:
    """Model view for one stock on its ATLAS page."""
    import strategy
    m = get_model()
    snap = universe_snapshot()
    hit = next((r for r in snap.get("all", []) if r["symbol"] == sym), None)
    if hit:
        out = dict(hit)
        out["p_win"] = out.pop("p")
    else:
        # not in the local universe: compute from the page's own (Yahoo) data and
        # rank it against today's universe snapshot
        d = completed_bars(last_segment(d.sort_values("date").reset_index(drop=True)))
        if len(d) < 230:
            return {"error": "needs ~1 year of daily history"}
        x = build_features(d, _market_with_yahoo())
        last = x.iloc[-1].copy()
        if pd.isna(last[["dist_sma200", "atr_pct"]]).any():
            return {"error": "not enough history for every indicator"}
        frame = snap.get("frame")
        if frame is None or frame.empty:
            return {"error": "universe snapshot unavailable (run update_data.py)"}
        last["symbol"], last["date"] = sym, frame["date"].iloc[0]
        both = add_cross_section(pd.concat([frame.drop(columns=CROSS_FEATURES + ["p"], errors="ignore"), last.to_frame().T], ignore_index=True))
        row = both.iloc[[-1]].copy()
        for c in m.features:
            row[c] = pd.to_numeric(row[c], errors="coerce")
        p = float(m.proba(row)[0])
        g = bool(strategy.passes_gate(row).iloc[0])
        out = m.plan(row.iloc[0], p)
        out.update({"p_win": round(p, 4), "asOf": str(pd.Timestamp(last["date"]).date()), "gate": g})
        if not g:
            out["signal"] = "NO TRADE"
    out.update({"threshold": m.threshold, "oos": _oos_summary(m),
                "in_training_universe": sym in set(m.meta.get("symbols", [])),
                "gate_rules": strategy.GATE})
    return out
