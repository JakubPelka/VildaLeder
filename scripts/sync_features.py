#!/usr/bin/env python3
"""Synchronise Halland trails and nature reserves into GeoJSON and PostGIS.

Trails come from named OSM hiking/foot route relations, queried municipality by
municipality to keep Overpass requests bounded. Nature reserves come from the
authoritative Naturvårdsregistret REST API. Every feature receives an analysis
geometry consisting of the route corridor or reserve polygon plus 200 metres.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
import requests
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.ops import transform

try:
    from scripts.refresh_data import (
        BUFFER_METERS,
        OVERPASS_URL,
        RefreshError,
        iso_timestamp,
        new_session,
        relation_lines,
        request_json,
        trail_geometry,
        write_json,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/ rather than the repository root.
    from refresh_data import (  # type: ignore[no-redef]
        BUFFER_METERS,
        OVERPASS_URL,
        RefreshError,
        iso_timestamp,
        new_session,
        relation_lines,
        request_json,
        trail_geometry,
        write_json,
    )


ROOT = Path(__file__).resolve().parents[1]
NVR_BASE_URL = "https://geodata.naturvardsverket.se/naturvardsregistret/rest/v3"
COUNTY = "Halland"
MUNICIPALITIES = {
    "1315": "Hylte",
    "1380": "Halmstad",
    "1381": "Laholm",
    "1382": "Falkenberg",
    "1383": "Varberg",
    "1384": "Kungsbacka",
}
NVR_WORKERS = 6
OVERPASS_URLS = tuple(
    dict.fromkeys(
        filter(
            None,
            (
                os.environ.get("OVERPASS_URL"),
                OVERPASS_URL,
                "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter",
                "https://overpass.private.coffee/api/interpreter",
            ),
        )
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "features.json",
        help="Public feature catalog (default: data/features.json)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Optional PostgreSQL DSN; when set, features are also upserted into PostGIS",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        help="Use an existing feature catalog instead of querying OSM and NVR",
    )
    parser.add_argument("--trail-limit", type=int, help="Limit trails for local tests")
    parser.add_argument("--reserve-limit", type=int, help="Limit reserves for local tests")
    return parser.parse_args()


def normalized_name(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    ).casefold().strip()


def overpass_query(municipality_code: str) -> str:
    return (
        "[out:json][timeout:180];"
        f'area["boundary"="administrative"]["ref:scb"="{municipality_code}"]->.searchArea;'
        'relation(area.searchArea)["type"="route"]["route"~"^(hiking|foot)$"]["name"];'
        "out body geom;"
    )


def fetch_municipality_routes(
    municipality_code: str,
    municipality_name: str,
) -> tuple[str, list[dict[str, Any]]]:
    failures = []
    result = None
    for url in OVERPASS_URLS:
        try:
            result = request_json(
                new_session(),
                "POST",
                url,
                attempts=3,
                data={"data": overpass_query(municipality_code)},
                timeout=180,
            )
            break
        except RefreshError as exc:
            failures.append(f"{url}: {exc}")
    if result is None:
        raise RefreshError("; ".join(failures))
    relations = [item for item in result.get("elements", []) if item.get("type") == "relation"]
    return municipality_name, relations


def fetch_halland_routes() -> list[dict[str, Any]]:
    relations: dict[int, dict[str, Any]] = {}
    memberships: dict[int, set[str]] = defaultdict(set)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fetch_municipality_routes, code, name): name
            for code, name in MUNICIPALITIES.items()
        }
        for future in as_completed(futures):
            expected_name = futures[future]
            try:
                municipality, rows = future.result()
            except Exception as exc:
                raise RefreshError(f"Failed to discover routes in {expected_name}: {exc}") from exc
            print(f"Routes in {municipality}: {len(rows)}", file=sys.stderr)
            for relation in rows:
                relation_id = int(relation["id"])
                relations.setdefault(relation_id, relation)
                memberships[relation_id].add(municipality)

    features = []
    for relation_id, relation in relations.items():
        lines = relation_lines(relation)
        geometry, analysis_geometry, length_km = trail_geometry(lines)
        tags = relation.get("tags", {})
        municipalities = sorted(memberships[relation_id])
        features.append(
            {
                "id": f"osm-{relation_id}",
                "featureKind": "trail",
                "source": "osm",
                "sourceFeatureId": str(relation_id),
                "name": tags.get("name") or f"OSM route {relation_id}",
                "county": COUNTY,
                "municipalities": municipalities,
                "municipality": municipalities[0] if municipalities else None,
                "lengthKm": round(length_km, 1),
                "network": tags.get("network"),
                "operator": tags.get("operator"),
                "sourceUrl": f"https://www.openstreetmap.org/relation/{relation_id}",
                "geometry": geometry,
                "analysisGeometry": analysis_geometry,
            }
        )
    return sorted(features, key=lambda item: (item["name"].casefold(), item["id"]))


def request_text(session: requests.Session, url: str, attempts: int = 6) -> str:
    last_message = ""
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=120)
            if response.ok:
                return response.text
            last_message = f"HTTP {response.status_code}: {response.text[:200]}"
            retryable = response.status_code == 429 or response.status_code >= 500
        except requests.RequestException as exc:
            last_message = str(exc)
            retryable = True
        if not retryable or attempt == attempts - 1:
            break
        time.sleep(min(20, 1.5 * 2**attempt) + random.random())
    raise RefreshError(f"Request failed after {attempts} attempts for {url}: {last_message}")


def reserve_municipalities(value: Any) -> list[str]:
    if not value:
        return []
    names = []
    for part in str(value).replace(";", ",").split(","):
        name = part.strip().removesuffix("s kommun").removesuffix(" kommun")
        if name == "Halmstads":
            name = "Halmstad"
        if name and name not in names:
            names.append(name)
    return sorted(names)


def polygonal(geometry: Any) -> Polygon | MultiPolygon:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    polygons = [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon)]
    if not polygons:
        raise RefreshError(f"NVR geometry is not polygonal: {geometry.geom_type}")
    return MultiPolygon(polygons)


def reserve_feature(item: dict[str, Any]) -> dict[str, Any]:
    reserve_id = str(item["id"])
    status = str(item.get("beslutsstatus") or "Gällande")
    geometry_url = f"{NVR_BASE_URL}/omrade/{quote(reserve_id)}/{quote(status)}/wkt"
    projected = polygonal(wkt.loads(request_text(new_session(), geometry_url)))
    if not projected.is_valid:
        projected = projected.buffer(0)
    display_projected = projected.simplify(4, preserve_topology=True)
    analysis_projected = projected.buffer(BUFFER_METERS).simplify(12, preserve_topology=True)
    to_wgs84 = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    geometry = transform(to_wgs84, display_projected)
    analysis_geometry = transform(to_wgs84, analysis_projected)
    municipalities = reserve_municipalities(item.get("kommunerAsText"))
    return {
        "id": f"nvr-{reserve_id}",
        "featureKind": "reserve",
        "source": "nvr",
        "sourceFeatureId": reserve_id,
        "name": item.get("namn") or f"Naturreservat {reserve_id}",
        "county": COUNTY,
        "municipalities": municipalities,
        "municipality": municipalities[0] if municipalities else None,
        "areaHa": item.get("areaHa"),
        "iucnCategory": item.get("iucnKategori"),
        "manager": item.get("forvaltare"),
        "decisionStatus": status,
        "sourceUrl": (
            "https://skyddadnatur.naturvardsverket.se/sknat/?nvrid=" + quote(reserve_id)
        ),
        "geometry": mapping(geometry),
        "analysisGeometry": mapping(analysis_geometry),
    }


def fetch_halland_reserves(limit: int | None = None) -> list[dict[str, Any]]:
    rows = request_json(
        new_session(),
        "GET",
        f"{NVR_BASE_URL}/omrade",
        params={
            "lan": "N",
            "skyddstypkod": "NR",
            "beslutsstatus": "Gällande",
            "limit": 1_000,
        },
        timeout=120,
    )
    if not isinstance(rows, list):
        raise RefreshError("Naturvårdsregistret returned an unexpected reserve list")
    if limit:
        rows = rows[:limit]
    features = []
    with ThreadPoolExecutor(max_workers=NVR_WORKERS) as executor:
        futures = {executor.submit(reserve_feature, item): item for item in rows}
        completed = 0
        for future in as_completed(futures):
            item = futures[future]
            try:
                features.append(future.result())
            except Exception as exc:
                raise RefreshError(
                    f"Failed to fetch reserve {item.get('namn') or item.get('id')}: {exc}"
                ) from exc
            completed += 1
            if completed % 25 == 0 or completed == len(rows):
                print(f"Reserve geometries: {completed}/{len(rows)}", file=sys.stderr)
    return sorted(features, key=lambda item: (item["name"].casefold(), item["id"]))


def geometry_version(feature: dict[str, Any]) -> str:
    encoded = json.dumps(
        feature["analysisGeometry"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def ensure_sources(connection: psycopg.Connection[Any]) -> dict[str, int]:
    rows = (
        ("osm", "OpenStreetMap", "https://www.openstreetmap.org/", "© OpenStreetMap contributors"),
        (
            "nvr",
            "Naturvårdsregistret / Naturvårdsverket",
            "https://geodata.naturvardsverket.se/naturvardsregistret/",
            "Naturvårdsverket, Naturvårdsregistret",
        ),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO vildaleder.data_source(
                   source_key, name, source_kind, base_url, attribution
               ) VALUES (%s, %s, 'spatial', %s, %s)
               ON CONFLICT (source_key) DO UPDATE
               SET name = EXCLUDED.name,
                   base_url = EXCLUDED.base_url,
                   attribution = EXCLUDED.attribution""",
            rows,
        )
    return dict(
        connection.execute(
            "SELECT source_key, source_id FROM vildaleder.data_source WHERE source_key IN ('osm', 'nvr')"
        ).fetchall()
    )


def upsert_postgis(database_url: str, features: list[dict[str, Any]]) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        sources = ensure_sources(connection)
        feature_ids = []
        for feature in features:
            source_id = sources[feature["source"]]
            properties = {
                key: feature.get(key)
                for key in (
                    "county",
                    "municipalities",
                    "network",
                    "operator",
                    "areaHa",
                    "iucnCategory",
                    "manager",
                    "decisionStatus",
                )
                if feature.get(key) is not None
            }
            feature_id = connection.execute(
                """INSERT INTO vildaleder.spatial_feature(
                       feature_kind, source_id, source_feature_id, canonical_name,
                       length_km, geom, analysis_geom, source_url, properties,
                       geometry_version, source_updated_at
                   ) VALUES (
                       %s::vildaleder.spatial_feature_kind, %s, %s, %s, %s,
                       ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                       ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                       %s, %s::jsonb, %s, now()
                   )
                   ON CONFLICT (source_id, source_feature_id) DO UPDATE
                   SET feature_kind = EXCLUDED.feature_kind,
                       canonical_name = EXCLUDED.canonical_name,
                       length_km = EXCLUDED.length_km,
                       geom = EXCLUDED.geom,
                       analysis_geom = EXCLUDED.analysis_geom,
                       source_url = EXCLUDED.source_url,
                       properties = EXCLUDED.properties,
                       geometry_version = EXCLUDED.geometry_version,
                       source_updated_at = EXCLUDED.source_updated_at,
                       is_active = true,
                       updated_at = now()
                   RETURNING feature_id""",
                (
                    feature["featureKind"],
                    source_id,
                    feature["sourceFeatureId"],
                    feature["name"],
                    feature.get("lengthKm"),
                    json.dumps(feature["geometry"], ensure_ascii=False),
                    json.dumps(feature["analysisGeometry"], ensure_ascii=False),
                    feature.get("sourceUrl"),
                    json.dumps(properties, ensure_ascii=False),
                    geometry_version(feature),
                ),
            ).fetchone()[0]
            feature_ids.append(feature_id)
            connection.execute(
                """INSERT INTO vildaleder.feature_name(
                       feature_id, language_code, name, name_normalized, source_id, is_preferred
                ) VALUES (%s, 'und', %s, %s, %s, true)
                   ON CONFLICT (feature_id, language_code, name, source_id) DO UPDATE
                   SET name_normalized = EXCLUDED.name_normalized, is_preferred = true""",
                (feature_id, feature["name"], normalized_name(feature["name"]), source_id),
            )
        connection.execute(
            """UPDATE vildaleder.spatial_feature
               SET is_active = false, updated_at = now()
               WHERE properties->>'county' = %s
                 AND feature_id <> ALL(%s::bigint[])""",
            (COUNTY, feature_ids),
        )
        connection.commit()
        counts = dict(
            connection.execute(
                """SELECT feature_kind::text, count(*)
                   FROM vildaleder.spatial_feature
                   WHERE is_active AND properties->>'county' = %s
                   GROUP BY feature_kind""",
                (COUNTY,),
            ).fetchall()
        )
    return {"trails": counts.get("trail", 0), "reserves": counts.get("reserve", 0)}


def build_catalog(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "meta": {
            "generatedAt": iso_timestamp(),
            "bufferMeters": BUFFER_METERS,
            "area": COUNTY,
            "municipalities": sorted(MUNICIPALITIES.values()),
            "maximumObservationYears": 10,
            "sources": {
                "trails": "OpenStreetMap contributors",
                "reserves": "Naturvårdsverket, Naturvårdsregistret",
            },
        },
        "features": features,
    }


def main() -> int:
    args = parse_args()
    try:
        if args.from_file:
            catalog = json.loads(args.from_file.read_text(encoding="utf-8"))
            features = catalog["features"]
            trails = [feature for feature in features if feature["featureKind"] == "trail"]
            reserves = [feature for feature in features if feature["featureKind"] == "reserve"]
        else:
            trails = fetch_halland_routes()
            if args.trail_limit:
                trails = trails[: args.trail_limit]
            reserves = fetch_halland_reserves(args.reserve_limit)
            features = sorted(
                [*trails, *reserves],
                key=lambda item: (item["featureKind"], item["name"].casefold(), item["id"]),
            )
            write_json(args.output, build_catalog(features))
        stats = {"trails": len(trails), "reserves": len(reserves)}
        if args.database_url:
            stats["postgis"] = upsert_postgis(args.database_url, features)
    except (KeyError, OSError, ValueError, RefreshError, requests.RequestException, psycopg.Error) as exc:
        print(f"Feature sync failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
