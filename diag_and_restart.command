#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

echo "=== All python processes before cleanup ==="
ps aux | grep -i python | grep -v grep

echo ""
echo "=== Killing ALL bot.py / python processes tied to this project ==="
pkill -9 -f "bot.py" 2>/dev/null
pkill -9 -f "auth.py" 2>/dev/null
pkill -9 -f "get_token.py" 2>/dev/null
sleep 2

echo ""
echo "=== All python processes after cleanup ==="
ps aux | grep -i python | grep -v grep

echo ""
echo "=== Who is on port 5000? ==="
lsof -i :5000 2>/dev/null || echo "(nothing on port 5000)"

echo ""
echo "=== Quick standalone quote test (bypasses bot.py entirely) ==="
python3 - << 'PYEOF'
import time
from config import get_fyers_client
print("Getting fyers client...")
t0 = time.time()
fyers = get_fyers_client()
print(f"  client ready in {time.time()-t0:.1f}s")
print("Calling fyers.quotes() for NSE:HDFCBANK-EQ ...")
t0 = time.time()
try:
    resp = fyers.quotes({"symbols": "NSE:HDFCBANK-EQ"})
    print(f"  responded in {time.time()-t0:.1f}s")
    print("  response:", resp)
except Exception as e:
    print(f"  FAILED after {time.time()-t0:.1f}s: {e}")
PYEOF

echo ""
echo "=== Starting bot fresh ==="
echo "Dashboard: http://127.0.0.1:5000"
echo "Press Ctrl+C here (or close this window) to stop the bot."
python bot.py 2>&1 | tee bot_output.log
