"""Persist DataFrames produced by the ingestion + join pipeline into the
Postgres schema defined in homeiq_ingest.db."""

from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping
from sqlalchemy import Engine

from homeiq_ingest.privacy import strip_pii

_PERMIT_COLUMNS = [
    "permitnum", "permitclass", "permitclassmapped", "permittypemapped", "permittypedesc",
    "description", "estprojectcost", "applieddate", "issueddate", "expiresdate",
    "completeddate", "statuscurrent", "originaladdress1", "originalzip",
    "latitude", "longitude", "has_geocode",
]


def load_permits(engine: Engine, permits: pd.DataFrame) -> None:
    permits = strip_pii(permits)
    cols = [c for c in _PERMIT_COLUMNS if c in permits.columns]
    permits[cols].to_sql("permits", engine, if_exists="append", index=False, method="multi", chunksize=1000)


def load_parcels(engine: Engine, parcels: gpd.GeoDataFrame) -> None:
    df = pd.DataFrame({
        "pin": parcels["pin"],
        "major": parcels["major"],
        "minor": parcels["minor"],
        "present_use": parcels["present_use"],
        "geom_geojson": parcels["geometry"].apply(lambda g: json.dumps(mapping(g))),
    })
    df.to_sql("parcels", engine, if_exists="append", index=False, method="multi", chunksize=1000)


def load_matches(engine: Engine, matches: pd.DataFrame) -> None:
    if matches.empty:
        return
    matches.to_sql("permit_pin_match", engine, if_exists="append", index=False, method="multi", chunksize=1000)


def load_quarantine(engine: Engine, quarantine: pd.DataFrame) -> None:
    if quarantine.empty:
        return
    quarantine.to_sql("permit_quarantine", engine, if_exists="append", index=False, method="multi", chunksize=1000)
