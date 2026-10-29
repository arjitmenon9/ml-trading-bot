"""
backtest.py
===========
Answers the question that actually matters: "if the bot had traded using this
model's signals, would it have made money?" Accuracy alone doesn't tell you
that - a model could be 90% accurate at predicting HOLD and still miss every
profitable trade. A backtest replays history bar-by-bar and simulates real
paper trades.

We compare THREE strategies side by side, all on the same held-out test
period (the same "future" data the model never trained on):
  1. ML model         - buys/sells when the trained model says so
  2. Rule-based score - your ORIGINAL bot.py scoring system (score>=3 buy,
                        score<=-3 or score<0-while-holding => sell), so you
                        can see concretely whether ML actually beats the
                        hand-tuned rules it's meant to replace
  3. Buy & Hold        - just buy on day 1 and hold, the standard benchmark
                        every trading strategy has to beat to be worth the
                        extra complexity/risk

For each we report: total return %, number of trades, win rate, and max
drawdown (the worst peak-to-trough dip in account value - a risk measure,
not just a return measure).

IMPORTANT HONESTY NOTE: this backtest is intentionally simple (no fees, no
slippage, one unit position size, no compounding across symbols). Good enough
for comparing strategies apples-to-apples for a college project - NOT good
enough to decide whether to trade real money. See the "limitations" section
of the accompanying write-up before this goes anywhere near a live account.
"""
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from features import build_dataset, FEATURE_COLUMNS

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent / "data"
MODEL_DIR = Path(__file__).parent / "models"
REPORT_DIR = Path(__file__).parent / "reports"
TEST_FRACTION = 0.2


def rule_based_signal(row) -> int:
    """A vectorized re-implementation of bot.py's original score system,
    using the same-named features computed in features.py, so it's a fair
    comparison against the exact same feature set the ML model sees."""
    score = 0
    if row["ema_gap_pct"] > 0:
        score += 1
    else:
        score -= 1
    if row["rsi"] < 40:
        score += 2
    elif row["rsi"] < 60:
        score += 1
    elif row["rsi"] > 75:
        score -= 2
    elif row["rsi"] > 60:
        score -= 1
    if row["macd"] > row["macd_signal"]:
        score += 1
    else:
        score -= 1
    if row["bb_pct_b"] < 0:
        score += 2
    elif row["bb_pct_b"] > 1:
        score -= 2
    elif row["bb_pct_b"] < 0.5:
        score += 1
    else:
        score -= 1
    if row["vwap_gap_pct"] > 0:
        score += 1
    else:
        score -= 1
    if row["stoch"] < 20:
        score += 2
    elif row["stoch"] > 80:
        score -= 2

    if score >= 3:
        return 1   # BUY
    elif score <= -3:
        return -1  # SELL
    return 0       # HOLD


def simulate(df: pd.DataFrame, signal_col: str) -> dict:
    """
    Walks through rows in date order (within each symbol) and simulates
    paper trades: BUY signal opens a position (if flat), SELL signal (or a
    negative-score exit, mirrored here as any SELL signal) closes it.
    Only one position per symbol at a time, position size = 1 unit.
    """
    equity_curve = [0.0]
    trade_pnls = []
    position = None  # entry price, or None if flat

    for _, row in df.iterrows():
        sig = row[signal_col]
        price = row["close"]
        if sig == 1 and position is None:
            position = price
        elif sig == -1 and position is not None:
            pnl = price - position
            trade_pnls.append(pnl)
            equity_curve.append(equity_curve[-1] + pnl)
            position = None
        else:
            equity_curve.append(equity_curve[-1])

    if position is not None:  # close out anything left open at the end
        pnl = df["close"].iloc[-1] - position
        trade_pnls.append(pnl)
        equity_curve.append(equity_curve[-1] + pnl)

    equity = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity) if len(equity) else np.array([0])
    drawdown = equity - running_max
    max_dd = drawdown.min() if len(drawdown) else 0

    wins = [p for p in trade_pnls if p > 0]
    return {
        "total_pnl": round(float(equity[-1]), 2) if len(equity) else 0.0,
        "num_trades": len(trade_pnls),
        "win_rate": round(100 * len(wins) / len(trade_pnls), 1) if trade_pnls else 0.0,
        "max_drawdown": round(float(max_dd), 2),
        "equity_curve": equity,
    }


def main():
    model = joblib.load(MODEL_DIR / "model.pkl")

    all_results = {"ML Model": {}, "Rule-Based (original bot)": {}, "Buy & Hold": {}}
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=False)
    axes = axes.flatten()

    csvs = sorted(DATA_DIR.glob("*.csv"))
    for i, csv_path in enumerate(csvs):
        symbol = csv_path.stem
        raw = pd.read_csv(csv_path, parse_dates=["date"]).sort_values("date")
        ds = build_dataset(raw)
        cutoff = int(len(ds) * (1 - TEST_FRACTION))
        test = ds.iloc[cutoff:].reset_index(drop=True)

        test["ml_signal"] = model.predict(test[FEATURE_COLUMNS])
        test["rule_signal"] = test.apply(rule_based_signal, axis=1)

        ml_res = simulate(test, "ml_signal")
        rule_res = simulate(test, "rule_signal")

        bh_shares_pnl = test["close"].iloc[-1] - test["close"].iloc[0]
        bh_curve = test["close"].values - test["close"].iloc[0]

        all_results["ML Model"][symbol] = ml_res
        all_results["Rule-Based (original bot)"][symbol] = rule_res
        all_results["Buy & Hold"][symbol] = {"total_pnl": round(float(bh_shares_pnl), 2),
                                              "num_trades": 1, "win_rate": None,
                                              "max_drawdown": None, "equity_curve": bh_curve}

        ax = axes[i] if i < len(axes) else None
        if ax is not None:
            ax.plot(ml_res["equity_curve"], label="ML Model")
            ax.plot(rule_res["equity_curve"], label="Rule-Based")
            ax.plot(bh_curve, label="Buy & Hold", linestyle="--", color="gray")
            ax.set_title(symbol)
            ax.set_ylabel("Cumulative P&L (Rs./share)")
            ax.legend(fontsize=8)

    fig.suptitle("Backtest: Cumulative P&L on held-out test period", fontsize=13)
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "backtest_equity_curves.png", dpi=150)
    plt.close(fig)

    print("=" * 78)
    print(f"{'Symbol':<12}{'Strategy':<28}{'P&L (Rs)':>10}{'Trades':>9}{'Win %':>8}{'Max DD':>10}")
    print("=" * 78)
    for symbol in [c.stem for c in csvs]:
        for strat in all_results:
            r = all_results[strat][symbol]
            win = f"{r['win_rate']}" if r["win_rate"] is not None else "-"
            dd = f"{r['max_drawdown']}" if r["max_drawdown"] is not None else "-"
            print(f"{symbol:<12}{strat:<28}{r['total_pnl']:>10}{r['num_trades']:>9}{win:>8}{dd:>10}")
        print("-" * 78)

    print(f"\nSaved chart -> reports/backtest_equity_curves.png")
    print("\nRemember: this is a SIMPLIFIED backtest (no fees/slippage, 1 unit size).")
    print("Treat the *comparison* between strategies as the useful signal, not the")
    print("absolute Rs. numbers.")


if __name__ == "__main__":
    main()
