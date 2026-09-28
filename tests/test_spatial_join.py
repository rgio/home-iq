import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from homeiq_ingest.address_match import ZipAddressIndex
from homeiq_ingest.spatial_join import (
    MISSING_GEOCODE,
    MULTIPLE_POLYGON_MATCH,
    NO_POLYGON_MATCH,
    run_join,
)


def _square(cx: float, cy: float, half: float = 0.001) -> Polygon:
    return Polygon([
        (cx - half, cy - half), (cx + half, cy - half),
        (cx + half, cy + half), (cx - half, cy + half),
    ])


def _make_parcels() -> gpd.GeoDataFrame:
    rows = [
        # Ordinary single-family parcel.
        {"pin": "AAA0000000", "major": "AAA000", "minor": "0000", "present_use": 11,
         "geometry": _square(-122.300, 47.600)},
        # Condo-style building: two unit PINs sharing (approximately) the same
        # footprint. Handled the same as a split parcel — see spatial_join's
        # docstring on why this isn't distinguished by present_use.
        {"pin": "CONDO0001", "major": "CONDO0", "minor": "0001", "present_use": 14,
         "geometry": _square(-122.310, 47.610)},
        {"pin": "CONDO0002", "major": "CONDO0", "minor": "0002", "present_use": 14,
         "geometry": _square(-122.310, 47.610)},
        # Split parcel: two overlapping non-condo polygons at the same spot.
        {"pin": "SPLIT0001", "major": "SPLIT0", "minor": "0001", "present_use": 11,
         "geometry": _square(-122.320, 47.620)},
        {"pin": "SPLIT0002", "major": "SPLIT0", "minor": "0002", "present_use": 11,
         "geometry": _square(-122.320, 47.620)},
    ]
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs="EPSG:4326")


def _make_permits() -> pd.DataFrame:
    rows = [
        # Clean spatial hit.
        {"permitnum": "P1", "latitude": 47.600, "longitude": -122.300, "has_geocode": True,
         "originaladdress1": "1 MAIN ST", "originalzip": "98100"},
        # Geocoded to the right-of-way — no polygon contains it — but the
        # address matches a known parcel, so the fallback should recover it.
        {"permitnum": "P2", "latitude": 47.699, "longitude": -122.399, "has_geocode": True,
         "originaladdress1": "2 MAIN ST", "originalzip": "98100"},
        # No geocode at all, and no address match either.
        {"permitnum": "P3", "latitude": None, "longitude": None, "has_geocode": False,
         "originaladdress1": "999 NOWHERE LN", "originalzip": "98199"},
        # Lands inside the condo footprint.
        {"permitnum": "P4", "latitude": 47.610, "longitude": -122.310, "has_geocode": True,
         "originaladdress1": "4 CONDO WAY", "originalzip": "98100"},
        # Lands inside the overlapping split parcels.
        {"permitnum": "P5", "latitude": 47.620, "longitude": -122.320, "has_geocode": True,
         "originaladdress1": "5 SPLIT AVE", "originalzip": "98100"},
    ]
    return pd.DataFrame(rows)


def _make_address_index() -> ZipAddressIndex:
    index = ZipAddressIndex()
    index.add("98100", "AAA000", "0000", "2 MAIN ST")
    return index


def test_run_join_classifies_every_failure_mode():
    result = run_join(_make_permits(), _make_parcels(), _make_address_index())

    matched = dict(zip(result.matches["permitnum"], result.matches["pin"]))
    assert matched["P1"] == "AAA0000000"
    assert matched["P2"] == "AAA0000000"  # recovered via address fallback
    assert result.matches.loc[result.matches.permitnum == "P1", "match_method"].iloc[0] == "spatial"
    assert result.matches.loc[result.matches.permitnum == "P2", "match_method"].iloc[0] == "address_fuzzy"

    quarantine = dict(zip(result.quarantine["permitnum"], result.quarantine["reason"]))
    assert quarantine["P3"] == MISSING_GEOCODE
    assert quarantine["P4"] == MULTIPLE_POLYGON_MATCH  # condo-style shared footprint
    assert quarantine["P5"] == MULTIPLE_POLYGON_MATCH  # split parcel
    assert "P2" not in quarantine  # recovered, should not also be quarantined


def test_no_polygon_match_without_address_recovery_stays_quarantined():
    permits = pd.DataFrame([
        {"permitnum": "P6", "latitude": 47.699, "longitude": -122.399, "has_geocode": True,
         "originaladdress1": "UNMATCHABLE ADDR", "originalzip": "98100"},
    ])
    result = run_join(permits, _make_parcels(), _make_address_index())
    assert result.matches.empty
    assert result.quarantine.iloc[0]["reason"] == NO_POLYGON_MATCH
