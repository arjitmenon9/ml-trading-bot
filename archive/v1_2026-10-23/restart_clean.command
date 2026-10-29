#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

echo "Killing all bot.py instances (there may be a few stacked up from testing)..."
pkill -9 -f "bot.py" 2>/dev/null
sleep 2
echo "Remaining python processes:"
ps aux | grep -i "python.*bot.py" | grep -v grep
echo "(empty above = all clear)"
echo ""
echo "Starting the bot with the thread-local Fyers client fix..."
echo "Dashboard: http://127.0.0.1:5000"
echo "Press Ctrl+C here (or close this window) to stop the bot."
python bot.py 2>&1 | tee bot_output.log
