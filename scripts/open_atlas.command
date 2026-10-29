#!/bin/bash
# Website only (no bot, no Fyers login needed) - e.g. evenings and weekends.
cd "$(dirname "$0")/.."   # project root
source venv/bin/activate
pip install -q -r requirements.txt >/dev/null 2>&1
( sleep 3 && open "http://127.0.0.1:5000" ) &
python src/web/atlas_server.py
