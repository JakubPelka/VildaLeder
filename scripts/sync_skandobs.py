#!/usr/bin/env python3
"""Refresh public Skandobs predator observations matched to Halland features.

The Skandobs web client uses an anonymous JSON API. This adapter deliberately
stores only a small, explicit whitelist of public observation fields. Reporter
names, contact details, comments, validator identities, and other personal data
returned by the upstream endpoint are never copied to the public snapshot.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
API_BASE_URL = "https://www.skandobs.no/skandobsAPI"
PUBLIC_USER_ID = "00000000-0000-0000-0000-000000000000"
SWEDISH_LANGUAGE_ID = 1
SWEDEN_COUNTRY_ID = "1"
HALLAND_COUNTY_ID = "13"
DEFAULT_DAYS = 3_650
DEFAULT_WORKERS = 4
MAP_RESULT_LIMIT = 1_998
USER_AGENT = "VildaLeder/0.3 (+https://github.com/JakubPelka/VildaLeder)"

# Red List categories are from Rödlistade arter i Sverige 2025. Names in the
# three MVP languages are kept with their provenance instead of being flattened
# into one display-only value.
KNOWN_TAXA: dict[int, dict[str, Any]] = {
    100145: {
        "scientificName": "Ursus arctos",
        "vernacularName": "brunbjörn",
        "vernacularNames": {"sv": "brunbjörn", "en": "brown bear", "pl": "niedźwiedź brunatny"},
        "redlistCategory": "NT",
    },
    100024: {
        "scientificName": "Canis lupus",
        "vernacularName": "varg",
        "vernacularNames": {"sv": "varg", "en": "grey wolf", "pl": "wilk szary"},
        "redlistCategory": "EN",
    },
    100057: {
        "scientificName": "Lynx lynx",
        "vernacularName": "lodjur",
        "vernacularNames": {"sv": "lodjur", "en": "Eurasian lynx", "pl": "ryś euroazjatycki"},
        "redlistCategory": "VU",
    },
    100066: {
        "scientificName": "Gulo gulo",
        "vernacularName": "järv",
        "vernacularNames": {"sv": "järv", "en": "wolverine", "pl": "rosomak tundrowy"},
        "redlistCategory": "VU",
    },
}


class SkandobsError(RuntimeError):
    """A refresh failure that must leave the last good snapshot untouched."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=ROOT / "data" / "features.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "skandobs.json")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    return parser.parse_args()


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    attempts: int = 5,
    **kwargs: Any,
) -> Any:
    """Request JSON with bounded retry for a best-effort, undocumented API."""
    last_message = ""
    for attempt in range(attempts):
        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_message = str(exc)
            retryable = True
        else:
            if response.ok:
                try:
                    return response.json()
                except ValueError as exc:
                    raise SkandobsError(f"Skandobs returned invalid JSON for {url}") from exc
            last_message = response.text[:300]
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                raise SkandobsError(
                    f"Skandobs request failed ({response.status_code}) for {url}: {last_message}"
                )
        if attempt == attempts - 1:
            break
        time.sleep(min(12, 1.25 * (2**attempt)) + random.random())
    raise SkandobsError(f"Skandobs request failed after {attempts} attempts: {last_message}")


def search_payload(start_date: date, end_date: date) -> dict[str, Any]:
    criteria = {
        "species": "",
        "speciesID": "",
        "fromDate": start_date.isoformat(),
        "toDate": end_date.isoformat(),
        "country": SWEDEN_COUNTRY_ID,
        "county": HALLAND_COUNTY_ID,
        "municipality": "",
        "region": "",
        "countExpr": "",
        "count": "",
        "age": "",
        "sex": "",
        "activity": "",
        "validstatus": "",
        "affiliation": "",
        "myObservations": False,
        "hasMedia": False,
        "searchPeriod": "select",
        "observationId": "",
    }
    # The extra searchCriteria level mirrors the current Skandobs web client.
    return {
        "searchCriteria": {"searchCriteria": [criteria]},
        "currentPosition": {
            "currentPos": {"lat": 56.95, "lng": 12.75},
            "northEast": {"lat": 57.75, "lng": 13.80},
            "northWest": {"lat": 57.75, "lng": 11.15},
            "southEast": {"lat": 56.25, "lng": 13.80},
            "southWest": {"lat": 56.25, "lng": 11.15},
        },
    }


def map_search_url() -> str:
    return (
        f"{API_BASE_URL}/Area/API_Observations_Select/{uuid.uuid4()}/"
        f"{PUBLIC_USER_ID}/{SWEDISH_LANGUAGE_ID}/true/0/"
    )


def fetch_map_window(
    session: requests.Session, start_date: date, end_date: date
) -> list[dict[str, Any]]:
    result = request_json(
        session,
        "POST",
        map_search_url(),
        json=search_payload(start_date, end_date),
        timeout=60,
    )
    if not isinstance(result, dict) or not isinstance(result.get("Observations"), list):
        raise SkandobsError("Skandobs map search returned an unexpected response shape")
    records = result["Observations"]
    total = int(result.get("NumberOfObservations") or len(records))
    if len(records) >= total and len(records) < MAP_RESULT_LIMIT:
        return records
    if start_date >= end_date:
        raise SkandobsError(
            f"Skandobs result limit reached for a single day ({start_date.isoformat()})"
        )
    middle = start_date + timedelta(days=(end_date - start_date).days // 2)
    return fetch_map_window(session, start_date, middle) + fetch_map_window(
        session, middle + timedelta(days=1), end_date
    )


def year_windows(start_date: date, end_date: date) -> Iterable[tuple[date, date]]:
    current = start_date
    while current <= end_date:
        window_end = min(end_date, date(current.year, 12, 31))
        yield current, window_end
        current = window_end + timedelta(days=1)


def fetch_public_map_records(
    session: requests.Session, start_date: date, end_date: date
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for window_start, window_end in year_windows(start_date, end_date):
        for record in fetch_map_window(session, window_start, window_end):
            observation_id = str(record.get("observationID") or "")
            if observation_id:
                records[observation_id] = record
    return list(records.values())


def feature_index(feature_catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Any], STRtree]:
    features = feature_catalog.get("features") or []
    geometries = [shape(feature["analysisGeometry"]) for feature in features]
    return features, geometries, STRtree(geometries)


def matches_for_point(
    longitude: float,
    latitude: float,
    features: list[dict[str, Any]],
    geometries: list[Any],
    tree: STRtree,
) -> list[str]:
    point = Point(longitude, latitude)
    return sorted(
        features[int(index)]["id"]
        for index in tree.query(point)
        if geometries[int(index)].covers(point)
    )


def candidate_ids_near_features(
    map_records: Iterable[dict[str, Any]],
    features: list[dict[str, Any]],
    geometries: list[Any],
    tree: STRtree,
) -> list[str]:
    result = []
    for record in map_records:
        try:
            longitude = float(record["longitude"])
            latitude = float(record["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if matches_for_point(longitude, latitude, features, geometries, tree):
            result.append(str(record["observationID"]))
    return sorted(set(result))


def detail_url(observation_id: str) -> str:
    return (
        f"{API_BASE_URL}/Observations/API_Observation_Get/{PUBLIC_USER_ID}/"
        f"{SWEDISH_LANGUAGE_ID}/{observation_id}"
    )


def fetch_detail(observation_id: str) -> dict[str, Any] | None:
    result = request_json(new_session(), "GET", detail_url(observation_id), timeout=45)
    if not isinstance(result, list) or not result:
        return None
    record = result[0]
    return record if isinstance(record, dict) else None


def fetch_details(observation_ids: Iterable[str], workers: int) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_detail, item): item for item in observation_ids}
        for future in as_completed(futures):
            record = future.result()
            if record is not None:
                details.append(record)
    return details


def iso_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
    except (TypeError, ValueError) as exc:
        raise SkandobsError(f"Unexpected Skandobs observation date: {value!r}") from exc


def taxon_record(source_taxon_id: int, upstream_name: str = "") -> dict[str, Any]:
    known = KNOWN_TAXA.get(source_taxon_id, {})
    vernacular_name = known.get("vernacularName") or upstream_name or str(source_taxon_id)
    return {
        "taxonId": f"skandobs:{source_taxon_id}",
        "sourceTaxonId": source_taxon_id,
        "scientificName": known.get("scientificName"),
        "vernacularName": vernacular_name,
        "vernacularNames": known.get("vernacularNames", {"sv": vernacular_name}),
        "organismGroup": "Däggdjur",
        "redlistCategory": known.get("redlistCategory"),
        "redlistAssessment": "Sweden 2025" if known else None,
    }


def public_record(detail: dict[str, Any]) -> dict[str, Any] | None:
    """Return the public whitelist; never pass through arbitrary API fields."""
    if detail.get("hidden") or detail.get("protect"):
        return None
    try:
        observation_id = str(detail["observationID"])
        source_taxon_id = int(detail["speciesID"])
        longitude = float(detail["longitude"])
        latitude = float(detail["latitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "id": observation_id,
        "publicId": detail.get("publicID"),
        "date": iso_date(detail.get("date")),
        "taxonId": f"skandobs:{source_taxon_id}",
        "sourceTaxonId": source_taxon_id,
        "individualCount": detail.get("count"),
        "validationId": detail.get("validationID"),
        "validationStatus": detail.get("validationStatus"),
        "activity": detail.get("activity"),
        "municipality": detail.get("municipalityName"),
        "latitude": latitude,
        "longitude": longitude,
        "locationIsGeneralized": bool(detail.get("diffused")),
        "sourceUrl": f"https://www.skandobs.se/#showObservationOne/{observation_id}",
    }


def dated_counts(values: dict[str, int]) -> list[list[Any]]:
    return [[day, values[day]] for day in sorted(values)]


def build_snapshot(
    feature_catalog: dict[str, Any],
    map_records: list[dict[str, Any]],
    details: list[dict[str, Any]],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    features, geometries, tree = feature_index(feature_catalog)
    records = [record for detail in details if (record := public_record(detail)) is not None]
    records.sort(key=lambda item: (item["date"], item["id"]), reverse=True)

    matches: dict[str, list[str]] = defaultdict(list)
    retained: list[dict[str, Any]] = []
    for record in records:
        feature_ids = matches_for_point(
            record["longitude"], record["latitude"], features, geometries, tree
        )
        if not feature_ids:
            continue
        retained.append(record)
        for feature_id in feature_ids:
            matches[feature_id].append(record["id"])

    records_by_id = {record["id"]: record for record in retained}
    trail_days: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    taxon_trail_days: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for feature_id, observation_ids in matches.items():
        for observation_id in observation_ids:
            record = records_by_id[observation_id]
            trail_days[feature_id][record["date"]] += 1
            taxon_trail_days[record["taxonId"]][feature_id][record["date"]] += 1

    taxa_by_id = {
        record["taxonId"]: taxon_record(record["sourceTaxonId"])
        for record in retained
    }
    taxa = []
    for taxon_id in sorted(taxa_by_id):
        taxon = taxa_by_id[taxon_id]
        taxon["trails"] = {
            feature_id: dated_counts(days)
            for feature_id, days in sorted(taxon_trail_days[taxon_id].items())
        }
        taxa.append(taxon)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        "schemaVersion": 1,
        "meta": {
            "generatedAt": generated_at,
            "windowStart": start_date.isoformat(),
            "windowEnd": end_date.isoformat(),
            "area": "Hallands län",
            "bufferMeters": feature_catalog.get("meta", {}).get("bufferMeters", 200),
            "source": "Skandobs public web API",
            "sourceUrl": "https://www.skandobs.se/",
            "publicObservationsInArea": len(map_records),
            "matchedObservations": len(retained),
            "featureMatches": sum(len(items) for items in matches.values()),
            "privacy": "Public whitelist only; no reporter, contact, comment, or validator identity fields.",
        },
        "records": retained,
        "matches": {feature_id: ids for feature_id, ids in sorted(matches.items())},
        "trails": {
            feature_id: dated_counts(days) for feature_id, days in sorted(trail_days.items())
        },
        "taxa": taxa,
    }


def sync(
    features_path: Path,
    output_path: Path,
    end_date: date,
    days: int,
    workers: int,
) -> dict[str, Any]:
    if days < 1 or days > DEFAULT_DAYS:
        raise SkandobsError(f"days must be between 1 and {DEFAULT_DAYS}")
    feature_catalog = json.loads(features_path.read_text(encoding="utf-8"))
    start_date = end_date - timedelta(days=days - 1)
    session = new_session()
    map_records = fetch_public_map_records(session, start_date, end_date)
    features, geometries, tree = feature_index(feature_catalog)
    candidate_ids = candidate_ids_near_features(map_records, features, geometries, tree)
    details = fetch_details(candidate_ids, workers)
    if len(details) != len(candidate_ids):
        raise SkandobsError(
            f"Skandobs detail fetch was incomplete: {len(details)} of {len(candidate_ids)}"
        )
    snapshot = build_snapshot(
        feature_catalog, map_records, details, start_date, end_date
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
    return snapshot


def main() -> int:
    args = parse_args()
    try:
        snapshot = sync(args.features, args.output, args.end_date, args.days, args.workers)
    except (OSError, ValueError, requests.RequestException, SkandobsError) as exc:
        print(f"Skandobs refresh failed; existing snapshot retained: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(snapshot["meta"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
