#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT}/.venv/bin/python"

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "Error: Python environment not found at ${PYTHON_BIN}" >&2
  exit 1
fi

password="$(
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' vildaleder_database_1 |
    sed -n 's/^POSTGRES_PASSWORD=//p' |
    head -n 1
)"
if [ -z "${password}" ]; then
  echo "Could not read DB password from docker container" >&2
  exit 1
fi

encoded="$(printf '%s' "${password}" | "${PYTHON_BIN}" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))')"
export DATABASE_URL="postgresql://vildaleder:${encoded}@127.0.0.1:5432/vildaleder"

echo "Starting 10-year GBIF backfill for all features..."
echo "This process will run slowly (1 worker) to respect GBIF's rate limits."

"${PYTHON_BIN}" "${ROOT}/scripts/sync_halland_postgis.py" --days 3650 --workers 1 --only-gbif

echo "GBIF backfill completed successfully."
