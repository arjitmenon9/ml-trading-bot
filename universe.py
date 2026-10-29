"""
universe.py
===========
Which stocks the model learns from and the bot scans, and how their daily
history is loaded. Shared by train_model.py, backtest.py, update_data.py,
ml_signals.py and bot.py.

Data layout
-----------
data/history/<SYMBOL>.csv   daily OHLCV (+ delivery qty when known) per NSE stock
data/index/NIFTY50.csv      NIFTY 50 index (market-regime features)
data/index/INDIAVIX.csv     India VIX (market-regime features)

History up to 13 Oct 2026 came from the public eod2 dataset
(github.com/BennyThadikaran/eod2_data, NSE bhavcopy, split/bonus adjusted;
it matches the Fyers History API to the paisa on every overlapping day).
update_data.py tops it up every morning from Fyers.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent
HIST_DIR = BASE_DIR / "data" / "history"
INDEX_DIR = BASE_DIR / "data" / "index"

# The original 4 stocks of this project. They stay front-and-centre on the
# dashboard; the bot now scans the whole universe below for setups, because
# a strict (high win-rate) filter fires too rarely on just 4 names.
FOCUS_SYMBOLS = ["HDFCBANK", "AUBANK", "BAJAJFINSV", "RELIANCE"]

TRAIN_START = "2007-01-01"   # pre-2007 eod2 data has unadjusted splits
BREAK_JUMP = 0.35            # a >35% one-day move = corporate action / data break
MIN_ROWS = 300


def all_symbols() -> list[str]:
    return sorted(p.stem for p in HIST_DIR.glob("*.csv"))


def fyers_symbol(sym: str) -> str:
    return f"NSE:{sym}-EQ"


def short_symbol(fy: str) -> str:
    return fy.replace("NSE:", "").replace("-EQ", "")


def load_history(sym: str) -> pd.DataFrame:
    d = pd.read_csv(HIST_DIR / f"{sym}.csv", parse_dates=["date"])
    return d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def load_index(name: str) -> pd.DataFrame:
    p = INDEX_DIR / f"{name}.csv"
    if not p.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    d = pd.read_csv(p, parse_dates=["date"])
    return d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def load_market():
    from features import market_frame
    return market_frame(load_index("NIFTY50"), load_index("INDIAVIX"))


def segments(d: pd.DataFrame, start: str = TRAIN_START):
    """Split one symbol's history at data breaks (demergers, unadjusted
    splits) so no indicator or label ever straddles a fake price jump."""
    d = d[d["date"] >= start].reset_index(drop=True)
    jump = d["close"].pct_change().abs() > BREAK_JUMP
    for _, g in d.groupby(jump.cumsum()):
        g = g.reset_index(drop=True)
        if len(g) >= MIN_ROWS:
            yield g


def last_segment(d: pd.DataFrame) -> pd.DataFrame:
    """The part of the history after the most recent data break (what the
    live model should compute features on)."""
    jump = d["close"].pct_change().abs() > BREAK_JUMP
    if jump.any():
        d = d.loc[jump[jump].index[-1]:].reset_index(drop=True)
    return d
