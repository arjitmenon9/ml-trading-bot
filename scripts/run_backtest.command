#!/bin/bash
cd "$(dirname "$0")/.."   # project root
mkdir -p logs
source venv/bin/activate
echo "Running backtest on the real-data model..."
python src/ml/backtest.py 2>&1 | tee logs/backtest_output.log
echo ""
echo "=================================================================="
echo "  Done. Output also saved to logs/backtest_output.log"
echo "=================================================================="
echo "Press Enter to close this window..."
read
