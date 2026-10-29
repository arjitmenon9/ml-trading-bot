"""
train_model.py
===============
This is the heart of the ML pipeline. Read the comments top-to-bottom -
they're written for someone who has never trained a model before.

WHAT WE'RE DOING, IN PLAIN ENGLISH
-----------------------------------
Your original bot decides BUY/SELL/HOLD with a hand-written scoring rule
("+1 if EMA9>EMA21, +2 if RSI<40, ..."). Those weights (+1, +2, -2...) were
guessed by a human. Machine learning replaces the guessing: we show the
computer thousands of past examples of "here's what the indicators looked
like on some day" paired with "here's what actually happened next," and let
an algorithm figure out its own weights/rules from the data.

Concretely, for every historical trading day we build:
  - a FEATURE VECTOR: the indicator values on that day (22 of them - see
    features.py: EMA/RSI/MACD/Bollinger/VWAP/Stochastic plus ATR, ADX, CCI,
    Williams %R, OBV rate-of-change, and 52-week-range position)
  - a LABEL: what SHOULD have been done that day, decided in hindsight by
    looking 5 days into the future (see features.py: LABEL_HORIZON/THRESHOLD)

WHY WE SPLIT DATA BY TIME, NOT RANDOMLY
-----------------------------------------
If we shuffled and split randomly, the model could "train" on a day in March
and be "tested" on a day in February that came before it - indirectly
leaking the future into training. That's LOOKAHEAD BIAS, the #1 way
beginner trading models look great in testing and lose money in reality.
Every split here - both the final train/test split AND the cross-validation
used for tuning below - respects time order.

HOW WE TRY TO GET THE BEST ACCURACY WE HONESTLY CAN
------------------------------------------------------
1. Train several very different model families and compare them fairly on
   the same held-out future data: a linear baseline (Logistic Regression),
   three tree-ensemble families (Random Forest, Extra Trees, sklearn's
   Gradient Boosting / HistGradientBoosting), and two industry-standard
   boosting libraries (XGBoost, LightGBM) if installed - these are
   consistently what wins Kaggle competitions on tabular data like this.
2. For the two strongest tree ensembles, run RandomizedSearchCV with
   TimeSeriesSplit cross-validation to tune hyperparameters properly,
   instead of guessing numbers by hand - the ML equivalent of the rule-based
   bot's hand-guessed score weights, done automatically and validated.
3. Pick whichever model actually wins on the untouched, most-recent,
   held-out slice of data - not whichever "sounds fancier."

READ THIS BEFORE TRUSTING ANY NUMBER THIS SCRIPT PRINTS: short-horizon stock
price movement is one of the noisiest, most heavily-arbitraged prediction
problems that exists - thousands of professional quant funds compete to
find any edge, which erodes it fast. No legitimate model gets anywhere near
90%+ accuracy predicting 3-way BUY/SELL/HOLD on daily equities, and any
result that looks THAT good almost always means a bug (usually lookahead
bias) rather than a breakthrough. Treat 40-55% (vs. ~33% random-chance
baseline for 3 classes) as a genuinely good, plausible result.

OUTPUT
------
- models/model.pkl + models/metadata.json   the selected model + its info
- reports/confusion_matrix.png, feature_importance.png, model_comparison.png
- prints a full evaluation report to the console
"""
import json
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import (
    RandomForestClassifier, ExtraTreesClassifier,
    GradientBoostingClassifier, HistGradientBoostingClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score

from features import build_dataset, FEATURE_COLUMNS

warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent / "data"
MODEL_DIR = Path(__file__).parent / "models"
REPORT_DIR = Path(__file__).parent / "reports"
TEST_FRACTION = 0.2  # most recent 20% of each stock's history is held out for testing
LABEL_NAMES = {1: "BUY", 0: "HOLD", -1: "SELL"}
LABEL_TO_CODE = {-1: 0, 0: 1, 1: 2}   # XGBoost/LightGBM require labels 0..K-1
CODE_TO_LABEL = {v: k for k, v in LABEL_TO_CODE.items()}

try:
    from xgboost import XGBClassifier
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from lightgbm import LGBMClassifier
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False


class RelabeledClassifier:
    """
    Wraps a classifier that insists on labels 0..K-1 (XGBoost, LightGBM) so
    it exposes the exact same .fit/.predict/.predict_proba/.feature_importances_
    interface as every native sklearn model here, using our natural -1/0/1
    (SELL/HOLD/BUY) labels. This means ml_signals.py never needs to know or
    care which library actually produced the winning model.
    """
    def __init__(self, inner):
        self.inner = inner

    def fit(self, X, y):
        y_code = pd.Series(y).map(LABEL_TO_CODE).values
        self.inner.fit(X, y_code)
        return self

    def predict(self, X):
        codes = self.inner.predict(X)
        return np.array([CODE_TO_LABEL[c] for c in codes])

    def predict_proba(self, X):
        return self.inner.predict_proba(X)

    @property
    def feature_importances_(self):
        return self.inner.feature_importances_


def load_all_symbols() -> pd.DataFrame:
    """Load every CSV in data/, compute features+labels, tag each row with its symbol."""
    frames = []
    for csv_path in sorted(DATA_DIR.glob("*.csv")):
        symbol = csv_path.stem
        raw = pd.read_csv(csv_path, parse_dates=["date"]).sort_values("date")
        ds = build_dataset(raw)
        ds["symbol"] = symbol
        frames.append(ds)
        print(f"  {symbol}: {len(ds)} usable rows after feature/label computation")
    if not frames:
        raise SystemExit(
            "No CSV files found in data/. Run generate_demo_data.py (quick test), "
            "fetch_data_fyers.py (real data via your broker), or fetch_data.py "
            "(real data via Yahoo Finance) first."
        )
    return pd.concat(frames, ignore_index=True)


def chronological_split(df: pd.DataFrame):
    """Split each symbol's rows by date so TEST is always the most recent slice."""
    train_parts, test_parts = [], []
    for symbol, group in df.groupby("symbol"):
        group = group.sort_values("date")
        cutoff = int(len(group) * (1 - TEST_FRACTION))
        train_parts.append(group.iloc[:cutoff])
        test_parts.append(group.iloc[cutoff:])
    return pd.concat(train_parts, ignore_index=True), pd.concat(test_parts, ignore_index=True)


def evaluate(name, model, X_test, y_test, results):
    preds = model.predict(X_test)
    acc = accuracy_score(y_test, preds)
    f1m = f1_score(y_test, preds, average="macro")
    results[name] = {"model": model, "preds": preds, "accuracy": acc, "f1_macro": f1m}
    print(f"\n--- {name} ---  accuracy={acc:.3f}  f1_macro={f1m:.3f}")
    print(classification_report(y_test, preds, target_names=["SELL", "HOLD", "BUY"],
                                 labels=[-1, 0, 1], zero_division=0))


def tune_with_time_series_cv(name, base_estimator, param_dist, X_train, y_train, n_iter=20):
    """
    RandomizedSearchCV, but with TimeSeriesSplit instead of the default
    random K-fold - each fold trains on an earlier slice and validates on a
    later one, same rule as the main train/test split, so tuning itself
    can't cheat by peeking at the future either.
    """
    print(f"\nTuning {name} with time-respecting cross-validation ({n_iter} random configs)...")
    search = RandomizedSearchCV(
        base_estimator, param_dist, n_iter=n_iter,
        cv=TimeSeriesSplit(n_splits=4), scoring="f1_macro",
        random_state=42, n_jobs=-1,
    )
    search.fit(X_train, y_train)
    print(f"  best CV f1_macro: {search.best_score_:.3f}  params: {search.best_params_}")
    return search.best_estimator_


def main():
    MODEL_DIR.mkdir(exist_ok=True)
    REPORT_DIR.mkdir(exist_ok=True)

    print("Loading data and building features/labels...")
    df = load_all_symbols()

    print(f"\nLabel balance (full dataset):")
    print(df["label"].map(LABEL_NAMES).value_counts().to_string())

    train_df, test_df = chronological_split(df.sort_values("date"))
    print(f"\nTrain rows: {len(train_df)}  (earliest bars per stock)")
    print(f"Test rows:  {len(test_df)}  (most recent {int(TEST_FRACTION*100)}% per stock, held out)")

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["label"]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df["label"]

    print("\n" + "=" * 78)
    print("STAGE 1: BASELINE MODEL COMPARISON  (accuracy on held-out, never-seen data)")
    print("=" * 78)
    results = {}

    baselines = {
        "Logistic Regression (baseline)": LogisticRegression(max_iter=1000, class_weight="balanced"),
        "Random Forest": RandomForestClassifier(
            n_estimators=300, max_depth=6, min_samples_leaf=20,
            class_weight="balanced", random_state=42, n_jobs=-1,
        ),
        "Extra Trees": ExtraTreesClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=20,
            class_weight="balanced", random_state=42, n_jobs=-1,
        ),
        "Gradient Boosted Trees (sklearn)": GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42,
        ),
        "Hist Gradient Boosting": HistGradientBoostingClassifier(
            max_depth=6, learning_rate=0.05, random_state=42,
        ),
    }
    if HAS_XGB:
        baselines["XGBoost"] = RelabeledClassifier(XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            eval_metric="mlogloss", n_jobs=-1,
        ))
    if HAS_LGBM:
        baselines["LightGBM"] = RelabeledClassifier(LGBMClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.05,
            random_state=42, n_jobs=-1, verbosity=-1,
        ))

    for name, model in baselines.items():
        model.fit(X_train, y_train)
        evaluate(name, model, X_test, y_test, results)

    print("\n" + "=" * 78)
    print("STAGE 2: HYPERPARAMETER TUNING  (time-series cross-validation on train data)")
    print("=" * 78)

    rf_tuned = tune_with_time_series_cv(
        "Random Forest",
        RandomForestClassifier(class_weight="balanced", random_state=42, n_jobs=-1),
        {
            "n_estimators": [200, 300, 500],
            "max_depth": [4, 6, 8, 10, None],
            "min_samples_leaf": [5, 10, 20, 40],
            "max_features": ["sqrt", "log2", 0.5],
        },
        X_train, y_train,
    )
    evaluate("Random Forest (tuned)", rf_tuned, X_test, y_test, results)

    if HAS_XGB:
        xgb_tuned = tune_with_time_series_cv(
            "XGBoost",
            XGBClassifier(random_state=42, eval_metric="mlogloss", n_jobs=-1),
            {
                "n_estimators": [200, 300, 500],
                "max_depth": [3, 4, 5, 6],
                "learning_rate": [0.01, 0.03, 0.05, 0.1],
                "subsample": [0.6, 0.8, 1.0],
                "colsample_bytree": [0.6, 0.8, 1.0],
            },
            X_train, pd.Series(y_train).map(LABEL_TO_CODE),
        )
        evaluate("XGBoost (tuned)", _wrap_fitted(xgb_tuned), X_test, y_test, results)
    elif HAS_LGBM:
        lgbm_tuned = tune_with_time_series_cv(
            "LightGBM",
            LGBMClassifier(random_state=42, n_jobs=-1, verbosity=-1),
            {
                "n_estimators": [200, 300, 500],
                "max_depth": [3, 4, 6, 8],
                "learning_rate": [0.01, 0.03, 0.05, 0.1],
                "num_leaves": [15, 31, 63],
            },
            X_train, pd.Series(y_train).map(LABEL_TO_CODE),
        )
        evaluate("LightGBM (tuned)", _wrap_fitted(lgbm_tuned), X_test, y_test, results)

    # --- Pick the overall winner by macro-F1 (fairer than raw accuracy when
    # classes are imbalanced - see ML_WALKTHROUGH.md section 5) ---
    print("\n" + "=" * 78)
    print("FINAL COMPARISON")
    print("=" * 78)
    summary = pd.DataFrame({
        name: {"accuracy": r["accuracy"], "f1_macro": r["f1_macro"]}
        for name, r in results.items()
    }).T.sort_values("f1_macro", ascending=False)
    print(summary.to_string(float_format=lambda x: f"{x:.3f}"))

    best_name = summary.index[0]
    best = results[best_name]
    best_model, best_preds, best_acc = best["model"], best["preds"], best["accuracy"]
    print(f"\nSelected model for deployment: {best_name}  "
          f"(test accuracy {best_acc:.3f}, macro-F1 {best['f1_macro']:.3f})")
    print("(Random-chance baseline for 3 balanced classes would be ~0.33 accuracy.)")

    # --- Bar chart comparing every model tried ---
    fig, ax = plt.subplots(figsize=(8, 5))
    summary_sorted = summary.sort_values("f1_macro")
    ax.barh(summary_sorted.index, summary_sorted["f1_macro"], color="#4C72B0", label="macro-F1")
    ax.axvline(0.33, color="gray", linestyle="--", linewidth=1, label="random-chance baseline")
    ax.set_xlabel("macro-F1 on held-out test data")
    ax.set_title("Model comparison")
    ax.legend()
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "model_comparison.png", dpi=150)
    plt.close(fig)

    # --- Confusion matrix for the winner ---
    cm = confusion_matrix(y_test, best_preds, labels=[-1, 0, 1])
    fig, ax = plt.subplots(figsize=(5, 4.5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks([0, 1, 2]); ax.set_xticklabels(["SELL", "HOLD", "BUY"])
    ax.set_yticks([0, 1, 2]); ax.set_yticklabels(["SELL", "HOLD", "BUY"])
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
    ax.set_title(f"{best_name}: Confusion Matrix")
    for i in range(3):
        for j in range(3):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                     color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "confusion_matrix.png", dpi=150)
    plt.close(fig)

    # --- Feature importance for the winner (if it has one) ---
    if hasattr(best_model, "feature_importances_"):
        importances = pd.Series(best_model.feature_importances_, index=FEATURE_COLUMNS).sort_values()
        fig, ax = plt.subplots(figsize=(6, 6))
        importances.plot(kind="barh", ax=ax, color="#4C72B0")
        ax.set_title(f"{best_name}: Feature Importance")
        ax.set_xlabel("Relative importance")
        fig.tight_layout()
        fig.savefig(REPORT_DIR / "feature_importance.png", dpi=150)
        plt.close(fig)

    # --- Save the model + everything ml_signals.py needs to use it correctly ---
    source_marker = DATA_DIR / ".source"
    data_source = source_marker.read_text().strip() if source_marker.exists() else "unknown"

    joblib.dump(best_model, MODEL_DIR / "model.pkl")
    metadata = {
        "model_name": best_name,
        "feature_columns": FEATURE_COLUMNS,
        "label_map": {"1": "BUY", "0": "HOLD", "-1": "SELL"},
        "test_accuracy": round(float(best_acc), 4),
        "test_f1_macro": round(float(best["f1_macro"]), 4),
        "all_models_compared": summary.reset_index().rename(columns={"index": "model"}).to_dict("records"),
        "trained_rows": len(train_df),
        "symbols": sorted(df["symbol"].unique().tolist()),
        "data_source": data_source,
    }
    with open(MODEL_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nSaved model -> models/model.pkl")
    print(f"Saved plots -> reports/model_comparison.png, confusion_matrix.png, feature_importance.png")
    if data_source.startswith("demo"):
        print("\n*** This model was trained on SYNTHETIC demo data. It's for learning the")
        print("*** pipeline only - do NOT use its live signals to trade real money.")
        print("*** Run fetch_data_fyers.py (or fetch_data.py) for real data, then re-run this script.")
    print("\nNext: python backtest.py")


def _wrap_fitted(fitted_inner_model):
    """RandomizedSearchCV already fit the inner model on 0..K-1 codes (we passed
    it pre-mapped labels above) - this just wraps the already-fitted estimator
    so its .predict()/.predict_proba() speak our -1/0/1 label language."""
    wrapper = RelabeledClassifier.__new__(RelabeledClassifier)
    wrapper.inner = fitted_inner_model
    return wrapper


if __name__ == "__main__":
    main()
