"""
backtest.py  (v2)
=================
Reads the walk-forward trades written by train_model.py
(models/oos_trades.csv - every trade comes from a model that had never seen
that year) and answers the practical questions:

  1. How did the strategy do, year by year, after Fyers charges?
  2. How much does POSITION SIZE matter? (Fyers' flat Rs 20/order brokerage
     and Rs 14.75 DP fee are a big % of a small trade.)
  3. How does it compare with simply holding the NIFTY 50 index?
  4. How did it do on the 4 original focus stocks?

Run:  python backtest.py      -> prints a report, writes reports/backtest_v2.png
"""
import json

import numpy as np
import pandas as pd

import strategy
from universe import BASE_DIR, FOCUS_SYMBOLS, load_index

MODEL_DIR = BASE_DIR / "models"
REPORT_DIR = BASE_DIR / "reports"


def summarize(t: pd.DataFrame, cost: float) -> dict:
    net = t["gross_ret"] - cost
    if not len(t):
        return {"n": 0}
    gp, gl = net[net > 0].sum(), -net[net < 0].sum()
    return {"n": len(t), "win": (net > 0).mean(), "avg": net.mean(), "pf": gp / gl if gl else np.inf,
            "worst": net.min(), "best": net.max()}


def line(name, s, pv):
    if not s["n"]:
        return f"  {name:28s} no trades"
    return (f"  {name:28s} {s['n']:4d} trades | win {s['win']:6.1%} | avg {s['avg']*100:+.2f}% | "
            f"PF {s['pf']:5.2f} | P&L Rs {s['avg']*s['n']*pv:>10,.0f} | worst {s['worst']*100:+.1f}%")


def main():
    path = MODEL_DIR / "oos_trades.csv"
    if not path.exists():
        raise SystemExit("models/oos_trades.csv not found - run `python train_model.py` first.")
    meta = json.loads((MODEL_DIR / "metadata.json").read_text())
    pv = meta.get("position_value", 50000)
    t = pd.read_csv(path, parse_dates=["entry_date", "exit_date"])
    cost = strategy.round_trip_cost(pv)
    print("=" * 96)
    print(f"  Walk-forward backtest | threshold P(win) >= {meta['threshold']} | rules {meta['strategy']}")
    print(f"  Charges: {cost*100:.2f}% round trip at Rs {pv:,}/trade (incl. 0.05%/side slippage)")
    print("=" * 96)
    print(line("ALL out-of-sample", summarize(t, cost), pv))
    for s in ("calibration", "holdout"):
        print(line(f"  {s}", summarize(t[t["set"] == s], cost), pv))
    print("\n  By year:")
    for y, g in t.groupby(t["entry_date"].dt.year):
        print(line(f"    {y}", summarize(g, cost), pv))
    print(line("\n  Focus stocks " + "/".join(FOCUS_SYMBOLS), summarize(t[t["symbol"].isin(FOCUS_SYMBOLS)], cost), pv))
    print("\n  Exit reasons:", t["exit_reason"].value_counts().to_dict())

    print("\n  Position size matters (same trades, different trade size):")
    for v in (5_000, 10_000, 25_000, 50_000, 100_000):
        c = strategy.round_trip_cost(v)
        s = summarize(t, c)
        print(f"    Rs {v:>7,}/trade -> charges {c*100:.2f}% | win {s['win']:.1%} | avg {s['avg']*100:+.2f}% | PF {s['pf']:.2f}")

    nifty = load_index("NIFTY50").set_index("date")["close"]
    a, b = t["entry_date"].min(), t["exit_date"].max()
    n0, n1 = nifty[nifty.index >= a].iloc[0], nifty[nifty.index <= b].iloc[-1]
    yrs = (b - a).days / 365.25
    # capital needed = max simultaneous positions x position value
    ev = pd.concat([pd.Series(1, t["entry_date"]), pd.Series(-1, t["exit_date"] + pd.Timedelta(days=1))]).sort_index().cumsum()
    peak_pos = int(ev.max())
    pnl = (t["gross_ret"] - cost).sum() * pv
    cap = max(1, peak_pos) * pv
    print(f"\n  Capital needed (peak {peak_pos} positions x Rs {pv:,}) = Rs {cap:,}")
    print(f"  Strategy: Rs {pnl:,.0f} on Rs {cap:,} over {yrs:.1f} yrs = {pnl/cap*100:+.1f}% "
          f"({((1+pnl/cap)**(1/yrs)-1)*100:+.1f}%/yr, simple, cash idle between trades)")
    print(f"  NIFTY 50 buy & hold, same window: {(n1/n0-1)*100:+.1f}% ({((n1/n0)**(1/yrs)-1)*100:+.1f}%/yr)")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        REPORT_DIR.mkdir(exist_ok=True)
        tt = t.sort_values("exit_date")
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for v, col in ((50_000, "#1f9d6b"), (5_000, "#c0563f")):
            c = strategy.round_trip_cost(v)
            ax.plot(tt["exit_date"], ((tt["gross_ret"] - c) * 100).cumsum(), color=col, lw=1.7,
                    label=f"Rs {v:,}/trade (charges {c*100:.2f}%)")
        ax.set_ylabel("cumulative return, % of one position"); ax.grid(alpha=.25); ax.legend()
        ax.set_title("Walk-forward trades: the same signals at two position sizes")
        fig.tight_layout(); fig.savefig(REPORT_DIR / "backtest_v2.png", dpi=130)
        print(f"\n  Chart: {REPORT_DIR / 'backtest_v2.png'}")
    except Exception as e:
        print("  (chart skipped:", e, ")")


if __name__ == "__main__":
    main()
