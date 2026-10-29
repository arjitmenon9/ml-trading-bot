"""
fetch_data_fyers.py
====================
Downloads REAL historical daily OHLCV data for your 4 stocks straight from
your Fyers account - the same data source your live bot actually trades on,
so there's zero mismatch between what the model trained on and what it sees
live (beyond the daily-vs-tick handling already covered in ml_signals.py).

Run this in your own terminal (needs internet + a fresh access token):
    python src/broker/auth.py            # step 1 of the daily login
    python src/broker/get_token.py       # step 2: paste the auth code
    python src/data_pipeline/fetch_data_fyers.py

Fyers' history API caps how many days you can request per call (roughly a
year at a time for daily candles on most plans), so this fetches in yearly
chunks and stitches them together. If your plan's lookback is shorter than
what's requested here, Fyers will just return less data for the older
chunks - you'll still get whatever your plan allows, it just won't error out.
"""

import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: E402,F401  (puts src/* on sys.path)
import time
import datetime
from pathlib import Path

import pandas as pd

from config import get_fyers_client

SYMBOLS = [
    "NSE:HDFCBANK-EQ",
    "NSE:AUBANK-EQ",
    "NSE:BAJAJFINSV-EQ",
    "NSE:RELIANCE-EQ",
]
YEARS_OF_HISTORY = 5
from paths import DATA_DIR as OUT_DIR  # noqa: E402


def short_symbol(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace("-EQ", "")


def fetch_symbol_history(fyers, symbol: str) -> pd.DataFrame:
    all_candles = []
    today = datetime.date.today()
    for years_back in range(YEARS_OF_HISTORY, 0, -1):
        range_from = today - datetime.timedelta(days=years_back * 365)
        range_to = today - datetime.timedelta(days=(years_back - 1) * 365 - 1)
        payload = {
            "symbol": symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": range_from.isoformat(),
            "range_to": min(range_to, today).isoformat(),
            "cont_flag": "1",
        }
        try:
            resp = fyers.history(payload)
        except Exception as e:
            print(f"    ! request failed for {range_from}..{range_to}: {e}")
            continue
        if resp.get("s") != "ok" or not resp.get("candles"):
            print(f"    ! no data for {range_from}..{range_to}: {resp.get('message', resp)}")
            continue
        all_candles.extend(resp["candles"])
        time.sleep(0.5)  # be gentle on rate limits

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop(columns=["ts"]).drop_duplicates(subset="date").sort_values("date")
    return df[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def main():
    OUT_DIR.mkdir(exist_ok=True)
    print("Connecting to Fyers...")
    fyers = get_fyers_client()

    for symbol in SYMBOLS:
        name = short_symbol(symbol)
        print(f"Fetching {name} ({symbol}) ...")
        df = fetch_symbol_history(fyers, symbol)
        if df.empty:
            print(f"  ! No data returned for {symbol} - skipping.")
            continue
        path = OUT_DIR / f"{name}.csv"
        df.to_csv(path, index=False)
        print(f"  wrote {path}  ({len(df)} rows, {df['date'].min().date()} -> {df['date'].max().date()})")

    (OUT_DIR / ".source").write_text("real (Fyers History API)")
    print("\nDone. Next: python src/ml/train_model.py")


if __name__ == "__main__":
    main()
