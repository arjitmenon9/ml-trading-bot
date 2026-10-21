TRADEBOT FRONTEND - README
==========================

HOW TO USE
----------
1. Open dashboard.html in any web browser (double click it)
2. Click "START BOT" to begin the paper trading simulation
3. The dashboard will show:
   - Live simulated price data
   - EMA9, EMA21 and RSI indicators
   - BUY/SELL/HOLD signals
   - Paper trade order log
   - Unrealized P&L on open positions

FEATURES
--------
- Real-time price chart with EMA lines
- RSI + EMA crossover strategy (same as your Python bot)
- Paper trading order log (no real money used)
- Configurable symbol, quantity, EMA periods, RSI settings
- Live P&L tracking
- Bot activity terminal log

CONNECTING TO YOUR PYTHON BOT
------------------------------
This frontend currently runs in simulation mode.
To connect it to your live Fyers bot, you would need to:
1. Add a Flask/FastAPI server to bot.py
2. Send price/signal data to the frontend via WebSocket

FILES
-----
dashboard.html  - Main dashboard (open this in browser)
README.txt      - This file

ML UPGRADE (NEW)
-----------------
This bot now has a machine-learning signal engine alongside the original
rule-based scorer. See ML_WALKTHROUGH.md for the full explanation (what
was added, how it works, how to run it, and honest limitations).

Quick start:
  pip install -r requirements_ml.txt
  python generate_demo_data.py   (or fetch_data.py for real data)
  python train_model.py
  python backtest.py
  python bot.py

Your original bot.py was backed up as bot_original_rule_based.py.bak.
