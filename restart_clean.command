#!/bin/bash
# Restart the bot WITHOUT a new Fyers login (today's token already saved).
# Not needed after get_token.py any more: a running bot reloads new tokens itself.
cd "$(dirname "$0")"
source venv/bin/activate
pkill -9 -f "bot.py" 2>/dev/null
sleep 2
echo "Starting the bot + ATLAS website: http://127.0.0.1:5000"
( sleep 4 && open "http://127.0.0.1:5000/#/bot" ) &
python bot.py 2>&1 | tee bot_output.log
