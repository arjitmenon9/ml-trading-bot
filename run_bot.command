#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo "=================================================================="
echo "  Starting the bot (paper trading mode)"
echo "  Dashboard: http://127.0.0.1:5000"
echo "  Press Ctrl+C here (or close this window) to stop the bot."
echo "=================================================================="
echo ""
python bot.py 2>&1 | tee bot_output.log
