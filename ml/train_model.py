"""
Train the no-show classifier on a hybrid dataset of synthetic and real bookings.

This script uses sample weighting to prioritize learning from real patient data
while using synthetic data as a baseline. It evaluates the model separately
on synthetic and real test partitions.

Usage:
    python ml/train_model.py
"""

import os
import sys
import django

# Bootstrap Django
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookingcore.settings")
django.setup()

from datetime import datetime
import joblib
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix,
)

from bookings.models import Booking
from ml.features import build_feature_rows, feature_columns

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trained_models")
MODEL_PATH = os.path.join(MODEL_DIR, "no_show_model.joblib")
METADATA_PATH = os.path.join(MODEL_DIR, "model_metadata.joblib")
REPORT_PATH = os.path.join(PROJECT_ROOT, "ml", "training_report.md")


def load_training_data():
    """Pull resolved (completed/no_show) bookings from all tenants."""
    bookings = Booking.objects.filter(
        status__in=[Booking.Status.COMPLETED, Booking.Status.NO_SHOW],
    )
    return bookings


def calibration_check(y_test, y_proba, n_bins=5):
    """Bucket predictions into bins by predicted probability and calculate no-show rates."""
    if len(y_test) == 0:
        return pd.DataFrame()
    df = pd.DataFrame({"actual": y_test, "predicted_proba": y_proba})
    df["bin"] = pd.cut(df["predicted_proba"], bins=n_bins, include_lowest=True)
    grouped = df.groupby("bin", observed=True).agg(
        avg_predicted=("predicted_proba", "mean"),
        actual_rate=("actual", "mean"),
        count=("actual", "size"),
    )
    return grouped


def get_metrics(y_true, y_pred, y_proba):
    """Compute standard classifier metrics safely."""
    if len(y_true) == 0:
        return None
    try:
        auc = roc_auc_score(y_true, y_proba)
    except ValueError:
        auc = float('nan')
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "roc_auc": auc,
    }


def main():
    print("Loading training data from database...")
    bookings_qs = load_training_data()
    total_count = bookings_qs.count()
    print(f"  Found {total_count} resolved bookings in total.")
    
    if total_count < 20:
        print("  WARNING: Not enough resolved bookings to train a model.")
        return

    print("Building feature matrix (chronological, leakage-safe)...")
    df = build_feature_rows(bookings_qs)
    
    num_synthetic = int(df["is_synthetic"].sum())
    num_real = len(df) - num_synthetic
    print(f"  Dataset composition: {num_synthetic} synthetic, {num_real} real.")

    feature_cols = feature_columns()
    X = df[feature_cols]
    y = df["no_show"]

    print("Splitting into train/test sets (80/20, stratified)...")
    train_df, test_df = train_test_split(
        df, test_size=0.2, random_state=42, stratify=df["no_show"]
    )

    X_train = train_df[feature_cols]
    y_train = train_df["no_show"]
    X_test = test_df[feature_cols]
    y_test = test_df["no_show"]

    # Assign sample weights: prioritize real data (weight=10.0) over synthetic (weight=1.0)
    # This prevents the synthetic data from overriding real customer behavior patterns.
    sample_weights = train_df["is_synthetic"].apply(lambda is_syn: 1.0 if is_syn == 1 else 10.0).values

    print(f"Training logistic regression with weighted samples (Real Weight = 10x)...")
    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train, sample_weight=sample_weights)

    print("Evaluating model...")
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    # Overall metrics
    overall_metrics = get_metrics(y_test, y_pred, y_proba)
    cm = confusion_matrix(y_test, y_pred)
    calibration = calibration_check(y_test.values, y_proba)

    # Separate evaluations
    test_df = test_df.copy()
    test_df["pred"] = y_pred
    test_df["proba"] = y_proba

    syn_test_df = test_df[test_df["is_synthetic"] == 1]
    real_test_df = test_df[test_df["is_synthetic"] == 0]

    synthetic_metrics = get_metrics(syn_test_df["no_show"], syn_test_df["pred"], syn_test_df["proba"])
    real_metrics = get_metrics(real_test_df["no_show"], real_test_df["pred"], real_test_df["proba"])

    feature_importance = sorted(
        zip(feature_cols, model.coef_[0]), key=lambda x: abs(x[1]), reverse=True
    )

    # --- Print to console ---
    print("\n=== EVALUATION METRICS (Overall held-out test set) ===")
    for name, value in overall_metrics.items():
        print(f"  {name:10s}: {value:.3f}")
    
    if synthetic_metrics:
        print(f"\n=== SYNTHETIC SEGMENT METRICS (n={len(syn_test_df)}) ===")
        for name, value in synthetic_metrics.items():
            print(f"  {name:10s}: {value:.3f}")

    if real_metrics:
        print(f"\n=== REAL CUSTOMER SEGMENT METRICS (n={len(real_test_df)}) ===")
        for name, value in real_metrics.items():
            print(f"  {name:10s}: {value:.3f}")
    else:
        print("\n=== REAL CUSTOMER SEGMENT METRICS ===")
        print("  No real customer bookings present in the test set yet.")

    print("\n  Confusion matrix (rows=actual, cols=predicted) [completed, no_show]:")
    print(f"    {cm}")
    print("\n  Feature importance (coefficients, sorted by magnitude):")
    for name, coef in feature_importance:
        direction = "raises" if coef > 0 else "lowers"
        print(f"    {name:25s} {coef:+.3f}  ({direction} no-show risk)")

    # --- Save model + metadata ---
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    joblib.dump({
        "feature_columns": feature_cols,
        "trained_at": datetime.now().isoformat(),
        "training_rows": len(df),
        "synthetic_rows": num_synthetic,
        "real_rows": num_real,
        "metrics": overall_metrics,
    }, METADATA_PATH)
    print(f"\nModel saved to {MODEL_PATH}")
    print(f"Metadata saved to {METADATA_PATH}")

    # --- Save human-readable report ---
    with open(REPORT_PATH, "w") as f:
        f.write("# No-Show Model — Training Report\n\n")
        f.write(f"Trained: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"Training data: {len(df)} resolved bookings ({num_synthetic} synthetic, {num_real} real)\n\n")
        
        f.write("## Segmented Evaluation Metrics\n\n")
        f.write("| Metric | Overall Test Set | Synthetic Segment | Real Customer Segment |\n")
        f.write("|---|---|---|---|\n")
        for name in overall_metrics.keys():
            overall_val = f"{overall_metrics[name]:.3f}"
            syn_val = f"{synthetic_metrics[name]:.3f}" if synthetic_metrics else "N/A"
            real_val = f"{real_metrics[name]:.3f}" if real_metrics else "N/A"
            f.write(f"| {name} | {overall_val} | {syn_val} | {real_val} |\n")
            
        f.write("\n## Feature importance\n\n")
        f.write("| Feature | Coefficient | Effect |\n|---|---|---|\n")
        for name, coef in feature_importance:
            direction = "raises risk" if coef > 0 else "lowers risk"
            f.write(f"| {name} | {coef:+.3f} | {direction} |\n")
        
        if not calibration.empty:
            f.write("\n## Calibration (predicted risk bucket vs actual outcome rate)\n\n")
            f.write(calibration.to_markdown())
            
        f.write("\n\n*Generated automatically by ml/train_model.py — do not edit by hand.*\n")
    print(f"Training report saved to {REPORT_PATH}")


if __name__ == "__main__":
    main()