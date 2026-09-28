"""Deflate permit-year dollars to a constant year (spec §6) using a public
FRED series — no API key needed via the plain CSV export endpoint (FRED's
JSON API does require a free key; this sidesteps that entirely).

Series: WPUSI012011 — PPI, Inputs to Construction industries. This is the
named, visible deflator the spec asks for; it is NOT the Zonda-calibrated
bias-correction multiplier also required by spec §6 (permit valuations
running systematically below actual cost) — that one needs real Zonda Cost
vs. Value data, which requires manual registration this pipeline doesn't
have. See homeiq_ingest.cost_model for where that gap is surfaced rather
than papered over with an invented number.
"""

from __future__ import annotations

import pandas as pd
import requests

FRED_SERIES_ID = "WPUSI012011"
FRED_CSV_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_SERIES_ID}"


def fetch_annual_index() -> pd.Series:
    """Returns a Series indexed by year (int) -> annual average index value."""
    resp = requests.get(FRED_CSV_URL, timeout=30)
    resp.raise_for_status()
    from io import StringIO

    df = pd.read_csv(StringIO(resp.text), parse_dates=["observation_date"])
    df = df.rename(columns={FRED_SERIES_ID: "value"})
    df["year"] = df["observation_date"].dt.year
    annual = df.groupby("year")["value"].mean()
    return annual


def build_deflator(annual_index: pd.Series, target_year: int) -> dict[int, float]:
    """multiplier[y] * (nominal dollars in year y) = dollars in target_year terms."""
    base = annual_index.loc[target_year]
    return {int(year): float(base / value) for year, value in annual_index.items()}
