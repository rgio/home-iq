"""Load KC parcel polygons + PresentUse attribute from the bulk extracts
already on disk (spec §3: KC GIS parcel polygons, EXTR_Parcel).

PresentUse is carried through as raw, uninterpreted data. Its code-to-meaning
mapping could not be verified against authoritative KC documentation — an
EXTR_LookUp table exists but its LUType grouping doesn't line up cleanly
with this column (see README for the two wrong guesses this ruled out), and
the values' real-world distribution didn't match either candidate mapping.
Left as an opaque integer here rather than risk a third unverified
interpretation; a future phase that needs it (e.g. P1's grade/condition
features) should resolve the real mapping from KC's official schema docs
first.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from homeiq_ingest.config import SEATTLE_BBOX

logger = logging.getLogger(__name__)


def load_parcel_geometries(geojson_path: Path, bbox: tuple = SEATTLE_BBOX) -> gpd.GeoDataFrame:
    """Bbox-filtered read so we don't pull all ~600k King County parcels into
    memory for a Seattle-only permit set. Geometry is already EPSG:4326,
    matching the permit lat/long directly — no reprojection needed."""
    gdf = gpd.read_file(geojson_path, bbox=bbox)
    gdf = gdf.rename(columns={"MAJOR": "major", "MINOR": "minor", "PIN": "pin"})
    gdf = gdf[["pin", "major", "minor", "geometry"]].copy()
    gdf["pin"] = gdf["pin"].astype(str)
    gdf["major"] = gdf["major"].astype(str)
    gdf["minor"] = gdf["minor"].astype(str)
    gdf = gdf.drop_duplicates(subset=["pin"])
    logger.info("Loaded %d parcel polygons within Seattle bbox", len(gdf))
    return gdf.set_crs(epsg=4326, allow_override=True)


def load_present_use(parcel_zip_path: Path) -> pd.DataFrame:
    """EXTR_Parcel.csv is ~250MB; only pull the three columns we need."""
    with zipfile.ZipFile(parcel_zip_path) as zf:
        inner_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(inner_name) as f:
            df = pd.read_csv(
                f,
                usecols=["Major", "Minor", "PresentUse"],
                dtype={"Major": str, "Minor": str, "PresentUse": "Int64"},
                encoding="latin-1",
            )
    df = df.rename(columns={"Major": "major", "Minor": "minor", "PresentUse": "present_use"})
    return df


def load_lot_sqft(parcel_zip_path: Path) -> pd.DataFrame:
    """SqFtLot for the cost model's lot-size feature (spec §6). Separate
    from load_present_use to avoid changing the already-loaded `parcels`
    table's shape — read fresh from the raw extract when needed."""
    with zipfile.ZipFile(parcel_zip_path) as zf:
        inner_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(inner_name) as f:
            df = pd.read_csv(
                f,
                usecols=["Major", "Minor", "SqFtLot"],
                dtype={"Major": str, "Minor": str},
                encoding="latin-1",
            )
    df["SqFtLot"] = pd.to_numeric(df["SqFtLot"], errors="coerce")
    return df.rename(columns={"Major": "major", "Minor": "minor", "SqFtLot": "sqft_lot"})


def build_parcels_table(geojson_path: Path, parcel_zip_path: Path, bbox: tuple = SEATTLE_BBOX) -> gpd.GeoDataFrame:
    geoms = load_parcel_geometries(geojson_path, bbox=bbox)
    present_use = load_present_use(parcel_zip_path)
    merged = geoms.merge(present_use, on=["major", "minor"], how="left")
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=geoms.crs)
    n_unmatched = merged["present_use"].isna().sum()
    logger.info("%d/%d parcels in bbox have no PresentUse match in EXTR_Parcel", n_unmatched, len(merged))
    return merged
