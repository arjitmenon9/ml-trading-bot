#!/bin/bash
# Double-click this file in Finder to run it.
# Does: rebuild the broken venv, install everything needed, log into Fyers,
# fetch real historical data, and train the model - all in one go.
set -e
cd "$(dirname "$0")"

echo "=================================================================="
echo "  Smart Signal - one-time setup + daily Fyers login"
echo "=================================================================="

echo ""
echo "[1/6] Rebuilding the Python environment (the old one pointed at a"
echo "      drive that's no longer connected)..."
rm -rf venv
PYBIN=python3
if ! command -v python3 &>/dev/null && command -v python3.11 &>/dev/null; then
  PYBIN=python3.11
fi
"$PYBIN" -m venv venv
source venv/bin/activate
pip install --upgrade pip -q

echo ""
echo "[2/6] Installing packages (this can take a few minutes the first time)..."
pip install -q fyers-apiv3 flask flask-cors
pip install -q -r requirements_ml.txt

echo ""
echo "[3/6] Opening the Fyers login page in your browser..."
echo "      Log in normally, then you'll land on a page that looks broken"
echo "      (can't connect to 127.0.0.1) - that's expected. Copy the"
echo "      'auth_code=' value out of that page's URL bar."
python auth.py | tee /tmp/fyers_auth_url.txt
AUTH_URL=$(grep -o 'http[s]*://[^ ]*' /tmp/fyers_auth_url.txt | head -1)
if [ -n "$AUTH_URL" ]; then
  open "$AUTH_URL"
fi

echo ""
echo "[4/6] Paste the auth_code below and press Enter:"
python get_token.py

echo ""
echo "[5/6] Fetching real historical data from your Fyers account..."
python fetch_data_fyers.py || echo "  (fetch had trouble - see messages above; you can retry later with: python fetch_data_fyers.py)"

echo ""
echo "[6/6] Training the model on real data..."
python train_model.py || echo "  (training had trouble - see messages above; retry with: python train_model.py)"

echo ""
echo "=================================================================="
echo "  Done. To run the bot: source venv/bin/activate && python bot.py"
echo "=================================================================="
echo "Press Enter to close this window..."
read
