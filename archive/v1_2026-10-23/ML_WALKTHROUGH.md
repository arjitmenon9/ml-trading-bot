# Smart Signal — ML Walkthrough

This explains what's in your project now, in plain English, and how to run
it. Read section 0 first regardless of anything else — it sets expectations
that matter for how you use (and write up) the rest of this.

## 0. Set your expectations correctly first — this matters for your grade too

You asked for 100% profit and "highest accuracy of any model on the
internet." Neither exists, for any stock, by anyone, ever, and it's worth
understanding *why*, not just taking that on faith:

- If a model could predict price moves with near-100% accuracy, whoever ran
  it would have an infinite money machine and would very quickly own the
  entire market — capital would flow toward that strategy until its edge
  vanished. Markets move on genuinely new information nobody has yet; that's
  not a limitation of this codebase, it's closer to a law of how markets work.
- Thousands of professional quant funds, with far more data, compute, and
  data-source cleanliness than a 4-stock college project, spend billions of
  dollars competing for tiny edges. Realistic, published, honestly-evaluated
  accuracy for short-horizon equity direction prediction is usually in the
  40-55% range for 3-way classification (vs. ~33% random chance) — genuinely
  useful when combined with risk management, nowhere near "predicting the
  future."
- If you see a backtest or a claimed model anywhere online boasting 90%+
  accuracy on this kind of task, the far more likely explanation is a bug
  (almost always lookahead bias — the model accidentally got to peek at
  future data) than a genuine breakthrough. This project's own backtest is
  explicitly designed to avoid that bug — see section 3.

None of this means the project is pointless — it means the honest goal is
"meaningfully better than a hand-tuned rule, evaluated fairly," not "never
loses." That's also a *much* stronger thing to say in a college report than
an unverifiable "100% profitable," which would (correctly) read as a red
flag to anyone who evaluates it.

## 1. What actually changed, at a glance

Your original `bot.py` decided BUY/SELL/HOLD with a formula a human wrote:
"+1 point if EMA9 is above EMA21, +2 points if RSI is under 40..." Machine
learning replaces the guessing: an algorithm learns its own rule from
thousands of historical examples of "here's what the indicators looked like"
paired with "here's what actually happened next."

| File | Purpose |
|---|---|
| `features.py` | The ONE place that computes every indicator and defines what counts as a BUY/SELL/HOLD label. 22 features now (see section 4). |
| `config.py` | Central, git-ignored storage for your Fyers credentials (replaces hard-coded secrets - see section 7). |
| `auth.py` / `get_token.py` | The daily Fyers login routine (unchanged in spirit, now reads/writes through `config.py`). |
| `generate_demo_data.py` | Fake but semi-realistic price history for instant, no-internet testing. |
| `fetch_data_fyers.py` | Downloads REAL historical daily data from your Fyers account (see section 6). |
| `fetch_data.py` | Real data from Yahoo Finance - kept as a fallback if Fyers' free-plan history lookback is too short for your needs. |
| `train_model.py` | Trains and tunes multiple models, picks the best one honestly (see section 5). |
| `predict_trend.py` | Stand-alone "what does the model currently think will happen" report (see section 8). |
| `backtest.py` | Replays history to compare the ML model, your original rule-based bot, and buy-and-hold. |
| `ml_signals.py` | Bridges the trained model into the *live* bot correctly (see section 9 on train/serve skew). |
| `fyers_execution.py` | Where a decision becomes a real (or simulated) order, with position-size limits and a kill-switch (see section 10). |
| `bot.py` | Wires all of the above together. Two real bugs fixed along the way - see section 11. |

## 2. Features vs. labels

For every historical trading day we build a **feature vector** (22 numbers
describing that day - see section 4) and a **label**: the "correct answer,"
decided in hindsight by looking 5 trading days ahead. If price rose >1.5% →
BUY, fell >1.5% → SELL, otherwise → HOLD (`LABEL_HORIZON` / `LABEL_THRESHOLD`
in `features.py` - change these to forecast a different horizon).

## 3. Why the train/test split is by TIME, not randomly

The single most important idea here. Shuffling data before splitting would
let the model "train" on a day in March and get "tested" on an earlier day
in February — indirectly leaking the future into training (**lookahead
bias**), the #1 reason beginner trading models look great in testing and
lose money for real. `train_model.py` always trains on the OLDER 80% of each
stock's history and tests on the most recent 20%. The hyperparameter tuning
in Stage 2 (section 5) uses `TimeSeriesSplit` instead of ordinary K-fold
cross-validation for the exact same reason — tuning can leak the future too
if you're not careful.

## 4. The 22 features (up from 15)

**Original six, now vectorized correctly:** EMA9, EMA21 (and their %gap),
RSI (proper Wilder smoothing), MACD (line/signal/histogram), Bollinger Bands
(as %B and band-width), a 20-day rolling VWAP-gap (see the bug note in
section 11 about why it's now a rolling window, not a lifetime cumulative
one), and Stochastic Oscillator.

**Added this round, all in `features.py`:**
- **ATR (Average True Range)**, as % of price — how much a stock typically
  moves per day, a volatility measure independent of direction.
- **ADX (Average Directional Index)** — how *strong* the current trend is,
  regardless of direction. Low = choppy/sideways, high = trending hard.
  This is a genuinely useful complement to the directional indicators above:
  a BUY signal during a strong uptrend (high ADX) means something different
  than the same signal in a choppy market (low ADX).
- **CCI (Commodity Channel Index)** — how far price has stretched from its
  recent average, scaled by typical deviation.
- **Williams %R** — similar idea to Stochastic, price's position within its
  recent high/low range.
- **OBV rate-of-change** — On-Balance Volume (a running volume-weighted
  measure of buying vs. selling pressure) isn't comparable in raw form
  across stocks, so we use its 10-day rate of change instead.
- **Position in the 52-week range** — % distance from the 1-year high and
  low, i.e. is this stock near a breakout or capitulating.
- **Momentum** now includes a 20-day window in addition to 5- and 10-day.

The feature importance chart (`reports/feature_importance.png`) tells you
which of these 22 the winning model actually leans on for YOUR data - it's
common (and a good sign, not a bad one) for several features to matter
little; it means the model isn't grasping at noise.

## 5. How model selection works now (Stage 1 + Stage 2)

`train_model.py` runs in two stages:

**Stage 1 - baseline comparison.** Trains a linear baseline (Logistic
Regression) plus five tree-ensemble families with reasonable default
settings: Random Forest, Extra Trees, sklearn's Gradient Boosting and
HistGradientBoosting, and — if installed — **XGBoost** and **LightGBM**,
the two boosting libraries that most consistently win real-world tabular-data
competitions. All are evaluated on the same untouched, most-recent test
slice.

**Stage 2 - tuning.** Random Forest and (if available) XGBoost get a proper
hyperparameter search — `RandomizedSearchCV` with `TimeSeriesSplit`
cross-validation (20 random configurations each, scored by macro-F1) instead
of hand-guessed settings. This is the ML equivalent of the original bot's
score weights (+1, +2, -2...) being tuned by search instead of guessed by a
human.

**Selection.** Every model tried — baseline and tuned — is ranked by
**macro-F1** on the held-out test set (`reports/model_comparison.png`), and
whichever wins is saved as `models/model.pkl`. Macro-F1 (average of each
class's F1 score) is used instead of raw accuracy because it doesn't let a
model coast by always predicting the majority class (HOLD) — see the
precision/recall explanation below.

**Reading the per-model report:**
```
              precision    recall  f1-score   support
        SELL       0.45      0.45      0.45       346
        HOLD       0.44      0.21      0.29       460
         BUY       0.39      0.63      0.48       350
```
- **precision** for BUY: of everything called BUY, what fraction actually
  rallied? (Low = false alarms.)
- **recall** for BUY: of everything that actually rallied, what fraction did
  the model catch? (Low = missed opportunities.)
- There's an unavoidable tension between the two — f1-score balances them.

On the included demo run, the tuned Random Forest reached ~42% accuracy /
0.41 macro-F1 (vs. ~33% random-chance for 3 balanced classes) — see section
0 for why that's a reasonable, not disappointing, result, and re-read it
once you retrain on real data and the number is similarly modest.

## 6. Getting REAL data (three options, pick based on what works for you)

**Fyers historical API (`fetch_data_fyers.py`) - what you asked for, and the
most consistent choice** since it's the exact same data source your live bot
trades on:
```bash
python auth.py            # prints a login URL
python get_token.py       # paste the auth code it gives you back
python fetch_data_fyers.py
```
Free/basic Fyers plans have lookback limits on historical daily data (often
under a year) - the script fetches in yearly chunks and just gets however
much your plan allows for older years; it won't error out, you'll just have
less training data the further back you ask.

**Yahoo Finance (`fetch_data.py`)** - already built, free, no login, useful
if Fyers' lookback is too short for a solid training set. Run whichever
you choose and watch for errors; the scripts print clear diagnostics either way.

**NSE's own website** — you asked about pulling directly from
nseindia.com. Worth knowing before you try: NSE's site has aggressive
bot-detection (session cookies, changing endpoints, rate limits) and isn't
built for casual scripted downloads the way Yahoo's or a broker's API is.
Its official EOD "bhavcopy" archives are public and downloadable, but
scraping them reliably usually needs browser-like headers/cookies and
tends to break when NSE tweaks its site. Given Fyers already gives you
consistent, broker-grade data, I'd only chase this if you specifically need
"NSE said so" for your report — a scraper can be
added later if the two options above aren't enough.

Whichever you use, retrain afterward:
```bash
python train_model.py
python backtest.py
python predict_trend.py
```
Both `bot.py` and `train_model.py` print a loud warning if the currently
loaded model was trained on demo data — that warning should be gone before
you trust any live signal.

## 7. Credentials: no more secrets hard-coded in the code

Your original `auth.py`, `get_token.py`, and `bot.py` each had your Fyers
App ID, secret key, and access token typed directly into the file - a real
risk the moment this is pushed to GitHub for your group project. Now:
- `fyers_secrets.json` (git-ignored, see `.gitignore`) holds all of it in
  one place. Create it once with your
  app_id/secret_key (nothing else changes about your Fyers app).
- `fyers_secrets.example.json` (safe to commit) shows the shape for a
  teammate setting this up fresh.
- Every script that needs Fyers now goes through `config.py` instead of
  having its own copy-pasted token.
- Fyers access tokens still expire every day (a Fyers platform rule) - run
  `auth.py` then `get_token.py` each morning before trading; the fresh
  token is saved automatically and every other script just picks it up.

**You should still rotate your actual secret key** on myapi.fyers.in at some
point, since it was previously visible in plaintext in files that may already
have been shared - this refactor stops the bleeding going forward, it
doesn't undo prior exposure.

## 8. "Predicting future trends" — `predict_trend.py`

Run this any time for a snapshot:
```
python predict_trend.py
```
```
Symbol      Outlook (next ~5 trading days)          Confidence
HDFCBANK    HOLD (expect sideways movement)             37.2%
RELIANCE    SELL (expect a decline)                     44.6%
```
Important honesty note, also printed by the script itself: the label means
"the model expects a >1.5% move within 5 trading days," not a specific price
target, and the confidence score reflects how strongly the model's internal
votes agree — not a calibrated "probability of being correct." The model's
overall `test_accuracy` / `test_f1_macro` (in `models/metadata.json`) is a
better guide to how much to trust it than any single day's confidence
number. `bot.py` computes this same forecast continuously while running live.

## 9. Wiring into the live bot without breaking the model (train/serve skew)

`train_model.py` learns from **daily** bars; the live bot polls a quote
every ~10 seconds. Feeding raw ticks into "EMA21" would produce "average of
the last ~8 minutes," nothing like the daily EMA21 the model actually
learned from - a real ML failure mode called **train/serve skew**, where
live features don't match training features and predictions become
meaningless. `ml_signals.py`'s `LiveFeatureBuilder` fixes this by keeping a
real daily-bar history per symbol (loaded from `data/<SYMBOL>.csv`) plus a
continuously-updating "today so far" bar, so every prediction is computed on
daily-bar-shaped data just like training was.

## 10. Live trading safety — paper mode, position limits, kill-switch

You chose to start in **paper mode**, which is the default and is exactly
what `fyers_execution.py` implements: `LIVE_TRADING = False` means the bot
connects to your real account and real market data, computes real signals,
but every order is only logged (`state[...]["orders"]`) - nothing reaches
the exchange.

When and if you're ready to flip `LIVE_TRADING = True` in
`fyers_execution.py`, three things are already built in:
- **`MAX_POSITION_VALUE`** (default Rs.5000) - caps how much any single
  trade risks, computed into a share quantity automatically.
- **`DAILY_LOSS_LIMIT`** (default Rs.2000) - if today's realized P&L drops
  past -this, the **kill-switch** trips and the bot stops opening new
  positions for the rest of the day (it leaves existing positions alone
  rather than force-closing them, since that's its own decision with its
  own risk).
- **`MAX_TRADES_PER_DAY`** (default 40) - a safety net against a bug that
  flips signals rapidly and churns trades.

These limits are active in paper mode too (harmlessly, since no real money
moves), which means you can watch the kill-switch logic actually fire during
paper trading before you ever risk money on it. Recommended before flipping
the switch: run several full paper-trading sessions, read the trade log, set
both limits to numbers you're genuinely fine losing completely, and start
small. This project - like any college-project-stage model - cannot promise
a level of profitability; automated trading can lose money quickly if left
unmonitored.

## 11. Bugs found and fixed along the way

1. **RSI silently froze.** The original `calculate_rsi()` always read
   `prices[0:period+1]` — the *oldest* ticks ever collected — instead of a
   rolling recent window, so RSI locked at whatever it was after the first
   15 ticks and never updated again all day. Fixed to use
   `prices[-(period+1):]`.
2. **The bot crashed once it had collected 10-14 ticks.** `NEEDED = 10`
   lets scoring start before `calculate_rsi`/`calculate_macd` have enough
   points internally (they need 15 / 35), so they return `None` - and the
   original status-line print formatted `rsi` with `:.1f` unconditionally,
   crashing with `TypeError: unsupported format string passed to
   NoneType.__format__` the first time that happened. Fixed to print `n/a`
   until real values exist.
3. **VWAP drifted meaninglessly over a multi-year dataset.** The original
   VWAP was cumulative from the very first row of price history - fine for
   an intraday VWAP that resets daily, not fine for years of daily bars,
   where it drifts arbitrarily far from price and its value depends on how
   much history happens to be loaded (not a stable, comparable feature).
   Changed to a rolling 20-day VWAP.

## 12. How to run everything, start to finish

```bash
cd "ml code"
source venv/bin/activate
pip install -r requirements_ml.txt

# Get real data (pick one - see section 6):
python auth.py && python get_token.py && python fetch_data_fyers.py
#   -- or --
python fetch_data.py
#   -- or, for instant no-internet testing --
python generate_demo_data.py

python train_model.py       # trains + tunes + saves models/model.pkl
python backtest.py          # reports/backtest_equity_curves.png
python predict_trend.py     # today's outlook per stock

python bot.py                # paper trading by default (see section 10)
```

## 13. Honest limitations (good material for your project report)

- Small universe (4 stocks); a model tuned here may not generalize elsewhere.
- Backtest has no fees/slippage and 1-unit position sizing.
- Fixed label threshold (1.5% over 5 days) rather than volatility-adjusted
  per stock.
- One static train/test split, not a rolling walk-forward evaluation
  (retrain periodically as new data arrives) - a natural next step, and
  explicitly called out as future work in your synopsis.
- Confidence scores are not calibrated probabilities.
- This is, and will remain, a college-project-stage system: real trading
  firms combine models like this with far more data, execution
  sophistication, and risk infrastructure. Treat it as a genuine, honestly-
  evaluated demonstration of the ML pipeline - which is what the synopsis
  actually asks for - not a finished trading product.
