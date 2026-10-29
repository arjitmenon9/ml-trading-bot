"""
atlas_server.py
===============
Runs ONLY the ATLAS website + data API (no trading bot, no Fyers login).
Handy when the market is closed, when today's token isn't set up yet, or to
host the site somewhere else.

    python atlas_server.py            -> http://127.0.0.1:5000
    PORT=8080 python atlas_server.py

(bot.py serves the same website plus live bot data, so you normally run
just bot.py via start_today.command.)

Optional settings (environment variables, or a .env file next to this one):
    COMMODITY_API_KEY   CommodityPriceAPI key (else Yahoo futures are used)
    FINNHUB_API_KEY     adds World / Crypto / Forex headlines
    QUOTE_CACHE_SECONDS, NEWS_CACHE_SECONDS, CACHE_SECONDS
"""
import os
from pathlib import Path

env = Path(__file__).parent / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from flask import Flask

import atlas_api

app = Flask(__name__)
app.register_blueprint(atlas_api.bp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"ATLAS running on http://{host}:{port}  (website only - start bot.py for live bot data)")
    app.run(host=host, port=port, threaded=True)
