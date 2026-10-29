SMART SIGNAL v2 - ML trading bot + ATLAS markets website
=========================================================

EVERY TRADING MORNING
  Double-click start_today.command
  -> log in to Fyers, paste the URL when asked
  -> the bot + website start, and http://127.0.0.1:5000/#/bot opens

WEBSITE ONLY (evenings/weekends, no login): open_atlas.command
RETRAIN (monthly, after hours):             relogin_and_retrain.command
BACKTEST REPORT:                            run_backtest.command

Paper trading only. LIVE_TRADING in fyers_execution.py stays False until
you change it yourself (read section 5 of ML_WALKTHROUGH.md first: at
Rs 5,000 per trade, Fyers charges eat the whole edge).

Full explanation, results and limitations: ML_WALKTHROUGH.md
