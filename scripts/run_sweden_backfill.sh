#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYTHON_BIN="${VILDA_REFRESH_PYTHON:-${SCRIPT_ROOT}/.venv/bin/python}"
readonly DB_CONTAINER="${VILDA_DB_CONTAINER:-vildaleder_database_1}"
readonly DATABASE_NAME="${VILDA_SWEDEN_DATABASE:-vildaleder_sweden}"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/vildaleder-sweden-backfill.lock"

if [[ "${DATABASE_NAME}" == "vildaleder" ]]; then
  printf '%s\n' "Refusing to use the Halland database for the national backfill" >&2
  exit 1
fi
if [[ -z "${SOS_SUBSCRIPTION_KEY:-}" && ( -z "${SOS_SUBSCRIPTION_KEY_FILE:-}" || ! -r "${SOS_SUBSCRIPTION_KEY_FILE}" ) ]]; then
  printf '%s\n' "Set SOS_SUBSCRIPTION_KEY or SOS_SUBSCRIPTION_KEY_FILE" >&2
  exit 1
fi
docker inspect "${DB_CONTAINER}" >/dev/null
if [[ "$(docker inspect --format '{{.State.Running}}' "${DB_CONTAINER}")" != "true" ]]; then
  docker start "${DB_CONTAINER}" >/dev/null
fi
if ! docker exec "${DB_CONTAINER}" psql -U vildaleder -d postgres -X -Atqc \
  "SELECT 1 FROM pg_database WHERE datname='${DATABASE_NAME}'" | grep -qx 1; then
  printf 'Database does not exist: %s\n' "${DATABASE_NAME}" >&2
  exit 1
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  printf '%s\n' "Another Sweden backfill already owns ${LOCK_FILE}" >&2
  exit 0
fi

password="$(
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${DB_CONTAINER}" |
    sed -n 's/^POSTGRES_PASSWORD=//p' |
    head -n 1
)"
encoded="$(printf '%s' "${password}" | "${PYTHON_BIN}" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))')"
export DATABASE_URL="postgresql://vildaleder:${encoded}@127.0.0.1:5432/${DATABASE_NAME}"
unset password encoded

cd "${SCRIPT_ROOT}"
exec "${PYTHON_BIN}" -u scripts/sync_sweden_postgis.py "$@"
