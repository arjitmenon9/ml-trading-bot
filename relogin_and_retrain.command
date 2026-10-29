#!/bin/bash
# Fresh login -> update data from Fyers -> FULL walk-forward retrain (20-40 min)
# -> backtest report. Run this every month or two, after market hours.
set -e
cd "$(dirname "$0")"
source venv/bin/activate
pip install -q -r requirements_ml.txt >/dev/null 2>&1
echo "[1/4] Fyers login (copy the whole URL of the broken-looking page and paste it)"
python auth.py | tee /tmp/fyers_auth_url.txt
AUTH_URL=$(grep -o 'http[s]*://[^ ]*' /tmp/fyers_auth_url.txt | head -1)
[ -n "$AUTH_URL" ] && open "$AUTH_URL"
python get_token.py
echo "[2/4] Updating ~190 stocks' daily history..."
python update_data.py --days 120
echo "[3/4] Walk-forward retrain (this takes a while)..."
python train_model.py 2>&1 | tee train_output.log
echo "[4/4] Backtest report..."
python backtest.py 2>&1 | tee backtest_output.log
echo ""
echo "Done. Restart the bot (restart_clean.command) to load the new model."
echo "Press Enter to close this window..."
read
