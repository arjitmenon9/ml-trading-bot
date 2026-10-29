# ATLAS — ML swing-trading bot for NSE stocks

A machine-learning trading bot for Indian equities (NSE), connected to a
[Fyers](https://fyers.in) broker account. Each morning it scores about 190 NSE
stocks with a trained gradient-boosting ensemble, buys only the
highest-confidence setups, and manages every position with a take-profit, a
stop-loss and a time stop. The **ATLAS** web dashboard shows live positions,
signals, historical replays and market context.

> **Paper trading by default.** `LIVE_TRADING = False` in
> `src/trading/fyers_execution.py`. Nothing here is investment advice, and past
> results don't guarantee future ones.

---

## Project structure

```
ml-trading-bot/
├── src/
│   ├── paths.py              project paths; puts the src/ folders on sys.path
│   ├── trading/              live bot
│   │   ├── bot.py            main loop + web server (run this)
│   │   ├── strategy.py       entry/exit rules, costs
│   │   └── fyers_execution.py  order placement, risk limits, LIVE_TRADING switch
│   ├── broker/               Fyers login + credentials
│   │   ├── config.py
│   │   ├── auth.py           step 1 of the daily login
│   │   └── get_token.py      step 2 of the daily login
│   ├── ml/                   model
│   │   ├── features.py       48 scale-free features
│   │   ├── ensemble.py       bagged HistGradientBoosting
│   │   ├── train_model.py    walk-forward training
│   │   ├── backtest.py       out-of-sample report
│   │   ├── ml_signals.py     live scoring
│   │   └── predict_trend.py  today's candidates from the command line
│   ├── data_pipeline/        market data
│   │   ├── universe.py       stock universe + loaders
│   │   ├── update_data.py    daily top-up (Fyers, falls back to GitHub)
│   │   └── fetch_data*.py, generate_demo_data.py   (v1 helpers)
│   └── web/                  ATLAS backend
│       ├── atlas_api.py      Flask API
│       ├── atlas_server.py   website without the bot
│       ├── market_data.py    quotes, news, FII/DII
│       ├── replay.py         replay of out-of-sample trades
│       └── sim_market.py     live simulation mode
├── frontend/
│   ├── atlas/                ATLAS single-page site (index.html, live.js)
│   ├── classic/              original v1 dashboard
│   └── OPEN_LIVE_SIM.html
├── scripts/                  one-click macOS launchers (.command)
├── data/                     daily OHLCV: history/ (191 stocks), index/ (NIFTY, VIX…)
├── models/                   trained model, metadata, out-of-sample trades
├── reports/                  backtest and walk-forward charts
├── docs/                     walkthrough + project reports
├── archive/v1_2026-10-23/    the original v1 bot, kept for reference
├── requirements.txt
└── fyers_secrets.example.json
```

Local-only files stay in the project root and are git-ignored:
`fyers_secrets.json`, `paper_trades.json`, `.env`, `venv/` and `logs/`.

---

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp fyers_secrets.example.json fyers_secrets.json   # add your app_id + secret_key
```

On macOS you can instead double-click `scripts/setup_and_login.command`.

## Daily use

| What | macOS one-click | Command line |
|---|---|---|
| Login + start bot and website | `scripts/start_today.command` | `python src/broker/auth.py` → `python src/broker/get_token.py` → `python src/trading/bot.py` |
| Restart bot (token still valid) | `scripts/restart_clean.command` | `python src/trading/bot.py` |
| Website only (no login) | `scripts/open_atlas.command` | `python src/web/atlas_server.py` |
| Today's candidates | — | `python src/ml/predict_trend.py [SYMBOL]` |
| Monthly retrain (20–40 min) | `scripts/relogin_and_retrain.command` | `python src/data_pipeline/update_data.py` → `python src/ml/train_model.py` |
| Backtest report | `scripts/run_backtest.command` | `python src/ml/backtest.py` |

The website runs at <http://127.0.0.1:5000>. The bot view is `/#/bot` and the
v1 dashboard is `/classic`.

---

## How it works

**Strategy.** Long-only swing trades. Each stock is scored on yesterday's
completed daily bar. Stocks above the threshold are bought at the next open.
A trade exits at the first of: take-profit 0.75×ATR (0.8–6%), stop-loss
4×ATR (2–15%), or 15 trading days. At most 3 new trades a day and 10 open
positions.

**Market gate.** Before any stock is scored, the market has to pass four
checks: the SMA200 slope is positive, price is within 10% of its SMA200,
India VIX is at or below 30, and ATR is under 4%.

**Model.** 3× bagged HistGradientBoosting on 48 scale-free features: moving
averages, mean reversion, NIFTY/VIX regime, cross-sectional ranks and market
breadth. It was trained on real daily data for 191 NSE stocks from 2007 to
Oct 2026. The buy threshold is the top 0.3% of the last 250 trading days'
scores.

**Walk-forward results** (after Fyers charges, ₹50,000 per trade):

| Period | Trades | Win rate | Profit factor |
|---|---|---|---|
| Calibration 2018–2021 | 46 | 93.5% | 2.27 |
| Holdout 2022–Oct 2026 | 87 | 87.4% | 2.04 |
| 2026 year to date | 17 | 71% | — |

Limitations, stated plainly:
- The strategy trades rarely, about 16 times a year across the whole universe.
- Total return is modest: about 1.3% a year on ₹5 lakh of reserved capital,
  against about 10% a year for NIFTY buy-and-hold.
- At small position sizes (₹5,000) charges wipe out the edge.
- The strategy design was refined on the same 2022–2026 history, so the
  holdout isn't perfectly untouched.

Full details are in [`docs/ML_WALKTHROUGH.md`](docs/ML_WALKTHROUGH.md).

---

## Data sources

- **Training history:** the public [eod2](https://github.com/BennyThadikaran/eod2_data) NSE dataset, cross-checked against Fyers.
- **Daily top-ups and live quotes:** Fyers API v3.
- **Website market data:** Yahoo Finance, NSE FII/DII, and Moneycontrol / Economic Times RSS feeds.
