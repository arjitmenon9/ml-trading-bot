#!/bin/bash
# Daily routine: fresh Fyers login, then (re)start the bot + ATLAS website.
cd "$(dirname "$0")/.."   # project root
mkdir -p logs
source venv/bin/activate
pip install -q -r requirements.txt >/dev/null 2>&1

echo "=================================================================="
echo "  Daily start: Fyers login, then the bot + ATLAS website"
echo "=================================================================="
echo ""
echo "[1/3] Stopping any already-running bot..."
pkill -f "bot.py" 2>/dev/null       # old or new layout
sleep 1

echo ""
echo "[2/3] Fresh Fyers login - a browser window will open. Log in, then"
echo "      copy the WHOLE address bar URL from the broken-looking page"
echo "      and paste it below when asked."
python src/broker/auth.py | tee /tmp/fyers_auth_url.txt
AUTH_URL=$(grep -o 'http[s]*://[^ ]*' /tmp/fyers_auth_url.txt | head -1)
if [ -n "$AUTH_URL" ]; then
  open "$AUTH_URL"
fi
python src/broker/get_token.py

echo ""
echo "[3/3] Starting the bot (paper trading) + website..."
echo "      ATLAS:   http://127.0.0.1:5000        (opens in a few seconds)"
echo "      Bot:     http://127.0.0.1:5000/#/bot"
echo "      After 9:15 the bot updates yesterday's data from Fyers, scores"
echo "      ~190 stocks and buys only the setups above the model threshold."
echo "      Close this window (or Ctrl+C) to stop the bot."
echo "=================================================================="
( sleep 4 && open "http://127.0.0.1:5000/#/bot" ) &
python src/trading/bot.py 2>&1 | tee logs/bot_output.log
