#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

echo "Stopping the stuck bot process (it hit a Fyers API hang - fixed now)..."
pkill -f "python bot.py" 2>/dev/null
pkill -f "python3 bot.py" 2>/dev/null
sleep 1

echo "Restarting the bot with the socket-timeout fix (today's login is still valid)..."
echo "Dashboard: http://127.0.0.1:5000"
echo "Press Ctrl+C here (or close this window) to stop the bot."
echo ""
python bot.py 2>&1 | tee bot_output.log
