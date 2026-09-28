#!/usr/bin/env python3
"""Fit the quantile cost model (spec §6) per project class and report
pinball loss + P25-P75 coverage on the temporal holdout. Persists fitted
models to data/processed/cost_models.joblib for the CLI (08_estimate_cost.py)."""

import joblib

from homeiq_ingest.config import PROCESSED_DIR
from homeiq_ingest.cost_model import MIN_SAMPLE_SIZE, fit_all_class_models
from homeiq_ingest.db import get_engine
from homeiq_ingest.features import build_training_frame
from homeiq_ingest.taxonomy import PROJECT_CLASSES


def main() -> None:
    engine = get_engine()
    training_df = build_training_frame(engine, classification_source="rules")
    print(f"Training frame: {len(training_df)} classified, cost-matched permits.")

    class_counts = training_df["project_class"].value_counts()
    print("\nPer-class sample sizes:")
    for cls in PROJECT_CLASSES:
        n = class_counts.get(cls, 0)
        flag = "" if n >= MIN_SAMPLE_SIZE else "  <- below min_n, falls back to benchmark (unavailable, see README)"
        print(f"  {cls:<20} {n:>6}{flag}")

    models = fit_all_class_models(training_df)

    print(f"\nFit {len(models)}/{len(PROJECT_CLASSES)} classes with enough data:")
    print(f"{'class':<20} {'n_train':>8} {'n_test':>7} {'pinball@50':>11} {'p25-p75 coverage':>18}")
    for cls, m in models.items():
        pb50 = m.pinball_loss.get(0.5)
        cov = m.coverage
        print(
            f"{cls:<20} {m.n_train:>8} {m.n_test:>7} "
            f"{f'{pb50:,.0f}' if pb50 is not None else 'n/a':>11} "
            f"{f'{cov:.1%}' if cov is not None else 'n/a (no 2024+ data)':>18}"
        )

    out_path = PROCESSED_DIR / "cost_models.joblib"
    joblib.dump(models, out_path)
    print(f"\nSaved models to {out_path}")
    print(
        "\nNote: coverage target per spec is 45-55% (P25-P75 should contain ~50% of "
        "held-out actuals). Classes fit on the 'rules' classifier's noisy labels — "
        "re-fit against 'llm' classifications once available for a cleaner signal."
    )


if __name__ == "__main__":
    main()
