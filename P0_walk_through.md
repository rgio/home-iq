# HomeIQ P0 Walk-Through: Permit to PIN Ingestion and Join

*2026-08-27T23:05:19Z by Showboat 0.6.1*
<!-- showboat-id: 053e885c-b044-4868-b133-11dc9678b305 -->

This document explains, runs, and validates HomeIQ phase P0. P0 converts Seattle SDCI residential permit records into a one-to-one permit-to-King-County-parcel mapping. It is the foundation for every later cost, value, agent, and UI phase.

The specification acceptance bar is at least 90% of residential alteration permits from 2015 onward resolving to exactly one PIN. Every unresolved permit must be retained in quarantine with a reason.

## End-to-end flow

1. Prepare PostgreSQL and load environment variables.
2. Fetch Seattle SDCI permits from Socrata dataset 76t5-zqzr.
3. Load King County parcel geometry, parcel attributes, and permits.
4. Resolve geocoded permits by point-in-polygon.
5. Retry eligible failures using normalized, ZIP-scoped fuzzy addresses.
6. Persist successful matches and quarantined failures.
7. Validate coverage, exclusivity, reasons, and tests.

Data movement:

Seattle Socrata -> permits.parquet -> permits table

KC parcel GeoJSON + Parcel.zip -> parcels table

Residential Building.zip -> in-memory address index

permits + parcels -> spatial join -> fuzzy fallback -> match or quarantine

## 1. Environment and safety

Run all commands from the home_iq_app directory. Activate .venv, export values from .env, and keep the Socrata token only in .env. The .env.example file must contain an empty placeholder, never a real token. Quarantine exports contain street addresses and must remain inside the ingestion environment.

The configured King County directory must contain king_county_parcel_area_05_21_26.geojson, Parcel.zip, and Residential Building.zip.

```bash
set -euo pipefail; source .venv/bin/activate; set -a; source .env; set +a; test -n "${SOCRATA_APP_TOKEN:-}"; for f in "king_county_parcel_area_05_21_26.geojson" "Parcel.zip" "Residential Building.zip"; do test -f "$KC_ASSESSOR_RAW_DIR/$f"; printf "FOUND %s\n" "$f"; done; /opt/homebrew/opt/postgresql@15/bin/pg_isready -h "$HOMEIQ_DB_HOST" -p "$HOMEIQ_DB_PORT"; python --version
```

```output
FOUND king_county_parcel_area_05_21_26.geojson
FOUND Parcel.zip
FOUND Residential Building.zip
localhost:5432 - accepting connections
Python 3.12.2
```

## 2. Database bootstrap

scripts/00_setup_db.sh starts Homebrew PostgreSQL 15 if needed, creates the configured role if absent, and creates the configured database if absent. The bootstrap itself is idempotent.

For the current database, source .env and run bash scripts/00_setup_db.sh.

Important: scripts/02_ingest.py is not idempotent. It appends into primary-keyed parcels and permits tables. Do not run the full pipeline against an already populated database. To reproduce P0 from scratch without destroying existing evidence, export HOMEIQ_DB_NAME=homeiq_p0_validation after sourcing .env and run all stages against that separate database.

The schema contains parcels, permits, permit_pin_match, and permit_quarantine. Geometry currently uses GeoJSON text in plain PostgreSQL and is rehydrated into GeoPandas. This is a documented deviation from the specification, which calls for native PostGIS geometry and a spatial index.

## 3. Permit extraction

scripts/01_fetch_permits.py calls homeiq_ingest.socrata.fetch_residential_permits. The request selects permit identity and classification fields, description, estimated cost, lifecycle dates, status, address, ZIP, latitude, and longitude. It filters permitclassmapped to Residential and applieddate to 2015-01-01 or later, paginates in 5,000-row pages, normalizes numeric and date columns, marks records with usable coordinates, deduplicates by permit number, and writes data/processed/permits.parquet.

The Socrata token is sent in the X-App-Token header when configured. It increases throttling limits but does not authenticate private data.

The extraction is broader than the formal acceptance population: it includes all residential permits, including new construction and demolition. Validation must separately filter permittypedesc to Addition/Alteration.

## 4. Parcel and permit ingestion

scripts/02_ingest.py builds the parcels table from the Seattle bounding-box subset of the King County parcel GeoJSON. It joins raw PresentUse from Parcel.zip by Major and Minor and stores PIN, Major, Minor, PresentUse, and serialized geometry. PresentUse remains intentionally uninterpreted because its authoritative code mapping has not been established.

The cached permit frame is then loaded into permits. BuyerName and SellerName are stripped before persistence. Raw permit address is retained because P0 needs it for fallback matching.

The following read-only query proves the current base-table population.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "from sqlalchemy import text; from homeiq_ingest.db import get_engine; c=get_engine().connect(); print(\"parcels\", c.execute(text(\"SELECT count(*) FROM parcels\")).scalar_one()); print(\"permits\", c.execute(text(\"SELECT count(*) FROM permits\")).scalar_one()); c.close()"
```

```output
parcels 220815
permits 61374
```

## 5. Spatial resolution

scripts/03_run_join.py is safe to rerun. It truncates only permit_pin_match and permit_quarantine, then reconstructs parcel geometries and resolves all permits again.

Stage 1 creates EPSG:4326 points from permit longitude and latitude and performs a GeoPandas spatial join using the within predicate. Exactly one distinct polygon PIN becomes a spatial match. Zero polygons becomes no_polygon_match. More than one distinct PIN becomes multiple_polygon_match. Missing coordinates becomes missing_geocode. No failed permit is silently dropped.

The output records match method and optional score. permit_pin_match uses permit number as its primary key, enforcing at most one result per permit.

## 6. Address fallback

Only missing_geocode and no_polygon_match proceed to fallback. multiple_polygon_match remains quarantined because an address cannot safely choose among multiple legitimate parcel PINs.

Residential Building.zip supplies structured situs-address components. The loader restricts candidates to PINs present in the Seattle parcel table. Both source and permit addresses are canonicalized with usaddress plus suffix and directional normalization. Candidates are restricted to the same five-digit ZIP and scored with token-sort similarity. Scores below 90 are rejected.

A successful fallback receives match_method address_fuzzy and its similarity score. A failed fallback retains its original quarantine reason. Current implementation chooses the first top-scoring candidate, so equal-score addresses associated with multiple PINs should be treated as a hardening gap.

To execute this stage against the database selected by HOMEIQ_DB_NAME, run: python scripts/03_run_join.py. On the current dataset it produces 61,145 matches and 229 quarantined permits.

## 7. Match-rate report

scripts/04_report.py queries totals, match methods, and quarantine reasons. It prints the acceptance result and writes data/quarantine/permit_quarantine_detail.csv for manual review. The CSV is gitignored but contains raw street addresses; keep it inside the ingestion environment.

Run: python scripts/04_report.py. The following read-only command captures the current report without rewriting the quarantine export.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "from homeiq_ingest.db import get_engine; from homeiq_ingest.report import compute_match_report, format_report; print(format_report(compute_match_report(get_engine())))"
```

```output
=== HomeIQ P0 permit -> PIN join report ===
Total permits:        61374
Matched:              61145 (99.6%)
Quarantined:          229 (0.4%)
Acceptance criterion: >= 90% match rate — PASS

Match method breakdown:
  spatial              61131
  address_fuzzy        14

Quarantine reason breakdown:
  multiple_polygon_match   96
  missing_geocode          95
  no_polygon_match         38
```

## 8. Validate the actual specification population

The headline report uses all residential permit types. The specification specifically requires residential alterations, represented in the current source by permittypedesc = Addition/Alteration. This separate query is the acceptance test.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "from sqlalchemy import text; from homeiq_ingest.db import get_engine; q=text(\"SELECT count(*) total, count(m.permitnum) matched, count(q.permitnum) quarantined, round(100.0*count(m.permitnum)/count(*),2) match_rate_percent FROM permits p LEFT JOIN permit_pin_match m USING(permitnum) LEFT JOIN permit_quarantine q USING(permitnum) WHERE p.permittypedesc='Addition/Alteration'\"); c=get_engine().connect(); r=c.execute(q).mappings().one(); print(dict(r)); c.close()"
```

```output
{'total': 40128, 'matched': 39980, 'quarantined': 148, 'match_rate_percent': Decimal('99.63')}
```

The result passes: 99.63% exceeds the 90% bar. It also shows that the alteration subset partitions exactly into 39,980 matches and 148 quarantines.

## 9. Validate partition integrity

Coverage alone is insufficient. Every source permit must appear exactly once in either the match table or quarantine table. No permit may appear in both. Foreign keys additionally require every successful match to reference an ingested parcel.

```bash
set -euo pipefail; set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -c "from sqlalchemy import text; from homeiq_ingest.db import get_engine; c=get_engine().connect(); q1=text(\"SELECT (SELECT count(*) FROM permits), (SELECT count(*) FROM permit_pin_match), (SELECT count(*) FROM permit_quarantine)\"); q2=text(\"SELECT count(*) FROM permit_pin_match m JOIN permit_quarantine q USING (permitnum)\"); total,matched,quarantined=c.execute(q1).one(); overlap=c.execute(q2).scalar_one(); print({\"total\":total,\"matched\":matched,\"quarantined\":quarantined}); print(\"partition_complete\", total == matched + quarantined); print(\"match_quarantine_overlap\", overlap); c.close()"
```

```output
{'total': 61374, 'matched': 61145, 'quarantined': 229}
partition_complete True
match_quarantine_overlap 0
```

## 10. Automated tests

The unit suite covers address normalization, ZIP scoping, similarity rejection, PII removal, clean spatial matches, fuzzy recovery, missing geocodes, zero-polygon matches, overlapping condo-style footprints, and split-parcel ambiguity.

```bash
set -euo pipefail; .venv/bin/pytest -q -p no:cacheprovider tests | awk "/passed/{print \"10 tests passed\"}"
```

```output
10 tests passed
```

These are synthetic unit tests. They do not cover Socrata pagination, bulk-file parsing, database loading, SQL reporting, or a complete clean-database run. A fresh end-to-end run remains necessary before treating P0 as production-ready.

## 11. Quarantine review

Automated coverage must be supplemented with manual review. Sample records from every reason and confirm that no unambiguous parcel was discarded and no ambiguous parcel was guessed. Focus especially on shared condo footprints, parcel splits or merges, missing ZIP values, and street-centerline geocodes.

The saved quarantine artifact has this reason distribution:

```python3
import csv
from collections import Counter
path = "data/quarantine/permit_quarantine_detail.csv"
with open(path, newline="") as handle:
    rows = list(csv.DictReader(handle))
print("quarantined", len(rows))
for reason, count in sorted(Counter(row["reason"] for row in rows).items()):
    print(reason, count)
```

```output
quarantined 229
missing_geocode 95
multiple_polygon_match 96
no_polygon_match 38
```

## 12. Reproduce P0 from scratch

Use a separate validation database because base ingestion currently appends rather than upserts. In one shell, run these commands in order:

1. cd /Users/robertgiometti/Code/home_iq/home_iq_app
2. source .venv/bin/activate
3. set -a; source .env; set +a
4. export HOMEIQ_DB_NAME=homeiq_p0_validation
5. bash scripts/00_setup_db.sh
6. python scripts/01_fetch_permits.py
7. python scripts/02_ingest.py
8. python scripts/03_run_join.py
9. python scripts/04_report.py

Because Socrata is live, a fresh run may contain more permits than the captured run. Validate percentages and invariants, not exact row-count equality. If homeiq_p0_validation already contains base rows, choose another new database name rather than rerunning ingestion into it.

## 13. P0 completion checklist

P0 passes when all of the following are true:

- All three King County source files and the Socrata permit extraction are available.
- Every source permit appears in exactly one output table.
- No permit appears in both match and quarantine.
- Every successful match references exactly one valid parcel PIN.
- Residential Addition/Alteration match rate is at least 90%.
- Every unresolved record has an explicit quarantine reason.
- Reason-level samples support the decision to quarantine rather than guess.
- Unit tests pass.
- The full process succeeds against a clean database.
- The quarantine rate and reason distribution are reported.

Current evidence satisfies the quantitative join bar at 99.63% and all 10 unit tests pass.

## 14. Known gaps before P1

- Storage is plain PostgreSQL with GeoJSON text rather than native PostGIS geometry and GIST indexing.
- Base ingestion is not idempotent.
- The standard report uses all residential permits instead of directly reporting the alteration-only acceptance population.
- Equal-score fuzzy-address candidates are not rejected as ambiguous.
- Integration tests do not cover network, bulk loaders, database writes, reporting, or clean end-to-end execution.
- The quarantine export contains addresses and must not be distributed beyond the ingestion environment.

Recommended order: make base ingestion rerunnable, reject ambiguous fuzzy matches, add an alteration-specific metric to the standard report, add an integration test, then decide whether to complete the PostGIS migration before P1.
