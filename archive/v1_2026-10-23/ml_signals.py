"""
ml_signals.py
=============
The bridge between the trained model and your LIVE bot (bot.py).

THE ONE THING THAT WOULD SILENTLY BREAK THIS IF WE WEREN'T CAREFUL
---------------------------------------------------------------------
train_model.py trained on DAILY bars (one row per trading day, years of
history). But bot.py's live loop polls a live quote every ~10 seconds and
was originally storing those raw ticks in a flat list. If we just fed 50
raw 10-second ticks into the same indicator math, the numbers (an "EMA21"
of the last 50 ticks = the last ~8 minutes) would look NOTHING like the
"EMA21" the model was trained on (last 21 trading DAYS). The model would
be evaluating features from a totally different distribution than it
learned from, and its predictions would be meaningless. This is a very
common real-world ML bug called TRAIN/SERVE SKEW.

The fix: this module keeps a real per-symbol DAILY bar history (loaded from
the same data/<SYMBOL>.csv that fetch_data.py produced) and continuously
updates a "today so far" bar (open/high/low/last price/volume) from each
live tick. Every prediction is computed on daily-bar-shaped data, exactly
like training - just with the most recent (still-forming) day appended.
That also means the predicted signal only really settles down once you
have enough of today's trading session behind you; early in the day it's
based on an incomplete bar and can be noisy - the confidence score is your
signal for how much to trust it (see MLSignalEngine.predict below).

Usage from bot.py:
    from ml_signals import MLSignalEngine
    ml_engine = MLSignalEngine()
    ...
    signal, confidence, features = ml_engine.predict(symbol, price, volume)
    # signal is "BUY" / "SELL" / "HOLD" / "COLLECTING" (not enough history yet)
"""
import datetime
import json
from pathlib import Path

import joblib
import pandas as pd

from features import add_indicators, FEATURE_COLUMNS, MIN_DAILY_HISTORY

BASE_DIR = Path(__file__).parent
MODEL_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
LABEL_NAMES = {1: "BUY", 0: "HOLD", -1: "SELL"}


def short_symbol(symbol: str) -> str:
    """'NSE:HDFCBANK-EQ' -> 'HDFCBANK' (matches bot.py's own naming)."""
    return symbol.replace("NSE:", "").replace("-EQ", "")


class LiveFeatureBuilder:
    """Tracks one symbol's daily-bar history + the still-forming 'today' bar."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.csv_path = DATA_DIR / f"{symbol}.csv"
        if self.csv_path.exists():
            self.daily_df = pd.read_csv(self.csv_path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        else:
            self.daily_df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        self.today_date = None
        self.today_bar = None

    def update_tick(self, price: float, volume: float):
        today = datetime.date.today()
        if self.today_date != today:
            self._finalize_previous_day()
            self.today_date = today
            self.today_bar = {"date": pd.Timestamp(today), "open": price, "high": price,
                               "low": price, "close": price, "volume": volume}
        else:
            b = self.today_bar
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["close"] = price
            # Fyers' quotes() volume field is already cumulative for the trading
            # day, so we just take the latest value rather than summing ticks.
            b["volume"] = volume

    def _finalize_previous_day(self):
        """Called when a new calendar day starts: bakes yesterday's evolving
        bar into permanent daily history and appends it to the CSV, so the
        model keeps getting fresher history the more days you run the bot."""
        if self.today_bar is not None:
            row = pd.DataFrame([self.today_bar])
            self.daily_df = pd.concat([self.daily_df, row], ignore_index=True)
            try:
                self.daily_df.to_csv(self.csv_path, index=False)
            except Exception as e:
                print(f"  [ml_signals] could not persist daily bar for {self.symbol}: {e}")
        self.today_bar = None

    def feature_row(self):
        if self.today_bar is None or len(self.daily_df) < MIN_DAILY_HISTORY:
            return None
        combined = pd.concat([self.daily_df, pd.DataFrame([self.today_bar])], ignore_index=True)
        combined = add_indicators(combined)
        last = combined.iloc[-1]
        if last[FEATURE_COLUMNS].isna().any():
            return None
        return {col: float(last[col]) for col in FEATURE_COLUMNS}


class MLSignalEngine:
    def __init__(self, model_dir: Path = MODEL_DIR):
        model_path = model_dir / "model.pkl"
        meta_path = model_dir / "metadata.json"
        if not model_path.exists():
            raise FileNotFoundError(
                f"No trained model found at {model_path}. Run train_model.py first "
                f"(generate_demo_data.py or fetch_data.py, then train_model.py)."
            )
        self.model = joblib.load(model_path)
        self.metadata = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        self.builders: dict[str, LiveFeatureBuilder] = {}

    def predict(self, symbol: str, price: float, volume: float):
        """
        symbol: the bot's full symbol string, e.g. "NSE:HDFCBANK-EQ"
        Returns (signal: str, confidence: float 0-1, feature_dict or None).
        signal is "COLLECTING" if there isn't enough daily history yet -
        callers should treat this the same way bot.py's original code did.
        """
        sym = short_symbol(symbol)
        if sym not in self.builders:
            self.builders[sym] = LiveFeatureBuilder(sym)
        builder = self.builders[sym]
        builder.update_tick(price, volume)

        features = builder.feature_row()
        if features is None:
            return "COLLECTING", 0.0, None

        row = pd.DataFrame([[features[col] for col in FEATURE_COLUMNS]], columns=FEATURE_COLUMNS)
        pred = self.model.predict(row)[0]
        proba = self.model.predict_proba(row)[0]
        confidence = float(max(proba))
        signal = LABEL_NAMES.get(int(pred), "HOLD")
        return signal, confidence, features
