"""Pull Seattle SDCI Building Permits (Socrata dataset 76t5-zqzr) via the
SODA API. Free, no key required at this volume (~60k rows), but an app token
raises the throttling ceiling — see homeiq_ingest.config."""

from __future__ import annotations

import logging

import pandas as pd
import requests
from tqdm import tqdm

from homeiq_ingest.config import (
    SOCRATA_PERMITS_DATASET,
    SOCRATA_PERMITS_DOMAIN,
    get_socrata_app_token,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 5000

SELECT_FIELDS = [
    "permitnum",
    "permitclass",
    "permitclassmapped",
    "permittypemapped",
    "permittypedesc",
    "description",
    "estprojectcost",
    "applieddate",
    "issueddate",
    "expiresdate",
    "completeddate",
    "statuscurrent",
    "originaladdress1",
    "originalzip",
    "latitude",
    "longitude",
]


def _base_url() -> str:
    return f"https://{SOCRATA_PERMITS_DOMAIN}/resource/{SOCRATA_PERMITS_DATASET}.json"


def fetch_residential_permits(since: str = "2015-01-01", page_size: int = PAGE_SIZE) -> pd.DataFrame:
    """Fetch all residential permits applied for on/after `since`. Paginates
    with $limit/$offset since Socrata caps a single response."""
    headers = {}
    token = get_socrata_app_token()
    if token:
        headers["X-App-Token"] = token

    where = f"permitclassmapped='Residential' AND applieddate >= '{since}T00:00:00.000'"
    count_resp = requests.get(
        _base_url(),
        params={"$select": "count(*)", "$where": where},
        headers=headers,
        timeout=30,
    )
    count_resp.raise_for_status()
    total = int(count_resp.json()[0]["count"])
    logger.info("Fetching %d residential permits since %s", total, since)

    rows: list[dict] = []
    offset = 0
    with tqdm(total=total, desc="fetching permits") as pbar:
        while True:
            resp = requests.get(
                _base_url(),
                params={
                    "$select": ",".join(SELECT_FIELDS),
                    "$where": where,
                    "$order": "permitnum",
                    "$limit": page_size,
                    "$offset": offset,
                },
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            rows.extend(batch)
            pbar.update(len(batch))
            offset += page_size
            if len(batch) < page_size:
                break

    df = pd.DataFrame(rows)
    return _clean_permits(df)


def _clean_permits(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for col in ("applieddate", "issueddate", "expiresdate", "completeddate"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    if "estprojectcost" in df.columns:
        df["estprojectcost"] = pd.to_numeric(df["estprojectcost"], errors="coerce")
    for col in ("latitude", "longitude"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["has_geocode"] = df["latitude"].notna() & df["longitude"].notna()
    df = df.drop_duplicates(subset=["permitnum"])
    return df.reset_index(drop=True)
