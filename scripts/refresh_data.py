#!/usr/bin/env python3
"""Build the public, static VildaLeder pilot dataset from OSM and SLU SOS.

The SOS subscription key is used only while this script runs. It is never
written to the generated catalog or sent to the browser.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, mapping
from shapely.ops import transform


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SOS_BASE_URL = "https://api.artdatabanken.se/species-observation-system/v1"
USER_AGENT = "VildaLeder/0.1 (+https://github.com/JakubPelka/VildaLeder)"
REQUESTING_SYSTEM = "VildaLeder data refresh"
BUFFER_METERS = 200
SOS_PAGE_SIZE = 1_000
MAX_OBSERVATIONS_PER_TRAIL = 10_000


@dataclass(frozen=True)
class PilotTrail:
    osm_relation_id: int
    municipality: str = "Halmstad"
    county: str = "Halland"


PILOT_TRAILS = (
    PilotTrail(8_394_095),  # Brearedssjön runt
    PilotTrail(8_394_110),  # Danska Fall
    PilotTrail(8_394_180),  # Simlången runt
    PilotTrail(9_158_828),  # Prins Bertils stig
    PilotTrail(13_262_342),  # Orange stig Haverdal
)


class RefreshError(RuntimeError):
    """A recoverable data refresh error with a useful operator message."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "catalog.json",
        help="Generated catalog path (default: data/catalog.json)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Observation history to include (default: 365 days)",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=date.today(),
        help="Snapshot end date in YYYY-MM-DD format (default: today)",
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


def fetch_osm_relations(
    session: requests.Session, trails: Iterable[PilotTrail]
) -> dict[int, dict[str, Any]]:
    relation_ids = ",".join(str(trail.osm_relation_id) for trail in trails)
    query = (
        "[out:json][timeout:90];"
        f"relation(id:{relation_ids});"
        "out body geom;"
    )
    response = session.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=120,
    )
    response.raise_for_status()
    elements = response.json().get("elements", [])
    relations = {int(item["id"]): item for item in elements if item.get("type") == "relation"}
    missing = sorted(set(map(int, relation_ids.split(","))) - relations.keys())
    if missing:
        raise RefreshError(f"Overpass did not return OSM relations: {missing}")
    return relations


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


def search_observations(
    session: requests.Session,
    subscription_key: str,
    corridor: dict[str, Any],
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], int]:
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
        "location.county",
        "location.municipality",
        "taxon.id",
        "taxon.scientificName",
        "taxon.vernacularName",
        "taxon.attributes.organismGroup",
        "taxon.attributes.redlistCategory",
        "taxon.attributes.isRedlisted",
    ]
    payload = {
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
    headers = {
        "Ocp-Apim-Subscription-Key": subscription_key,
        "X-Requesting-System": REQUESTING_SYSTEM,
    }
    records: list[dict[str, Any]] = []
    total_count = 0
    skip = 0
    while skip < min(total_count or SOS_PAGE_SIZE, MAX_OBSERVATIONS_PER_TRAIL):
        response = session.post(
            f"{SOS_BASE_URL}/Observations/Search",
            params={
                "skip": skip,
                "take": SOS_PAGE_SIZE,
                "sortBy": "event.startDate",
                "sortOrder": "Desc",
                "translationCultureCode": "sv-SE",
                "validateSearchFilter": "true",
            },
            headers=headers,
            json=payload,
            timeout=180,
        )
        if not response.ok:
            message = response.text[:500].replace(subscription_key, "[redacted]")
            raise RefreshError(f"SOS search failed ({response.status_code}): {message}")
        result = response.json()
        page = result.get("records", [])
        total_count = int(result.get("totalCount", len(page)))
        records.extend(page)
        if not page:
            break
        skip += len(page)

    unique_records: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        occurrence_id = nested(record, "occurrence", "occurrenceId")
        fallback = f"row-{index}-{nested(record, 'taxon', 'id')}-{nested(record, 'event', 'startDate')}"
        unique_records.setdefault(occurrence_id or fallback, record)
    return list(unique_records.values())[:MAX_OBSERVATIONS_PER_TRAIL], total_count


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


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    if args.days < 1 or args.days > 3660:
        raise RefreshError("--days must be between 1 and 3660")
    start_date = args.end_date - timedelta(days=args.days - 1)
    subscription_key = read_subscription_key()
    session = new_session()
    relations = fetch_osm_relations(session, PILOT_TRAILS)

    generated_trails = []
    for index, trail in enumerate(PILOT_TRAILS, start=1):
        relation = relations[trail.osm_relation_id]
        name = relation.get("tags", {}).get("name") or f"OSM route {trail.osm_relation_id}"
        print(f"[{index}/{len(PILOT_TRAILS)}] {name}", file=sys.stderr)
        lines = relation_lines(relation)
        geometry, corridor, length_km = trail_geometry(lines)
        raw_observations, total_count = search_observations(
            session, subscription_key, corridor, start_date, args.end_date
        )
        observations = [simplify_observation(item) for item in raw_observations]
        observations.sort(key=lambda item: item.get("date") or "", reverse=True)
        generated_trails.append(
            {
                "id": f"osm-{trail.osm_relation_id}",
                "osmRelationId": trail.osm_relation_id,
                "name": name,
                "municipality": trail.municipality,
                "county": trail.county,
                "lengthKm": round(length_km, 1),
                "network": relation.get("tags", {}).get("network"),
                "operator": relation.get("tags", {}).get("operator"),
                "osmUrl": f"https://www.openstreetmap.org/relation/{trail.osm_relation_id}",
                "geometry": geometry,
                "corridor": corridor,
                "observations": observations,
                "observationTotal": total_count,
                "observationLimitReached": total_count > len(observations),
            }
        )
        print(f"    {len(observations)} observations (total {total_count})", file=sys.stderr)

    return {
        "schemaVersion": 1,
        "meta": {
            "generatedAt": iso_timestamp(),
            "windowStart": start_date.isoformat(),
            "windowEnd": args.end_date.isoformat(),
            "bufferMeters": BUFFER_METERS,
            "pilotArea": "Halmstads kommun, Hallands län",
            "sources": {
                "trails": "OpenStreetMap contributors",
                "observations": "Artportalen via SLU Species Observation System",
            },
        },
        "trails": generated_trails,
    }


def main() -> int:
    args = parse_args()
    try:
        catalog = build_catalog(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except (RefreshError, requests.RequestException) as exc:
        print(f"refresh failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
