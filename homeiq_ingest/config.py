"""Environment-driven configuration. Nothing here should require a live DB
or network connection to import."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
QUARANTINE_DIR = PROJECT_ROOT / "data" / "quarantine"

# Seattle city limits, generous bounding box (WGS84). Used to bbox-filter the
# county-wide parcel layer so we don't load parcels for the other 38 King
# County cities into memory.
SEATTLE_BBOX = (-122.4596, 47.4810, -122.2244, 47.7341)  # (minx, miny, maxx, maxy)

SOCRATA_PERMITS_DOMAIN = "data.seattle.gov"
SOCRATA_PERMITS_DATASET = "76t5-zqzr"

# Columns that must never leave the ingestion layer per the licensing note in
# spec §3 (RCW 42.56.070(9) — no lists of individuals for commercial use).
PII_COLUMNS = {"SellerName", "BuyerName", "buyername", "sellername"}
ADDRESS_COLUMNS = {"originaladdress1", "Address", "situs_address"}


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    name: str
    user: str
    password: str

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


def get_db_config() -> DBConfig:
    return DBConfig(
        host=os.environ.get("HOMEIQ_DB_HOST", "localhost"),
        port=int(os.environ.get("HOMEIQ_DB_PORT", "5432")),
        name=os.environ.get("HOMEIQ_DB_NAME", "homeiq"),
        user=os.environ.get("HOMEIQ_DB_USER", "homeiq"),
        password=os.environ.get("HOMEIQ_DB_PASSWORD", "homeiq"),
    )


def get_socrata_app_token() -> str | None:
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    return token or None


def get_kc_assessor_raw_dir() -> Path:
    configured = os.environ.get("KC_ASSESSOR_RAW_DIR", "").strip()
    return Path(configured) if configured else RAW_DIR / "kc_assessor"


# Restricts to permits that are actually a "renovate my existing home"
# question — the spec's own use case. Excludes:
#   - permitclass = 'Multifamily' — spec §1's non-goals explicitly exclude
#     commercial/multifamily property; this field separates it cleanly
#     (confirmed against real data, not a keyword guess).
#   - permittypedesc 'New'/'Demolition'/'Deconstruction'/'Relocation' —
#     brand-new construction and teardown are a different economic question
#     (raw build cost) than renovation ROI on an existing structure.
# Found by reading a sample of what the rules classifier was dumping into
# "other" — see README's taxonomy-validation section. Applied everywhere a
# query pulls permits for classification, labeling, or cost-model training.
IN_SCOPE_PERMIT_FILTER_SQL = (
    "permitclass = 'Single Family/Duplex' "
    "AND permittypedesc NOT IN ('New', 'Demolition', 'Deconstruction', 'Relocation')"
)

# Filenames as published by KC GIS / the Assessor's bulk extract, expected
# inside get_kc_assessor_raw_dir().
KC_PARCEL_GEOJSON = "king_county_parcel_area_05_21_26.geojson"
KC_PARCEL_ATTRIBUTES_ZIP = "Parcel.zip"
KC_RESBLDG_ZIP = "Residential Building.zip"
