#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "=================================================================="
echo "  Smart Signal - fresh Fyers login + fetch real data + retrain"
echo "=================================================================="
echo ""
source venv/bin/activate
echo "[1/4] Opening a FRESH Fyers login page (the old code expired)..."
echo "      Log in normally. You'll land on a page that looks broken"
echo "      (can't connect to 127.0.0.1) - that's expected and fine."
echo "      Copy the ENTIRE address bar URL from that broken page."
python auth.py | tee /tmp/fyers_auth_url.txt
AUTH_URL=$(grep -o 'http[s]*://[^ ]*' /tmp/fyers_auth_url.txt | head -1)
if [ -n "$AUTH_URL" ]; then
  open "$AUTH_URL"
fi
echo ""
echo "[2/4] Paste the WHOLE url from the broken page below and press Enter"
echo "      (or just the auth_code= part - either works now):"
python get_token.py
echo ""
echo "[3/4] Fetching real historical data from your Fyers account..."
python fetch_data_fyers.py
echo ""
echo "[4/4] Training the model on real data..."
python train_model.py
echo ""
echo "=================================================================="
echo "  Done. To run the bot: source venv/bin/activate && python bot.py"
echo "=================================================================="
echo "Press Enter to close this window..."
read
