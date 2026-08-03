#!/usr/bin/env python3
"""Import a generated Halland snapshot into the canonical PostGIS schema."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import psycopg


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog.json")
    parser.add_argument("--search-index", type=Path, default=ROOT / "data" / "search-index.json")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DSN; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized_name(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    ).casefold().strip()


def source_ids(connection: psycopg.Connection[Any]) -> dict[str, int]:
    sources = (
        (
            "sos",
            "SLU Species Observation System / Artportalen",
            "observation",
            "https://api.artdatabanken.se/",
        ),
        ("osm", "OpenStreetMap", "spatial", "https://www.openstreetmap.org/"),
        (
            "nvr",
            "Naturvårdsregistret / Naturvårdsverket",
            "spatial",
            "https://geodata.naturvardsverket.se/naturvardsregistret/",
        ),
        (
            "nvl",
            "Leder och friluftsanordningar / Naturvårdsverket",
            "spatial",
            "https://geodata.naturvardsverket.se/nedladdning/friluftsliv/",
        ),
        ("gbif", "Global Biodiversity Information Facility", "taxonomy", "https://www.gbif.org/"),
        ("dyntaxa", "Dyntaxa / SLU Artdatabanken", "taxonomy", "https://www.dyntaxa.se/"),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO vildaleder.data_source(source_key, name, source_kind, base_url)
               VALUES (%s, %s, %s::vildaleder.source_kind, %s)
               ON CONFLICT (source_key) DO UPDATE
               SET name = EXCLUDED.name,
                   source_kind = EXCLUDED.source_kind,
                   base_url = EXCLUDED.base_url""",
            sources,
        )
    return dict(
        connection.execute(
            "SELECT source_key, source_id FROM vildaleder.data_source"
        ).fetchall()
    )


def upsert_taxa(
    connection: psycopg.Connection[Any],
    taxa: Iterable[dict[str, Any]],
    sos_source_id: int,
) -> dict[str, int]:
    taxon_ids: dict[str, int] = {}
    for item in taxa:
        external_id = str(item["taxonId"])
        taxon_id = connection.execute(
            """INSERT INTO vildaleder.taxon(
                   canonical_source_id,
                   canonical_source_taxon_id,
                   scientific_name,
                   organism_group,
                   redlist_category
               ) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (canonical_source_id, canonical_source_taxon_id) DO UPDATE
               SET scientific_name = COALESCE(EXCLUDED.scientific_name, vildaleder.taxon.scientific_name),
                   organism_group = COALESCE(EXCLUDED.organism_group, vildaleder.taxon.organism_group),
                   redlist_category = EXCLUDED.redlist_category,
                   updated_at = now()
               RETURNING taxon_id""",
            (
                sos_source_id,
                external_id,
                item.get("scientificName"),
                item.get("organismGroup"),
                item.get("redlistCategory"),
            ),
        ).fetchone()[0]
        taxon_ids[external_id] = taxon_id
        connection.execute(
            """INSERT INTO vildaleder.taxon_external_id(
                   taxon_id, source_id, external_id, is_accepted
               ) VALUES (%s, %s, %s, true)
               ON CONFLICT (source_id, external_id) DO UPDATE
               SET taxon_id = EXCLUDED.taxon_id,
                   is_accepted = EXCLUDED.is_accepted""",
            (taxon_id, sos_source_id, external_id),
        )
        names = (
            (item.get("scientificName"), "zxx", "scientific"),
            # refresh_data requests Swedish translations from SOS.
            (item.get("vernacularName"), "sv", "vernacular"),
        )
        for name, language_code, name_kind in names:
            if not name:
                continue
            connection.execute(
                """INSERT INTO vildaleder.taxon_name(
                       taxon_id,
                       language_code,
                       name,
                       name_normalized,
                       name_kind,
                       source_id,
                       is_preferred
                   ) VALUES (%s, %s, %s, %s, %s::vildaleder.taxon_name_kind, %s, true)
                   ON CONFLICT (taxon_id, language_code, name, name_kind, source_id) DO UPDATE
                   SET name_normalized = EXCLUDED.name_normalized,
                       is_preferred = EXCLUDED.is_preferred""",
                (
                    taxon_id,
                    language_code,
                    name,
                    normalized_name(name),
                    name_kind,
                    sos_source_id,
                ),
            )
    return taxon_ids


def upsert_features(
    connection: psycopg.Connection[Any],
    features: Iterable[dict[str, Any]],
    spatial_source_ids: dict[str, int],
    geometry_version: str,
) -> None:
    for feature in features:
        source_key, source_feature_id = feature_identity(feature)
        source_id = spatial_source_ids[source_key]
        properties = {
            key: feature.get(key)
            for key in (
                "municipality",
                "municipalities",
                "county",
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
        feature_id = connection.execute(
            """INSERT INTO vildaleder.spatial_feature(
                   feature_kind,
                   source_id,
                   source_feature_id,
                   canonical_name,
                   length_km,
                   geom,
                   analysis_geom,
                   source_url,
                   properties,
                   geometry_version
               ) VALUES (
                   %s::vildaleder.spatial_feature_kind, %s, %s, %s, %s,
                   ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                   ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                   %s, %s::jsonb, %s
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
                   is_active = true,
                   updated_at = now()
               RETURNING feature_id""",
            (
                feature.get("featureKind", "trail"),
                source_id,
                source_feature_id,
                feature["name"],
                feature.get("lengthKm"),
                json.dumps(feature["geometry"], ensure_ascii=False),
                json.dumps(feature["corridor"], ensure_ascii=False),
                feature.get("sourceUrl") or feature.get("osmUrl"),
                json.dumps(properties, ensure_ascii=False),
                geometry_version,
            ),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO vildaleder.feature_name(
                   feature_id, language_code, name, name_normalized, source_id, is_preferred
               ) VALUES (%s, 'und', %s, %s, %s, true)
               ON CONFLICT (feature_id, language_code, name, source_id) DO UPDATE
               SET name_normalized = EXCLUDED.name_normalized,
                   is_preferred = EXCLUDED.is_preferred""",
            (feature_id, feature["name"], normalized_name(feature["name"]), source_id),
        )


def feature_identity(feature: dict[str, Any]) -> tuple[str, str]:
    """Return the spatial source key and its stable feature identifier."""
    source_key = str(feature.get("source") or "osm")
    source_feature_id = feature.get("sourceFeatureId", feature.get("osmRelationId"))
    if source_feature_id is None:
        raise ValueError(f"Feature {feature.get('id') or feature.get('name')} has no source ID")
    return source_key, str(source_feature_id)


def partition_path(catalog_path: Path, manifest_path: str) -> Path:
    relative = Path(manifest_path)
    if relative.parts and relative.parts[0] == "data":
        relative = relative.relative_to("data")
    return catalog_path.parent / relative


def number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stage_snapshot(
    connection: psycopg.Connection[Any],
    catalog_path: Path,
    trails: Iterable[dict[str, Any]],
) -> int:
    connection.execute(
        """CREATE TEMP TABLE import_observation_match (
               source_record_id text NOT NULL,
               taxon_external_id text,
               observed_on date NOT NULL,
               individual_count double precision,
               verified boolean NOT NULL,
               uncertain_identification boolean NOT NULL,
               latitude double precision NOT NULL,
               longitude double precision NOT NULL,
               coordinate_uncertainty_m double precision,
               feature_source_key text NOT NULL,
               feature_source_id text NOT NULL
           ) ON COMMIT DROP"""
    )
    count = 0
    with connection.cursor().copy(
        """COPY import_observation_match(
               source_record_id, taxon_external_id, observed_on, individual_count,
               verified, uncertain_identification, latitude, longitude,
               coordinate_uncertainty_m, feature_source_key, feature_source_id
           ) FROM STDIN"""
    ) as copy:
        for trail in trails:
            feature_source_key, feature_source_id = feature_identity(trail)
            for manifest in trail.get("observationFiles", []):
                partition = load_json(partition_path(catalog_path, manifest["path"]))
                for record in partition.get("records", []):
                    if record[5] is None or record[6] is None:
                        continue
                    flags = int(record[4] or 0)
                    copy.write_row(
                        (
                            str(record[0]),
                            str(record[2]) if record[2] is not None else None,
                            record[1],
                            number_or_none(record[3]),
                            bool(flags & 1),
                            bool(flags & 2),
                            float(record[5]),
                            float(record[6]),
                            number_or_none(record[7]),
                            feature_source_key,
                            feature_source_id,
                        )
                    )
                    count += 1
    connection.execute(
        "CREATE INDEX import_observation_match_record_idx ON import_observation_match(source_record_id)"
    )
    connection.execute(
        "CREATE INDEX import_observation_match_feature_idx ON import_observation_match(feature_source_id)"
    )
    connection.execute("ANALYZE import_observation_match")
    return count


def import_observations(
    connection: psycopg.Connection[Any],
    sos_source_id: int,
    generated_at: str,
    window_start: str,
    window_end: str,
) -> tuple[int, int, int]:
    observations = connection.execute(
        """INSERT INTO vildaleder.observation(
               canonical_key,
               taxon_id,
               observed_on,
               individual_count,
               verified,
               uncertain_identification,
               geom,
               coordinate_uncertainty_m,
               first_seen_at,
               last_seen_at
           )
           SELECT DISTINCT ON (staged.source_record_id)
               'sos:' || staged.source_record_id,
               external.taxon_id,
               staged.observed_on,
               staged.individual_count,
               staged.verified,
               staged.uncertain_identification,
               ST_SetSRID(ST_MakePoint(staged.longitude, staged.latitude), 4326),
               staged.coordinate_uncertainty_m,
               %s::timestamptz,
               %s::timestamptz
           FROM import_observation_match staged
           LEFT JOIN vildaleder.taxon_external_id external
             ON external.source_id = %s
            AND external.external_id = staged.taxon_external_id
           ORDER BY staged.source_record_id, staged.feature_source_key, staged.feature_source_id
           ON CONFLICT (canonical_key) DO UPDATE
           SET taxon_id = EXCLUDED.taxon_id,
               observed_on = EXCLUDED.observed_on,
               individual_count = EXCLUDED.individual_count,
               verified = EXCLUDED.verified,
               uncertain_identification = EXCLUDED.uncertain_identification,
               geom = EXCLUDED.geom,
               coordinate_uncertainty_m = EXCLUDED.coordinate_uncertainty_m,
               last_seen_at = EXCLUDED.last_seen_at,
               is_deleted = false,
               updated_at = now()
           RETURNING observation_id""",
        (generated_at, generated_at, sos_source_id),
    ).rowcount

    source_records = connection.execute(
        """INSERT INTO vildaleder.observation_source_record(
               observation_id,
               source_id,
               source_record_id,
               source_url,
               is_primary,
               first_seen_at,
               last_seen_at
           )
           SELECT DISTINCT ON (staged.source_record_id)
               observed.observation_id,
               %s,
               staged.source_record_id,
               CASE WHEN staged.source_record_id ~ '^[0-9]+$'
                    THEN 'https://www.artportalen.se/sighting/' || staged.source_record_id
                    ELSE NULL END,
               true,
               %s::timestamptz,
               %s::timestamptz
           FROM import_observation_match staged
           JOIN vildaleder.observation observed
             ON observed.canonical_key = 'sos:' || staged.source_record_id
           ORDER BY staged.source_record_id
           ON CONFLICT (source_id, source_record_id) DO UPDATE
           SET observation_id = EXCLUDED.observation_id,
               source_url = EXCLUDED.source_url,
               is_primary = EXCLUDED.is_primary,
               last_seen_at = EXCLUDED.last_seen_at,
               is_deleted = false
           RETURNING observation_id""",
        (sos_source_id, generated_at, generated_at),
    ).rowcount

    connection.execute(
        """DELETE FROM vildaleder.observation_feature matched
           USING vildaleder.observation observed,
                 vildaleder.observation_source_record source_record,
                 vildaleder.spatial_feature feature,
                 vildaleder.data_source feature_source
           WHERE matched.observation_id = observed.observation_id
             AND source_record.observation_id = observed.observation_id
             AND source_record.source_id = %s
             AND matched.feature_id = feature.feature_id
             AND feature.source_id = feature_source.source_id
             AND EXISTS (
                 SELECT 1
                 FROM import_observation_match staged
                 WHERE staged.feature_source_key = feature_source.source_key
                   AND staged.feature_source_id = feature.source_feature_id
             )
             AND observed.observed_on BETWEEN %s::date AND %s::date""",
        (sos_source_id, window_start, window_end),
    )
    matches = connection.execute(
        """INSERT INTO vildaleder.observation_feature(
               observation_id,
               feature_id,
               match_method,
               feature_geometry_version,
               matched_at
           )
           SELECT DISTINCT
               observed.observation_id,
               feature.feature_id,
               'snapshot_corridor_match',
               feature.geometry_version,
               %s::timestamptz
           FROM import_observation_match staged
           JOIN vildaleder.observation observed
             ON observed.canonical_key = 'sos:' || staged.source_record_id
           JOIN vildaleder.spatial_feature feature
             ON feature.source_feature_id = staged.feature_source_id
           JOIN vildaleder.data_source feature_source
             ON feature_source.source_id = feature.source_id
            AND feature_source.source_key = staged.feature_source_key
           ON CONFLICT (observation_id, feature_id) DO UPDATE
           SET match_method = EXCLUDED.match_method,
               feature_geometry_version = EXCLUDED.feature_geometry_version,
               matched_at = EXCLUDED.matched_at
           RETURNING observation_id""",
        (generated_at,),
    ).rowcount
    return observations, source_records, matches


def build_import(
    catalog_path: Path,
    index_path: Path,
    database_url: str,
) -> dict[str, int]:
    if not database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    catalog = load_json(catalog_path)
    index = load_json(index_path)
    meta = catalog["meta"]
    with psycopg.connect(database_url) as connection:
        connection.execute("SET search_path TO vildaleder, public")
        sources = source_ids(connection)
        taxon_ids = upsert_taxa(connection, index["taxa"], sources["sos"])
        upsert_features(connection, catalog["trails"], sources, meta["generatedAt"])
        staged = stage_snapshot(connection, catalog_path, catalog["trails"])
        observations, source_records, matches = import_observations(
            connection,
            sources["sos"],
            meta["generatedAt"],
            meta["windowStart"],
            meta["windowEnd"],
        )
        daily = connection.execute(
            "SELECT vildaleder.refresh_daily_feature_taxon(%s::date, %s::date)",
            (meta["windowStart"], meta["windowEnd"]),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO vildaleder.sync_run(
                   source_id, mode, started_at, completed_at, window_start, window_end,
                   status, records_seen, records_inserted, records_updated
               ) VALUES (
                   %s, 'import', %s::timestamptz, now(), %s::date, %s::date,
                   'complete', %s, %s, %s
               )""",
            (
                sources["sos"],
                meta["generatedAt"],
                meta["windowStart"],
                meta["windowEnd"],
                staged,
                source_records,
                observations - source_records if observations > source_records else 0,
            ),
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO vildaleder.metadata(key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                (
                    ("snapshot_generated_at", meta["generatedAt"]),
                    ("snapshot_window_start", meta["windowStart"]),
                    ("snapshot_window_end", meta["windowEnd"]),
                ),
            )
            cursor.executemany(
                """INSERT INTO vildaleder.metadata(key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                (
                    (
                        f"sos_complete:{trail['id']}:{meta['windowStart']}:{meta['windowEnd']}",
                        meta["generatedAt"],
                    )
                    for trail in catalog["trails"]
                ),
            )
        connection.commit()
        unique_observations = connection.execute(
            "SELECT count(*) FROM vildaleder.observation"
        ).fetchone()[0]
        feature_count = connection.execute(
            "SELECT count(*) FROM vildaleder.spatial_feature WHERE is_active"
        ).fetchone()[0]
        name_count = connection.execute("SELECT count(*) FROM vildaleder.taxon_name").fetchone()[0]
        aggregate_count = connection.execute(
            "SELECT count(*) FROM vildaleder.daily_feature_taxon"
        ).fetchone()[0]
    return {
        "features": feature_count,
        "taxa": len(taxon_ids),
        "taxonNames": name_count,
        "uniqueObservations": unique_observations,
        "snapshotMatches": matches,
        "dailyAggregates": aggregate_count,
        "stagedRows": staged,
        "aggregateRowsRefreshed": daily,
    }


def main() -> int:
    args = parse_args()
    try:
        stats = build_import(args.catalog, args.search_index, args.database_url)
    except (OSError, ValueError, RuntimeError, psycopg.Error) as exc:
        print(f"PostGIS import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
