"""Load situs address components from EXTR_ResBldg.csv, used to build the
ZIP-indexed fallback address matcher (spec §4's fuzzy-match fallback)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from homeiq_ingest.address_match import ZipAddressIndex, build_resbldg_address

_COLUMNS = [
    "Major", "Minor", "BuildingNumber", "DirectionPrefix",
    "StreetName", "StreetType", "DirectionSuffix", "ZipCode",
]


def load_resbldg_addresses(resbldg_zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(resbldg_zip_path) as zf:
        inner_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(inner_name) as f:
            df = pd.read_csv(f, usecols=_COLUMNS, dtype=str, encoding="latin-1")
    df = df.fillna("")
    df["major"] = df["Major"].str.strip()
    df["minor"] = df["Minor"].str.strip()
    df["zip5"] = df["ZipCode"].str.strip().str[:5]
    df["normalized_address"] = df.apply(
        lambda r: build_resbldg_address(
            r["BuildingNumber"], r["DirectionPrefix"], r["StreetName"],
            r["StreetType"], r["DirectionSuffix"],
        ),
        axis=1,
    )
    return df[["major", "minor", "zip5", "normalized_address"]]


_CHARACTERISTIC_COLUMNS = [
    "Major", "Minor", "SqFtTotLiving", "YrBuilt", "YrRenovated",
    "BldgGrade", "Condition", "Bedrooms", "BathFullCount",
]


def load_building_characteristics(resbldg_zip_path: Path) -> pd.DataFrame:
    """Building-level features for the cost model (spec §6): sqft, year
    built, assessor grade/condition. One row per Major+Minor+BldgNbr in the
    source; multi-building parcels (rare for single-family) collapse to the
    largest structure by living area."""
    with zipfile.ZipFile(resbldg_zip_path) as zf:
        inner_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(inner_name) as f:
            df = pd.read_csv(
                f, usecols=_CHARACTERISTIC_COLUMNS, dtype=str, encoding="latin-1",
            )
    df["major"] = df["Major"].str.strip()
    df["minor"] = df["Minor"].str.strip()
    for col in ("SqFtTotLiving", "YrBuilt", "YrRenovated", "BldgGrade", "Condition", "Bedrooms", "BathFullCount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("SqFtTotLiving", ascending=False).drop_duplicates(subset=["major", "minor"])
    return df[["major", "minor", "SqFtTotLiving", "YrBuilt", "YrRenovated", "BldgGrade", "Condition", "Bedrooms", "BathFullCount"]].rename(
        columns={
            "SqFtTotLiving": "sqft_living", "YrBuilt": "yr_built", "YrRenovated": "yr_renovated",
            "BldgGrade": "bldg_grade", "Condition": "condition",
            "Bedrooms": "bedrooms", "BathFullCount": "bath_full_count",
        }
    )


def build_zip_address_index(
    resbldg_zip_path: Path, valid_pins: set[str] | None = None,
) -> ZipAddressIndex:
    """`valid_pins` (major+minor, matching parcels.pin) restricts candidates
    to PINs we actually have parcel geometry for. EXTR_ResBldg covers all of
    King County, not just Seattle, so without this filter the fallback can
    "match" a permit to a PIN with no row in the `parcels` table — a real
    bug caught by the foreign key on `permit_pin_match.pin` rejecting it
    outright rather than silently accepting an out-of-bbox resolution."""
    df = load_resbldg_addresses(resbldg_zip_path)
    if valid_pins is not None:
        df = df[(df["major"] + df["minor"]).isin(valid_pins)]
    index = ZipAddressIndex()
    for row in df.itertuples(index=False):
        index.add(row.zip5, row.major, row.minor, row.normalized_address)
    return index
