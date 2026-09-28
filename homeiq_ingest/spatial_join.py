"""The core of P0 (spec §4): resolve each permit to exactly one PIN.

Stage 1 — spatial: point-in-polygon of the permit's lat/long against parcel
polygons. A permit is quarantined rather than guessed at when:
  - it has no usable geocode at all                    -> missing_geocode
  - the point lands in zero polygons (street centerline
    geocode falling in the right-of-way)                -> no_polygon_match
  - the point lands in more than one distinct PIN's
    polygon — condo/townhouse buildings where one
    footprint maps to many PINs, or a parcel that was
    split/merged since the permit date                  -> multiple_polygon_match

Stage 2 — address fallback: permits quarantined for missing_geocode or
no_polygon_match (genuinely single-PIN-resolvable, just not spatially) get a
second chance via fuzzy address matching within ZIP. multiple_polygon_match
is not retried here — a closer address match doesn't resolve which of
several legitimate PINs is correct, so guessing would be worse than
quarantining.

Note on condos specifically: EXTR_Parcel.PresentUse looked like a promising
way to give the condo case its own, more specific quarantine reason, but its
code-to-meaning mapping couldn't be verified against authoritative KC
documentation (see README) and an empirical check of this GIS layer showed
condo buildings are overwhelmingly represented as a single polygon per
building, not one polygon per unit PIN — so a present_use-based rule
wouldn't actually catch most condo permits anyway. multiple_polygon_match
catches the cases where the layer does expose more than one PIN at a point;
the remainder resolve to a single (likely building- or land-level) PIN via
ordinary spatial match. Distinguishing unit-level condo PINs properly would
need a dedicated condo-unit source, which is out of scope for P0.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd

from homeiq_ingest.address_match import ZipAddressIndex, normalize_address

MISSING_GEOCODE = "missing_geocode"
NO_POLYGON_MATCH = "no_polygon_match"
MULTIPLE_POLYGON_MATCH = "multiple_polygon_match"

# Reasons eligible for the address-fallback retry — see module docstring.
_FALLBACK_ELIGIBLE = {MISSING_GEOCODE, NO_POLYGON_MATCH}

ADDRESS_FUZZY_THRESHOLD = 90.0


@dataclass
class JoinResult:
    matches: pd.DataFrame       # permitnum, pin, major, minor, match_method, match_score
    quarantine: pd.DataFrame    # permitnum, reason, detail


def _spatial_stage(permits: pd.DataFrame, parcels: gpd.GeoDataFrame) -> tuple[list[dict], list[dict]]:
    matches: list[dict] = []
    quarantine: list[dict] = []

    geocoded = permits[permits["has_geocode"]]
    missing = permits[~permits["has_geocode"]]
    for permitnum in missing["permitnum"]:
        quarantine.append({"permitnum": permitnum, "reason": MISSING_GEOCODE, "detail": None})

    if geocoded.empty:
        return matches, quarantine

    points = gpd.GeoDataFrame(
        geocoded[["permitnum"]].reset_index(drop=True),
        geometry=gpd.points_from_xy(geocoded["longitude"], geocoded["latitude"]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        points,
        parcels[["pin", "major", "minor", "present_use", "geometry"]],
        predicate="within",
        how="left",
    )

    for permitnum, group in joined.groupby("permitnum", sort=False):
        hits = group.dropna(subset=["pin"])
        if hits.empty:
            quarantine.append({"permitnum": permitnum, "reason": NO_POLYGON_MATCH, "detail": None})
            continue
        pins = hits["pin"].unique()
        if len(pins) > 1:
            quarantine.append({
                "permitnum": permitnum, "reason": MULTIPLE_POLYGON_MATCH,
                "detail": ",".join(map(str, pins)),
            })
            continue
        row = hits.iloc[0]
        matches.append({
            "permitnum": permitnum, "pin": row["pin"], "major": row["major"], "minor": row["minor"],
            "match_method": "spatial", "match_score": None,
        })

    return matches, quarantine


def _address_fallback_stage(
    permits: pd.DataFrame, quarantine: list[dict], address_index: ZipAddressIndex,
    threshold: float = ADDRESS_FUZZY_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    permits_by_num = permits.set_index("permitnum")
    still_quarantined: list[dict] = []
    recovered: list[dict] = []

    for entry in quarantine:
        if entry["reason"] not in _FALLBACK_ELIGIBLE:
            still_quarantined.append(entry)
            continue
        permit = permits_by_num.loc[entry["permitnum"]]
        query = normalize_address(str(permit.get("originaladdress1", "") or ""))
        zip5 = str(permit.get("originalzip", "") or "").strip()[:5]
        match = address_index.match(query, zip5, threshold=threshold)
        if match is None:
            still_quarantined.append(entry)
            continue
        recovered.append({
            "permitnum": entry["permitnum"], "pin": f"{match.major}{match.minor}",
            "major": match.major, "minor": match.minor,
            "match_method": "address_fuzzy", "match_score": match.score,
        })

    return recovered, still_quarantined


def run_join(
    permits: pd.DataFrame, parcels: gpd.GeoDataFrame, address_index: ZipAddressIndex,
    address_threshold: float = ADDRESS_FUZZY_THRESHOLD,
) -> JoinResult:
    spatial_matches, quarantine = _spatial_stage(permits, parcels)
    fallback_matches, final_quarantine = _address_fallback_stage(
        permits, quarantine, address_index, threshold=address_threshold
    )

    matches_df = pd.DataFrame(
        spatial_matches + fallback_matches,
        columns=["permitnum", "pin", "major", "minor", "match_method", "match_score"],
    )
    quarantine_df = pd.DataFrame(final_quarantine, columns=["permitnum", "reason", "detail"])
    return JoinResult(matches=matches_df, quarantine=quarantine_df)
