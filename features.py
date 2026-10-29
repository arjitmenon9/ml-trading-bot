"""
features.py  (v2)
=================
ONE shared place for every indicator / feature calculation in this project.

train_model.py, backtest.py, ml_signals.py (the live bot) and the Atlas
backend all import their indicator maths from THIS file, so a feature is
computed exactly the same way in training and live (no train/serve skew).

What changed in v2 (October 2026)
---------------------------------
* Every model feature is now SCALE-FREE (percentages, ratios, 0-100
  oscillators). v1 fed raw EMA / MACD rupee values to the model, which made
  a ₹700 stock and a ₹3,000 stock look like different universes. Scale-free
  features let ONE model learn from ~55 NSE stocks and 20 years of history
  instead of 4 stocks x 5 years.
* Added the indicators the Atlas front end shows on its Technicals tab:
  SMA 20 / 50 / 200 and EMA 20 (as % distance from price), plus the
  SMA50-vs-SMA200 "golden cross" gap and the slope of SMA 200.
* Added short-term mean-reversion features (RSI-2, IBS, down-streak, 1-3 day
  returns, distance from 5-day low, 20-day z-score, opening gap) - these are
  what make short swing trades predictable.
* Added market-regime features from NIFTY 50 and India VIX, and relative
  strength of the stock vs the index.

The label / exit rules for the swing strategy live in strategy.py.
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Periods (every "magic number" in one place)
# ---------------------------------------------------------------------------
EMA_FAST, EMA_SLOW = 9, 21
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_STD = 20, 2
STOCH_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
CCI_PERIOD = 20
WILLIAMS_PERIOD = 14
OBV_ROC_PERIOD = 10
LOOKBACK_52W = 252
LOOKBACK_52W_MIN = 60
SMA_PERIODS = (20, 50, 200)        # Atlas Technicals tab
EMA_ATLAS = 20                     # Atlas Technicals tab

# ---------------------------------------------------------------------------
# Model features (all scale-free). Order matters: the model is trained on it.
# ---------------------------------------------------------------------------
STOCK_FEATURES = [
    # trend
    "ema_gap_pct", "dist_ema20", "dist_sma20", "dist_sma50", "dist_sma200",
    "sma50_200_gap", "sma200_slope",
    # momentum / oscillators
    "rsi", "rsi2", "rsi5", "macd_pct", "macd_hist_pct", "stoch", "williams_r",
    "cci", "adx", "di_diff",
    # returns
    "mom_1", "mom_3", "mom_5", "mom_10", "mom_20", "mom_60",
    # volatility / bands
    "bb_pct_b", "bb_width", "atr_pct", "volatility_10", "vol_ratio_10_60",
    # volume
    "volume_ratio", "vwap_gap_pct", "obv_roc",
    # price action (mean reversion)
    "ibs", "gap_pct", "down_streak", "dist_low5", "dist_high5", "zscore_20",
    # yearly range
    "pct_from_52w_high", "pct_from_52w_low",
]
MARKET_FEATURES = [
    "mkt_mom_5", "mkt_mom_20", "mkt_dist_sma200", "mkt_rsi", "mkt_rsi2",
    "vix", "vix_chg_5", "rel_str_20", "rel_str_60",
]
FEATURE_COLUMNS = STOCK_FEATURES + MARKET_FEATURES

# Bars of daily history needed before every stock feature is defined
# (SMA200 + its 20-day slope is the binding constraint).
MIN_DAILY_HISTORY = 200 + 20 + 5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


# ---------------------------------------------------------------------------
# Stock indicators (vectorised, one symbol, sorted by date ascending)
# ---------------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df needs open, high, low, close, volume (lowercase). Returns a copy with
    raw indicator columns (for display) and the scale-free STOCK_FEATURES."""
    df = df.copy()
    o, h, l, c, v = (df[k].astype(float) for k in ("open", "high", "low", "close", "volume"))

    # --- raw indicators (also shown on the dashboard / Atlas Technicals tab)
    df["ema9"] = c.ewm(span=EMA_FAST, adjust=False).mean()
    df["ema21"] = c.ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema20"] = c.ewm(span=EMA_ATLAS, adjust=False).mean()
    for p in SMA_PERIODS:
        df[f"sma{p}"] = c.rolling(p).mean()
    ema_f = c.ewm(span=MACD_FAST, adjust=False).mean()
    ema_s = c.ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd"] = ema_f - ema_s
    df["macd_signal"] = df["macd"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    mid = c.rolling(BB_PERIOD).mean()
    std = c.rolling(BB_PERIOD).std()
    df["bb_mid"], df["bb_upper"], df["bb_lower"] = mid, mid + BB_STD * std, mid - BB_STD * std
    vwap = (c * v).rolling(BB_PERIOD).sum() / v.rolling(BB_PERIOD).sum().replace(0, np.nan)
    df["vwap"] = vwap
    tr = _true_range(df)
    atr = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()
    df["atr"] = atr

    # --- trend features
    df["ema_gap_pct"] = (df["ema9"] - df["ema21"]) / df["ema21"] * 100
    df["dist_ema20"] = (c / df["ema20"] - 1) * 100
    for p in SMA_PERIODS:
        df[f"dist_sma{p}"] = (c / df[f"sma{p}"] - 1) * 100
    df["sma50_200_gap"] = (df["sma50"] / df["sma200"] - 1) * 100
    df["sma200_slope"] = df["sma200"].pct_change(20) * 100

    # --- momentum / oscillators
    df["rsi"] = _rsi(c, RSI_PERIOD)
    df["rsi2"] = _rsi(c, 2)
    df["rsi5"] = _rsi(c, 5)
    df["macd_pct"] = df["macd"] / c * 100
    df["macd_hist_pct"] = df["macd_hist"] / c * 100
    lo14, hi14 = l.rolling(STOCH_PERIOD).min(), h.rolling(STOCH_PERIOD).max()
    df["stoch"] = ((c - lo14) / (hi14 - lo14).replace(0, np.nan) * 100).fillna(50)
    hh, ll = h.rolling(WILLIAMS_PERIOD).max(), l.rolling(WILLIAMS_PERIOD).min()
    df["williams_r"] = (hh - c) / (hh - ll).replace(0, np.nan) * -100
    tp = (h + l + c) / 3
    md = tp.rolling(CCI_PERIOD).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci"] = (tp - tp.rolling(CCI_PERIOD).mean()) / (0.015 * md.replace(0, np.nan))
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    atr_di = tr.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * pdm.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean() / atr_di
    mdi = 100 * mdm.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean() / atr_di
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / ADX_PERIOD, adjust=False).mean()
    df["di_diff"] = pdi - mdi

    # --- returns
    for p in (1, 3, 5, 10, 20, 60):
        df[f"mom_{p}"] = c.pct_change(p) * 100

    # --- volatility / bands
    df["bb_pct_b"] = (c - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / mid * 100
    df["atr_pct"] = atr / c * 100
    r = c.pct_change()
    df["volatility_10"] = r.rolling(10).std() * 100
    df["vol_ratio_10_60"] = r.rolling(10).std() / r.rolling(60).std().replace(0, np.nan)

    # --- volume
    df["volume_ratio"] = v / v.rolling(20).mean().replace(0, np.nan)
    df["vwap_gap_pct"] = (c / vwap - 1) * 100
    obv = (np.sign(c.diff().fillna(0)) * v).cumsum()
    # OBV change over 10 days, normalised by 20-day average volume (the raw
    # OBV level and its pct_change are both meaningless across stocks)
    df["obv_roc"] = (obv - obv.shift(OBV_ROC_PERIOD)) / v.rolling(20).mean().replace(0, np.nan)

    # --- price action (short-term mean reversion)
    df["ibs"] = ((c - l) / (h - l).replace(0, np.nan)).fillna(0.5)
    df["gap_pct"] = (o / c.shift(1) - 1) * 100
    down = (c.diff() < 0).astype(int)
    df["down_streak"] = down.groupby((down == 0).cumsum()).cumsum()
    df["dist_low5"] = (c / l.rolling(5).min() - 1) * 100
    df["dist_high5"] = (c / h.rolling(5).max() - 1) * 100
    df["zscore_20"] = (c - mid) / std.replace(0, np.nan)

    # --- yearly range
    mx = c.rolling(LOOKBACK_52W, min_periods=LOOKBACK_52W_MIN).max()
    mn = c.rolling(LOOKBACK_52W, min_periods=LOOKBACK_52W_MIN).min()
    df["pct_from_52w_high"] = (c / mx - 1) * 100
    df["pct_from_52w_low"] = (c / mn - 1) * 100
    return df


# ---------------------------------------------------------------------------
# Market regime (NIFTY 50 + India VIX), joined onto each stock by date
# ---------------------------------------------------------------------------
def market_frame(nifty: pd.DataFrame, vix: pd.DataFrame | None = None) -> pd.DataFrame:
    """nifty/vix: DataFrames with date + close (vix optional). Returns one row
    per date with the market features (except relative strength)."""
    n = nifty.sort_values("date").set_index("date")["close"].astype(float)
    m = pd.DataFrame(index=n.index)
    m["mkt_close"] = n
    m["mkt_mom_5"] = n.pct_change(5) * 100
    m["mkt_mom_20"] = n.pct_change(20) * 100
    m["mkt_mom_60"] = n.pct_change(60) * 100
    m["mkt_dist_sma200"] = (n / n.rolling(200).mean() - 1) * 100
    m["mkt_rsi"] = _rsi(n, 14)
    m["mkt_rsi2"] = _rsi(n, 2)
    if vix is not None and len(vix):
        vx = vix.sort_values("date").set_index("date")["close"].astype(float)
        vx = vx.reindex(m.index).ffill(limit=3)
        m["vix"] = vx
        m["vix_chg_5"] = vx.pct_change(5) * 100
    else:
        m["vix"] = np.nan
        m["vix_chg_5"] = np.nan
    return m.reset_index()


def add_market(df: pd.DataFrame, mkt: pd.DataFrame) -> pd.DataFrame:
    """Join market features (by date) onto a stock frame that already has
    add_indicators() columns, and add relative strength vs the index."""
    out = df.merge(mkt, on="date", how="left")
    out["rel_str_20"] = out["mom_20"] - out["mkt_mom_20"]
    out["rel_str_60"] = out["mom_60"] - out["mkt_mom_60"]
    return out


def build_features(df: pd.DataFrame, mkt: pd.DataFrame | None) -> pd.DataFrame:
    """OHLCV for one symbol -> indicators + market features. If mkt is None
    the market columns are NaN (the model handles missing values natively)."""
    out = add_indicators(df)
    if mkt is not None:
        out = add_market(out, mkt)
    else:
        for col in MARKET_FEATURES:
            out[col] = np.nan
    return out


# ---------------------------------------------------------------------------
# Cross-sectional features (v2.1): how a stock looks RELATIVE TO THE WHOLE
# UNIVERSE on the same day, plus market breadth. Needs every stock's row for
# a date, so it runs on the combined frame (training) or on the morning's
# full scan (live bot).
# ---------------------------------------------------------------------------
CS_SOURCE = ["mom_5", "rsi2", "mom_20", "dist_sma200", "zscore_20", "volume_ratio", "atr_pct"]
CROSS_FEATURES = [f"cs_{c}" for c in CS_SOURCE] + ["breadth_50", "breadth_down5"]
MODEL_FEATURES = FEATURE_COLUMNS + CROSS_FEATURES


def add_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """df: many symbols, with STOCK_FEATURES and a `date` column."""
    df = df.copy()
    g = df.groupby("date")
    for c in CS_SOURCE:
        df[f"cs_{c}"] = g[c].rank(pct=True)
    df["breadth_50"] = (df["dist_sma50"] > 0).astype(float).groupby(df["date"]).transform("mean")
    df["breadth_down5"] = (df["mom_5"] < 0).astype(float).groupby(df["date"]).transform("mean")
    return df
