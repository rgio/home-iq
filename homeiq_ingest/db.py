"""Postgres connection and schema for the P0 permit-to-parcel join.

Tables:
  parcels             — one row per PIN, polygon geometry, from KC GIS
  permits             — one row per SDCI permit, lat/long from Socrata
  permit_pin_match    — successful joins, tagged with how they were resolved
  permit_quarantine   — permits that did not resolve to exactly one PIN,
                         with a reason, per spec §4's acceptance criterion

The spec calls for PostGIS. This runs against plain Postgres instead —
`brew install postgis` failed to build from source in this environment (see
README's "Why plain Postgres" section) — with parcel geometry stored as
GeoJSON text and rehydrated into a GeoDataFrame in Python. All of the actual
point-in-polygon logic already lives in `homeiq_ingest.spatial_join`
(GeoPandas/Shapely), never in SQL, so this only changes how geometry is
persisted, not how the join works. Swapping in a native `geometry` column
+ GIST index later is an isolated change to this file and `load.py`.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

from homeiq_ingest.config import DBConfig, get_db_config

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parcels (
    pin           TEXT PRIMARY KEY,
    major         TEXT NOT NULL,
    minor         TEXT NOT NULL,
    present_use   INTEGER,
    geom_geojson  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permits (
    permitnum         TEXT PRIMARY KEY,
    permitclass       TEXT,
    permitclassmapped TEXT,
    permittypemapped  TEXT,
    permittypedesc    TEXT,
    description       TEXT,
    estprojectcost    NUMERIC,
    applieddate       TIMESTAMP,
    issueddate        TIMESTAMP,
    expiresdate       TIMESTAMP,
    completeddate     TIMESTAMP,
    statuscurrent     TEXT,
    originaladdress1  TEXT,
    originalzip       TEXT,
    latitude          DOUBLE PRECISION,
    longitude         DOUBLE PRECISION,
    has_geocode       BOOLEAN
);

CREATE TABLE IF NOT EXISTS permit_pin_match (
    permitnum     TEXT PRIMARY KEY REFERENCES permits(permitnum),
    pin           TEXT NOT NULL REFERENCES parcels(pin),
    major         TEXT NOT NULL,
    minor         TEXT NOT NULL,
    match_method  TEXT NOT NULL CHECK (match_method IN ('spatial', 'address_fuzzy')),
    match_score   DOUBLE PRECISION,
    matched_at    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permit_quarantine (
    permitnum       TEXT PRIMARY KEY REFERENCES permits(permitnum),
    reason          TEXT NOT NULL,
    detail          TEXT,
    quarantined_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permit_classification (
    permitnum       TEXT NOT NULL REFERENCES permits(permitnum),
    source          TEXT NOT NULL CHECK (source IN ('rules', 'llm')),
    prompt_version  TEXT NOT NULL DEFAULT '',
    project_class   TEXT NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL,
    scope_flags     TEXT[] NOT NULL DEFAULT '{}',
    classified_at   TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (permitnum, source, prompt_version)
);
"""


def get_engine(config: DBConfig | None = None) -> Engine:
    config = config or get_db_config()
    return create_engine(config.sqlalchemy_url)


def init_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for statement in _SCHEMA_SQL.strip().split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))


def reset_join_tables(engine: Engine) -> None:
    """Clear join output so a re-run of the spatial join starts clean,
    without re-loading permits/parcels."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE permit_pin_match"))
        conn.execute(text("TRUNCATE permit_quarantine"))
