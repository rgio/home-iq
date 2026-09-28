#!/usr/bin/env bash
# Full P0 pipeline, start to finish. Run from the project root.
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
set -a; source .env; set +a

bash scripts/00_setup_db.sh
python scripts/01_fetch_permits.py
python scripts/02_ingest.py
python scripts/03_run_join.py
python scripts/04_report.py
