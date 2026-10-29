"""
train_model.py  (v2: swing-trade filter, walk-forward validated)
=================================================================
Trains the model the bot trades on, measures it honestly, and saves it.

WHAT CHANGED FROM v1 AND WHY
----------------------------
v1 tried to predict BUY / HOLD / SELL for the next 5 days on 4 stocks x 5
years. Test accuracy was 37% (barely above the 33% coin-flip for 3
classes). Live, the signal flipped every few seconds because it was re-run
on a half-formed daily bar, and SIM mode wasn't using the model at all.

v2 asks a narrower question the data can actually answer:

    "If I buy this stock at tomorrow's open and exit with the rules in
     strategy.py (take-profit / stop-loss / 15-day time stop), will the
     trade make money AFTER Fyers charges?"

and only trades when the model is very sure. It learns from ~190 liquid NSE
stocks over 2007-2026 (~730,000 stock-days) instead of 4 stocks x 5 years.

HOW IT IS VALIDATED (no peeking at the future)
----------------------------------------------
1. Walk-forward: for every year Y from 2018 on, a model is trained ONLY on
   data that ended 30 days before 1 Jan Y, then predicts every stock-day in Y.
   Those are genuine out-of-sample predictions.
2. Those predictions are turned into trades with the bot's real portfolio
   rules (one position per stock, at most CAP_PER_DAY new trades a day,
   at most MAX_OPEN open at once, Fyers charges on a POSITION_VALUE trade).
3. The buy threshold is chosen using ONLY the 2018-2021 out-of-sample
   trades: the lowest model confidence that gave >= TARGET_WIN_RATE with a
   healthy profit factor.
4. That frozen threshold is then scored on 2022-today, which played no part
   in choosing anything. THAT is the number to believe.
5. The deployed model is retrained on all data with the same settings.

Run:   python train_model.py            (full walk-forward, ~20-40 min)
       python train_model.py --final-only  (refit on the latest data and keep
                                            the threshold already chosen; ~3 min)
Outputs: models/model.pkl, models/metadata.json, models/oos_trades.csv,
         reports/walkforward_equity.png, reports/walkforward_by_year.png
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import warnings

import joblib
import numpy as np
import pandas as pd

import strategy
from ensemble import BaggedHGB
from features import FEATURE_COLUMNS, MODEL_FEATURES, add_cross_section, build_features
from universe import BASE_DIR, FOCUS_SYMBOLS, all_symbols, load_history, load_market, segments

warnings.filterwarnings("ignore")
MODEL_DIR = BASE_DIR / "models"
REPORT_DIR = BASE_DIR / "reports"

# ---- strategy + portfolio settings (the bot reads these from metadata.json)
TP_ATR, SL_ATR, MAX_HOLD = 0.75, 4.0, 15
CAP_PER_DAY = 3            # new positions per day
MAX_OPEN = 10              # open positions at once
POSITION_VALUE = 50_000    # rupees per trade used for charges in the backtest
TARGET_WIN_RATE = 0.92     # on 2018-2021. Walk-forward tests lost ~5 points from calibration
                           # to unseen years, so 92% here is what keeps unseen years >= 85%
N_BAG = 3                  # models averaged in the ensemble
QUANTILES = [0.97, 0.98, 0.99, 0.995, 0.997]   # candidate "top x%" thresholds
WINDOW = 250               # trading days of past scores the threshold is set from
MIN_PROFIT_FACTOR = 1.5
MIN_CALIB_TRADES = 30
WF_FIRST_YEAR, CALIB_LAST_YEAR = 2018, 2021
EMBARGO = pd.Timedelta(days=30)   # > MAX_HOLD trading days, so labels never leak


def make_model():
    return BaggedHGB(n=N_BAG, max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                     min_samples_leaf=400, l2_regularization=1.0)


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------
def build_dataset(verbose=True) -> pd.DataFrame:
    t0 = time.time()
    mkt = load_market()
    cost = strategy.round_trip_cost(POSITION_VALUE)
    parts = []
    syms = all_symbols()
    for sym in syms:
        for seg in segments(load_history(sym)):
            x = build_features(seg, mkt)
            ret, days, why = strategy.simulate_outcomes(x, TP_ATR, SL_ATR, MAX_HOLD)
            dates = x["date"].to_numpy()
            n = len(x)
            ent_i = np.minimum(np.arange(n) + 1, n - 1)
            ex_i = np.minimum(np.arange(n) + np.nan_to_num(days, nan=0).astype(int), n - 1)
            g = x[["date"] + FEATURE_COLUMNS].copy()
            g["symbol"] = sym
            g["gross_ret"] = ret
            g["net_ret"] = ret - cost
            g["days"] = days
            g["why"] = why
            g["entry_date"] = dates[ent_i]
            g["exit_date"] = dates[ex_i]
            g["entry_px"] = x["open"].to_numpy()[ent_i]
            parts.append(g)
    a = pd.concat(parts, ignore_index=True)
    a = a.dropna(subset=["dist_sma200"])  # need ~1 year of history for every feature
    a = add_cross_section(a)                # ranks vs the whole universe that day
    a["gate"] = strategy.passes_gate(a).to_numpy()   # setups the bot may trade
    a["y"] = (a["net_ret"] > 0).astype(int)
    if verbose:
        print(f"  gate {strategy.GATE}: {int(a['gate'].sum()):,} of {len(a):,} stock-days are tradable setups "
              f"(the model learns from all of them; the gate only filters what it buys)")
    if verbose:
        lab = a["net_ret"].notna()
        print(f"  dataset: {len(a):,} stock-days, {a['symbol'].nunique()} stocks, "
              f"{a['date'].min().date()} -> {a['date'].max().date()} ({time.time()-t0:.0f}s)")
        print(f"  base rate (buy every day, no model): win {a.loc[lab,'y'].mean():.1%}, "
              f"avg net {a.loc[lab,'net_ret'].mean()*100:+.2f}% per trade")
    return a


# ---------------------------------------------------------------------------
# portfolio simulation (same rules as the live bot)
# ---------------------------------------------------------------------------
def select_trades(pred: pd.DataFrame, th: float, cap=CAP_PER_DAY, max_open=MAX_OPEN) -> pd.DataFrame:
    """pred: rows with date, symbol, p, net_ret, entry_date, exit_date, ...
    Walks forward day by day, opening the highest-p candidates first."""
    c = pred[(pred["p"] >= th) & pred["net_ret"].notna()].sort_values(["date", "p"], ascending=[True, False])
    open_until: dict[str, pd.Timestamp] = {}
    keep = []
    for day, g in c.groupby("date", sort=True):
        open_until = {s: e for s, e in open_until.items() if e >= day}
        new = 0
        for idx, r in g.iterrows():
            if new >= cap or len(open_until) >= max_open:
                break
            if r["symbol"] in open_until:
                continue
            open_until[r["symbol"]] = r["exit_date"]
            keep.append(idx)
            new += 1
    return c.loc[keep]


def stats(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0}
    gp, gl = t.loc[t.net_ret > 0, "net_ret"].sum(), -t.loc[t.net_ret < 0, "net_ret"].sum()
    return {"n": int(len(t)), "win_rate": float((t.net_ret > 0).mean()),
            "avg_net_ret": float(t.net_ret.mean()), "profit_factor": float(gp / gl) if gl > 0 else None,
            "avg_win": float(t.loc[t.net_ret > 0, "net_ret"].mean()) if (t.net_ret > 0).any() else None,
            "avg_loss": float(t.loc[t.net_ret <= 0, "net_ret"].mean()) if (t.net_ret <= 0).any() else None,
            "total_pnl_rs": float(t.net_ret.sum() * POSITION_VALUE),
            "avg_days": float(t.days.mean())}


def rolling_threshold(pred: pd.DataFrame, q: float, window: int = WINDOW) -> pd.Series:
    """Causal threshold per date: the q-quantile of ALL scores from the
    previous `window` trading days (never the same day or the future).
    Model scores drift with the market regime, so "top x% of recent scores"
    transfers between years far better than a fixed probability does."""
    daily = pred.groupby("date")["p"].apply(lambda x: x.to_numpy())
    out = {}
    for i, d in enumerate(daily.index):
        out[d] = np.quantile(np.concatenate(daily.iloc[max(0, i - window):i].to_numpy()), q) if i >= 60 else 1.0
    return pred["date"].map(out)


def trades_at(pred: pd.DataFrame, q: float) -> pd.DataFrame:
    """Threshold from ALL recent scores, then only gated setups may trade."""
    x = pred.assign(th=rolling_threshold(pred, q))
    return select_trades(x[(x["p"] >= x["th"]) & x["gate"]], 0.0)


def choose_quantile(pred: pd.DataFrame):
    """Lowest q (= most trades) whose CALIBRATION-years trades meet the target."""
    rows = []
    for q in QUANTILES:
        t = trades_at(pred, q)
        cal = stats(t[t["fold"] <= CALIB_LAST_YEAR])
        rows.append((q, cal, t))
        print(f"    q={q}: calibration {cal.get('n',0)} trades, win {cal.get('win_rate',0):.1%}, "
              f"PF {cal.get('profit_factor') or 0:.2f}")
    ok = [r for r in rows if r[1].get("n", 0) >= MIN_CALIB_TRADES and r[1]["win_rate"] >= TARGET_WIN_RATE
          and (r[1]["profit_factor"] or 0) >= MIN_PROFIT_FACTOR]
    if ok:
        return ok[0]
    print("  ! no quantile met the target on calibration years; using the best one")
    rows = [r for r in rows if r[1].get("n", 0) >= 20] or rows
    return max(rows, key=lambda r: (r[1].get("win_rate", 0), r[1].get("profit_factor") or 0))


# ---------------------------------------------------------------------------
def walk_forward(a: pd.DataFrame) -> pd.DataFrame:
    last_year = int(a["date"].max().year)
    out = []
    for Y in range(WF_FIRST_YEAR, last_year + 1):
        t0 = time.time()
        y0 = pd.Timestamp(f"{Y}-01-01")
        tr = a[(a["date"] < y0 - EMBARGO) & a["net_ret"].notna()]
        te = a[(a["date"] >= y0) & (a["date"] < pd.Timestamp(f"{Y+1}-01-01"))].copy()
        m = make_model().fit(tr[MODEL_FEATURES], tr["y"])
        te["p"] = m.predict_proba(te[MODEL_FEATURES])[:, 1]
        te["fold"] = Y
        out.append(te)
        print(f"  fold {Y}: trained on {len(tr):,} rows up to {(y0-EMBARGO).date()}, "
              f"scored {len(te):,} ({time.time()-t0:.0f}s)", flush=True)
    return pd.concat(out, ignore_index=True)


def plots(trades: pd.DataFrame, th: float):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    REPORT_DIR.mkdir(exist_ok=True)
    t = trades.sort_values("exit_date")
    fig, ax = plt.subplots(figsize=(10, 4.2))
    eq = (t["net_ret"] * POSITION_VALUE).cumsum()
    ax.plot(pd.to_datetime(t["exit_date"]), eq, color="#1f9d6b", lw=1.8)
    ax.axvline(pd.Timestamp(f"{CALIB_LAST_YEAR+1}-01-01"), color="#888", ls="--", lw=1)
    ax.text(pd.Timestamp(f"{CALIB_LAST_YEAR+1}-02-01"), ax.get_ylim()[0], "  holdout (threshold frozen) →", color="#555", va="bottom")
    ax.set_title(f"Walk-forward paper P&L, Rs {POSITION_VALUE:,}/trade after charges (threshold {th:.3f})")
    ax.set_ylabel("cumulative P&L (Rs)"); ax.grid(alpha=.25)
    fig.tight_layout(); fig.savefig(REPORT_DIR / "walkforward_equity.png", dpi=130); plt.close(fig)
    by = t.groupby(pd.to_datetime(t["entry_date"]).dt.year)["net_ret"].agg(n="size", win=lambda r: (r > 0).mean())
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.bar(by.index.astype(str), by["win"] * 100, color=["#7a8" if int(y) <= CALIB_LAST_YEAR else "#1f9d6b" for y in by.index])
    ax.axhline(85, color="#c44", ls="--", lw=1); ax.set_ylim(0, 100); ax.set_ylabel("win rate %")
    for i, (n, w) in enumerate(zip(by["n"], by["win"])):
        ax.text(i, w * 100 + 1, f"{w:.0%}\n{n} tr", ha="center", fontsize=8)
    ax.set_title("Out-of-sample win rate by year (light = threshold-calibration years)")
    fig.tight_layout(); fig.savefig(REPORT_DIR / "walkforward_by_year.png", dpi=130); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final-only", action="store_true", help="refit on all data, keep saved threshold")
    args = ap.parse_args()
    MODEL_DIR.mkdir(exist_ok=True)
    print("=" * 70 + "\n  Building dataset\n" + "=" * 70)
    a = build_dataset()
    old_meta = {}
    if (MODEL_DIR / "metadata.json").exists():
        old_meta = json.loads((MODEL_DIR / "metadata.json").read_text())

    if args.final_only:
        if "quantile" not in old_meta:
            raise SystemExit("No saved threshold yet - run the full `python train_model.py` once.")
        q = old_meta["quantile"]
        meta_eval = {k: old_meta.get(k) for k in ("calibration", "holdout", "oos_all", "by_year", "focus_symbols_oos", "quantile")}
    else:
        print("=" * 70 + "\n  Walk-forward (each year predicted by a model that never saw it)\n" + "=" * 70)
        pred = walk_forward(a)
        calib = pred[pred["fold"] <= CALIB_LAST_YEAR]
        hold = pred[pred["fold"] > CALIB_LAST_YEAR]
        print("  choosing the threshold on 2018-%d only:" % CALIB_LAST_YEAR)
        q, s_cal, t_all = choose_quantile(pred)
        th = q
        t_hold = t_all[t_all["fold"] > CALIB_LAST_YEAR]
        t_cal = t_all[t_all["fold"] <= CALIB_LAST_YEAR]
        s_hold, s_all = stats(t_hold), stats(t_all)
        by_year = {int(y): stats(g) for y, g in t_all.groupby("fold")}
        print("\n" + "=" * 70 + "\n  RESULTS (all out-of-sample, after Fyers charges)\n" + "=" * 70)
        print(f"  threshold chosen on 2018-{CALIB_LAST_YEAR}: buy the top {(1-q)*100:.1f}% of the last {WINDOW} days' scores")
        fmt = lambda s: (f"{s['n']:4d} trades | win {s['win_rate']:.1%} | avg {s['avg_net_ret']*100:+.2f}% | "
                         f"PF {s['profit_factor'] or float('nan'):.2f} | P&L Rs {s['total_pnl_rs']:,.0f}") if s.get("n") else "no trades"
        print(f"  calibration 2018-{CALIB_LAST_YEAR}: {fmt(stats(t_cal))}")
        print(f"  HOLDOUT {CALIB_LAST_YEAR+1}-now  : {fmt(s_hold)}   <- the honest number")
        for y, s in by_year.items():
            print(f"    {y}: {fmt(s)}")
        foc = t_all[t_all["symbol"].isin(FOCUS_SYMBOLS)]
        print(f"  focus stocks {FOCUS_SYMBOLS}: {fmt(stats(foc))}")
        cols = ["symbol", "date", "entry_date", "exit_date", "entry_px", "p", "th", "net_ret", "gross_ret", "days", "why", "fold"]
        recent = pred[pred["date"] >= sorted(pred["date"].unique())[-WINDOW]][["date", "symbol", "p"]]
        recent.assign(date=recent["date"].dt.strftime("%Y-%m-%d")).to_csv(MODEL_DIR / "recent_scores.csv", index=False)
        o = t_all[cols].copy()
        o["exit_reason"] = o["why"].map({1.0: "target", -1.0: "stop", 0.0: "time"})
        o["set"] = np.where(o["fold"] <= CALIB_LAST_YEAR, "calibration", "holdout")
        for c in ("date", "entry_date", "exit_date"):
            o[c] = pd.to_datetime(o[c]).dt.strftime("%Y-%m-%d")
        o.rename(columns={"date": "signal_date"}).to_csv(MODEL_DIR / "oos_trades.csv", index=False)
        plots(t_all, float(rolling_threshold(pred, q).iloc[-1]))
        meta_eval = {"calibration": s_cal, "holdout": s_hold, "oos_all": s_all, "by_year": by_year,
                     "focus_symbols_oos": stats(foc), "quantile": q}

    print("=" * 70 + "\n  Training the deployed model on all data\n" + "=" * 70)
    t0 = time.time()
    tr = a[a["net_ret"].notna()]
    model = make_model().fit(tr[MODEL_FEATURES], tr["y"])
    rs = pd.read_csv(MODEL_DIR / "recent_scores.csv")
    th_now = float(np.quantile(rs["p"], q))
    joblib.dump(model, MODEL_DIR / "model.pkl")
    meta = {
        "model_name": f"Bagged HistGradientBoosting x{N_BAG} swing-trade filter v2.1",
        "model_version": 2,
        "trained_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_through": str(a["date"].max().date()),
        "trained_rows": int(len(tr)),
        "symbols": sorted(a["symbol"].unique().tolist()),
        "bot_symbols": FOCUS_SYMBOLS,
        "feature_columns": MODEL_FEATURES,
        "threshold_mode": "rolling_quantile", "window": WINDOW,
        "threshold": round(th_now, 4),   # value right now; the bot re-computes it daily
        "gate": strategy.GATE,
        "strategy": {"tp_atr": TP_ATR, "sl_atr": SL_ATR, "max_hold": MAX_HOLD,
                     "tp_min": strategy.TP_MIN, "tp_max": strategy.TP_MAX,
                     "sl_min": strategy.SL_MIN, "sl_max": strategy.SL_MAX,
                     "entry": "next session open", "cap_per_day": CAP_PER_DAY, "max_open": MAX_OPEN},
        "position_value": POSITION_VALUE,
        "round_trip_cost_pct": round(strategy.round_trip_cost(POSITION_VALUE) * 100, 3),
        "target_win_rate": TARGET_WIN_RATE,
        "data_source": "eod2 NSE bhavcopy history (GitHub) + Fyers History API top-ups",
        **{k: v for k, v in meta_eval.items() if v is not None},
    }
    (MODEL_DIR / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"  saved models/model.pkl and models/metadata.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
