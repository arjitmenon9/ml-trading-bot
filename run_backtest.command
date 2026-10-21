#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo "Running backtest on the real-data model..."
python backtest.py 2>&1 | tee backtest_output.log
echo ""
echo "=================================================================="
echo "  Done. Output also saved to backtest_output.log"
echo "=================================================================="
echo "Press Enter to close this window..."
read
