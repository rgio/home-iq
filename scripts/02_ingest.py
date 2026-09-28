#!/usr/bin/env python3
"""Create the schema, then load parcels (polygons + PresentUse) and cached
permits into Postgres/PostGIS."""

import logging

import pandas as pd

from homeiq_ingest.config import (
    KC_PARCEL_ATTRIBUTES_ZIP,
    KC_PARCEL_GEOJSON,
    PROCESSED_DIR,
    get_kc_assessor_raw_dir,
)
from homeiq_ingest.db import get_engine, init_schema
from homeiq_ingest.load import load_parcels, load_permits
from homeiq_ingest.parcels import build_parcels_table

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    engine = get_engine()
    init_schema(engine)
    print("Schema ready.")

    raw_dir = get_kc_assessor_raw_dir()
    parcels = build_parcels_table(
        geojson_path=raw_dir / KC_PARCEL_GEOJSON,
        parcel_zip_path=raw_dir / KC_PARCEL_ATTRIBUTES_ZIP,
    )
    load_parcels(engine, parcels)
    print(f"Loaded {len(parcels)} parcels.")

    permits_path = PROCESSED_DIR / "permits.parquet"
    permits = pd.read_parquet(permits_path)
    load_permits(engine, permits)
    print(f"Loaded {len(permits)} permits.")


if __name__ == "__main__":
    main()
