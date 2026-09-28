#!/usr/bin/env python3
"""Run the permit -> PIN spatial join (spec §4) against what's already in
Postgres, and persist matches + quarantine. Safe to re-run: truncates the
join output tables first without touching permits/parcels."""

import json
import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from homeiq_ingest.config import KC_RESBLDG_ZIP, get_kc_assessor_raw_dir
from homeiq_ingest.db import get_engine, reset_join_tables
from homeiq_ingest.load import load_matches, load_quarantine
from homeiq_ingest.resbldg import build_zip_address_index
from homeiq_ingest.spatial_join import run_join

logging.basicConfig(level=logging.INFO, format="%(message)s")


def _load_parcels(engine) -> gpd.GeoDataFrame:
    df = pd.read_sql("SELECT pin, major, minor, present_use, geom_geojson FROM parcels", engine)
    geometry = df["geom_geojson"].apply(lambda g: shape(json.loads(g)))
    return gpd.GeoDataFrame(
        df[["pin", "major", "minor", "present_use"]], geometry=geometry, crs="EPSG:4326"
    )


def main() -> None:
    engine = get_engine()
    reset_join_tables(engine)

    permits = pd.read_sql(
        "SELECT permitnum, latitude, longitude, has_geocode, originaladdress1, originalzip FROM permits",
        engine,
    )
    parcels = _load_parcels(engine)
    address_index = build_zip_address_index(
        get_kc_assessor_raw_dir() / KC_RESBLDG_ZIP, valid_pins=set(parcels["pin"])
    )

    result = run_join(permits, parcels, address_index)
    load_matches(engine, result.matches)
    load_quarantine(engine, result.quarantine)

    print(f"Matched: {len(result.matches)}  Quarantined: {len(result.quarantine)}")


if __name__ == "__main__":
    main()
