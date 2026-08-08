#!/usr/bin/env python3
"""Build the public VildaLeder dataset from Halmstad OSM routes and SLU SOS.

The browser receives route geometry, lightweight daily aggregates, and compact
observation partitions. The SOS subscription key is used only by this script;
it is never written to generated files or sent to the browser.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, mapping
from shapely.ops import transform

try:
    from scripts.split_search_index import write_search_bundle
except ModuleNotFoundError:
    from split_search_index import write_search_bundle  # type: ignore[no-redef]


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SOS_BASE_URL = "https://api.artdatabanken.se/species-observation-system/v1"
USER_AGENT = "VildaLeder/0.2 (+https://github.com/JakubPelka/VildaLeder)"
REQUESTING_SYSTEM = "VildaLeder data refresh"
BUFFER_METERS = 200
SOS_PAGE_SIZE = 1_000
# SOS Search cannot page safely past 10,000. Split before reaching that edge.
SAFE_SEARCH_WINDOW = 9_000
DEFAULT_DAYS = 3_650
DEFAULT_WORKERS = 2
MUNICIPALITY = "Halmstad"
COUNTY = "Halland"
OVERPASS_AREA = "Halmstads kommun"
OBSERVATION_FIELDS = (
    "sourceId",
    "date",
    "taxonId",
    "individualCount",
    "flags",
    "latitude",
    "longitude",
    "uncertaintyMeters",
)
REDLIST_PRIORITY = {
    "EX": 0,
    "RE": 1,
    "CR": 2,
    "EN": 3,
    "VU": 4,
    "NT": 5,
    "DD": 6,
    "LC": 7,
    "NE": 8,
    "NA": 9,
}


class RefreshError(RuntimeError):
    """A recoverable data refresh error with a useful operator message."""


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "data" / "catalog.json",
        help="Generated catalog path (default: data/catalog.json)",
    )
    parser.add_argument(
        "--index-output",
        type=Path,
        default=root / "data" / "search-index.json",
        help="Generated aggregate index (default: data/search-index.json)",
    )
    parser.add_argument(
        "--observations-dir",
        type=Path,
        default=root / "data" / "observations",
        help="Compact observation partition directory",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Observation history to retain (default: {DEFAULT_DAYS} days)",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=date.today(),
        help="Snapshot end date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Concurrent SOS trail requests (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Refresh the current month and retain existing historical partitions",
    )
    parser.add_argument(
        "--trail-limit",
        type=int,
        help="Limit discovered routes for local pipeline testing",
    )
    return parser.parse_args()


def read_subscription_key() -> str:
    direct = os.environ.get("SOS_SUBSCRIPTION_KEY", "").strip()
    if direct:
        return direct

    key_file = os.environ.get("SOS_SUBSCRIPTION_KEY_FILE", "").strip()
    if not key_file:
        raise RefreshError(
            "Set SOS_SUBSCRIPTION_KEY or SOS_SUBSCRIPTION_KEY_FILE before refreshing data."
        )

    path = Path(key_file).expanduser()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RefreshError(f"Cannot read SOS key file: {path}") from exc
    if not key:
        raise RefreshError(f"SOS key file is empty: {path}")
    return key


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    secret: str = "",
    attempts: int = 7,
    **kwargs: Any,
) -> dict[str, Any]:
    """Request JSON with bounded exponential backoff for transient failures."""
    def redact(message: str) -> str:
        return message.replace(secret, "[redacted]") if secret else message

    last_message = ""
    for attempt in range(attempts):
        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_message = redact(str(exc))
            retryable = True
        else:
            if response.ok:
                try:
                    return response.json()
                except ValueError as exc:
                    raise RefreshError(f"Invalid JSON returned by {url}") from exc
            last_message = redact(response.text[:500])
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                raise RefreshError(
                    f"Request failed ({response.status_code}) for {url}: {last_message}"
                )
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 120))
        if not retryable or attempt == attempts - 1:
            break
        delay = min(120, 1.5 * (2**attempt)) + random.random()
        time.sleep(delay)
    raise RefreshError(f"Request failed after {attempts} attempts for {url}: {last_message}")


def fetch_osm_relations(session: requests.Session) -> list[dict[str, Any]]:
    query = (
        "[out:json][timeout:120];"
        f'area["boundary"="administrative"]["name"="{OVERPASS_AREA}"]->.searchArea;'
        'relation(area.searchArea)["type"="route"]["route"~"^(hiking|foot)$"]["name"];'
        "out body geom;"
    )
    result = request_json(
        session,
        "POST",
        OVERPASS_URL,
        data={"data": query},
        timeout=180,
    )
    relations = [item for item in result.get("elements", []) if item.get("type") == "relation"]
    if not relations:
        raise RefreshError(f"Overpass returned no named hiking routes in {OVERPASS_AREA}")
    return sorted(
        relations,
        key=lambda item: (
            str(item.get("tags", {}).get("name", "")).casefold(),
            int(item["id"]),
        ),
    )


def relation_lines(relation: dict[str, Any]) -> MultiLineString:
    segments: list[LineString] = []
    for member in relation.get("members", []):
        geometry = member.get("geometry")
        if member.get("type") != "way" or not geometry or len(geometry) < 2:
            continue
        coordinates = [(float(point["lon"]), float(point["lat"])) for point in geometry]
        if member.get("role") == "backward":
            coordinates.reverse()
        segments.append(LineString(coordinates))
    if not segments:
        raise RefreshError(f"OSM relation {relation.get('id')} has no usable way geometry")
    return MultiLineString(segments)


def trail_geometry(lines_wgs84: MultiLineString) -> tuple[dict[str, Any], dict[str, Any], float]:
    to_sweref = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True).transform
    to_wgs84 = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    projected = transform(to_sweref, lines_wgs84)
    corridor_projected = projected.buffer(BUFFER_METERS).simplify(12, preserve_topology=True)
    corridor_wgs84 = transform(to_wgs84, corridor_projected)
    return mapping(lines_wgs84), mapping(corridor_wgs84), projected.length / 1000


def sos_polygon_geometry(corridor: dict[str, Any]) -> dict[str, Any]:
    """Return GeoJSON accepted by SOS, normalizing type casing."""
    geometry_type = corridor["type"].lower()
    if geometry_type not in {"polygon", "multipolygon"}:
        raise RefreshError(f"Unsupported corridor geometry: {corridor['type']}")
    return {"type": geometry_type, "coordinates": corridor["coordinates"]}


def observation_payload(
    corridor: dict[str, Any], start_date: date, end_date: date
) -> dict[str, Any]:
    fields = [
        "datasetName",
        "occurrence.occurrenceId",
        "occurrence.individualCount",
        "occurrence.organismQuantityInt",
        "occurrence.url",
        "event.startDate",
        "event.endDate",
        "identification.verified",
        "identification.uncertainIdentification",
        "location.decimalLatitude",
        "location.decimalLongitude",
        "location.coordinateUncertaintyInMeters",
        "taxon.id",
        "taxon.scientificName",
        "taxon.vernacularName",
        "taxon.attributes.organismGroup",
        "taxon.attributes.redlistCategory",
        "taxon.attributes.isRedlisted",
    ]
    return {
        "dataProvider": {"ids": [1]},
        "date": {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dateFilterType": "OnlyStartDate",
        },
        "geographics": {
            "geometries": [sos_polygon_geometry(corridor)],
            "considerObservationAccuracy": True,
            "maxAccuracy": 1_000,
        },
        "output": {"fields": fields},
    }


def search_page(
    session: requests.Session,
    subscription_key: str,
    payload: dict[str, Any],
    skip: int,
) -> dict[str, Any]:
    return request_json(
        session,
        "POST",
        f"{SOS_BASE_URL}/Observations/Search",
        secret=subscription_key,
        params={
            "skip": skip,
            "take": SOS_PAGE_SIZE,
            "sortBy": "event.startDate",
            "sortOrder": "Desc",
            "translationCultureCode": "sv-SE",
            "validateSearchFilter": "true",
        },
        headers={
            "Ocp-Apim-Subscription-Key": subscription_key,
            "X-Requesting-System": REQUESTING_SYSTEM,
        },
        json=payload,
        timeout=180,
    )


def search_observation_window(
    session: requests.Session,
    subscription_key: str,
    corridor: dict[str, Any],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch a date window, recursively splitting it below the SOS 10k edge."""
    payload = observation_payload(corridor, start_date, end_date)
    first = search_page(session, subscription_key, payload, 0)
    total_count = int(first.get("totalCount", len(first.get("records", []))))
    if total_count > SAFE_SEARCH_WINDOW:
        if start_date >= end_date:
            raise RefreshError(
                f"A single SOS day contains {total_count} records; date splitting cannot avoid the limit"
            )
        midpoint = start_date + timedelta(days=(end_date - start_date).days // 2)
        return search_observation_window(
            session, subscription_key, corridor, start_date, midpoint
        ) + search_observation_window(
            session, subscription_key, corridor, midpoint + timedelta(days=1), end_date
        )

    records = list(first.get("records", []))
    skip = len(records)
    while skip < total_count:
        result = search_page(session, subscription_key, payload, skip)
        page = result.get("records", [])
        if not page:
            raise RefreshError(
                f"SOS returned an empty page at {skip}/{total_count} for {start_date}–{end_date}"
            )
        records.extend(page)
        skip += len(page)
    return records



def search_gbif_observations(
    session: requests.Session,
    corridor: dict[str, Any],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch GBIF observations, skipping Artportalen and paginating up to the limit."""
    import json
    
    from shapely.geometry import shape, MultiPolygon
    from shapely.geometry.polygon import orient

    geom = shape(corridor)
    # GBIF requires counter-clockwise exterior rings (Right-hand rule).
    # Projection transforms may have inverted the orientation.
    if geom.geom_type == "Polygon":
        geom = orient(geom, sign=1.0)
    elif geom.geom_type == "MultiPolygon":
        geom = MultiPolygon([orient(p, sign=1.0) for p in geom.geoms])
    
    from shapely.wkt import dumps
    wkt = dumps(geom, rounding_precision=4)
    if len(wkt) > 1000:
        geom = geom.simplify(0.001, preserve_topology=True)
        if geom.geom_type == "Polygon":
            geom = orient(geom, sign=1.0)
        elif geom.geom_type == "MultiPolygon":
            geom = MultiPolygon([orient(p, sign=1.0) for p in geom.geoms])
        wkt = dumps(geom, rounding_precision=4)

    url = "https://api.gbif.org/v1/occurrence/search"
    records = []
    limit = 300
    offset = 0
    artportalen_key = "38b4c89f-584c-41bb-bd8f-cd1def33e92f"

    while True:
        params = {
            "geometry": wkt,
            "hasCoordinate": "true",
            "hasGeospatialIssue": "false",
            "country": "SE",
            "eventDate": f"{start_date.isoformat()},{end_date.isoformat()}",
            "limit": limit,
            "offset": offset
        }
        
        import time
        retries = 0
        while retries < 8:
            time.sleep(0.5)  # Throttle requests slightly
            response = session.get(url, params=params, timeout=120)
            if response.status_code == 200:
                break
            if response.status_code in (429, 503, 502, 504):
                retries += 1
                print(f"GBIF rate limit hit, sleeping {15 * retries}s...", file=sys.stderr)
                time.sleep(15 * retries)
            else:
                raise RefreshError(f"GBIF API error {response.status_code}: {response.text}")
        else:
            raise RefreshError(f"GBIF API error {response.status_code}: {response.text} after {retries} retries")
            
        data = response.json()
        results = data.get("results", [])
        
        for item in results:
            if item.get("datasetKey") == artportalen_key:
                continue
                
            # Normalize to match our unified structure
            # GBIF doesn't have Dyntaxa ID, so we use gbifTaxonKey
            date_val = item.get("eventDate", "").split("T")[0]
            if not date_val or len(date_val) < 10:
                continue
                
            taxon_id = item.get("speciesKey") or item.get("taxonKey")
            if not taxon_id:
                continue
                
            records.append({
                "id": f"gbif-{item.get('gbifID')}",
                "date": date_val,
                "endDate": date_val,
                "taxonId": f"gbif-{taxon_id}",
                "scientificName": item.get("species") or item.get("scientificName"),
                "vernacularName": item.get("vernacularName", ""),
                "organismGroup": None,  # Will be mapped in upsert_taxa
                "redlistCategory": None,
                "isRedlisted": False,
                "individualCount": item.get("individualCount", 1),
                "latitude": item.get("decimalLatitude"),
                "longitude": item.get("decimalLongitude"),
                "coordinateUncertaintyInMeters": item.get("coordinateUncertaintyInMeters"),
                "sourceUrl": f"https://www.gbif.org/occurrence/{item.get('gbifID')}" if item.get("gbifID") else None,
            })
            
        if data.get("endOfRecords", True) or not results:
            break
            
        offset += limit
        
    # Deduplicate in memory
    unique = {r["id"]: r for r in records}
    return list(unique.values())

def search_observations(
    session: requests.Session,
    subscription_key: str,
    corridor: dict[str, Any],
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], int]:
    records = search_observation_window(
        session, subscription_key, corridor, start_date, end_date
    )
    unique_records: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        occurrence_id = nested(record, "occurrence", "occurrenceId")
        fallback = f"row-{index}-{nested(record, 'taxon', 'id')}-{nested(record, 'event', 'startDate')}"
        unique_records.setdefault(str(occurrence_id or fallback), record)
    return list(unique_records.values()), len(records)


def nested(item: dict[str, Any], *path: str, default: Any = None) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def simplify_observation(item: dict[str, Any]) -> dict[str, Any]:
    taxon_id = nested(item, "taxon", "id")
    occurrence_id = nested(item, "occurrence", "occurrenceId")
    return {
        "id": occurrence_id or f"{taxon_id}-{nested(item, 'event', 'startDate')}",
        "date": nested(item, "event", "startDate"),
        "endDate": nested(item, "event", "endDate"),
        "taxonId": taxon_id,
        "scientificName": nested(item, "taxon", "scientificName"),
        "vernacularName": nested(item, "taxon", "vernacularName"),
        "organismGroup": nested(item, "taxon", "attributes", "organismGroup"),
        "redlistCategory": nested(item, "taxon", "attributes", "redlistCategory"),
        "isRedlisted": bool(nested(item, "taxon", "attributes", "isRedlisted", default=False)),
        "individualCount": nested(item, "occurrence", "organismQuantityInt")
        or nested(item, "occurrence", "individualCount"),
        "verified": bool(nested(item, "identification", "verified", default=False)),
        "uncertainIdentification": bool(
            nested(item, "identification", "uncertainIdentification", default=False)
        ),
        "latitude": nested(item, "location", "decimalLatitude"),
        "longitude": nested(item, "location", "decimalLongitude"),
        "uncertaintyMeters": nested(item, "location", "coordinateUncertaintyInMeters"),
        "sourceUrl": nested(item, "occurrence", "url"),
        "dataset": item.get("datasetName", "Artportalen"),
    }


def date_only(value: Any) -> str:
    return str(value or "")[:10]


def source_id(value: Any) -> str | int:
    tail = str(value or "").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else tail


def compact_observation(observation: dict[str, Any]) -> list[Any]:
    flags = int(bool(observation.get("verified")))
    flags |= int(bool(observation.get("uncertainIdentification"))) << 1
    latitude = observation.get("latitude")
    longitude = observation.get("longitude")
    return [
        source_id(observation.get("id")),
        date_only(observation.get("date")),
        observation.get("taxonId"),
        observation.get("individualCount"),
        flags,
        round(float(latitude), 6) if latitude is not None else None,
        round(float(longitude), 6) if longitude is not None else None,
        observation.get("uncertaintyMeters"),
    ]


def partition_name(day_text: str, snapshot_end: date) -> str:
    observed = date.fromisoformat(day_text)
    if observed.year == snapshot_end.year:
        return observed.strftime("%Y-%m")
    return observed.strftime("%Y")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_observation_partitions(
    observations_dir: Path,
    trail_id: str,
    observations: Iterable[dict[str, Any]],
    snapshot_end: date,
) -> list[dict[str, Any]]:
    groups: dict[str, list[list[Any]]] = defaultdict(list)
    for observation in observations:
        day = date_only(observation.get("date"))
        if day:
            groups[partition_name(day, snapshot_end)].append(compact_observation(observation))

    trail_dir = observations_dir / trail_id
    if trail_dir.exists():
        shutil.rmtree(trail_dir)
    trail_dir.mkdir(parents=True, exist_ok=True)
    manifests = []
    for partition, records in sorted(groups.items()):
        records.sort(key=lambda row: (row[1], str(row[0])), reverse=True)
        file_path = trail_dir / f"{partition}.json"
        write_json(file_path, {"schemaVersion": 1, "records": records})
        manifests.append(
            {
                "path": f"data/observations/{trail_id}/{partition}.json",
                "start": min(row[1] for row in records),
                "end": max(row[1] for row in records),
                "count": len(records),
            }
        )
    return manifests


def replace_current_partition(
    observations_dir: Path,
    trail_id: str,
    existing: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    snapshot_end: date,
) -> list[dict[str, Any]]:
    partition = snapshot_end.strftime("%Y-%m")
    relative_path = f"data/observations/{trail_id}/{partition}.json"
    manifests = [item for item in existing if item.get("path") != relative_path]
    file_path = observations_dir / trail_id / f"{partition}.json"
    compact = [compact_observation(item) for item in observations if date_only(item.get("date"))]
    compact.sort(key=lambda row: (row[1], str(row[0])), reverse=True)
    if compact:
        write_json(file_path, {"schemaVersion": 1, "records": compact})
        manifests.append(
            {
                "path": relative_path,
                "start": min(row[1] for row in compact),
                "end": max(row[1] for row in compact),
                "count": len(compact),
            }
        )
    elif file_path.exists():
        file_path.unlink()
    return sorted(manifests, key=lambda item: item["start"])


def prune_observation_partitions(
    observations_dir: Path,
    trail_id: str,
    manifests: list[dict[str, Any]],
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    """Remove records outside the rolling snapshot while retaining partitions."""
    start_text = window_start.isoformat()
    end_text = window_end.isoformat()
    retained = []
    for manifest in manifests:
        file_path = observations_dir / trail_id / Path(manifest["path"]).name
        if manifest["end"] < start_text or manifest["start"] > end_text:
            if file_path.exists():
                file_path.unlink()
            continue
        if manifest["start"] >= start_text and manifest["end"] <= end_text:
            retained.append(manifest)
            continue
        try:
            partition = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RefreshError(f"Cannot prune observation partition: {file_path}") from exc
        records = [
            record
            for record in partition.get("records", [])
            if start_text <= str(record[1]) <= end_text
        ]
        if not records:
            file_path.unlink()
            continue
        write_json(file_path, {"schemaVersion": 1, "records": records})
        retained.append(
            {
                "path": manifest["path"],
                "start": min(str(record[1]) for record in records),
                "end": max(str(record[1]) for record in records),
                "count": len(records),
            }
        )
    return sorted(retained, key=lambda item: item["start"])


def update_taxon_metadata(target: dict[str, dict[str, Any]], observation: dict[str, Any]) -> None:
    taxon_id = observation.get("taxonId")
    if taxon_id is None:
        return
    key = str(taxon_id)
    current = target.setdefault(
        key,
        {
            "taxonId": taxon_id,
            "scientificName": None,
            "vernacularName": None,
            "organismGroup": None,
            "redlistCategory": None,
        },
    )
    for field in ("scientificName", "vernacularName", "organismGroup"):
        if observation.get(field) and not current.get(field):
            current[field] = observation[field]
    candidate = observation.get("redlistCategory")
    if REDLIST_PRIORITY.get(candidate, 99) < REDLIST_PRIORITY.get(
        current.get("redlistCategory"), 99
    ):
        current["redlistCategory"] = candidate


def read_partition(root: Path, manifest: dict[str, Any]) -> list[list[Any]]:
    relative = Path(manifest["path"])
    path = root / relative.relative_to("data") if relative.parts[0] == "data" else root / relative
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefreshError(f"Cannot read observation partition: {path}") from exc
    return result.get("records", [])


def build_search_index(
    catalog: dict[str, Any],
    data_root: Path,
    taxon_seed: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    start = catalog["meta"]["windowStart"]
    end = catalog["meta"]["windowEnd"]
    trail_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    taxon_counts: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for trail in catalog["trails"]:
        for manifest in trail.get("observationFiles", []):
            for record in read_partition(data_root, manifest):
                day = str(record[1])
                if day < start or day > end:
                    continue
                taxon_id = record[2]
                trail_counts[trail["id"]][day] += 1
                if taxon_id is not None:
                    taxon_counts[str(taxon_id)][trail["id"]][day] += 1

    taxa = []
    for taxon_id, trails in taxon_counts.items():
        metadata = dict(
            taxon_seed.get(
                taxon_id,
                {
                    "taxonId": int(taxon_id) if taxon_id.isdigit() else taxon_id,
                    "scientificName": None,
                    "vernacularName": None,
                    "organismGroup": None,
                    "redlistCategory": None,
                },
            )
        )
        metadata["trails"] = {
            trail_id: sorted(days.items()) for trail_id, days in sorted(trails.items())
        }
        taxa.append(metadata)
    taxa.sort(
        key=lambda item: (
            str(item.get("vernacularName") or item.get("scientificName") or "").casefold(),
            str(item["taxonId"]),
        )
    )
    return {
        "schemaVersion": 1,
        "generatedAt": catalog["meta"]["generatedAt"],
        "trails": {
            trail_id: sorted(days.items()) for trail_id, days in sorted(trail_counts.items())
        },
        "taxa": taxa,
    }


def seed_taxa(index_path: Path) -> dict[str, dict[str, Any]]:
    if not index_path.exists():
        return {}
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefreshError(f"Cannot read existing search index: {index_path}") from exc
    fields = ("taxonId", "scientificName", "vernacularName", "organismGroup", "redlistCategory")
    return {
        str(item["taxonId"]): {field: item.get(field) for field in fields}
        for item in index.get("taxa", [])
    }


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_route_observations(
    relation: dict[str, Any],
    subscription_key: str,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    lines = relation_lines(relation)
    geometry, corridor, length_km = trail_geometry(lines)
    session = new_session()
    raw, source_total = search_observations(
        session, subscription_key, corridor, start_date, end_date
    )
    observations = [simplify_observation(item) for item in raw]
    observations.sort(key=lambda item: item.get("date") or "", reverse=True)
    tags = relation.get("tags", {})
    trail_id = f"osm-{relation['id']}"
    trail = {
        "id": trail_id,
        "osmRelationId": int(relation["id"]),
        "name": tags.get("name") or f"OSM route {relation['id']}",
        "municipality": MUNICIPALITY,
        "county": COUNTY,
        "lengthKm": round(length_km, 1),
        "network": tags.get("network"),
        "operator": tags.get("operator"),
        "osmUrl": f"https://www.openstreetmap.org/relation/{relation['id']}",
        "geometry": geometry,
        "corridor": corridor,
    }
    return trail, observations, source_total


def existing_trails(catalog_path: Path) -> dict[str, dict[str, Any]]:
    if not catalog_path.exists():
        return {}
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefreshError(f"Cannot read existing catalog: {catalog_path}") from exc
    return {trail["id"]: trail for trail in catalog.get("trails", [])}


def build_dataset(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.days < 1 or args.days > 3_660:
        raise RefreshError("--days must be between 1 and 3660")
    if args.workers < 1 or args.workers > 6:
        raise RefreshError("--workers must be between 1 and 6")
    start_date = args.end_date - timedelta(days=args.days - 1)
    subscription_key = read_subscription_key()
    relations = fetch_osm_relations(new_session())
    if args.trail_limit:
        relations = relations[: args.trail_limit]
    prior_trails = existing_trails(args.output) if args.incremental else {}
    if args.incremental and not prior_trails:
        raise RefreshError("--incremental requires an existing catalog")
    query_start = args.end_date.replace(day=1) if args.incremental else start_date
    taxon_metadata = seed_taxa(args.index_output) if args.incremental else {}
    generated_by_id: dict[str, dict[str, Any]] = {}

    print(
        f"Fetching {len(relations)} routes for {query_start.isoformat()}–{args.end_date.isoformat()}",
        file=sys.stderr,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {}
        for relation in relations:
            trail_id = f"osm-{relation['id']}"
            relation_start = (
                query_start if not args.incremental or trail_id in prior_trails else start_date
            )
            future = executor.submit(
                fetch_route_observations,
                relation,
                subscription_key,
                relation_start,
                args.end_date,
            )
            futures[future] = relation
        completed = 0
        for future in as_completed(futures):
            relation = futures[future]
            try:
                trail, observations, source_total = future.result()
            except Exception as exc:
                name = relation.get("tags", {}).get("name") or relation.get("id")
                raise RefreshError(f"Failed while fetching route {name}: {exc}") from exc
            completed += 1
            for observation in observations:
                update_taxon_metadata(taxon_metadata, observation)
            if args.incremental and trail["id"] in prior_trails:
                existing = prior_trails.get(trail["id"], {}).get("observationFiles", [])
                trail["observationFiles"] = replace_current_partition(
                    args.observations_dir,
                    trail["id"],
                    existing,
                    observations,
                    args.end_date,
                )
                trail["observationFiles"] = prune_observation_partitions(
                    args.observations_dir,
                    trail["id"],
                    trail["observationFiles"],
                    start_date,
                    args.end_date,
                )
            else:
                trail["observationFiles"] = write_observation_partitions(
                    args.observations_dir,
                    trail["id"],
                    observations,
                    args.end_date,
                )
            trail["observationTotal"] = sum(
                item["count"] for item in trail["observationFiles"]
            )
            trail["observationLimitReached"] = False
            generated_by_id[trail["id"]] = trail
            print(
                f"[{completed}/{len(relations)}] {trail['name']}: "
                f"{len(observations)} unique ({source_total} source records)",
                file=sys.stderr,
            )

    generated_trails = sorted(
        generated_by_id.values(), key=lambda item: (item["name"].casefold(), item["osmRelationId"])
    )
    catalog = {
        "schemaVersion": 2,
        "meta": {
            "generatedAt": iso_timestamp(),
            "windowStart": start_date.isoformat(),
            "windowEnd": args.end_date.isoformat(),
            "bufferMeters": BUFFER_METERS,
            "pilotArea": f"{OVERPASS_AREA}, {COUNTY}s län",
            "observationRecordFields": list(OBSERVATION_FIELDS),
            "sources": {
                "trails": "OpenStreetMap contributors",
                "observations": "Artportalen via SLU Species Observation System",
            },
        },
        "trails": generated_trails,
    }
    data_root = args.observations_dir.parent
    index = build_search_index(catalog, data_root, taxon_metadata)
    for trail in catalog["trails"]:
        trail["observationTotal"] = sum(
            count for _, count in index["trails"].get(trail["id"], [])
        )
    return catalog, index


def main() -> int:
    args = parse_args()
    try:
        catalog, index = build_dataset(args)
        write_json(args.output, catalog)
        write_search_bundle(
            index,
            catalog["trails"],
            args.index_output,
            args.index_output.parent / "place-rankings",
            args.index_output.parent / "species-rankings",
        )
    except (RefreshError, requests.RequestException) as exc:
        print(f"refresh failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Wrote {args.output}, {args.index_output}, and {len(catalog['trails'])} route partitions",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
