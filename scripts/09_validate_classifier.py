#!/usr/bin/env python3
"""Score the classifier(s) against the filled-in hand-labeling sample
(spec §5's 300-permit F1 check). Run after data/processed/labeling_sample.csv
has human_project_class filled in for every row.

By default only validates the rules baseline (already in the CSV as
rules_predicted_class, free). Pass --with-llm to also classify the same 300
permits with the LLM backend and validate that too — this is the number the
spec actually cares about, but it calls the paid API.
"""

import argparse

import pandas as pd

from homeiq_ingest.config import PROCESSED_DIR
from homeiq_ingest.validate import format_validation, validate_classifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-llm", action="store_true", help="Also classify the sample with the LLM backend.")
    args = parser.parse_args()

    sample_path = PROCESSED_DIR / "labeling_sample.csv"
    df = pd.read_csv(sample_path)

    unlabeled = df["human_project_class"].isna() | (df["human_project_class"] == "")
    if unlabeled.any():
        print(f"Warning: {unlabeled.sum()}/{len(df)} rows have no human_project_class yet — "
              f"validating against the {len(df) - unlabeled.sum()} that are labeled.")

    print("=== Rules classifier ===")
    print(format_validation(validate_classifier(df, predicted_col="rules_predicted_class")))

    if args.with_llm:
        from homeiq_ingest.classify_llm import classify_batch
        llm_result = classify_batch(df[["permitnum", "description"]])
        df = df.merge(llm_result[["permitnum", "project_class"]], on="permitnum", how="left") \
                .rename(columns={"project_class": "llm_predicted_class"})
        print("\n=== LLM classifier ===")
        print(format_validation(validate_classifier(df, predicted_col="llm_predicted_class")))


if __name__ == "__main__":
    main()
