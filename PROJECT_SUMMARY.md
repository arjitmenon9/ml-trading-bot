# ATLAS — ML-Powered NSE Trading Bot (Fyers)

### Project summary as of 25 October 2026

---

## 1. What this project is

A fully automated, machine-learning-driven stock trading system for the Indian market (NSE), built on top of your existing Fyers broker account. It scans a universe of NSE stocks, scores them with a trained ML model, and decides when to buy and sell — currently in **paper trading mode only** (simulated money, real live market data, no real orders placed). It has its own web dashboard, called **ATLAS**, that shows live signals, open positions, backtested performance, and market context (NIFTY, VIX, news, FII/DII flows).

Nothing here executes real trades yet — `LIVE_TRADING` is deliberately set to `False`. That switch is yours to flip whenever you decide you've seen enough paper-trading evidence to trust it with real money.

---

## 2. The original ask, and the honest framing it got

You originally asked for a bot with 0% synthetic data, the highest accuracy of any model on the internet, 100% profit rate, auto buy/sell/hold, linked to your real trading account, running in real time.

Some of that isn't something any honest system can promise — no model has a 100% win rate or guaranteed profit, and "highest accuracy of any model on the internet" isn't a verifiable or meaningful claim. Rather than pretend otherwise, this project was built to the most honest, rigorous version of what you actually want: a real system, trained on 100% real historical NSE data (no synthetic/fake data anywhere), tested properly against data it never saw during training, paper-traded on live real-time market data first, with its real measured win rate and profit factor reported to you plainly — good or bad.

---

## 3. Version 1 — the first working bot

**Stack:** Python/Flask backend (`bot.py`) + Fyers API v3 (`fyers_apiv3`) for real quotes and order placement + an HTML dashboard (`dashboard.html`) polling a `/state` JSON endpoint.

**Decision engine:** a hybrid of a rule-based technical score (EMA9/21 crossover, RSI, MACD, Bollinger Bands, VWAP) and a simple ML signal, with the ML signal taking priority once it had enough data.

**What got fixed in v1 during live testing:**
- **Loop death after market close.** The scanning loop literally `break`'d out of the bot's main loop once past 3:30pm instead of sleeping and continuing — so the bot silently died every evening and never resumed on its own.
- **Cross-thread Fyers client hang.** The background scanning thread was reusing the same Fyers API client object created on the main thread, which caused indefinite hangs with zero error output. Fixed with a thread-local client instance per thread.
- **Misleading "LIVE" dashboard.** `dashboard.html` had its own client-side fallback that generates a fake random-walk price series and displays it looking exactly like real data — meaning the dashboard could say "LIVE" and show plausible numbers even if the real backend was dead. This was caught and flagged to you directly rather than letting it stand.
- **₹0 P&L confusion.** P&L only updates when a position is actually *sold* (realized), not while it's held open — so ₹0 while holding two open positions was correct behavior, not a bug, though it did reveal a deeper design gap: v1 had **no stop-loss or take-profit**, only a sell when the model itself flipped its signal, which could in theory hold a losing position indefinitely.

v1 proved the plumbing worked — real Fyers login, real quotes, real paper orders, a live dashboard — but its actual trading logic was unsophisticated and that stop-loss/exit gap needed solving properly, which led to v2.

---

## 4. Version 2 — the real model (current version, "ATLAS")

This is a substantially more serious rebuild, done on 23 October 2026. v1's files are preserved in `backup_v1_2026-10-23/` and nothing was thrown away.

### Strategy (`strategy.py`)
Long-only swing trading. The model scores each stock using *yesterday's completed daily bar* (no lookahead), buys at the next day's open, and exits on whichever comes first:
- **Take-profit:** 0.75× ATR (clamped to 0.8%–6%)
- **Stop-loss:** 4× ATR (clamped to 2%–15%)
- **Time stop:** 15 trading days

Risk limits: max 3 new trades/day, max 10 open positions at once.

### Entry gate
Before the model is even consulted, a stock has to pass a market-regime filter: NIFTY SMA200 trending up, price within 10% of its own SMA200, India VIX ≤ 30, and ATR under 4%. This exists to keep the model out of chaotic or badly-trending conditions it wasn't trained to handle well.

### The model (`models/model.pkl`, `ensemble.py`, `features.py`)
A bagged ensemble of 3 HistGradientBoosting classifiers, trained on **48 scale-free features** per stock per day — moving averages, mean-reversion indicators, NIFTY/VIX market-regime features, and cross-sectional rank/breadth features (how a stock is doing *relative to the rest of the market*, not just in isolation).

### Training data
**191 NSE stocks, daily bars from 2007 through 13 October 2026** — pulled from the public `eod2` historical dataset on GitHub, cross-checked to match Fyers' own numbers. This is 100% real exchange data, no synthetic data anywhere in the pipeline, as originally required.

### Entry threshold
Not fixed — it's **rolling**: the model only buys the top 0.3% of scores seen across the trailing 250 trading days, recalculated and logged daily (`data/score_history.csv`). As of 13 Oct 2026 that threshold sat around 0.920.

### Walk-forward backtest results (real transaction costs included, ~₹50k/trade)
| Period | Trades | Win rate | Profit factor |
|---|---|---|---|
| Calibration (2018–2021) | 46 | 93.5% | 2.27 |
| **Holdout (2022–Oct 2026)** | **87** | **87.4%** | **2.04** |
| All periods combined | 133 | 89.5% | — |
| 2026 year-to-date alone | 17 | 71% | weaker |

**The honest caveat, told to you directly at the time:** the exit rules, the entry gate, the ensemble design, and the 0.3% threshold target were all *tuned* while looking at 2022–2026 data. That means the "holdout" period isn't a perfectly untouched test set — there's some risk the strong numbers above are partly a product of fitting the strategy's knobs to that same stretch of history, not purely out-of-sample skill. This is a real and known limitation, not something smoothed over.

**The profitability reality check, also told to you directly:** this strategy trades rarely — about 16 trades a year across the whole 191-stock universe, and only 5 trades in 8 years across just your 4 original focus stocks (HDFCBANK, AUBANK, BAJAJFINSV, RELIANCE). At the position size the backtest reserves (₹5 lakh), net annual profit after charges is roughly **1.3%/year** — far below NIFTY's own ~10%/year. At the much smaller ₹5,000-per-trade size currently configured for live trading, transaction charges alone (1.33%) exceed the edge, and the strategy **loses money** at that size — profit factor drops to 0.97. Paper trading currently uses a more realistic ₹50,000 per position instead.

### The ATLAS website (`atlas/`, `atlas_api.py`, `atlas_server.py`, `market_data.py`)
A full dashboard rebuild, served alongside the bot at `http://127.0.0.1:5000` (the old v1 dashboard is still there too, at `/classic`, for comparison). New sections include a home page "Today's Performance" tile and a dedicated `#/bot` page showing live paper-trading state next to a SIM replay of the historical out-of-sample trades (`models/oos_trades.csv`) — so you can see "what it's doing live" right next to "what it would have done historically" on the same screen. Every individual NSE stock page also got its own ML model panel. Data sources: Yahoo Finance (yfinance) for quotes/history/fundamentals/news, a live Fyers price overlay for your bot's tracked symbols, NSE FII/DII flow data (best-effort), Moneycontrol/Economic Times news feeds, with optional commodity/Finnhub API keys if you want to add them later.

**Not yet verified:** the ATLAS site's Yahoo Finance integration was only tested against a faked/mocked data source in the sandbox (the sandbox has no real internet access to Yahoo) — the first real-world check of it happens whenever you next run it on your Mac with live internet.

---

## 5. Infrastructure, scripts, and ops

**One-click Mac scripts (`.command` files), what each does:**
- `start_today.command` — the daily driver: fresh Fyers login, pulls today's data, restarts the bot.
- `restart_clean.command` — quick restart without re-login (use only if today's token is still valid).
- `open_atlas.command` — opens the new ATLAS site.
- `open_dashboard.command` — opens the old v1 dashboard directly (bypasses Flask's lack of a `/` route).
- `relogin_and_retrain.command` — the monthly full model retrain (20–40 minutes).
- `run_backtest.command` — reruns the walk-forward backtest.
- `push_to_github.command` — superseded; GitHub CLI's device-flow login architecturally can't run through this session's tooling, so GitHub push was switched to SSH instead (see below).

**GitHub:** a private repo (**`ml-trading-bot`**) was planned, a local git repo was initialized and secret-scanned clean (no leaked API keys/tokens in any commit history), and an SSH key was generated for pushing:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJmVfm1KsbmlbXZ0IydPW/CVYp1yjgy6CN7Bn+kT54Xo ml-trading-bot-push
```
**Status: still pending.** Adding an SSH key to a GitHub account is a standing credential change, which this assistant is deliberately blocked from doing on your behalf — you'd need to add that key yourself at `github.com/settings/ssh/new`. Separately, all of v2's new files (the ensemble model, ATLAS site, strategy code, etc.) are currently **uncommitted** in the local repo — only the original v1 commit exists so far.

**Known environment quirks** (documented so future work doesn't re-discover them the hard way): the sandboxed shell this assistant uses to control your Mac cannot reach Yahoo Finance, NSE, or Fyers directly (only real tests on your actual Mac count); it cannot delete files without your explicit one-time permission; background-app computer-control calls are unreliable compared to full-screen ones; and Terminal.app blocks simulated typing (works fine for clicks only).

---

## 6. Where things stand right now (25 Oct 2026, a Sunday — market closed)

- v2 ("ATLAS") is fully built and installed on your Mac, but **you haven't yet run it live** — v1 may still have been the last thing actually running.
- No bot process is currently running (expected — it's the weekend).
- The next real trading day to test v2 end-to-end is **Monday 27 October** (today, when you read this, Oct 25 is Sunday, so Monday the 26th is next).
- `LIVE_TRADING` remains `False` everywhere — paper trading only, by your original choice.
- GitHub push is still blocked on you adding the SSH key above.
- v2's new files are uncommitted locally.

---

## 7. Next steps

1. On the next trading day, run `start_today.command` and confirm the morning data refresh, model scoring, and the ATLAS site all work correctly against real live Yahoo/Fyers data (not just the sandbox-mocked test).
2. Add the SSH key above to your GitHub account, then this assistant can commit v2's files and push the private repo.
3. Keep paper trading for a meaningful stretch — dozens of real signals, not just a day or two — before even considering `LIVE_TRADING = True`, given how infrequently this strategy trades (~16 signals/year across the whole universe).
4. Consider revisiting position sizing before ever going live — the backtest shows the edge only survives transaction costs at ₹5 lakh-scale positions, not the ₹5k size currently wired up for live mode.

---

*This document was generated as a snapshot summary at your request. The living operational status doc for this project is kept separately and updated as things change.*
