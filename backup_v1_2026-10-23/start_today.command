#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

echo "=================================================================="
echo "  Daily refresh: fresh Fyers login, then (re)start the bot"
echo "=================================================================="
echo ""
echo "[1/3] Stopping any already-running bot.py (it was started before"
echo "      today's login, so it's holding a stale token)..."
pkill -f "python bot.py" 2>/dev/null
pkill -f "python3 bot.py" 2>/dev/null
sleep 1

echo ""
echo "[2/3] Fresh Fyers login - a browser window will open. Log in, then"
echo "      copy the WHOLE address bar URL from the broken-looking page"
echo "      and paste it below when asked."
python auth.py | tee /tmp/fyers_auth_url.txt
AUTH_URL=$(grep -o 'http[s]*://[^ ]*' /tmp/fyers_auth_url.txt | head -1)
if [ -n "$AUTH_URL" ]; then
  open "$AUTH_URL"
fi
python get_token.py

echo ""
echo "[3/3] Starting the bot (paper trading mode)..."
echo "      Dashboard: http://127.0.0.1:5000"
echo "      Press Ctrl+C here (or close this window) to stop the bot."
echo "=================================================================="
echo ""
python bot.py 2>&1 | tee bot_output.log
