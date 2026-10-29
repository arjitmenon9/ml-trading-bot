#!/bin/bash
# Website only (no bot, no Fyers login needed) - e.g. evenings and weekends.
cd "$(dirname "$0")"
source venv/bin/activate
pip install -q -r requirements_ml.txt >/dev/null 2>&1
( sleep 3 && open "http://127.0.0.1:5000" ) &
python atlas_server.py
