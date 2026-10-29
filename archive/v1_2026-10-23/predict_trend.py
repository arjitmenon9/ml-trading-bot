"""
predict_trend.py
=================
Prints today's "outlook" for each stock: what the trained model currently
predicts will happen over the next few trading days, and how confident it
is. This is a stand-alone report you can run any time (once a day is
plenty, since it's built on daily bars) - the live bot (bot.py) computes
the same thing continuously while it's running.

WHAT "PREDICTING FUTURE TRENDS" ACTUALLY MEANS HERE - read this before
trusting the output:
    The model outputs one of BUY / HOLD / SELL, meaning "I expect this stock
    to move up / sideways / down by more than 1.5% over the next 5 trading
    days" (see features.py: LABEL_HORIZON, LABEL_THRESHOLD - change those
    and retrain to forecast a different horizon or sensitivity). It is NOT a
    specific price target, and the confidence score is NOT "probability of
    being right" in a strict sense - it is how strongly the model's internal
    votes agree, which tends to correlate with reliability but isn't a
    guarantee. Historical accuracy for this exact model is in
    models/metadata.json (test_accuracy / test_f1_macro) - that number, not
    any single day's confidence score, is your best guide to how much to
    trust it overall.

Usage:
    python predict_trend.py
"""
import json
from pathlib import Path

import joblib
import pandas as pd

from features import add_indicators, FEATURE_COLUMNS

DATA_DIR = Path(__file__).parent / "data"
MODEL_DIR = Path(__file__).parent / "models"
LABEL_NAMES = {1: "BUY (expect a rally)", 0: "HOLD (expect sideways movement)", -1: "SELL (expect a decline)"}


def main():
    model_path = MODEL_DIR / "model.pkl"
    meta_path = MODEL_DIR / "metadata.json"
    if not model_path.exists():
        raise SystemExit("No trained model found. Run train_model.py first.")

    model = joblib.load(model_path)
    metadata = json.loads(meta_path.read_text())

    print("=" * 72)
    print(f"Model: {metadata['model_name']}   "
          f"(historical test accuracy: {metadata['test_accuracy']:.1%}, "
          f"macro-F1: {metadata['test_f1_macro']:.2f})")
    if str(metadata.get("data_source", "")).startswith("demo"):
        print("*** WARNING: trained on SYNTHETIC demo data - not a real forecast. ***")
    print("=" * 72)
    print(f"{'Symbol':<12}{'Outlook (next ~5 trading days)':<38}{'Confidence':>12}")
    print("-" * 72)

    for csv_path in sorted(DATA_DIR.glob("*.csv")):
        symbol = csv_path.stem
        raw = pd.read_csv(csv_path, parse_dates=["date"]).sort_values("date")
        feats = add_indicators(raw)
        last_valid = feats.dropna(subset=FEATURE_COLUMNS).iloc[-1:]
        if last_valid.empty:
            print(f"{symbol:<12}{'not enough history yet':<38}")
            continue

        row = last_valid[FEATURE_COLUMNS]
        pred = model.predict(row)[0]
        proba = model.predict_proba(row)[0]
        confidence = max(proba)
        as_of = last_valid["date"].iloc[0].date()

        print(f"{symbol:<12}{LABEL_NAMES[int(pred)]:<38}{confidence:>11.1%}  (as of {as_of})")

    print("\nRemember: this reflects patterns in the training data, not certainty about")
    print("the future. Re-run after fetching fresh data + retraining for an up-to-date view.")


if __name__ == "__main__":
    main()
