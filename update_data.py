"""
update_data.py
==============
Keeps data/history/*.csv and data/index/*.csv current, so the model scores
yesterday's real completed bar every morning.

Sources, in order:
  1. Fyers History API (needs today's token; same data the bot trades on)
  2. eod2 dataset on GitHub (no login; NSE bhavcopy, usually a few days behind)

Run by hand:      python update_data.py            (Fyers, falls back to GitHub)
                  python update_data.py --github   (GitHub only, no login needed)
The bot also calls update_from_fyers() by itself each morning.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import time
import urllib.parse
import urllib.request

import pandas as pd

from universe import HIST_DIR, INDEX_DIR, all_symbols, fyers_symbol

INDEXES = {"NIFTY50": "NSE:NIFTY50-INDEX", "INDIAVIX": "NSE:INDIAVIX-INDEX"}
EOD2 = "https://raw.githubusercontent.com/BennyThadikaran/eod2_data/main/daily/{}.csv"
EOD2_INDEX = {"NIFTY50": "nifty 50", "INDIAVIX": "india vix"}
COLS = ["date", "open", "high", "low", "close", "volume"]
# Fyers Standard plan: 50 history requests per MINUTE (error code -360).
# 1.5 s between calls = 40/min, leaving room for the bot's quote calls.
PACE_SECONDS = 1.5


def _merge(path, new: pd.DataFrame):
    new = new[[c for c in new.columns if c in COLS + ["delivery"]]].copy()
    new["date"] = pd.to_datetime(new["date"])
    if path.exists():
        old = pd.read_csv(path, parse_dates=["date"])
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values("date").drop_duplicates("date", keep="last")
    new.to_csv(path, index=False)
    return new["date"].max()


class RateLimited(RuntimeError):
    pass


def _fyers_daily(fyers, symbol: str, days: int) -> pd.DataFrame:
    today = dt.date.today()
    payload = {"symbol": symbol, "resolution": "D", "date_format": "1",
               "range_from": (today - dt.timedelta(days=days)).isoformat(),
               "range_to": today.isoformat(), "cont_flag": "1"}
    for attempt in range(4):
        try:
            resp = fyers.history(payload)
        except Exception as e:          # fyers_apiv3 raises on an empty/HTML body when throttled
            resp = {"s": "error", "code": -360, "message": f"request failed: {e}"}
        if resp.get("s") == "ok" or not (resp.get("code") == -360 or "limit" in str(resp.get("message", "")).lower()
                                         or "request failed" in str(resp.get("message", ""))):
            break
        time.sleep(62)                  # wait out the per-minute window, then retry
    if resp.get("s") != "ok" or not resp.get("candles"):
        raise RuntimeError(resp.get("message", "no candles"))
    d = pd.DataFrame(resp["candles"], columns=["ts"] + COLS[1:])
    d["date"] = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None).dt.normalize()
    d = d.drop(columns="ts")
    now = dt.datetime.now()
    if (now.hour, now.minute) < (15, 35):          # never store today's half-formed bar
        d = d[d["date"].dt.date < today]
    return d


def _last_date(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 300))
            return pd.Timestamp(f.read().decode(errors="ignore").strip().splitlines()[-1].split(",")[0])
    except Exception:
        return None


def update_from_fyers(fyers, symbols=None, days: int = 20, log=print) -> dict:
    """Top up history from Fyers, paced under the 50 requests/minute limit.
    Symbols already up to the latest session (per NIFTY 50) are skipped."""
    symbols = symbols or all_symbols()
    ok, fail, skipped = 0, [], 0
    for name, fy in INDEXES.items():
        try:
            _merge(INDEX_DIR / f"{name}.csv", _fyers_daily(fyers, fy, days)); ok += 1
        except Exception as e:
            fail.append((name, str(e)[:80]))
        time.sleep(PACE_SECONDS)
    latest = _last_date(INDEX_DIR / "NIFTY50.csv")
    todo = [s for s in symbols if latest is None or (_last_date(HIST_DIR / f"{s}.csv") or pd.Timestamp(0)) < latest]
    skipped = len(symbols) - len(todo)
    if log:
        log(f"  [data] latest session {latest.date() if latest is not None else '?'}: {len(todo)} stocks to update, "
            f"{skipped} already current (~{len(todo)*PACE_SECONDS/60:.0f} min at Fyers' 50/min limit)")
    for i, s in enumerate(todo):
        try:
            _merge(HIST_DIR / f"{s}.csv", _fyers_daily(fyers, fyers_symbol(s), days)); ok += 1
        except Exception as e:
            fail.append((s, str(e)[:80]))
            if len(fail) >= 8 and ok == 0:     # token / network problem: stop early
                break
        time.sleep(PACE_SECONDS)
        if log and (i + 1) % 25 == 0:
            log(f"  [data] {i+1}/{len(todo)} stocks updated from Fyers")
    return {"ok": ok, "failed": fail, "skipped": skipped, "source": "Fyers"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ml-trading-bot/2 (data update)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def update_from_github(symbols=None, log=print) -> dict:
    symbols = symbols or all_symbols()
    ok, fail = 0, []
    jobs = [(INDEX_DIR / f"{k}.csv", EOD2.format(urllib.parse.quote(v))) for k, v in EOD2_INDEX.items()]
    jobs += [(HIST_DIR / f"{s}.csv", EOD2.format(urllib.parse.quote(s.lower()))) for s in symbols]
    for path, url in jobs:
        try:
            d = pd.read_csv(io.BytesIO(_get(url)), parse_dates=["Date"])
            d = d.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low",
                                  "Close": "close", "Volume": "volume", "DLV_QTY": "delivery"})
            d = d[d["date"] >= pd.Timestamp.today() - pd.Timedelta(days=120)]
            _merge(path, d); ok += 1
        except Exception as e:
            fail.append((path.stem, str(e)[:80]))
    return {"ok": ok, "failed": fail, "source": "GitHub eod2"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--github", action="store_true", help="use the GitHub dataset (no Fyers login)")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    res = None
    if not a.github:
        try:
            from config import get_fyers_client
            print("Updating from Fyers...")
            res = update_from_fyers(get_fyers_client(), days=a.days)
        except Exception as e:
            print(f"  Fyers update not possible ({e}); falling back to GitHub.")
    if res is None or res["ok"] == 0:
        print("Updating from the eod2 GitHub dataset...")
        res = update_from_github()
    print(f"Done via {res['source']}: {res['ok']} files updated, {len(res['failed'])} failed.")
    for s, why in res["failed"][:10]:
        print(f"   ! {s}: {why}")
    nifty = pd.read_csv(INDEX_DIR / "NIFTY50.csv", parse_dates=["date"])["date"].max()
    print(f"NIFTY 50 history now ends {nifty.date()}.")


if __name__ == "__main__":
    main()
