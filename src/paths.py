"""
paths.py - one place that knows where everything lives.

Importing this module puts every folder under src/ (trading, broker, ml,
data_pipeline, web) on sys.path, so the modules keep importing each other by
plain name (`import strategy`, `from universe import ...`). That also keeps
models/model.pkl loadable: the pickle refers to the `ensemble` module by name.

Every runnable script starts with the same two lines, which find src/ and
import this file:

    import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import paths  # noqa: E402  (puts src/* on sys.path)
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent                      # project root (where fyers_secrets.json, venv/ live)

DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models"
REPORT_DIR = ROOT / "reports"
FRONTEND_DIR = ROOT / "frontend"
LOG_DIR = ROOT / "logs"

for _pkg in sorted(SRC.iterdir()):
    if _pkg.is_dir() and not _pkg.name.startswith(("_", ".")) and str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))
