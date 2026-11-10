"""
v3 research pipeline: a wider universe, a return-based target, daily
cross-sectional ranking, and an ensemble of three model families.

    data    eod2 daily files -> point-in-time top-N liquid universe, the 57
            features, forward-return rank targets
    trees   XGBoost regression + LambdaMART ranker on the features
    gru     GRU on raw 60-day candle sequences          (GPU)
    cnn     CNN on 20-day candlestick chart images      (GPU)
    report  rank-average ensemble, Rank IC, a top-K portfolio after costs,
            compared with the equal-weight universe and the NIFTY 50

Every model is evaluated walk-forward: the model that scores year Y has only
seen data from at least 30 days before 1 January of Y. Nothing is tuned on
the test years; all settings below were fixed before the first run.

Run (Colab):  python src/ml/v3/pipeline.py --stage all --eod2 /content/eod2_data --out /content/v3_out
"""
from __future__ import annotations

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)

import argparse
import json
import math
import time

import numpy as np
import pandas as pd

from features import MODEL_FEATURES, add_cross_section, build_features, market_frame
from universe import segments

# ---- settings, fixed before the first run --------------------------------
TOP_N = 500            # stocks in the point-in-time liquidity universe
H = 10                 # holding period, trading days (buy next open, sell close of day t+H)
K = 10                 # stocks bought per day
COST = 0.0045          # round trip: 0.35% Fyers charges at Rs 50k + 0.05% slippage per side
SEQ = 60               # candles per sequence sample
IMG_DAYS = 20          # candles per chart image
FIRST_TEST = 2018
EMBARGO = pd.Timedelta(days=30)
SEEDS = 2


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ===========================================================================
# stage: data
# ===========================================================================
def stage_data(eod2: pathlib.Path, out: pathlib.Path, top_n: int, limit: int | None):
    out.mkdir(parents=True, exist_ok=True)
    isin = json.loads((eod2 / "isin_symbol_map.json").read_text())["sym2isin"]
    files = sorted((eod2 / "daily").glob("*.csv"))
    raw = {}
    for f in files:
        sym = f.stem.upper()
        if not str(isin.get(sym, "")).startswith("INE"):      # equities only (no ETFs, indices)
            continue
        d = pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close", "Volume"], parse_dates=["Date"])
        d.columns = ["date", "open", "high", "low", "close", "volume"]
        d = d[d["date"] >= "2006-01-01"].dropna()
        d = d[(d["close"] > 0) & (d["open"] > 0) & (d["volume"] > 0)]
        d = d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        if len(d) >= 300:
            raw[sym] = d
        if limit and len(raw) >= limit:
            break
    log(f"loaded {len(raw)} equity symbols")

    # point-in-time liquidity universe: top-N by trailing 60-day median turnover, known the day before
    to = pd.DataFrame({s: d.set_index("date").eval("close * volume") for s, d in raw.items()}).astype("float32")
    med = to.rolling(60, min_periods=40).median().shift(1)
    in_univ = med.rank(axis=1, ascending=False) <= top_n
    keep = [s for s in raw if in_univ[s].any()]
    log(f"universe: top {top_n} by turnover each day; {len(keep)} symbols were members at some point")

    def idx(name):
        d = pd.read_csv(eod2 / "daily" / f"{name}.csv", usecols=["Date", "Close"], parse_dates=["Date"])
        return d.rename(columns={"Date": "date", "Close": "close"}).dropna()
    nifty, vix = idx("nifty 50"), idx("india vix")
    mkt = market_frame(nifty, vix)

    parts, G, off = [], [], 0
    t0 = time.time()
    for n, sym in enumerate(keep):
        member = in_univ[sym]
        for seg in segments(raw[sym]):
            x = build_features(seg, mkt)
            o, c = x["open"].to_numpy(float), x["close"].to_numpy(float)
            m = len(x)
            ent = np.r_[o[1:], np.nan]                                   # next day's open
            ex = np.r_[c[H:], np.full(H, np.nan)] if m > H else np.full(m, np.nan)
            g = x[["date"] + [f for f in MODEL_FEATURES if f in x.columns]].copy()
            g["symbol"] = sym
            g["ret"] = ex / ent - 1                                      # open(t+1) -> close(t+H)
            g["gpos"] = off + np.arange(m)
            g["spos"] = np.arange(m)
            g["member"] = member.reindex(x["date"]).fillna(False).to_numpy()
            parts.append(g[g["member"]])
            G.append(np.c_[o, x["high"].to_numpy(float), x["low"].to_numpy(float), c, x["volume"].to_numpy(float),
                           x["date"].to_numpy("datetime64[D]").astype("int64")])
            off += m
        if (n + 1) % 200 == 0:
            log(f"  features {n+1}/{len(keep)} ({time.time()-t0:.0f}s)")
    a = pd.concat(parts, ignore_index=True)
    a = a.dropna(subset=["dist_sma200"])
    a = add_cross_section(a)
    a = a.sort_values(["date", "symbol"]).reset_index(drop=True)

    # target: the forward return ranked within each day. Positions are equal in rupees and costs
    # are a fixed percentage per trade, so the raw return (not a volatility-scaled one) is what pays.
    r = a.groupby("date")["ret"].rank(pct=True)
    a["y"] = (r - 0.5).astype("float32")                                  # regression / IC target
    a["ybin"] = np.floor(r * 10).clip(0, 9)                               # ranker label 0..9
    for f in MODEL_FEATURES:
        a[f] = a[f].astype("float32")
    a.drop(columns=["member"]).to_parquet(out / "panel.parquet")
    np.save(out / "G.npy", np.concatenate(G).astype("float64"))
    nifty.to_csv(out / "nifty.csv", index=False)
    lab = a["ret"].notna()
    log(f"panel: {len(a):,} stock-days, {a['symbol'].nunique()} symbols, {a['date'].min().date()} -> {a['date'].max().date()}, "
        f"{lab.mean():.1%} labelled; mean {H}-day return {a.loc[lab,'ret'].mean()*100:+.2f}%")


def load(out: pathlib.Path):
    a = pd.read_parquet(out / "panel.parquet")
    return a, np.load(out / "G.npy")


def fold_years(a):
    return list(range(FIRST_TEST, int(a["date"].max().year) + 1))


def fold_masks(a, Y):
    y0 = pd.Timestamp(f"{Y}-01-01")
    tr = (a["date"] < y0 - EMBARGO) & a["ret"].notna()
    te = (a["date"] >= y0) & (a["date"] < pd.Timestamp(f"{Y+1}-01-01"))
    return tr.to_numpy(), te.to_numpy()


def save_pred(out, name, a, score):
    p = a[["date", "symbol"]].copy()
    p["score"] = score
    p = p[np.isfinite(p["score"])]
    p.to_parquet(out / f"pred_{name}.parquet")
    log(f"saved pred_{name}: {len(p):,} out-of-sample rows")


# ===========================================================================
# stage: trees
# ===========================================================================
def stage_trees(out: pathlib.Path, seeds: int, rounds: int):
    import xgboost as xgb
    a, _ = load(out)
    try:
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        dev = "cpu"
    log(f"xgboost {xgb.__version__} on {dev}")
    X = a[MODEL_FEATURES].to_numpy(np.float32)
    common = dict(tree_method="hist", device=dev, max_depth=6, learning_rate=0.03, n_estimators=rounds,
                  subsample=0.7, colsample_bytree=0.5, min_child_weight=200, reg_lambda=5.0, max_bin=128)
    qid = a["date"].factorize()[0]
    for name in ("xgb_reg", "xgb_rank"):
        score = np.full(len(a), np.nan)
        for Y in fold_years(a):
            tr, te = fold_masks(a, Y)
            t0 = time.time(); ps = []
            for s in range(seeds):
                if name == "xgb_reg":
                    m = xgb.XGBRegressor(objective="reg:squarederror", random_state=s, **common)
                    m.fit(X[tr], a.loc[tr, "y"].to_numpy())
                else:
                    m = xgb.XGBRanker(objective="rank:ndcg", ndcg_exp_gain=False, lambdarank_pair_method="topk",
                                      lambdarank_num_pair_per_sample=8, random_state=s, **common)
                    m.fit(X[tr], a.loc[tr, "ybin"].to_numpy(), qid=qid[tr])
                ps.append(m.predict(X[te]))
            score[te] = np.mean(ps, axis=0)
            log(f"  {name} {Y}: train {tr.sum():,} rows, test {te.sum():,} ({time.time()-t0:.0f}s)")
        save_pred(out, name, a, score)


# ===========================================================================
# stage: gru / cnn  (PyTorch)
# ===========================================================================
def _torch_models():
    import torch
    import torch.nn as nn

    class GRUNet(nn.Module):
        def __init__(self, hidden=64):
            super().__init__()
            self.gru = nn.GRU(5, hidden, num_layers=2, batch_first=True, dropout=0.1)
            self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, 1))

        def forward(self, w):                       # w: (B, SEQ+1, 5) raw O,H,L,C,V
            o, h, l, c, v = w.unbind(-1)
            pc = c[:, :-1]
            f = torch.stack([torch.log(c[:, 1:] / pc), torch.log(o[:, 1:] / pc), torch.log(h[:, 1:] / c[:, 1:]),
                             torch.log(l[:, 1:] / c[:, 1:])], -1) * 20
            lv = torch.log(v[:, 1:] / v[:, 1:].mean(1, keepdim=True).clamp_min(1.0)).clamp(-4, 4) * 0.5
            x = torch.cat([f.clamp(-6, 6), lv.unsqueeze(-1)], -1)
            return self.head(self.gru(x)[0][:, -1]).squeeze(-1)

    class ChartCNN(nn.Module):
        """Draws each 20-day window as a 64x60 OHLC chart with a moving-average
        line and volume bars (after Jiang, Kelly and Xiu, 2023), then applies
        three convolution blocks."""
        def __init__(self, ch=(32, 64, 128)):
            super().__init__()
            layers, cin = [], 1
            for co in ch:
                layers += [nn.Conv2d(cin, co, (5, 3), padding=(2, 1)), nn.BatchNorm2d(co), nn.LeakyReLU(0.01), nn.MaxPool2d((2, 1))]
                cin = co
            self.conv = nn.Sequential(*layers)
            self.head = nn.Sequential(nn.Dropout(0.5), nn.Linear(ch[-1] * 8 * 60, 1))

        @staticmethod
        def render(w):                              # w: (B, SEQ+1, 5)
            B = w.shape[0]
            c_all = w[..., 3]
            ma = c_all.unfold(1, IMG_DAYS, 1).mean(-1)[:, -IMG_DAYS:]            # 20-day moving average
            o, h, l, c, v = w[:, -IMG_DAYS:].unbind(-1)
            hi = torch.maximum(h.max(1).values, ma.max(1).values).unsqueeze(1)
            lo = torch.minimum(l.min(1).values, ma.min(1).values).unsqueeze(1)
            row = lambda p: ((hi - p) / (hi - lo).clamp_min(1e-9) * 50).round().long().unsqueeze(1)   # (B,1,20), 0 = top
            r = torch.arange(64, device=w.device).view(1, 64, 1)
            bar = (r >= row(h)) & (r <= row(l))
            mal = r == row(ma)
            vh = (v / v.max(1, keepdim=True).values.clamp_min(1.0) * 11).round().long().unsqueeze(1)
            vol = (r >= 63 - vh) & (r >= 52)
            img = torch.zeros(B, 64, 60, device=w.device)
            img[:, :, 0::3] = ((r == row(o)) | mal).float()
            img[:, :, 1::3] = (bar | mal | vol).float()
            img[:, :, 2::3] = ((r == row(c)) | mal).float()
            return img.unsqueeze(1)

        def forward(self, w):
            return self.head(self.conv(self.render(w)).flatten(1)).squeeze(-1)

    return GRUNet, ChartCNN


def stage_seq(out: pathlib.Path, kind: str, seeds: int, ep_first: int, ep_next: int, days_per_batch: int, day_frac: float):
    import torch
    GRUNet, ChartCNN = _torch_models()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    a, Gn = load(out)
    log(f"{kind} on {dev}" + (f" ({torch.cuda.get_device_name(0)})" if dev == "cuda" else ""))
    Gt = torch.tensor(Gn[:, :5], dtype=torch.float32, device=dev)
    ok = (a["spos"] >= SEQ).to_numpy()
    gpos = torch.tensor(a["gpos"].to_numpy(), device=dev)
    y = torch.tensor(a["y"].fillna(0).to_numpy(np.float32), device=dev)
    day = a["date"].factorize()[0]
    offs = torch.arange(-SEQ, 1, device=dev)
    rows_by_day = pd.Series(np.arange(len(a))).groupby(day).apply(lambda s: s.to_numpy())

    def window(rows):
        return Gt[gpos[rows].unsqueeze(1) + offs]

    def ic_loss(s, t, g, n):
        cnt = torch.zeros(n, device=dev).index_add_(0, g, torch.ones_like(s)).clamp_min(1)
        mean = lambda v: torch.zeros(n, device=dev).index_add_(0, g, v) / cnt
        sc, tc = s - mean(s)[g], t - mean(t)[g]
        ic = mean(sc * tc) / (mean(sc * sc).sqrt() * mean(tc * tc).sqrt() + 1e-8)
        return -ic.mean()

    score = np.full(len(a), np.nan)
    years = fold_years(a)
    acc = np.zeros(len(a)); cnt = np.zeros(len(a))
    for s in range(seeds):
        torch.manual_seed(s); rng = np.random.default_rng(s)
        net = (GRUNet() if kind == "gru" else ChartCNN()).to(dev)
        if dev == "cuda" and kind == "cnn":
            net = net.to(memory_format=torch.channels_last)
        opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-4)
        scaler = torch.amp.GradScaler(enabled=dev == "cuda")
        for fi, Y in enumerate(years):
            tr, te = fold_masks(a, Y)
            t0 = time.time()
            tr_days = [d for d in np.unique(day[tr & ok])]
            epochs = ep_first if fi == 0 else ep_next              # later folds continue from the previous fold's weights
            for g in opt.param_groups:
                g["lr"] = 1e-3 if fi == 0 else 3e-4
            net.train(); last = 0.0
            for ep in range(epochs):
                use = rng.permutation(tr_days)[: max(1, int(len(tr_days) * day_frac))]
                tot = nb = 0
                for i in range(0, len(use), days_per_batch):
                    rl, gl = [], []
                    for j, d in enumerate(use[i:i + days_per_batch]):
                        r = rows_by_day[d]; r = r[tr[r] & ok[r]]
                        rl.append(r); gl.append(np.full(len(r), j))
                    rows = torch.tensor(np.concatenate(rl), device=dev); grp = torch.tensor(np.concatenate(gl), device=dev)
                    with torch.autocast(dev, enabled=dev == "cuda"):
                        loss = ic_loss(net(window(rows)).float(), y[rows], grp, len(rl))
                    opt.zero_grad(set_to_none=True)
                    scaler.scale(loss).backward(); scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                    scaler.step(opt); scaler.update()
                    tot += loss.item(); nb += 1
                last = -tot / max(nb, 1)
            net.eval()
            te_rows = np.where(te & ok)[0]
            with torch.no_grad(), torch.autocast(dev, enabled=dev == "cuda"):
                step = 8192 if kind == "gru" else 512      # chart images need far more memory per sample
                for i in range(0, len(te_rows), step):
                    r = te_rows[i:i + step]
                    p = net(window(torch.tensor(r, device=dev))).float().cpu().numpy()
                    acc[r] += p; cnt[r] += 1
            log(f"  {kind} seed {s} {Y}: {len(tr_days)} train days, {epochs} epochs, train IC {last:+.4f}, "
                f"scored {len(te_rows):,} ({time.time()-t0:.0f}s)")
    score[cnt > 0] = acc[cnt > 0] / cnt[cnt > 0]
    save_pred(out, kind, a, score)


# ===========================================================================
# stage: report
# ===========================================================================
def _rank_ic(df, col):
    g = df.groupby("date")
    ic = g.apply(lambda x: x[col].rank().corr(x["ret"].rank()), include_groups=False)
    return ic.dropna()


def _portfolio(picks: pd.DataFrame, G: np.ndarray, cost: float):
    """Daily returns of: each day buy K names at the next open (weight
    1/(H*K) each), sell at the close H days later. Charged `cost` per round
    trip, half on entry, half on exit."""
    o, c, dt = G[:, 0], G[:, 3], G[:, 5].astype("int64")
    w = 1.0 / (H * K)
    daily = {}
    for g in picks["gpos"].to_numpy():
        for j in range(1, H + 1):
            base = o[g + 1] if j == 1 else c[g + j - 1]
            r = c[g + j] / base - 1
            if j == 1:
                r -= cost / 2
            if j == H:
                r -= cost / 2
            daily[dt[g + j]] = daily.get(dt[g + j], 0.0) + w * r
    s = pd.Series(daily).sort_index()
    s.index = pd.to_datetime(s.index, unit="D")
    return s


def _perf(r: pd.Series):
    eq = (1 + r).cumprod()
    yrs = len(r) / 250
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": float(eq.iloc[-1] ** (1 / yrs) - 1), "vol": float(r.std() * math.sqrt(250)),
            "sharpe": float(r.mean() / r.std() * math.sqrt(250)) if r.std() > 0 else 0.0, "max_dd": float(dd)}


def stage_report(out: pathlib.Path):
    a, G = load(out)
    names = [n for n in ("xgb_reg", "xgb_rank", "gru", "cnn") if (out / f"pred_{n}.parquet").exists()]
    df = a[["date", "symbol", "ret", "gpos"]].copy()
    for n in names:
        p = pd.read_parquet(out / f"pred_{n}.parquet").rename(columns={"score": n})
        df = df.merge(p, on=["date", "symbol"], how="left")
        df[n + "_r"] = df.groupby("date")[n].rank(pct=True)
    df["ensemble"] = df[[n + "_r" for n in names]].mean(axis=1, skipna=False)   # equal-weight rank average
    df["reversal_5d"] = -a["mom_5"].to_numpy()                                 # simple baseline, no ML
    ev = df[df["ret"].notna() & df["ensemble"].notna() & (df["date"].dt.year >= FIRST_TEST)]
    log(f"evaluation: {len(ev):,} out-of-sample stock-days, {ev['date'].nunique()} days, "
        f"{ev['date'].min().date()} -> {ev['date'].max().date()}")

    res = {"settings": {"top_n": TOP_N, "H": H, "K": K, "cost": COST, "first_test": FIRST_TEST, "models": names}, "models": {}}
    print("\n== Rank IC (daily Spearman correlation of score with the realized 10-day return) ==")
    print(f"{'model':<14}{'mean IC':>9}{'ICIR':>7}{'IC>0':>7}{'top-decile minus universe, 10d':>34}")
    for n in names + ["ensemble", "reversal_5d"]:
        ic = _rank_ic(ev, n)
        dec = ev[ev.groupby("date")[n].rank(pct=True) > 0.9].groupby("date")["ret"].mean() - ev.groupby("date")["ret"].mean()
        res["models"][n] = {"rank_ic": float(ic.mean()), "icir": float(ic.mean() / ic.std()), "ic_pos": float((ic > 0).mean()),
                            "top_decile_spread": float(dec.mean())}
        print(f"{n:<14}{ic.mean():>9.4f}{ic.mean()/ic.std():>7.2f}{(ic>0).mean():>7.0%}{dec.mean()*100:>33.2f}%")
    if len(names) > 1:
        print("\ncorrelation between model scores (daily ranks):")
        print(ev[[n + "_r" for n in names]].corr().round(2).to_string())

    # top-K portfolio from the ensemble
    picks = ev.sort_values(["date", "ensemble"], ascending=[True, False]).groupby("date").head(K)
    gross, net = _portfolio(picks, G, 0.0), _portfolio(picks, G, COST)
    # benchmarks over the same days
    px = pd.DataFrame({"gpos": a["gpos"], "date": a["date"]})
    cc = G[:, 3][a["gpos"].to_numpy()] / G[:, 3][np.maximum(a["gpos"].to_numpy() - 1, 0)] - 1
    ew = pd.Series(cc).groupby(px["date"].to_numpy()).mean().reindex(net.index).fillna(0)
    nifty = pd.read_csv(out / "nifty.csv", parse_dates=["date"]).set_index("date")["close"].pct_change().reindex(net.index).fillna(0)
    rows = {"top-%d ensemble, after costs" % K: net, "top-%d ensemble, before costs" % K: gross,
            "equal-weight universe (no costs)": ew, "NIFTY 50 (no costs)": nifty}
    print(f"\n== Portfolio: buy the top {K} each day at the next open, hold {H} days, {COST:.2%} per round trip ==")
    print(f"{'':<36}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'max DD':>9}")
    res["portfolio"] = {}
    for k, r in rows.items():
        p = _perf(r); res["portfolio"][k] = p
        print(f"{k:<36}{p['cagr']:>8.1%}{p['vol']:>8.1%}{p['sharpe']:>8.2f}{p['max_dd']:>9.1%}")
    ex = net - ew
    t = ex.mean() / ex.std() * math.sqrt(len(ex))
    res["excess_vs_universe"] = {"annual": float(ex.mean() * 250), "t_stat": float(t)}
    print(f"\nexcess over the equal-weight universe after costs: {ex.mean()*250:+.1%} a year (t = {t:.2f})")
    tr = picks.assign(net=picks["ret"] - COST)
    res["trades"] = {"n": int(len(tr)), "per_year": float(len(tr) / (len(net) / 250)), "win_rate": float((tr["net"] > 0).mean()),
                     "avg_net": float(tr["net"].mean()), "profit_factor": float(tr.loc[tr.net > 0, "net"].sum() / -tr.loc[tr.net < 0, "net"].sum())}
    print(f"trades: {len(tr):,} ({res['trades']['per_year']:.0f} a year), win rate {res['trades']['win_rate']:.1%}, "
          f"average net {res['trades']['avg_net']*100:+.2f}%, profit factor {res['trades']['profit_factor']:.2f}")
    by = pd.DataFrame({"strategy": net, "universe": ew, "nifty": nifty})
    yr = by.groupby(by.index.year).apply(lambda x: (1 + x).prod() - 1)
    res["by_year"] = {int(k): {c: float(v[c]) for c in yr.columns} for k, v in yr.iterrows()}
    print("\nreturn by year (strategy after costs):")
    print((yr * 100).round(1).to_string())

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4))
        for k, r in rows.items():
            ax.plot((1 + r).cumprod(), label=k, lw=1.4)
        ax.set_yscale("log"); ax.legend(fontsize=8); ax.set_title("v3 walk-forward: growth of 1 (log scale)")
        fig.tight_layout(); fig.savefig(out / "equity.png", dpi=130)
    except Exception as e:
        log("chart skipped:", e)
    (out / "report.json").write_text(json.dumps(res, indent=1))
    with open(out / "trials.jsonl", "a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), **res["settings"], "ensemble_ic": res["models"]["ensemble"]["rank_ic"],
                            "net_sharpe": res["portfolio"]["top-%d ensemble, after costs" % K]["sharpe"]}) + "\n")
    log("report written to", out / "report.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all", help="data, trees, gru, cnn, report or all")
    ap.add_argument("--eod2", default="eod2_data")
    ap.add_argument("--out", default="v3_out")
    ap.add_argument("--quick", action="store_true", help="tiny settings, for a smoke test on CPU")
    args = ap.parse_args()
    out, q = pathlib.Path(args.out), args.quick
    st = args.stage
    if st in ("data", "all"):
        stage_data(pathlib.Path(args.eod2), out, 60 if q else TOP_N, 260 if q else None)
    if st in ("trees", "all"):
        stage_trees(out, 1 if q else SEEDS, 40 if q else 600)
    if st in ("gru", "all"):
        stage_seq(out, "gru", 1 if q else SEEDS, 1 if q else 6, 1 if q else 2, 4 if q else 8, 0.05 if q else 1.0)
    if st in ("cnn", "all"):
        stage_seq(out, "cnn", 1 if q else SEEDS, 1 if q else 3, 1, 2 if q else 4, 0.02 if q else 0.5)
    if st in ("report", "all"):
        stage_report(out)


if __name__ == "__main__":
    main()
