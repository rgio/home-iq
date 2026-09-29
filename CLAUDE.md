# HomeIQ Renovation ROI Engine

HomeIQ is a Python data pipeline that estimates renovation cost and ROI from public records. It ingests Seattle SDCI building permits and King County Assessor parcels, spatially joins permits to parcel PINs, classifies permits into a project taxonomy (a rules-based backend plus an OpenAI LLM backend), and fits a quantile cost model over the classified projects. It runs as CLI/batch scripts; there is no web server.

## Setup

- Python 3.11+.
- Install the package with dev dependencies: `pip install -e ".[dev]"`.
- In Claude Code on the web sessions, the SessionStart hook (`.claude/hooks/session-start.sh`) does this automatically inside a project-local `.venv`.

## Testing

- Run the suite with `pytest`.
- Tests use synthetic fixtures and require no database or network access.

## Linting

- There are currently no configured linters, formatters, or type checkers.

## Running the full pipeline

- Not required for tests. This is only for exercising the end-to-end pipeline against real data.
- Requires a live Postgres database and a `.env` file (copy from `.env.example`).
- `scripts/00_setup_db.sh` is macOS/Homebrew-specific and will not run in the Linux web sandbox.
- `scripts/run_pipeline.sh` runs the numbered pipeline steps.

## Project layout

- `homeiq_ingest/` — library modules (ingestion, spatial join, classification, cost model, etc.).
- `scripts/` — numbered pipeline steps (`01_fetch_permits.py` … `09_validate_classifier.py`, plus `run_pipeline.sh`).
- `tests/` — pytest tests backed by synthetic fixtures.
