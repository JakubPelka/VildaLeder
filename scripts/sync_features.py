#!/usr/bin/env python3
"""Synchronise trails and nature destinations into GeoJSON and PostGIS.

Trails come from named OSM hiking/foot route relations, queried municipality by
municipality to keep Overpass requests bounded, and from Naturvårdsverket's
national outdoor-recreation dataset. Protected areas come from the authoritative
Naturvårdsregistret REST API. Bird hides, observation towers and observation
platforms come from Naturvårdsverket. Every feature receives an analysis geometry
consisting of the route corridor, protected area, or destination plus 200 metres.
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
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    mapping,
    shape,
)
from shapely.ops import transform, unary_union

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
NVL_TRAILS_WFS_URL = "https://geodata.naturvardsverket.se/leder_friluftsliv/wfs"
NVL_DESTINATIONS_WFS_URL = (
    "https://geodata.naturvardsverket.se/anordningar_friluftsliv/wfs"
)
NVL_TRAILS_TYPENAME = "Leder_friluftsliv_WFS:LED"
NVL_DESTINATIONS_TYPENAME = "Anordningar_friluftsliv_WFS:ANORDNINGAR"
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
NVL_WALKING_TYPES = ("Vandringsled", "Naturstig", "Omarkerad stig", "Elljusspår")
NVL_DESTINATION_TYPES = {
    "Fågeltorn": "observation_tower",
    "Utsiktstorn": "observation_tower",
    "Gömsle": "bird_hide",
    "Observationsplattform": "observation_site",
    "Utsikt": "observation_site",
}
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


def clean_nvl_name(value: Any, fallback: str) -> str:
    text = str(value or "").replace('\\"', '"').strip().strip('"').strip()
    return text or fallback


def overpass_query(municipality_code: str) -> str:
    return (
        "[out:json][timeout:180];"
        f'area["boundary"="administrative"]["ref:scb"="{municipality_code}"]->.searchArea;'
        'relation(area.searchArea)["type"="route"]["route"~"^(hiking|foot)$"]["name"];'
        "out body geom;"
    )



def query_overpass(query: str, attempts: int = 3) -> dict:
    failures = []
    session = new_session()
    for url in OVERPASS_URLS:
        try:
            return request_json(
                session,
                "POST",
                url,
                attempts=attempts,
                data={"data": query},
                timeout=180,
            )
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    raise RefreshError(f"Overpass API failed on all endpoints: {failures}")

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


def fetch_routes(county: str, municipalities: dict[str, str]) -> list[dict[str, Any]]:
    relations: dict[int, dict[str, Any]] = {}
    memberships: dict[int, set[str]] = defaultdict(set)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fetch_municipality_routes, code, name): name
            for code, name in municipalities.items()
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
        try:
            lines = relation_lines(relation)
        except RefreshError as exc:
            print(f"Skipping {relation.get('tags', {}).get('name', 'relation')} ({relation_id}): {exc}", file=sys.stderr, flush=True)
            continue
            
        geometry, analysis_geometry, length_km = trail_geometry(lines)
        tags = relation.get("tags", {})
        member_municipalities = sorted(memberships[relation_id])
        features.append(
            {
                "id": f"osm-{relation_id}",
                "featureKind": "trail",
                "source": "osm",
                "sourceFeatureId": str(relation_id),
                "name": tags.get("name") or f"OSM route {relation_id}",
                "county": county,
                "municipalities": member_municipalities,
                "municipality": member_municipalities[0] if member_municipalities else None,
                "lengthKm": round(length_km, 1),
                "network": tags.get("network"),
                "operator": tags.get("operator"),
                "sourceUrl": f"https://www.openstreetmap.org/relation/{relation_id}",
                "geometry": geometry,
                "analysisGeometry": analysis_geometry,
            }
        )
    return sorted(features, key=lambda item: (item["name"].casefold(), item["id"]))


def fetch_halland_routes() -> list[dict[str, Any]]:
    return fetch_routes(COUNTY, MUNICIPALITIES)


def overpass_destinations_query(municipality_code: str) -> str:
    return (
        "[out:json][timeout:180];"
        f'area["boundary"="administrative"]["ref:scb"="{municipality_code}"]->.searchArea;'
        '('
        'node(area.searchArea)["leisure"="bird_hide"];'
        'node(area.searchArea)["man_made"="tower"]["tower:type"="observation"];'
        'node(area.searchArea)["tourism"="viewpoint"];'
        'nwr(area.searchArea)["leisure"="park"];'
        'nwr(area.searchArea)["landuse"="cemetery"];'
        'nwr(area.searchArea)["leisure"="garden"];'
        ');'
        'out center geom;'
    )

def fetch_osm_destinations(county: str, municipalities: dict[str, str]) -> list[dict[str, Any]]:
    features = []
    to_sweref = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True).transform
    to_wgs84 = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        for code, name in municipalities.items():
            futures[executor.submit(
                query_overpass,
                overpass_destinations_query(code),
            )] = name
        for future in as_completed(futures):
            municipality = futures[future]
            try:
                result = future.result()
                elements = result.get("elements", [])
                for element in elements:
                    tags = element.get("tags", {})
                    if tags.get("leisure") in ("park", "garden") or tags.get("landuse") == "cemetery":
                        feature_kind = "urban_green"
                    elif tags.get("leisure") == "bird_hide":
                        feature_kind = "bird_hide"
                    elif tags.get("tourism") == "viewpoint":
                        feature_kind = "observation_site"
                    else:
                        feature_kind = "observation_tower"
                    
                    element_id = element["id"]
                    element_type = element.get("type", "node")
                    
                    lat = element.get("lat")
                    lon = element.get("lon")
                    if lat is None or lon is None:
                        center = element.get("center", {})
                        lat = center.get("lat")
                        lon = center.get("lon")
                    
                    if lat is None or lon is None:
                        continue
                    display_geometry = Point(lon, lat)
                    if feature_kind == "urban_green":
                        polygon_geometry = element.get("geometry", [])
                        if not polygon_geometry:
                            continue
                        try:
                            # Build polygon and calculate area in square meters using SWEREF 99 TM
                            poly_points = [(pt["lon"], pt["lat"]) for pt in polygon_geometry]
                            if len(poly_points) >= 3:
                                area_polygon = Polygon(poly_points)
                                area_sqm = transform(to_sweref, area_polygon).area
                                if area_sqm < 10000:
                                    continue
                                display_geometry = area_polygon
                        except BaseException:
                            pass
                        
                    analysis = transform(to_wgs84, transform(to_sweref, display_geometry).buffer(BUFFER_METERS))
                    
                    name = tags.get("name") or f"OSM {feature_kind.replace('_', ' ')} {element_id}"
                    
                    features.append({
                        "id": f"osm-{element_type}-{element_id}",
                        "featureKind": feature_kind,
                        "source": "osm",
                        "sourceFeatureId": f"{element_type}-{element_id}",
                        "name": name,
                        "county": county,
                        "municipalities": [municipality],
                        "municipality": municipality,
                        "sourceUrl": f"https://www.openstreetmap.org/{element_type}/{element_id}",
                        "geometry": mapping(display_geometry),
                        "analysisGeometry": mapping(analysis),
                    })
            except Exception as exc:
                print(f"Failed OSM destinations in {municipality}: {exc}", file=sys.stderr)
    return features


def nvl_county_label(county: str) -> str:
    special = {
        "Halland": "Hallands Län",
        "Kronoberg": "Kronobergs Län",
        "Jönköping": "Jönköpings Län",
        "Västra Götaland": "Västra Götalands Län",
        "Östergötland": "Östergötlands Län",
        "Gotland": "Gotlands Län",
        "Södermanland": "Södermanlands Län",
        "Värmland": "Värmlands Län",
        "Västmanland": "Västmanlands Län",
        "Stockholm": "Stockholms Län",
        "Dalarna": "Dalarnas Län",
        "Gävleborg": "Gävleborgs Län",
        "Västernorrland": "Västernorrlands Län",
        "Jämtland": "Jämtlands Län",
        "Västerbotten": "Västerbottens Län",
        "Norrbotten": "Norrbottens Län",
    }
    return special.get(county, f"{county} Län")


def nvl_filter(county: str) -> str:
    return (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0">'
        "<fes:PropertyIsEqualTo>"
        "<fes:ValueReference>Län</fes:ValueReference>"
        f"<fes:Literal>{nvl_county_label(county)}</fes:Literal>"
        "</fes:PropertyIsEqualTo>"
        "</fes:Filter>"
    )


def fetch_nvl_rows(url: str, typename: str, county: str) -> list[dict[str, Any]]:
    payload = request_json(
        new_session(),
        "GET",
        url,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": typename,
            "outputFormat": "GEOJSON",
            "filter": nvl_filter(county),
        },
        timeout=180,
    )
    rows = payload.get("features", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        raise RefreshError("Naturvårdsverket returned an unexpected WFS response")
    return rows


def multilineal(geometry: Any) -> MultiLineString:
    if isinstance(geometry, LineString):
        return MultiLineString([geometry])
    if isinstance(geometry, MultiLineString):
        return geometry
    lines = [part for part in getattr(geometry, "geoms", []) if isinstance(part, LineString)]
    if not lines:
        raise RefreshError(f"NVV trail geometry is not linear: {geometry.geom_type}")
    return MultiLineString(lines)


def fetch_nvl_trails(county: str) -> list[dict[str, Any]]:
    rows = fetch_nvl_rows(NVL_TRAILS_WFS_URL, NVL_TRAILS_TYPENAME, county)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        properties = row.get("properties") or {}
        trail_type = str(properties.get("Typ_av_led") or "")
        if not any(kind in trail_type for kind in NVL_WALKING_TYPES):
            continue
        trail_id = str(properties.get("Led_ID") or "").strip()
        if trail_id and row.get("geometry"):
            grouped[trail_id].append(row)

    features = []
    for trail_id, segments in grouped.items():
        properties = segments[0].get("properties") or {}
        combined = multilineal(unary_union([shape(segment["geometry"]) for segment in segments]))
        geometry, analysis_geometry, length_km = trail_geometry(combined)
        municipalities = sorted(
            {
                str(segment.get("properties", {}).get("Kommun") or "").strip()
                for segment in segments
                if str(segment.get("properties", {}).get("Kommun") or "").strip()
            }
        )
        features.append(
            {
                "id": f"nvl-led-{trail_id}",
                "featureKind": "trail",
                "source": "nvl",
                "sourceFeatureId": f"led-{trail_id}",
                "name": clean_nvl_name(
                    properties.get("Lednamn"), f"Vandringsled {trail_id}"
                ),
                "county": county,
                "municipalities": municipalities,
                "municipality": municipalities[0] if municipalities else None,
                # Keep metre-level precision for short access paths; rounding a
                # 40 metre official trail to one decimal would expose 0.0 km.
                "lengthKm": round(length_km, 3),
                "trailType": properties.get("Typ_av_led"),
                "trailCategory": properties.get("Ledkategori"),
                "description": properties.get("Beskrivning"),
                "marking": properties.get("Ledmarkering"),
                "protectedArea": properties.get("Skyddat_område"),
                "protectedAreaId": properties.get("Skyddat_område_ID"),
                "sourceUrl": "https://www.naturvardsverket.se/amnesomraden/friluftsliv/",
                "geometry": geometry,
                "analysisGeometry": analysis_geometry,
            }
        )
    return sorted(features, key=lambda item: (item["name"].casefold(), item["id"]))


def destination_point(geometry: Any) -> Point:
    if isinstance(geometry, Point):
        return geometry
    if isinstance(geometry, MultiPoint):
        if not geometry.geoms:
            raise RefreshError("NVV destination has an empty point geometry")
        return geometry.centroid
    raise RefreshError(f"NVV destination geometry is not a point: {geometry.geom_type}")


def nvl_destination_feature(row: dict[str, Any], county: str) -> dict[str, Any]:
    properties = row.get("properties") or {}
    destination_id = str(properties["Anordning_ID"])
    feature_kind = NVL_DESTINATION_TYPES[str(properties["Typ"])]
    point = destination_point(shape(row["geometry"]))
    to_sweref = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True).transform
    to_wgs84 = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True).transform
    analysis = transform(to_wgs84, transform(to_sweref, point).buffer(BUFFER_METERS))
    municipality = str(properties.get("Kommun") or "").strip()
    return {
        "id": f"nvl-site-{destination_id}",
        "featureKind": feature_kind,
        "source": "nvl",
        "sourceFeatureId": f"site-{destination_id}",
        "name": clean_nvl_name(
            properties.get("Anordningsnamn"), f"{properties['Typ']} {destination_id}"
        ),
        "county": county,
        "municipalities": [municipality] if municipality else [],
        "municipality": municipality or None,
        "destinationType": properties.get("Typ"),
        "destinationSubtype": properties.get("Undertyp"),
        "description": properties.get("Beskrivning"),
        "protectedArea": properties.get("Skyddat_område"),
        "protectedAreaId": properties.get("Skyddat_område_ID"),
        "sourceUrl": "https://www.naturvardsverket.se/amnesomraden/friluftsliv/",
        "geometry": mapping(point),
        "analysisGeometry": mapping(analysis),
    }


def fetch_nvl_destinations(county: str) -> list[dict[str, Any]]:
    rows = fetch_nvl_rows(NVL_DESTINATIONS_WFS_URL, NVL_DESTINATIONS_TYPENAME, county)
    features = [
        nvl_destination_feature(row, county)
        for row in rows
        if str((row.get("properties") or {}).get("Typ")) in NVL_DESTINATION_TYPES
        and row.get("geometry")
    ]
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


def reserve_feature(
    item: dict[str, Any],
    county: str = COUNTY,
    feature_kind: str = "reserve",
) -> dict[str, Any]:
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
        "featureKind": feature_kind,
        "source": "nvr",
        "sourceFeatureId": reserve_id,
        "name": item.get("namn") or (
            f"Nationalpark {reserve_id}" if feature_kind == "national_park"
            else f"Naturreservat {reserve_id}"
        ),
        "county": county,
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


def fetch_protected_areas(
    county: str,
    nvr_county_code: str,
    protection_code: str,
    feature_kind: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows = request_json(
        new_session(),
        "GET",
        f"{NVR_BASE_URL}/omrade",
        params={
            "lan": nvr_county_code,
            "skyddstypkod": protection_code,
            "beslutsstatus": "Gällande",
            "limit": 1_000,
        },
        timeout=120,
    )
    if not isinstance(rows, list):
        raise RefreshError("Naturvårdsregistret returned an unexpected protected-area list")
    if limit:
        rows = rows[:limit]
    features = []
    with ThreadPoolExecutor(max_workers=NVR_WORKERS) as executor:
        futures = {
            executor.submit(reserve_feature, item, county, feature_kind): item for item in rows
        }
        completed = 0
        for future in as_completed(futures):
            item = futures[future]
            try:
                features.append(future.result())
            except Exception as exc:
                raise RefreshError(
                    f"Failed to fetch protected area {item.get('namn') or item.get('id')}: {exc}"
                ) from exc
            completed += 1
            if completed % 25 == 0 or completed == len(rows):
                print(f"{feature_kind} geometries: {completed}/{len(rows)}", file=sys.stderr)
    return sorted(features, key=lambda item: (item["name"].casefold(), item["id"]))


def fetch_reserves(
    county: str,
    nvr_county_code: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return fetch_protected_areas(county, nvr_county_code, "NR", "reserve", limit)


def fetch_national_parks(
    county: str,
    nvr_county_code: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return fetch_protected_areas(
        county, nvr_county_code, "NP", "national_park", limit
    )


def fetch_halland_reserves(limit: int | None = None) -> list[dict[str, Any]]:
    return fetch_reserves(COUNTY, "N", limit)


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
        (
            "nvl",
            "Leder och friluftsanordningar / Naturvårdsverket",
            "https://geodata.naturvardsverket.se/nedladdning/friluftsliv/",
            "Naturvårdsverket, Leder och friluftsanordningar",
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
            "SELECT source_key, source_id FROM vildaleder.data_source "
            "WHERE source_key IN ('osm', 'nvr', 'nvl')"
        ).fetchall()
    )


def upsert_postgis(
    database_url: str,
    features: list[dict[str, Any]],
    county: str = COUNTY,
    *,
    deactivate_missing: bool = True,
) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        sources = ensure_sources(connection)
        existing = {
            (source_key, source_feature_id): (version, properties)
            for source_key, source_feature_id, version, properties in connection.execute(
                """SELECT source.source_key, feature.source_feature_id,
                          feature.geometry_version, feature.properties
                   FROM vildaleder.spatial_feature feature
                   JOIN vildaleder.data_source source USING (source_id)
                   WHERE source.source_key IN ('osm', 'nvr', 'nvl')"""
            ).fetchall()
        }
        feature_ids = []
        invalidated_features = []
        for feature in features:
            source_id = sources[feature["source"]]
            new_geometry_version = geometry_version(feature)
            existing_row = existing.get((feature["source"], str(feature["sourceFeatureId"])))
            previous_geometry_version = existing_row[0] if existing_row else None
            previous_properties = existing_row[1] if existing_row else {}
            counties = set(previous_properties.get("counties") or [])
            if previous_properties.get("county"):
                counties.add(str(previous_properties["county"]))
            counties.add(str(feature["county"]))
            municipalities = set(previous_properties.get("municipalities") or [])
            municipalities.update(feature.get("municipalities") or [])
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
                    "trailType",
                    "trailCategory",
                    "description",
                    "marking",
                    "destinationType",
                    "destinationSubtype",
                    "protectedArea",
                    "protectedAreaId",
                )
                if feature.get(key) is not None
            }
            properties["county"] = previous_properties.get("county") or feature["county"]
            properties["counties"] = sorted(counties)
            properties["municipalities"] = sorted(municipalities)
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
                    new_geometry_version,
                ),
            ).fetchone()[0]
            feature_ids.append(feature_id)
            if (
                previous_geometry_version is not None
                and previous_geometry_version != new_geometry_version
            ):
                invalidated_features.append(feature["id"])
            connection.execute(
                """INSERT INTO vildaleder.feature_name(
                       feature_id, language_code, name, name_normalized, source_id, is_preferred
                ) VALUES (%s, 'und', %s, %s, %s, true)
                   ON CONFLICT (feature_id, language_code, name, source_id) DO UPDATE
                   SET name_normalized = EXCLUDED.name_normalized, is_preferred = true""",
                (feature_id, feature["name"], normalized_name(feature["name"]), source_id),
            )
        with connection.cursor() as cursor:
            cursor.executemany(
                """DELETE FROM vildaleder.metadata
                   WHERE key LIKE %s OR key LIKE %s""",
                (
                    (f"sos_window:{feature_id}:%", f"sos_complete:{feature_id}:%")
                    for feature_id in invalidated_features
                ),
            )
        if deactivate_missing:
            connection.execute(
                """UPDATE vildaleder.spatial_feature
                   SET is_active = false, updated_at = now()
                   WHERE (properties->>'county' = %s OR properties->'counties' ? %s)
                     AND feature_id <> ALL(%s::bigint[])""",
                (county, county, feature_ids),
            )
        connection.commit()
        counts = dict(
            connection.execute(
                """SELECT feature_kind::text, count(*)
                   FROM vildaleder.spatial_feature
                   WHERE is_active
                     AND (properties->>'county' = %s OR properties->'counties' ? %s)
                   GROUP BY feature_kind""",
                (county, county),
            ).fetchall()
        )
    return {
        "trails": counts.get("trail", 0),
        "reserves": counts.get("reserve", 0),
        "nationalParks": counts.get("national_park", 0),
        "birdHides": counts.get("bird_hide", 0),
        "observationTowers": counts.get("observation_tower", 0),
        "observationSites": counts.get("observation_site", 0),
        "invalidated": len(invalidated_features),
    }


def build_catalog(
    features: list[dict[str, Any]],
    county: str = COUNTY,
    municipalities: dict[str, str] = MUNICIPALITIES,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "meta": {
            "generatedAt": iso_timestamp(),
            "bufferMeters": BUFFER_METERS,
            "area": county,
            "municipalities": sorted(municipalities.values()),
            "maximumObservationYears": 10,
            "sources": {
                "trails": (
                    "OpenStreetMap contributors; Naturvårdsverket, "
                    "Leder och friluftsanordningar"
                ),
                "reserves": "Naturvårdsverket, Naturvårdsregistret",
                "nationalParks": "Naturvårdsverket, Naturvårdsregistret",
                "destinations": "Naturvårdsverket, Leder och friluftsanordningar",
            },
        },
        "features": features,
    }


def deduplicate_features(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # primary is kept, secondary is dropped if it intersects a primary feature significantly.
    kept = list(primary)
    primary_shapes = [(p, shape(p["analysisGeometry"])) for p in primary]
    for sec in secondary:
        sec_shape = shape(sec["analysisGeometry"])
        sec_name = sec["name"].casefold()
        is_duplicate = False
        for pri, pri_shape in primary_shapes:
            if sec_shape.intersects(pri_shape):
                intersection = sec_shape.intersection(pri_shape)
                ratio = intersection.area / min(sec_shape.area, pri_shape.area)
                if ratio > 0.85:
                    is_duplicate = True
                    break
                elif ratio > 0.30 and sec_name == pri["name"].casefold():
                    is_duplicate = True
                    break
        if not is_duplicate:
            kept.append(sec)
    return kept

def main() -> int:
    args = parse_args()
    try:
        if args.from_file:
            catalog = json.loads(args.from_file.read_text(encoding="utf-8"))
            features = catalog["features"]
            trails = [feature for feature in features if feature["featureKind"] == "trail"]
            reserves = [feature for feature in features if feature["featureKind"] == "reserve"]
        else:
            osm_routes = fetch_halland_routes()
            nvv_routes = fetch_nvl_trails(COUNTY)
            if args.trail_limit:
                osm_routes = osm_routes[: args.trail_limit]
                nvv_routes = nvv_routes[: args.trail_limit]
            trails = deduplicate_features(nvv_routes, osm_routes)
            
            reserves = fetch_halland_reserves(args.reserve_limit)
            national_parks = fetch_national_parks(COUNTY, "N")
            
            osm_dests = fetch_osm_destinations(COUNTY, MUNICIPALITIES)
            nvv_dests = fetch_nvl_destinations(COUNTY)
            destinations = deduplicate_features(nvv_dests, osm_dests)
            
            features = sorted(
                [*trails, *reserves, *national_parks, *destinations],
                key=lambda item: (item["featureKind"], item["name"].casefold(), item["id"]),
            )
            write_json(args.output, build_catalog(features))
        if args.from_file:
            national_parks = [
                feature for feature in features if feature["featureKind"] == "national_park"
            ]
            destinations = [
                feature
                for feature in features
                if feature["featureKind"] in NVL_DESTINATION_TYPES.values()
            ]
        stats = {
            "trails": len(trails),
            "reserves": len(reserves),
            "nationalParks": len(national_parks),
            "destinations": len(destinations),
        }
        if args.database_url:
            stats["postgis"] = upsert_postgis(args.database_url, features)
    except (KeyError, OSError, ValueError, RefreshError, requests.RequestException, psycopg.Error) as exc:
        print(f"Feature sync failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
