"""
features.py
============
ONE shared place for every indicator / feature calculation used by this project.

Why this file exists (read this if you're new to ML):
-------------------------------------------------------
The single most common way to accidentally ruin a trading ML model is to compute
a feature ONE way while training, and a SLIGHTLY DIFFERENT way when the bot is
actually running live. The model then sees numbers at prediction time that don't
look like anything it was trained on, and its predictions become garbage.

So both train_model.py (working on historical CSV data) and ml_signals.py
(working on the bot's live in-memory price list) import their indicator math
from THIS file. There is only one version of "what is RSI" in this whole project.

Two APIs are provided for every indicator:
  - a VECTORIZED pandas version (operates on a whole DataFrame column at once) -
    fast, used when we crunch years of historical data for training.
  - a "latest value from a list" version - used live, when the bot only wants
    the single most-recent indicator value from its running price/volume history.
Both call the same underlying math, so they agree with each other.
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config: keep every "magic number" in one place so it's easy to tune later.
# ---------------------------------------------------------------------------
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_STD = 20, 2
STOCH_PERIOD = 14
MOMENTUM_PERIODS = [5, 10, 20]  # % return over the last 5, 10, 20 bars
VOLATILITY_PERIOD = 10          # rolling std-dev of daily returns
ATR_PERIOD = 14
ADX_PERIOD = 14
CCI_PERIOD = 20
WILLIAMS_PERIOD = 14
OBV_ROC_PERIOD = 10
LOOKBACK_52W = 252               # ~1 trading year
LOOKBACK_52W_MIN = 60            # degrade gracefully with less history than a full year

# Labeling (how we decide what "correct" BUY/SELL/HOLD looks like in hindsight)
LABEL_HORIZON = 5              # look this many bars into the future
LABEL_THRESHOLD = 0.015        # +/-1.5% forward return splits BUY / SELL / HOLD

FEATURE_COLUMNS = [
    "ema9", "ema21", "ema_gap_pct",
    "rsi",
    "macd", "macd_signal", "macd_hist",
    "bb_pct_b", "bb_width",
    "vwap_gap_pct",
    "stoch",
    "mom_5", "mom_10", "mom_20",
    "volatility_10",
    "volume_ratio",
    "atr_pct",
    "adx",
    "cci",
    "williams_r",
    "obv_roc",
    "pct_from_52w_high",
    "pct_from_52w_low",
]


# ---------------------------------------------------------------------------
# Vectorized (pandas) versions - used on full historical DataFrames
# ---------------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    df must have columns: open, high, low, close, volume (lowercase), sorted
    by date ascending, one row per bar, for a SINGLE symbol.
    Returns a new DataFrame with indicator + feature columns appended.
    """
    df = df.copy()
    close = df["close"]
    volume = df["volume"]

    # --- Trend: EMAs ---
    df["ema9"] = close.ewm(span=EMA_FAST, adjust=False).mean()
    df["ema21"] = close.ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema_gap_pct"] = (df["ema9"] - df["ema21"]) / df["ema21"] * 100

    # --- Momentum oscillator: RSI (Wilder's smoothing, the standard definition) ---
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50)  # neutral when undefined (e.g. no losses yet)

    # --- MACD ---
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # --- Bollinger Bands ---
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std()
    upper = mid + BB_STD * std
    lower = mid - BB_STD * std
    # %B: where is price within the band? 0 = at lower band, 1 = at upper band
    df["bb_pct_b"] = (close - lower) / (upper - lower)
    df["bb_width"] = (upper - lower) / mid

    # --- VWAP, as a rolling 20-day window rather than cumulative-since-day-1.
    # (A cumulative-from-the-start VWAP drifts arbitrarily far from price over
    # a multi-year dataset and its value would depend on how much history
    # happens to be loaded - not a stable, comparable feature. A rolling
    # window keeps it meaningful and consistent between training and live.) ---
    rolling_pv = (close * volume).rolling(BB_PERIOD).sum()
    rolling_v = volume.rolling(BB_PERIOD).sum().replace(0, np.nan)
    vwap = rolling_pv / rolling_v
    df["vwap_gap_pct"] = (close - vwap) / vwap * 100

    # --- Stochastic Oscillator ---
    low_min = df["low"].rolling(STOCH_PERIOD).min()
    high_max = df["high"].rolling(STOCH_PERIOD).max()
    df["stoch"] = (close - low_min) / (high_max - low_min).replace(0, np.nan) * 100
    df["stoch"] = df["stoch"].fillna(50)

    # --- Derived: momentum & volatility & volume ---
    for p in MOMENTUM_PERIODS:
        df[f"mom_{p}"] = close.pct_change(p) * 100
    df["volatility_10"] = close.pct_change().rolling(VOLATILITY_PERIOD).std() * 100
    df["volume_ratio"] = volume / volume.rolling(20).mean().replace(0, np.nan)

    # --- ATR (Average True Range): how much a stock typically moves per day,
    # in % of price so it's comparable across stocks of different price levels ---
    prev_close = close.shift(1)
    true_range = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()
    df["atr_pct"] = atr / close * 100

    # --- ADX (Average Directional Index): how STRONG the current trend is,
    # regardless of direction. Low ADX = choppy/sideways, high ADX = trending. ---
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr_for_di = true_range.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / ADX_PERIOD, adjust=False).mean() / atr_for_di
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / ADX_PERIOD, adjust=False).mean() / atr_for_di
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()

    # --- CCI (Commodity Channel Index): how far price is from its recent
    # average, scaled by typical deviation - flags "stretched" moves ---
    typical_price = (df["high"] + df["low"] + close) / 3
    tp_sma = typical_price.rolling(CCI_PERIOD).mean()
    mean_dev = typical_price.rolling(CCI_PERIOD).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci"] = (typical_price - tp_sma) / (0.015 * mean_dev.replace(0, np.nan))

    # --- Williams %R: similar idea to Stochastic, where price sits within
    # its recent high/low range, on a -100..0 scale ---
    hh = df["high"].rolling(WILLIAMS_PERIOD).max()
    ll = df["low"].rolling(WILLIAMS_PERIOD).min()
    df["williams_r"] = (hh - close) / (hh - ll).replace(0, np.nan) * -100

    # --- OBV (On-Balance Volume): running total of volume, added when price
    # rises and subtracted when it falls. We use its 10-day RATE OF CHANGE
    # rather than the raw cumulative level, since the level's scale isn't
    # comparable across stocks (or even meaningful on its own). ---
    obv_direction = np.sign(close.diff().fillna(0))
    obv = (obv_direction * volume).cumsum()
    df["obv_roc"] = obv.pct_change(OBV_ROC_PERIOD).replace([np.inf, -np.inf], np.nan) * 100

    # --- Position relative to the 52-week range: is price near a breakout
    # (near its 1yr high) or near capitulation (near its 1yr low)? ---
    roll_max = close.rolling(LOOKBACK_52W, min_periods=LOOKBACK_52W_MIN).max()
    roll_min = close.rolling(LOOKBACK_52W, min_periods=LOOKBACK_52W_MIN).min()
    df["pct_from_52w_high"] = (close - roll_max) / roll_max * 100
    df["pct_from_52w_low"] = (close - roll_min) / roll_min * 100

    return df


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds a 'label' column: 1 = BUY, -1 = SELL, 0 = HOLD, based on the forward
    return LABEL_HORIZON bars ahead. This uses FUTURE data on purpose - it's
    how we build a "ground truth" answer key to train against. These rows get
    dropped before backtesting (a backtest must never see the future).
    """
    df = df.copy()
    fwd_return = df["close"].shift(-LABEL_HORIZON) / df["close"] - 1
    df["fwd_return"] = fwd_return
    df["label"] = 0
    df.loc[fwd_return > LABEL_THRESHOLD, "label"] = 1
    df.loc[fwd_return < -LABEL_THRESHOLD, "label"] = -1
    return df


def build_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """Full pipeline: raw OHLCV -> indicators -> labels -> clean rows only."""
    df = add_indicators(df)
    df = add_labels(df)
    # Drop rows where indicators or labels couldn't be computed
    # (start of series has no history; end of series has no future to label)
    needed = FEATURE_COLUMNS + ["label"]
    df = df.dropna(subset=needed).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Minimum bars of daily history needed before every indicator is computable.
# Used by ml_signals.py, which builds a real daily-bar history for the live
# bot (see that file for why: the model is trained on DAILY bars, not raw
# intraday ticks, so live inference needs to feed it daily bars too).
# ---------------------------------------------------------------------------
# Note: pct_from_52w_high/low use min_periods=LOOKBACK_52W_MIN (60), so they
# become available well before a full year of history - that's the binding
# constraint here, not the full 252-day window.
MIN_DAILY_HISTORY = max(EMA_SLOW, MACD_SLOW + MACD_SIGNAL, BB_PERIOD, STOCH_PERIOD,
                         ATR_PERIOD, ADX_PERIOD * 2, CCI_PERIOD, WILLIAMS_PERIOD,
                         OBV_ROC_PERIOD, LOOKBACK_52W_MIN, 20) + 5
