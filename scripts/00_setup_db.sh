#!/usr/bin/env bash
# One-time local Postgres setup. Idempotent — safe to re-run.
# Uses the ARM Homebrew Postgres explicitly (/opt/homebrew) — this machine
# has a second, separate Homebrew install at /usr/local that PATH may
# resolve first but that doesn't have Postgres.
set -euo pipefail

PG_BIN="/opt/homebrew/opt/postgresql@15/bin"
export PATH="${PG_BIN}:${PATH}"

DB_NAME="${HOMEIQ_DB_NAME:-homeiq}"
DB_USER="${HOMEIQ_DB_USER:-homeiq}"
DB_PASSWORD="${HOMEIQ_DB_PASSWORD:-homeiq}"

if ! pg_isready -q; then
  echo "Starting postgresql@15..."
  /opt/homebrew/bin/brew services start postgresql@15
  for _ in $(seq 1 10); do
    pg_isready -q && break
    sleep 1
  done
fi

psql postgres -tc "SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}'" | grep -q 1 \
  || psql postgres -c "CREATE ROLE ${DB_USER} WITH LOGIN CREATEDB PASSWORD '${DB_PASSWORD}';"

psql postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1 \
  || createdb -O "${DB_USER}" "${DB_NAME}"

echo "Database '${DB_NAME}' ready for role '${DB_USER}'."
