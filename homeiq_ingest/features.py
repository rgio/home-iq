"""Feature engineering for the cost model (spec §6).

Two documented gaps vs. the spec, both because the underlying data needs
something this environment doesn't have:

- `zip5` stands in for Census tract. True tract requires a spatial join
  against Census TIGER geometry; ACS tract-level median income (also spec
  §6) requires a CENSUS_API_KEY the keyless ACS endpoint no longer accepts
  (confirmed: it now redirects to a "Missing Key" page). ZIP is a coarser
  but directionally similar neighborhood control.
- The Zonda-calibrated bias multiplier for permit-valuation
  under-reporting (spec §6's "self-reported... systematically understated")
  is left as an explicit, visibly-named constant at 1.0 — seeing real
  Zonda Cost vs. Value figures requires manual registration this pipeline
  can't do on its own. See ZONDA_CALIBRATION_MULTIPLIER below.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from homeiq_ingest.config import KC_PARCEL_ATTRIBUTES_ZIP, KC_RESBLDG_ZIP, get_kc_assessor_raw_dir
from homeiq_ingest.fred import build_deflator, fetch_annual_index
from homeiq_ingest.parcels import load_lot_sqft
from homeiq_ingest.resbldg import load_building_characteristics
from homeiq_ingest.review_path import derive_review_path

# Real Zonda Cost vs. Value figures would calibrate this per project class
# (spec §6). Left at 1.0 (no adjustment) rather than invented, and named so
# it can't be silently forgotten once real data is available.
ZONDA_CALIBRATION_MULTIPLIER = 1.0

FEATURE_COLUMNS = [
    "sqft_living", "sqft_lot", "yr_built", "bldg_grade", "condition",
    "bedrooms", "bath_full_count", "permit_year", "zip5", "review_path",
    "layout_change", "structural", "plumbing_moved", "sqft_added",
    "had_prior_permit",
]

# Categorical (non-numeric, non-boolean) columns among FEATURE_COLUMNS —
# cost_model needs this to configure HistGradientBoostingRegressor correctly.
CATEGORICAL_FEATURE_COLUMNS = ["zip5", "review_path"]


def build_training_frame(engine: Engine, classification_source: str = "rules") -> pd.DataFrame:
    matched = pd.read_sql(
        """
        SELECT m.permitnum, m.pin, m.major, m.minor,
               p.estprojectcost, p.applieddate, p.originalzip, p.description
        FROM permit_pin_match m
        JOIN permits p ON p.permitnum = m.permitnum
        WHERE p.estprojectcost IS NOT NULL AND p.estprojectcost > 0
          AND p.applieddate IS NOT NULL
        """,
        engine,
    )
    classifications = pd.read_sql(
        text("SELECT permitnum, project_class, scope_flags FROM permit_classification WHERE source = :source"),
        engine,
        params={"source": classification_source},
    )
    df = matched.merge(classifications, on="permitnum", how="inner")

    raw_dir = get_kc_assessor_raw_dir()
    chars = load_building_characteristics(raw_dir / KC_RESBLDG_ZIP)
    lots = load_lot_sqft(raw_dir / KC_PARCEL_ATTRIBUTES_ZIP)
    df = df.merge(chars, on=["major", "minor"], how="left")
    df = df.merge(lots, on=["major", "minor"], how="left")

    df["applieddate"] = pd.to_datetime(df["applieddate"])
    df["permit_year"] = df["applieddate"].dt.year
    df["zip5"] = df["originalzip"].astype(str).str[:5]
    df["review_path"] = df["description"].apply(derive_review_path)

    for flag in ("layout_change", "structural", "plumbing_moved", "sqft_added"):
        df[flag] = df["scope_flags"].apply(lambda flags, f=flag: f in (flags or []))

    prior_counts = (
        df.sort_values("applieddate")
        .groupby("pin")
        .cumcount()
    )
    df["had_prior_permit"] = prior_counts > 0

    annual_index = fetch_annual_index()
    deflator = build_deflator(annual_index, target_year=annual_index.index.max())
    df["deflator"] = df["permit_year"].map(deflator).fillna(1.0)
    df["cost_deflated"] = df["estprojectcost"] * df["deflator"] * ZONDA_CALIBRATION_MULTIPLIER

    return df
