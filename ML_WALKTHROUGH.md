# Smart Signal v2: ML walkthrough (October 2026)

The v1 walkthrough is kept as `ML_WALKTHROUGH_v1.md`. This document covers what changed in v2, why, and how to run it.

---

## 0. Why v1 bought 2 stocks, never sold, and showed ₹0

| What you saw | Real cause | Fix in v2 |
|---|---|---|
| Live: 2 buys on 22 Oct, never sold | The v1 model only ever said BUY or HOLD for HDFCBANK and AUBANK, and the bot had **no exit rule** apart from a model SELL. Positions could stay open forever. | Every position now has a take-profit, a stop-loss and a 15-day time stop. Every trade closes. |
| P&L stuck at ₹0 | The dashboard only counted *closed* trades. The 2 open positions were actually about −₹25. | Today's P&L = realized + unrealized. |
| Live: 0 buys on 23 Oct | The bot ran all day on 22 Oct's Fyers token. Every quote came back "Please provide valid token". | The bot detects this, shows **FYERS TOKEN EXPIRED**, and reloads the token by itself when `get_token.py` saves a new one (no restart needed). |
| SIM: −₹3.44 at 69% win rate | SIM never used the model. It was a random-walk price generator running the old rule-based score. | SIM now replays the model's real walk-forward trades on real NSE history. |
| Signal flipping all day (BUY 981×, SELL 1053×, HOLD 1427× in one log) | v1 re-ran a daily-bar model every 10 s on a half-formed "today" bar. | Signals are computed **once a day**, from yesterday's completed bar, exactly as in training. |

## 1. The new question the model answers

v1 tried to predict BUY / HOLD / SELL for the next 5 days (37% test accuracy, about coin-flip for 3 classes). v2 asks something narrower that the data can actually answer:

> "If I buy this stock at **tomorrow's open** and exit at a **take-profit of 0.75 × ATR**, a **stop-loss of 4 × ATR**, or after **15 trading days**, will the trade make money **after Fyers charges**?"

(ATR = the stock's average daily range. A typical take-profit is +1.5–2.5% and a typical stop −8–12%. Levels are clamped to 0.8–6% and 2–15%.)

The model outputs P(win). The bot only buys the **rare setups where it is most confident**, and only if the stock passes a trend/volatility gate:
- 200-day average rising, and price no more than 10% below it (no "falling knives");
- ATR below 4% (not a wildly volatile stock);
- India VIX below 30 (no panic markets).

Portfolio rules: at most **3 new trades a day** and **10 open positions**, one position per stock.

## 2. Data: 20 years × 191 stocks instead of 5 years × 4

- History from the public **eod2** dataset (NSE bhavcopy, split/bonus adjusted; github.com/BennyThadikaran/eod2_data). It matches your Fyers history **to the paisa** on every overlapping day.
- 191 liquid NSE stocks (NIFTY 50 + NIFTY Next 50 + large midcaps), 2007 → 13 Oct 2026, **740,519 stock-days**. Pre-2007 data is skipped (unadjusted splits), and demergers / data breaks are cut out.
- NIFTY 50 and India VIX history for market-regime features.
- `update_data.py` tops this up from Fyers every morning (the bot does it automatically after 9:15).

## 3. Features (`features.py`): 48 in total, all scale-free

- **Atlas Technicals tab indicators (new):** SMA 20/50/200, EMA 20 (as % distance from price), SMA50-vs-SMA200 gap, SMA200 slope.
- **Kept from v1:** EMA 9/21 gap, RSI 14, MACD (now as % of price), Bollinger %B and width, rolling VWAP gap, Stochastic, ATR %, ADX (+ DI difference), CCI, Williams %R, OBV change, 52-week high/low distance, momentum 5/10/20, volatility, volume ratio.
- **Short-term mean reversion (new):** RSI-2, RSI-5, 1/3/60-day returns, IBS (where the close sits in the day's range), opening gap, down-streak, distance from the 5-day low/high, 20-day z-score, volatility ratio.
- **Market regime (new):** NIFTY 5/20-day momentum, distance from its 200-day average, NIFTY RSI-14/RSI-2, India VIX level and 5-day change, stock-vs-NIFTY relative strength (20/60 days).
- **Cross-sectional (new):** each stock's rank among all 191 that day (5/20-day return, RSI-2, distance to SMA200, z-score, volume ratio, ATR) plus market breadth (% of stocks above SMA50, % down over 5 days).

Every feature is a %, ratio or 0–100 oscillator, so one model can learn from all stocks at once.

## 4. Model and validation (`train_model.py`)

- **Model:** 3 averaged HistGradientBoosting classifiers (`ensemble.py`). Averaging makes the high-confidence tail, the only part that trades, less noisy.
- **Walk-forward:** each year 2018–2026 is predicted by a model trained only on data that ended 30 days before that year began. No future data is ever used.
- **Threshold:** model scores drift with the market regime, so a fixed probability cutoff does not transfer between years (tested: a cutoff chosen on 2018–21 gave just 3 trades in 2022–26). The bot instead buys the **top 0.3% of the last 250 trading days' scores**. That is a rolling threshold using only past scores; today it works out to P(win) ≥ 0.920.
- **Choosing that 0.3%:** only 2018–2021 results were used. The rule was the most trades that reach a 92% win rate and a profit factor of at least 1.5 on 2018–21. A 92% calibration target was used because walk-forward results dropped about 5 points from calibration years to unseen years.
- **Honest disclosure:** I tried several designs on the same history before settling on this one (exit rules, gate variants, bagging, gated vs ungated training). That design search touched 2022–2026, so treat the holdout as "very good evidence", not a perfectly untouched test.

## 5. Results (all out-of-sample, after Fyers charges at ₹50,000 per trade)

| | Trades | Win rate | Avg / trade | Profit factor |
|---|---:|---:|---:|---:|
| 2018–2021 (threshold chosen here) | 46 | 93.5% | +1.04% | 2.27 |
| **2022 – Oct 2026 (holdout)** | **87** | **87.4%** | **+0.76%** | **2.04** |
| All 2018–2026 | 133 | 89.5% | +0.86% | 2.12 |

By year: 2018 100% (13), 2019 100% (9), 2020 50% (2), 2021 91% (22), 2022 86% (7), 2023 100% (12), 2024 90% (39), 2025 92% (12), **2026 71% (17)**.

Exits: 119 take-profit, 9 time stop, 5 stop-loss. Winners average about +1.7% to +2.0%. Losers averaged −5.8% in 2022–26 and −12.6% in 2018–21, and the worst single trade was −15.4%. That asymmetry is the price of a high win rate: a single stop-out wipes out several wins.

**What the high win rate does *not* mean:**
1. **Not every year clears 85%.** 2026 so far is 71%.
2. **It trades rarely:** about 16 trades a year across 191 stocks, about 1 a year on your 4 focus stocks. Many weeks will have no trade.
3. **Modest total profit:** ₹57,011 over 8.2 years with up to ₹5 lakh reserved (10 × ₹50,000), about 1.3% a year on reserved capital. NIFTY buy-and-hold returned about 10% a year over the same window.
4. **Position size matters a lot:**

| ₹ per trade | Charges (round trip) | Win rate | Profit factor |
|---:|---:|---:|---:|
| 5,000 (current LIVE setting) | 1.33% | 85.0% | **0.97 (loses money)** |
| 10,000 | 0.94% | 88.7% | 1.44 |
| 25,000 | 0.57% | 89.5% | 1.95 |
| 50,000 (paper mode) | 0.45% | 89.5% | 2.12 |

Fyers' flat ₹20/order brokerage plus the ₹14.75 DP charge per sell dominate small trades. **Do not go live at ₹5,000 per trade.** `LIVE_TRADING` stays `False`; turning it on, and choosing `MAX_POSITION_VALUE`, is your decision.

Charts: `reports/walkforward_equity.png`, `reports/walkforward_by_year.png`, `reports/backtest_v2.png`.

## 6. How the bot runs each day (`bot.py`)

1. You double-click **`start_today.command`**: Fyers login, then the bot and website start, and the browser opens `http://127.0.0.1:5000/#/bot`.
2. **First scan after 9:15** (with a valid token): Fyers history top-up for 191 stocks and the indices (about 30–60 s). Then it scores every stock from yesterday's close, applies the gate, and buys up to 3 setups above today's threshold. Entries are allowed until 11:00.
3. **Every ~10 s:** live quotes for the focus stocks and open positions. It sells at the take-profit or the stop.
4. **15:20+:** sells positions that reached the 15-day time stop.
5. Positions and trades are saved in `paper_trades.json`, so restarts no longer lose positions.

Orders now use **CNC** (delivery). INTRADAY orders would have been force-closed by Fyers at about 3:15 PM, which can't work for multi-day holds.

## 7. The ATLAS website (`atlas/`, `atlas_api.py`, `market_data.py`)

`bot.py` serves the Atlas front end at http://127.0.0.1:5000 (the old dashboard is at `/classic`). `open_atlas.command` runs the website alone, with no bot and no login.

| Section | Source (previously DEMO) |
|---|---|
| Indices, stocks, forex, commodities, crypto, globe, heatmap | Yahoo Finance (delayed), plus **Fyers live** for the bot's symbols |
| Asset page chart (1D…MAX), stats, 52-week range | Yahoo history |
| Technicals tab | `features.py`, the same code the model uses |
| **ATLAS ML model panel** on every NSE stock | the trained model: P(win), gate status, and the trade plan if it qualifies |
| Overview / Financials / Shareholding / Peers / screener | Yahoo fundamentals |
| India page | NIFTY sector indices, India VIX, NIFTY 50 breadth and movers, NSE FII/DII provisional flows |
| News | Moneycontrol + Economic Times RSS, Yahoo per-stock news, Finnhub if `FINNHUB_API_KEY` is set |
| Commodities | CommodityPriceAPI if `COMMODITY_API_KEY` is set, else Yahoo futures |
| **Bot** (new nav item + "Today's Performance") | live bot state; **SIM** = model replay |

Not shown, because no free reliable source exists: GIFT Nifty, and the quarterly promoter/FII/DII shareholding split. Yahoo data is delayed and for personal use; read its terms before hosting the site publicly. If a source fails, that section falls back to its clearly labelled DEMO values.

## 8. Commands

| Double-click | Does |
|---|---|
| `start_today.command` | every trading morning: login → bot + website |
| `restart_clean.command` | restart the bot without logging in again |
| `open_atlas.command` | website only |
| `relogin_and_retrain.command` | monthly: login → `update_data.py` → full walk-forward retrain (20–40 min) → backtest |
| `run_backtest.command` | prints the report above |

Command line: `python predict_trend.py [SYMBOL]`, `python update_data.py [--github]`, `python train_model.py [--final-only]`.

## 9. Limitations (for your report)

- **Survivorship bias:** the universe is today's large caps. Companies that collapsed and left the index aren't in the history, which flatters dip-buying a little.
- **Fills:** the backtest assumes entry at the open and exit exactly at the target/stop. Gaps through the stop are modelled; intraday slippage beyond 0.05% per side is not.
- **Regime risk:** the strategy buys pullbacks in uptrends. A sharp market-wide crash (2020, early 2026) produces the losses.
- **Small sample:** 133 out-of-sample trades. The 95% confidence interval on an 89.5% win rate is roughly 84–95%.
- Nothing here is investment advice.
