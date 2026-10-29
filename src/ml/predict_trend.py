"""
predict_trend.py  (v2)
======================
Prints the model's view for every stock right now, from the latest
COMPLETED daily bar in data/history (run update_data.py first for fresh
data).

    python src/ml/predict_trend.py            # top 15 + the 4 focus stocks
    python src/ml/predict_trend.py RELIANCE   # one stock, with its trade plan

P(win) is the model's estimated chance that "buy at the next open, exit
with the strategy.py rules" ends in profit after charges. The bot only
buys at or above the threshold stored in models/metadata.json.
"""

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)
import sys

from ml_signals import daily_candidates, get_model
from universe import FOCUS_SYMBOLS


def main():
    m = get_model()
    want = [a.upper() for a in sys.argv[1:]] or None
    res = daily_candidates(symbols=want)
    rows = res["rows"]
    print(f"Model: {m.meta['model_name']} | buy threshold P(win) >= {m.threshold:.3f} | bars as of {res['asOf']}")
    h = m.meta.get("holdout", {})
    if h.get("n"):
        print(f"Walk-forward holdout record: {h['n']} trades, {h['win_rate']:.1%} won after charges")
    if res["stale"]:
        print(f"! {len(res['stale'])} stocks skipped: their history isn't current (run update_data.py)")
    show = rows if want else rows[:15] + [r for r in rows if r["symbol"] in FOCUS_SYMBOLS and r not in rows[:15]]
    print(f"\n{'stock':12s} {'P(win)':>7s}  {'signal':9s} {'close':>10s} {'target':>10s} {'stop':>10s}")
    for r in show:
        print(f"{r['symbol']:12s} {r['p']*100:6.1f}%  {r['signal']:9s} {r['close']:10.2f} "
              f"{r['tp']:10.2f} {r['sl']:10.2f}")
    print("\nNot investment advice. Past results do not guarantee future results.")


if __name__ == "__main__":
    main()
