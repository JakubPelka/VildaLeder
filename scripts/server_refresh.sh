#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYTHON_BIN="${VILDA_REFRESH_PYTHON:-${SCRIPT_ROOT}/.venv/bin/python}"
readonly REFRESH_REMOTE="${VILDA_REFRESH_REMOTE:-https://github.com/JakubPelka/VildaLeder.git}"
readonly REFRESH_BRANCH="${VILDA_REFRESH_BRANCH:-main}"
readonly DB_CONTAINER="${VILDA_DB_CONTAINER:-vildaleder_database_1}"
readonly ROLLING_DAYS="${VILDA_ROLLING_DAYS:-30}"
readonly FULL_RECONCILE_DAY="${VILDA_FULL_RECONCILE_DAY:-01}"
readonly WORK_ROOT="${VILDA_REFRESH_WORK_ROOT:-${XDG_CACHE_HOME:-${HOME}/.cache}/vildaleder-refresh}"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/vildaleder-server-refresh.lock"

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

require_command() {
  command -v "$1" >/dev/null || {
    log "Missing required command: $1"
    exit 1
  }
}

check_environment() {
  require_command git
  require_command docker
  require_command flock
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    log "Python environment is missing: ${PYTHON_BIN}"
    exit 1
  fi
  if [[ -z "${SOS_SUBSCRIPTION_KEY:-}" ]]; then
    if [[ -z "${SOS_SUBSCRIPTION_KEY_FILE:-}" || ! -r "${SOS_SUBSCRIPTION_KEY_FILE}" ]]; then
      log "Set SOS_SUBSCRIPTION_KEY or a readable SOS_SUBSCRIPTION_KEY_FILE"
      exit 1
    fi
  fi
  docker inspect "${DB_CONTAINER}" >/dev/null 2>&1 || {
    log "PostGIS container does not exist: ${DB_CONTAINER}"
    exit 1
  }
}

database_url() {
  if [[ -n "${DATABASE_URL:-}" ]]; then
    printf '%s' "${DATABASE_URL}"
    return
  fi
  local password encoded
  password="$(
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${DB_CONTAINER}" |
      sed -n 's/^POSTGRES_PASSWORD=//p' |
      head -n 1
  )"
  if [[ -z "${password}" ]]; then
    log "DATABASE_URL is unset and the PostGIS password could not be read" >&2
    exit 1
  fi
  encoded="$(printf '%s' "${password}" | "${PYTHON_BIN}" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=""))')"
  printf 'postgresql://vildaleder:%s@127.0.0.1:5432/vildaleder' "${encoded}"
}

check_environment
if [[ "${1:-}" == "--check" ]]; then
  log "Server refresh environment is ready"
  exit 0
fi

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "Another VildaLeder refresh is already running; leaving it in control"
  exit 0
fi

if [[ "$(docker inspect --format '{{.State.Running}}' "${DB_CONTAINER}")" != "true" ]]; then
  log "Starting PostGIS container ${DB_CONTAINER}"
  docker start "${DB_CONTAINER}" >/dev/null
fi

mkdir -p "${WORK_ROOT}"
run_directory="$(mktemp -d "${WORK_ROOT}/run.XXXXXX")"
cleanup() {
  case "${run_directory}" in
    "${WORK_ROOT}"/run.*) rm -rf -- "${run_directory}" ;;
    *) log "Refusing to remove unexpected work directory: ${run_directory}" ;;
  esac
}
trap cleanup EXIT

readonly DATABASE_URL_VALUE="$(database_url)"
export DATABASE_URL="${DATABASE_URL_VALUE}"

log "Cloning ${REFRESH_BRANCH} into an isolated refresh worktree"
git clone --quiet --depth 1 --branch "${REFRESH_BRANCH}" "${REFRESH_REMOTE}" "${run_directory}/repo"
cd "${run_directory}/repo"

log "Applying database migrations"
"${PYTHON_BIN}" scripts/migrate_postgis.py

log "Retaining all historical PostGIS observations; refresh windows do not prune by age"

log "Refreshing Halland OSM trails and Naturvårdsregistret reserves"
"${PYTHON_BIN}" scripts/sync_features.py --database-url "${DATABASE_URL}"

if [[ "${VILDA_FORCE_FULL_RECONCILE:-0}" == "1" || "$(date +%d)" == "${FULL_RECONCILE_DAY}" ]]; then
  log "Running scheduled full ten-year SOS reconciliation"
  "${PYTHON_BIN}" scripts/sync_halland_postgis.py --days 3650 --workers 1 --force
else
  log "Completing ten-year coverage for any new or changed places"
  "${PYTHON_BIN}" scripts/sync_halland_postgis.py --days 3650 --workers 1
  log "Refreshing the rolling ${ROLLING_DAYS}-day SOS correction window"
  "${PYTHON_BIN}" scripts/sync_halland_postgis.py --days "${ROLLING_DAYS}" --workers 1 --force
fi

log "Refreshing multilingual GBIF taxonomy names"
if ! "${PYTHON_BIN}" scripts/enrich_gbif_taxonomy.py --workers 6; then
  log "GBIF taxonomy enrichment failed; retaining the names already stored in PostGIS"
fi

log "Refreshing experimental public Skandobs evidence"
if "${PYTHON_BIN}" scripts/sync_skandobs.py; then
  "${PYTHON_BIN}" scripts/import_skandobs.py
else
  log "Skandobs refresh failed; retaining the last checked-in snapshot"
fi

log "Verifying canonical PostGIS data"
"${PYTHON_BIN}" scripts/verify_postgis.py

log "Exporting a complete atomic GitHub Pages snapshot"
"${PYTHON_BIN}" scripts/export_postgis_snapshot.py

log "Checking exported coverage and GitHub file limits"
"${PYTHON_BIN}" - <<'PY'
import json
from datetime import date
from pathlib import Path

features = json.loads(Path("data/features.json").read_text(encoding="utf-8"))["features"]
catalog = json.loads(Path("data/catalog.json").read_text(encoding="utf-8"))
search_index = json.loads(Path("data/search-index.json").read_text(encoding="utf-8"))
exported = catalog["trails"]
if len(exported) != len(features):
    raise SystemExit(f"Refusing partial publication: {len(exported)}/{len(features)} places")
start = date.fromisoformat(catalog["meta"]["windowStart"])
end = date.fromisoformat(catalog["meta"]["windowEnd"])
if (end - start).days + 1 != 3650:
    raise SystemExit("Refusing publication outside the exact ten-year window")
expected_feature_ids = sorted(feature["id"] for feature in features)
if search_index.get("speciesPointFeatureIds") != expected_feature_ids:
    raise SystemExit("Refusing publication with a mismatched species-point feature index")
species_files = [
    item
    for manifests in search_index.get("speciesObservationFiles", {}).values()
    for item in manifests
]
if not species_files or any(not Path(item["path"]).is_file() for item in species_files):
    raise SystemExit("Refusing publication with an incomplete species-point snapshot")
ranking_files = [
    *search_index.get("placeRankingFiles", {}).values(),
    *search_index.get("speciesRankingFiles", {}).values(),
]
if not ranking_files or any(not Path(path).is_file() for path in ranking_files):
    raise SystemExit("Refusing publication with incomplete lazy ranking files")
if any("trails" in taxon for taxon in search_index.get("taxa", [])):
    raise SystemExit("Refusing publication with inline taxon rankings in the startup index")
oversized = [path for path in Path("data").rglob("*") if path.is_file() and path.stat().st_size >= 95_000_000]
if oversized:
    raise SystemExit("Refusing files near GitHub's 100 MB limit: " + ", ".join(map(str, oversized)))
print(json.dumps({
    "features": len(exported),
    "matches": sum(item["observationTotal"] for item in exported),
    "speciesPoints": sum(item["count"] for item in species_files),
}))
PY

"${PYTHON_BIN}" -m unittest discover -s tests -v

git config user.name "VildaLeder server refresh"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data/catalog.json data/features.json data/search-index.json data/skandobs.json data/observations data/place-rankings data/species-observations data/species-rankings
if git diff --cached --quiet; then
  log "No publishable data changes"
  exit 0
fi

git commit --quiet -m "data: refresh complete Halland snapshot"
log "Pushing the verified snapshot; GitHub Pages will deploy it automatically"
git push origin "HEAD:${REFRESH_BRANCH}"
log "Daily VildaLeder refresh completed"
