#!/usr/bin/env python3
"""P1 deliverable per spec §11: a CLI that takes a PIN and project class and
prints a cost range. Requires 07_fit_cost_model.py to have run first."""

import argparse

import joblib
import pandas as pd
from sqlalchemy import text

from homeiq_ingest.config import (
    KC_PARCEL_ATTRIBUTES_ZIP,
    KC_RESBLDG_ZIP,
    PROCESSED_DIR,
    get_kc_assessor_raw_dir,
)
from homeiq_ingest.cost_model import estimate
from homeiq_ingest.db import get_engine
from homeiq_ingest.parcels import load_lot_sqft
from homeiq_ingest.resbldg import load_building_characteristics
from homeiq_ingest.taxonomy import PROJECT_CLASSES


def get_pin_features(engine, pin: str) -> dict:
    major, minor = pin[:6], pin[6:]
    raw_dir = get_kc_assessor_raw_dir()

    chars = load_building_characteristics(raw_dir / KC_RESBLDG_ZIP)
    char_row = chars[(chars["major"] == major) & (chars["minor"] == minor)]

    lots = load_lot_sqft(raw_dir / KC_PARCEL_ATTRIBUTES_ZIP)
    lot_row = lots[(lots["major"] == major) & (lots["minor"] == minor)]

    zip5_df = pd.read_sql(
        text(
            "SELECT originalzip FROM permit_pin_match m JOIN permits p ON p.permitnum = m.permitnum "
            "WHERE m.pin = :pin LIMIT 1"
        ),
        engine, params={"pin": pin},
    )
    zip5 = zip5_df["originalzip"].iloc[0][:5] if not zip5_df.empty else None

    prior_count = pd.read_sql(
        text("SELECT count(*) AS n FROM permit_pin_match WHERE pin = :pin"), engine, params={"pin": pin},
    )["n"].iloc[0]

    features = {
        "sqft_living": char_row["sqft_living"].iloc[0] if not char_row.empty else None,
        "yr_built": char_row["yr_built"].iloc[0] if not char_row.empty else None,
        "bldg_grade": char_row["bldg_grade"].iloc[0] if not char_row.empty else None,
        "condition": char_row["condition"].iloc[0] if not char_row.empty else None,
        "bedrooms": char_row["bedrooms"].iloc[0] if not char_row.empty else None,
        "bath_full_count": char_row["bath_full_count"].iloc[0] if not char_row.empty else None,
        "sqft_lot": lot_row["sqft_lot"].iloc[0] if not lot_row.empty else None,
        "permit_year": pd.Timestamp.now().year,
        "zip5": zip5,
        # review_path is "unknown" here, not derived, because at prediction time no permit has
        # been filed yet — a prospective query can't know whether a future permit will be marked
        # STFI. It's only observable on already-filed permits (see homeiq_ingest.review_path).
        "review_path": "unknown",
        "layout_change": False, "structural": False, "plumbing_moved": False, "sqft_added": False,
        "had_prior_permit": prior_count > 0,
    }
    return features


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pin", required=True, help="10-digit PIN (major+minor, e.g. 3298700741)")
    parser.add_argument("--project-class", required=True, choices=PROJECT_CLASSES)
    args = parser.parse_args()

    models_path = PROCESSED_DIR / "cost_models.joblib"
    if not models_path.exists():
        raise SystemExit(f"{models_path} not found — run scripts/07_fit_cost_model.py first.")
    models = joblib.load(models_path)

    engine = get_engine()
    pin_features = get_pin_features(engine, args.pin)
    result = estimate(models.get(args.project_class), pin_features)

    print(f"PIN {args.pin} — {args.project_class}")
    if result.source == "insufficient_data":
        print(
            "  Insufficient comparable permits for a model-based estimate, and no Zonda "
            "benchmark data is loaded to fall back to (see README) — refusing to fabricate a number."
        )
        return
    print(f"  ${result.p25:,.0f} - ${result.p75:,.0f}   (median ${result.p50:,.0f})")
    print(f"  Derived from {result.n_comparables} comparable permits. source: {result.source}")


if __name__ == "__main__":
    main()
