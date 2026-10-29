"""
fetch_data.py
=============
Downloads REAL historical daily OHLCV data for your 4 stocks from Yahoo
Finance (free, no login needed) and saves one CSV per symbol into data/.

Run this in your own terminal (needs internet access):
    pip install yfinance
    python src/data_pipeline/fetch_data.py

If it fails (Yahoo occasionally rate-limits or changes its API), you have
two fallbacks:
  1. Run generate_demo_data.py instead to keep learning with synthetic data.
  2. Manually download CSVs from https://finance.yahoo.com/quote/RELIANCE.NS/history
     (or NSE's own historical data page) and save them into data/<SYMBOL>.csv
     with columns: date,open,high,low,close,volume
"""

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)
import time
import pandas as pd
from pathlib import Path

# Yahoo Finance ticker suffix for NSE (National Stock Exchange, India) is ".NS"
SYMBOL_MAP = {
    "HDFCBANK": "HDFCBANK.NS",
    "AUBANK": "AUBANK.NS",
    "BAJAJFINSV": "BAJAJFINSV.NS",
    "RELIANCE": "RELIANCE.NS",
}
PERIOD = "5y"       # how far back to fetch
INTERVAL = "1d"     # daily bars
from paths import DATA_DIR as OUT_DIR  # noqa: E402


def main():
    import yfinance as yf  # imported here so generate_demo_data.py doesn't need it installed

    OUT_DIR.mkdir(exist_ok=True)
    for symbol, ticker in SYMBOL_MAP.items():
        print(f"Fetching {symbol} ({ticker}) ...")
        df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)
        if df is None or df.empty:
            print(f"  ! No data returned for {ticker} - skipping. See fallback options in this file's docstring.")
            continue

        # yfinance sometimes returns MultiIndex columns when given one ticker - flatten them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        df = df.rename(columns={"date": "date"})[["date", "open", "high", "low", "close", "volume"]]

        path = OUT_DIR / f"{symbol}.csv"
        df.to_csv(path, index=False)
        print(f"  wrote {path}  ({len(df)} rows, {df['date'].min()} -> {df['date'].max()})")
        time.sleep(1)  # be polite to Yahoo's servers

    (OUT_DIR / ".source").write_text("real (Yahoo Finance)")
    print("\nDone. Next: python src/ml/train_model.py")


if __name__ == "__main__":
    main()
