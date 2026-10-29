"""
market_data.py
==============
Data layer for the ATLAS front end (atlas/index.html), served by atlas_api.py.

Sources (all free, no API key needed unless noted):
  * Yahoo Finance via the `yfinance` package  - quotes, intraday + daily
    history, fundamentals, per-stock news, for every market ATLAS shows
    (NSE/BSE, US, Europe, Asia, forex, commodity futures, crypto).
    Yahoo data is DELAYED (NSE ~1 min, some exchanges up to 15-20 min) and
    is for personal use - see Yahoo's terms before showing it publicly.
  * Fyers (your broker login)                    - LIVE quotes for the bot's
    own NSE symbols, overlaid on top of Yahoo while the bot is running.
  * CommodityPriceAPI (optional, COMMODITY_API_KEY) - commodities, else Yahoo
    futures (GC=F, CL=F, ...).
  * Moneycontrol RSS (+ Finnhub if FINNHUB_API_KEY) - headline news.
  * NSE website                                  - FII/DII provisional flows
    (best effort; NSE blocks scripted access from time to time).

Every response says where its numbers came from (`src`) and how fresh they
are (`status`: LIVE / DELAYED / CACHED). If a source fails the API returns
an error and the page keeps its clearly-labelled DEMO values: we never
invent a price.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:  # pragma: no cover - reported by /api/health
    yf = None

BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Universe: ATLAS symbol -> (yahoo ticker, name, class, market, exchange/unit)
# Keep in sync with the `A` list at the top of atlas/index.html.
# ---------------------------------------------------------------------------
UNIVERSE = {
    # India indices
    "NIFTY50": ("^NSEI", "NIFTY 50", "Indices", "IN", "NSE"),
    "SENSEX": ("^BSESN", "SENSEX", "Indices", "IN", "BSE"),
    "BANKNIFTY": ("^NSEBANK", "BANK NIFTY", "Indices", "IN", "NSE"),
    "NIFTYIT": ("^CNXIT", "NIFTY IT", "Indices", "IN", "NSE"),
    "NIFTYMID": ("NIFTY_MIDCAP_100.NS", "NIFTY MIDCAP 100", "Indices", "IN", "NSE"),
    # US indices
    "SPX": ("^GSPC", "S&P 500", "Indices", "US", "US"),
    "NDX": ("^IXIC", "NASDAQ", "Indices", "US", "US"),
    "DJI": ("^DJI", "Dow Jones", "Indices", "US", "US"),
    "RUT": ("^RUT", "Russell 2000", "Indices", "US", "US"),
    # Europe / Asia / rest of world
    "UKX": ("^FTSE", "FTSE 100", "Indices", "UK", "LSE"),
    "DAX": ("^GDAXI", "DAX", "Indices", "EU", "XETRA"),
    "CAC": ("^FCHI", "CAC 40", "Indices", "EU", "Euronext"),
    "N225": ("^N225", "Nikkei 225", "Indices", "JP", "TSE"),
    "HSI": ("^HSI", "Hang Seng", "Indices", "HK", "HKEX"),
    "SSEC": ("000001.SS", "Shanghai Composite", "Indices", "CN", "SSE"),
    "TSX": ("^GSPTSE", "S&P/TSX Composite", "Indices", "CA", "TSX"),
    "IBOV": ("^BVSP", "Bovespa", "Indices", "BR", "B3"),
    "SMI": ("^SSMI", "SMI", "Indices", "CH", "SIX"),
    "SZI": ("399001.SZ", "Shenzhen Component", "Indices", "CN", "SZSE"),
    "KOSPI": ("^KS11", "KOSPI", "Indices", "KR", "KRX"),
    "ASX": ("^AXJO", "S&P/ASX 200", "Indices", "AU", "ASX"),
    "STI": ("^STI", "Straits Times", "Indices", "SG", "SGX"),
    # India stocks (incl. the trading bot's own symbols)
    "RELIANCE": ("RELIANCE.NS", "Reliance Industries", "Stocks", "IN", "NSE"),
    "TCS": ("TCS.NS", "TCS", "Stocks", "IN", "NSE"),
    "HDFCBANK": ("HDFCBANK.NS", "HDFC Bank", "Stocks", "IN", "NSE"),
    "ICICIBANK": ("ICICIBANK.NS", "ICICI Bank", "Stocks", "IN", "NSE"),
    "INFY": ("INFY.NS", "Infosys", "Stocks", "IN", "NSE"),
    "BHARTIARTL": ("BHARTIARTL.NS", "Bharti Airtel", "Stocks", "IN", "NSE"),
    "LT": ("LT.NS", "Larsen & Toubro", "Stocks", "IN", "NSE"),
    "SBIN": ("SBIN.NS", "SBI", "Stocks", "IN", "NSE"),
    "AUBANK": ("AUBANK.NS", "AU Small Finance Bank", "Stocks", "IN", "NSE"),
    "BAJAJFINSV": ("BAJAJFINSV.NS", "Bajaj Finserv", "Stocks", "IN", "NSE"),
    # US stocks
    "AAPL": ("AAPL", "Apple", "Stocks", "US", "NASDAQ"),
    "MSFT": ("MSFT", "Microsoft", "Stocks", "US", "NASDAQ"),
    "NVDA": ("NVDA", "NVIDIA", "Stocks", "US", "NASDAQ"),
    "AMZN": ("AMZN", "Amazon", "Stocks", "US", "NASDAQ"),
    "GOOGL": ("GOOGL", "Alphabet", "Stocks", "US", "NASDAQ"),
    "META": ("META", "Meta", "Stocks", "US", "NASDAQ"),
    "TSLA": ("TSLA", "Tesla", "Stocks", "US", "NASDAQ"),
    "BRKB": ("BRK-B", "Berkshire Hathaway", "Stocks", "US", "NYSE"),
    # Forex
    "USDINR": ("USDINR=X", "USD/INR", "Forex", "X", "FX"),
    "EURUSD": ("EURUSD=X", "EUR/USD", "Forex", "X", "FX"),
    "GBPUSD": ("GBPUSD=X", "GBP/USD", "Forex", "X", "FX"),
    "USDJPY": ("JPY=X", "USD/JPY", "Forex", "X", "FX"),
    "USDCNY": ("CNY=X", "USD/CNY", "Forex", "X", "FX"),
    "USDCHF": ("CHF=X", "USD/CHF", "Forex", "X", "FX"),
    "AUDUSD": ("AUDUSD=X", "AUD/USD", "Forex", "X", "FX"),
    # Commodities (front-month futures on Yahoo)
    "XAU": ("GC=F", "Gold", "Commodities", "X", "oz"),
    "XAG": ("SI=F", "Silver", "Commodities", "X", "oz"),
    "WTI": ("CL=F", "WTI Crude", "Commodities", "X", "bbl"),
    "BRENT": ("BZ=F", "Brent Crude", "Commodities", "X", "bbl"),
    "NG": ("NG=F", "Natural Gas", "Commodities", "X", "MMBtu"),
    "HG": ("HG=F", "Copper", "Commodities", "X", "lb"),
    # Crypto
    "BTC": ("BTC-USD", "Bitcoin", "Crypto", "X", "USD"),
    "ETH": ("ETH-USD", "Ethereum", "Crypto", "X", "USD"),
    "SOL": ("SOL-USD", "Solana", "Crypto", "X", "USD"),
    "BNB": ("BNB-USD", "BNB", "Crypto", "X", "USD"),
    "XRP": ("XRP-USD", "XRP", "Crypto", "X", "USD"),
}

# NIFTY 50 constituents (NSE, October 2026) - used for India breadth,
# top gainers/losers and the NIFTY heatmap.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO",
]

INDIA_SECTORS = [  # (label, yahoo ticker)
    ("Bank", "^NSEBANK"), ("IT", "^CNXIT"), ("Pharma", "^CNXPHARMA"), ("Auto", "^CNXAUTO"),
    ("FMCG", "^CNXFMCG"), ("Metal", "^CNXMETAL"), ("Realty", "^CNXREALTY"), ("Energy", "^CNXENERGY"),
]

# Exchange time zones, for "is this market open" (mirrors EX in the page)
EXCHANGE_TZ = {
    "US": ("America/New_York", 570, 960), "IN": ("Asia/Kolkata", 555, 930),
    "UK": ("Europe/London", 480, 990), "EU": ("Europe/Berlin", 540, 1050),
    "JP": ("Asia/Tokyo", 540, 900), "HK": ("Asia/Hong_Kong", 570, 960),
    "CN": ("Asia/Shanghai", 570, 900), "CA": ("America/Toronto", 570, 960),
    "BR": ("America/Sao_Paulo", 600, 1020), "CH": ("Europe/Zurich", 540, 1050),
    "KR": ("Asia/Seoul", 540, 930), "AU": ("Australia/Sydney", 600, 960),
    "SG": ("Asia/Singapore", 540, 1020),
}


def yahoo_for(sym: str) -> str | None:
    """ATLAS symbol -> Yahoo ticker. NSE stocks outside UNIVERSE (e.g. any
    NIFTY 50 name) map to <SYM>.NS."""
    sym = sym.upper()
    if sym in UNIVERSE:
        return UNIVERSE[sym][0]
    if re.fullmatch(r"[A-Z0-9&\-]{1,20}", sym):
        return f"{sym}.NS"
    return None


def market_open(market: str) -> bool:
    if market not in EXCHANGE_TZ:
        return True  # FX / crypto / commodities: treat as 24h
    from zoneinfo import ZoneInfo
    tz, o, c = EXCHANGE_TZ[market]
    now = dt.datetime.now(ZoneInfo(tz))
    m = now.hour * 60 + now.minute
    return now.weekday() < 5 and o <= m < c


# ---------------------------------------------------------------------------
# Small thread-safe TTL cache with "last good value" fallback
# ---------------------------------------------------------------------------
class TTLCache:
    def __init__(self):
        self._d: dict = {}
        self._lock = threading.Lock()
        self._inflight: dict = {}

    def get(self, key, ttl, fn):
        """Return (value, fresh: bool). Calls fn() when stale; if fn fails and
        an old value exists, returns the old value with fresh=False."""
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1], True
            ev = self._inflight.get(key)
            owner = ev is None
            if owner:
                ev = self._inflight[key] = threading.Event()
        if not owner:  # someone else is fetching; wait for them
            ev.wait(30)
            with self._lock:
                hit = self._d.get(key)
            if hit:
                return hit[1], time.time() - hit[0] < ttl
            raise RuntimeError("upstream fetch failed")
        try:
            val = fn()
            with self._lock:
                self._d[key] = (time.time(), val)
            return val, True
        except Exception:
            if hit:
                return hit[1], False
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            ev.set()


CACHE = TTLCache()
QUOTE_TTL = int(os.environ.get("QUOTE_CACHE_SECONDS", 60))


def _need_yf():
    if yf is None:
        raise RuntimeError("yfinance is not installed (pip install yfinance)")


def _num(x):
    try:
        x = float(x)
        return x if np.isfinite(x) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
def _download(tickers: list[str], **kw) -> pd.DataFrame:
    _need_yf()
    df = yf.download(tickers, group_by="ticker", auto_adjust=False, progress=False,
                     threads=4, timeout=20, **kw)
    if df is None or df.empty:
        raise RuntimeError("Yahoo returned no data")
    return df


def _col(df: pd.DataFrame, ticker: str, field: str) -> pd.Series:
    if isinstance(df.columns, pd.MultiIndex):
        if (ticker, field) in df.columns:
            return df[(ticker, field)].dropna()
        return pd.Series(dtype=float)
    return df[field].dropna() if field in df.columns else pd.Series(dtype=float)


def _fetch_quotes(syms: list[str]) -> dict:
    tick = {s: yahoo_for(s) for s in syms}
    tickers = sorted({t for t in tick.values() if t})
    daily = _download(tickers, period="10d", interval="1d")
    try:
        intra = _download(tickers, period="5d", interval="30m")
    except Exception:
        intra = None
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    out = {}
    for s, t in tick.items():
        c = _col(daily, t, "Close")
        if len(c) < 1:
            continue
        p = float(c.iloc[-1])
        prev = float(c.iloc[-2]) if len(c) > 1 else None
        pts = []
        if intra is not None:
            ic = _col(intra, t, "Close")
            if len(ic):
                p = float(ic.iloc[-1])  # most recent print, better than the daily bar
                pts = [round(float(v), 6) for v in ic.iloc[-40:]]
        if not pts:
            pts = [round(float(v), 6) for v in c.iloc[-10:]]
        ch = p - prev if prev else 0.0
        pc = ch / prev * 100 if prev else 0.0
        vol = _col(daily, t, "Volume")
        meta = UNIVERSE.get(s)
        out[s] = {
            "s": s, "p": p, "ch": ch, "pc": pc, "prev": prev, "pts": pts,
            "v": float(vol.iloc[-1]) if len(vol) else None,
            "hi": _num(_col(daily, t, "High").iloc[-1]) if len(_col(daily, t, "High")) else None,
            "lo": _num(_col(daily, t, "Low").iloc[-1]) if len(_col(daily, t, "Low")) else None,
            "op": _num(_col(daily, t, "Open").iloc[-1]) if len(_col(daily, t, "Open")) else None,
            "open": market_open(meta[3]) if meta else market_open("IN"),
            "src": "Yahoo Finance", "status": "DELAYED", "ts": now,
        }
    if not out:
        raise RuntimeError("no quotes parsed")
    return out


def get_quotes() -> dict:
    """All ATLAS universe quotes. {asOf, items:[...], status}"""
    syms = list(UNIVERSE.keys())
    val, fresh = CACHE.get("quotes", QUOTE_TTL, lambda: _fetch_quotes(syms))
    items = list(val.values())
    if not fresh:
        items = [dict(i, status="CACHED") for i in items]
    return {"items": items, "status": "DELAYED" if fresh else "CACHED",
            "asOf": max(i["ts"] for i in items), "source": "Yahoo Finance"}


def get_nifty50_quotes() -> dict:
    val, _ = CACHE.get("nifty50", 300, lambda: _fetch_quotes(NIFTY50))
    return val


# ---------------------------------------------------------------------------
# History (chart ranges match the page: 1D 5D 1M 6M YTD 1Y 5Y MAX)
# ---------------------------------------------------------------------------
RANGES = {
    "1D": ("1d", "5m", 60), "5D": ("5d", "30m", 300), "1M": ("1mo", "1d", 1800),
    "6M": ("6mo", "1d", 3600), "YTD": ("ytd", "1d", 3600), "1Y": ("1y", "1d", 3600),
    "5Y": ("5y", "1wk", 21600), "MAX": ("max", "1mo", 86400),
}


def get_history(sym: str, rng: str = "1D") -> dict:
    _need_yf()
    t = yahoo_for(sym)
    if not t or rng not in RANGES:
        raise ValueError("bad symbol or range")
    period, interval, ttl = RANGES[rng]

    def fetch():
        h = yf.Ticker(t).history(period=period, interval=interval, auto_adjust=False)
        if h is None or h.empty:
            # e.g. 1D before the session opens: fall back to the last 5 days
            if rng == "1D":
                h = yf.Ticker(t).history(period="5d", interval="5m", auto_adjust=False)
                if not h.empty:
                    last_day = h.index[-1].date()
                    h = h[[d.date() == last_day for d in h.index]]
            if h is None or h.empty:
                raise RuntimeError("no history")
        h = h.dropna(subset=["Close"])
        return {
            "s": sym, "r": rng, "src": "Yahoo Finance",
            "t": [ix.isoformat() for ix in h.index],
            "c": [round(float(x), 6) for x in h["Close"]],
            "o": [round(float(x), 6) for x in h["Open"]],
            "h": [round(float(x), 6) for x in h["High"]],
            "l": [round(float(x), 6) for x in h["Low"]],
            "v": [float(x) for x in h["Volume"]],
        }
    val, fresh = CACHE.get(("hist", sym, rng), ttl, fetch)
    return dict(val, status="DELAYED" if fresh else "CACHED")


def daily_frame(sym: str, period: str = "2y") -> pd.DataFrame:
    """Daily OHLCV as the lowercase frame features.py expects. Uses the
    project's local NSE history (data/history/<SYM>.csv) when it's fresh
    enough, else Yahoo."""
    local = BASE_DIR / "data" / "history" / f"{sym}.csv"

    def fetch():
        _need_yf()
        t = yahoo_for(sym)
        h = yf.Ticker(t).history(period=period, interval="1d", auto_adjust=False)
        if h is None or h.empty:
            raise RuntimeError("no daily history")
        h = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        idx = h.index.tz_localize(None) if getattr(h.index, "tz", None) is not None else h.index
        h.index = pd.to_datetime(idx).normalize()
        h = h.reset_index().rename(columns={"index": "date", "Date": "date"})
        h.columns = [c.lower() for c in h.columns]
        return h

    try:
        val, _ = CACHE.get(("daily", sym, period), 900, fetch)
        return val.copy()
    except Exception:
        if local.exists():
            return pd.read_csv(local, parse_dates=["date"])
        raise


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------
def _fetch_fundamentals(sym: str) -> dict:
    _need_yf()
    t = yf.Ticker(yahoo_for(sym))
    info = {}
    try:
        info = t.info or {}
    except Exception:
        info = {}
    price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    div_rate = _num(info.get("dividendRate") or info.get("trailingAnnualDividendRate"))
    dy = div_rate / price * 100 if (div_rate and price) else None
    out = {
        "s": sym, "src": "Yahoo Finance", "name": info.get("longName") or info.get("shortName"),
        "currency": info.get("currency"), "mc": _num(info.get("marketCap")),
        "pe": _num(info.get("trailingPE")), "fpe": _num(info.get("forwardPE")),
        "eps": _num(info.get("trailingEps")), "dy": dy, "beta": _num(info.get("beta")),
        "sector": info.get("sector"), "industry": info.get("industry"),
        "hi52": _num(info.get("fiftyTwoWeekHigh")), "lo52": _num(info.get("fiftyTwoWeekLow")),
        "pb": _num(info.get("priceToBook")), "roe": _num(info.get("returnOnEquity")),
        "de": _num(info.get("debtToEquity")),
        "insiders": _num(info.get("heldPercentInsiders")),
        "institutions": _num(info.get("heldPercentInstitutions")),
        "summary": (info.get("longBusinessSummary") or "")[:600],
        "quarters": [],
    }
    try:
        q = t.quarterly_income_stmt
        if q is not None and not q.empty:
            cols = list(q.columns)[:4][::-1]  # oldest -> latest
            def row(*names):
                for n in names:
                    if n in q.index:
                        return q.loc[n]
                return None
            rev = row("Total Revenue", "Operating Revenue")
            ni = row("Net Income", "Net Income Common Stockholders")
            oi = row("Operating Income", "EBIT")
            eps = row("Diluted EPS", "Basic EPS")
            for c in cols:
                r = _num(rev[c]) if rev is not None else None
                o = _num(oi[c]) if oi is not None else None
                out["quarters"].append({
                    "l": pd.Timestamp(c).strftime("%b %Y"), "rev": r,
                    "ni": _num(ni[c]) if ni is not None else None,
                    "eps": _num(eps[c]) if eps is not None else None,
                    "opm": (o / r * 100) if (o is not None and r) else None,
                })
    except Exception:
        pass
    return out


def get_fundamentals(sym: str) -> dict:
    val, fresh = CACHE.get(("fund", sym), 6 * 3600, lambda: _fetch_fundamentals(sym))
    return dict(val, status="DELAYED" if fresh else "CACHED")


def get_fundamentals_many(syms: list[str]) -> dict:
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for s, r in zip(syms, ex.map(lambda s: _safe(get_fundamentals, s), syms)):
            if r:
                out[s] = r
    return out


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# India page extras
# ---------------------------------------------------------------------------
def _fetch_fii_dii() -> list:
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
    })
    s.get("https://www.nseindia.com/", timeout=10)
    r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
    r.raise_for_status()
    rows = r.json()
    out = []
    for x in rows:
        out.append({"cat": x.get("category"), "date": x.get("date"),
                    "net": _num(str(x.get("netValue", "")).replace(",", ""))})
    if not out:
        raise RuntimeError("empty FII/DII response")
    return out


def get_india() -> dict:
    """Everything the India page needs, with per-block source labels."""
    res = {"asOf": dt.datetime.now(dt.timezone.utc).isoformat()}
    # sectors
    try:
        tick = [t for _, t in INDIA_SECTORS] + ["^INDIAVIX"]
        def fetch():
            d = _download(tick, period="10d", interval="1d")
            o = {}
            for lab, t in INDIA_SECTORS + [("VIX", "^INDIAVIX")]:
                c = _col(d, t, "Close")
                if len(c) >= 2:
                    o[lab] = {"p": float(c.iloc[-1]), "pc": float((c.iloc[-1] / c.iloc[-2] - 1) * 100)}
            return o
        sec, fresh = CACHE.get("india_sec", 300, fetch)
        res["sectors"] = [{"n": k, "pc": v["pc"]} for k, v in sec.items() if k != "VIX"]
        if "VIX" in sec:
            res["vix"] = sec["VIX"]
        res["sectorsSrc"] = "Yahoo Finance · " + ("DELAYED" if fresh else "CACHED")
    except Exception as e:
        res["sectorsError"] = str(e)
    # breadth + movers from NIFTY 50 constituents
    try:
        q = get_nifty50_quotes()
        vals = [v for v in q.values() if v.get("prev")]
        adv = sum(1 for v in vals if v["pc"] > 0.05)
        dec = sum(1 for v in vals if v["pc"] < -0.05)
        res["breadth"] = {"adv": adv, "dec": dec, "unc": len(vals) - adv - dec, "universe": "NIFTY 50"}
        srt = sorted(vals, key=lambda v: v["pc"], reverse=True)
        slim = lambda v: {"s": v["s"], "p": v["p"], "pc": v["pc"], "v": v.get("v")}
        res["gainers"] = [slim(v) for v in srt[:5]]
        res["losers"] = [slim(v) for v in srt[::-1][:5]]
        res["active"] = [slim(v) for v in sorted(vals, key=lambda v: (v.get("v") or 0) * v["p"], reverse=True)[:5]]
        res["nifty50"] = [slim(v) for v in vals]
    except Exception as e:
        res["breadthError"] = str(e)
    # FII / DII
    try:
        fl, fresh = CACHE.get("fiidii", 3600, _fetch_fii_dii)
        res["flows"] = fl
        res["flowsSrc"] = "NSE provisional · " + ("latest" if fresh else "CACHED")
    except Exception as e:
        res["flowsError"] = "NSE did not return FII/DII data (" + str(e)[:80] + ")"
    return res


# ---------------------------------------------------------------------------
# Commodities: CommodityPriceAPI if a key is set, else Yahoo futures
# ---------------------------------------------------------------------------
COMMODITY_MAP = {"XAU": "XAU", "XAG": "XAG", "WTI": "WTIOIL-FUT", "BRENT": "BRENTOIL-FUT",
                 "NG": "NG-FUT", "HG": "HG-FUT"}


def _fetch_commodityapi() -> dict:
    import requests
    key = os.environ["COMMODITY_API_KEY"]
    base = os.environ.get("COMMODITY_API_BASE", "https://api.commoditypriceapi.com/v2")
    hdr = {"x-api-key": key}
    syms = ",".join(COMMODITY_MAP.values())
    j = requests.get(f"{base}/rates/latest?symbols={syms}", headers=hdr, timeout=8).json()
    if j.get("success") is False:
        raise RuntimeError(j.get("error", {}).get("message", "provider error"))
    rates = j.get("rates", {})
    prev = {}
    for d in range(1, 6):
        day = (dt.date.today() - dt.timedelta(days=d)).isoformat()
        try:
            h = requests.get(f"{base}/rates/historical?symbols={syms}&date={day}", headers=hdr, timeout=8).json()
            if h.get("rates"):
                prev = h["rates"]; break
        except Exception:
            continue
    num = lambda v: v if isinstance(v, (int, float)) else (v or {}).get("close") or (v or {}).get("rate")
    ts = dt.datetime.fromtimestamp(j.get("timestamp", time.time()), dt.timezone.utc).isoformat()
    items = []
    for s, ps in COMMODITY_MAP.items():
        p = num(rates.get(ps))
        if not p:
            continue
        pv = num(prev.get(ps))
        ch = p - pv if pv else 0
        items.append({"s": s, "p": p, "ch": ch, "pc": ch / pv * 100 if pv else 0, "pts": [p], "ts": ts})
    return {"items": items, "source": "CommodityPriceAPI"}


def get_commodities() -> dict:
    status = os.environ.get("COMMODITY_DATA_STATUS", "DELAYED")
    if os.environ.get("COMMODITY_API_KEY"):
        try:
            v, fresh = CACHE.get("cmdty", int(os.environ.get("CACHE_SECONDS", 120)), _fetch_commodityapi)
            return dict(v, status=status if fresh else "CACHED", asOf=v["items"][0]["ts"] if v["items"] else None)
        except Exception:
            pass  # fall through to Yahoo
    q = get_quotes()
    items = [i for i in q["items"] if UNIVERSE.get(i["s"], ("",) * 3)[2] == "Commodities"]
    return {"items": items, "source": "Yahoo Finance (front-month futures)", "status": q["status"], "asOf": q["asOf"]}


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------
NEWS_TTL = int(os.environ.get("NEWS_CACHE_SECONDS", 90))


def _clean(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", s or "")
    s = re.sub(r"<[^>]*>", "", s)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"), ("&amp;", "&")):
        s = s.replace(a, b)
    return s.strip()


def _rss(url: str, name: str, cat: str) -> list:
    import requests
    r = requests.get(url, headers={"User-Agent": "ATLAS-Markets/0.3 (RSS reader)"}, timeout=8)
    r.raise_for_status()
    root = ET.fromstring(r.content[:2_000_000])
    out = []
    for it in root.iter("item"):
        h = _clean(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        pd_ = it.findtext("pubDate")
        try:
            ts = pd.Timestamp(pd_).tz_convert("UTC").isoformat() if pd_ else None
        except Exception:
            try:
                ts = pd.Timestamp(pd_).isoformat()
            except Exception:
                ts = None
        if h and link.startswith("http"):
            out.append({"h": h, "url": link, "ts": ts, "src": name, "cat": cat})
    return out[:20]


def _finnhub(cat_q: str, cat: str) -> list:
    import requests
    key = os.environ["FINNHUB_API_KEY"]
    r = requests.get(f"https://finnhub.io/api/v1/news?category={cat_q}", headers={"X-Finnhub-Token": key}, timeout=8)
    r.raise_for_status()
    out = []
    for x in r.json():
        h, u = _clean(str(x.get("headline", ""))), str(x.get("url", ""))
        if h and u.startswith("http"):
            ts = dt.datetime.fromtimestamp(x["datetime"], dt.timezone.utc).isoformat() if x.get("datetime") else None
            out.append({"h": h, "url": u, "ts": ts, "src": str(x.get("source") or "Finnhub"), "cat": cat})
    return out[:15]


def news_sources():
    src = [("rss", os.environ.get("NEWS_SOURCE_NAME", "Moneycontrol"), "India",
            lambda: _rss(os.environ.get("NEWS_FEED_URL", "https://www.moneycontrol.com/rss/latestnews.xml"),
                         os.environ.get("NEWS_SOURCE_NAME", "Moneycontrol"), "India")),
           ("et", "Economic Times", "Markets",
            lambda: _rss("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "Economic Times", "Markets"))]
    if os.environ.get("FINNHUB_API_KEY"):
        for q, cat in (("general", "World"), ("crypto", "Crypto"), ("forex", "Forex")):
            src.append(("fh-" + q, "Finnhub", cat, (lambda q=q, cat=cat: _finnhub(q, cat))))
    return src


def get_news() -> dict:
    items, sources = [], []
    for sid, name, cat, fn in news_sources():
        try:
            v, fresh = CACHE.get(("news", sid), NEWS_TTL, fn)
            items += v
            sources.append({"name": name, "cat": cat, "ok": True, "count": len(v)})
        except Exception:
            sources.append({"name": name, "cat": cat, "ok": False, "count": 0})
    seen, out = set(), []
    for i in sorted(items, key=lambda i: i.get("ts") or "", reverse=True):
        k = re.sub(r"[^a-z0-9]", "", i["h"].lower())[:60]
        if k not in seen:
            seen.add(k); out.append(i)
    if not out:
        raise RuntimeError("no news from any source")
    return {"asOf": dt.datetime.now(dt.timezone.utc).isoformat(), "items": out[:40], "sources": sources}


def get_symbol_news(sym: str) -> dict:
    _need_yf()

    def fetch():
        raw = yf.Ticker(yahoo_for(sym)).news or []
        out = []
        for x in raw[:12]:
            c = x.get("content", x)  # yfinance >=0.2.50 nests under "content"
            title = c.get("title")
            url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link")
            prov = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher")
            ts = c.get("pubDate")
            if not ts and x.get("providerPublishTime"):
                ts = dt.datetime.fromtimestamp(x["providerPublishTime"], dt.timezone.utc).isoformat()
            if title and url and str(url).startswith("http"):
                out.append({"h": _clean(title), "url": url, "src": prov or "Yahoo Finance", "ts": ts})
        return out
    v, fresh = CACHE.get(("snews", sym), 600, fetch)
    return {"s": sym, "items": v, "status": "DELAYED" if fresh else "CACHED"}
