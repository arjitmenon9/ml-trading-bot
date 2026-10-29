"""
generate_demo_data.py
======================
Creates FAKE (synthetic) daily OHLCV data so you can run the entire ML
pipeline right now, without waiting on internet access or a broker API.

This is a teaching tool, not real data. It simulates prices that drift in
"regimes" (a few weeks of mild uptrend, then a few weeks of downtrend, etc.)
on purpose, so that technical indicators actually have *something* real to
learn from -- pure random-walk prices would be unpredictable by design, and
you'd have no way to tell "the pipeline is broken" apart from "the market is
noisy," which is a bad first experience when you're learning.

Once this all works end to end, swap in real data with fetch_data.py and
re-run everything -- every other script (features.py, train_model.py,
backtest.py) works unchanged, because they only care about the column names
(date, open, high, low, close, volume), not where the CSV came from.

Usage:
    python src/data_pipeline/generate_demo_data.py
"""

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)
import numpy as np
import pandas as pd
from pathlib import Path

SYMBOLS = ["HDFCBANK", "AUBANK", "BAJAJFINSV", "RELIANCE"]
START_PRICE = {"HDFCBANK": 1650, "AUBANK": 650, "BAJAJFINSV": 1750, "RELIANCE": 2900}
N_DAYS = 1500  # roughly 6 years of trading days
from paths import DATA_DIR as OUT_DIR  # noqa: E402


def simulate_one_symbol(symbol: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = START_PRICE[symbol]
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=N_DAYS)

    closes = []
    regime_drift = 0.0
    regime_len_left = 0
    for _ in range(N_DAYS):
        if regime_len_left <= 0:
            # start a new "regime": a drift that persists for 15-40 days
            regime_drift = rng.normal(0, 0.0035)
            regime_len_left = rng.integers(15, 40)
        regime_len_left -= 1
        noise = rng.normal(0, 0.011)
        ret = regime_drift + noise
        price *= (1 + ret)
        closes.append(price)

    closes = np.array(closes)
    highs = closes * (1 + np.abs(rng.normal(0, 0.006, N_DAYS)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.006, N_DAYS)))
    opens = lows + (highs - lows) * rng.uniform(0, 1, N_DAYS)
    volumes = rng.integers(500_000, 5_000_000, N_DAYS)

    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


def main():
    OUT_DIR.mkdir(exist_ok=True)
    for i, symbol in enumerate(SYMBOLS):
        df = simulate_one_symbol(symbol, seed=42 + i)
        path = OUT_DIR / f"{symbol}.csv"
        df.to_csv(path, index=False)
        print(f"  wrote {path}  ({len(df)} rows, {df['date'].min().date()} -> {df['date'].max().date()})")
    (OUT_DIR / ".source").write_text("demo")
    print("\nDemo data ready. Next: python src/ml/train_model.py")
    print("Reminder: this is FAKE data for learning the pipeline. Run fetch_data.py")
    print("and retrain before trusting any signal on a real account.")


if __name__ == "__main__":
    main()
