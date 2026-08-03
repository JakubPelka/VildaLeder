#!/usr/bin/env python3
"""Import the checked public Skandobs snapshot into the canonical PostGIS store."""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Any

import psycopg


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "skandobs.json")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DSN; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def normalized_name(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    ).casefold().strip()


def ensure_source(connection: psycopg.Connection[Any]) -> int:
    return connection.execute(
        """INSERT INTO vildaleder.data_source(
               source_key, name, source_kind, base_url, attribution
           ) VALUES (
               'skandobs', 'Skandobs', 'observation',
               'https://www.skandobs.no/skandobsAPI/', 'Skandobs / SLU Viltskadecenter'
           )
           ON CONFLICT (source_key) DO UPDATE
           SET name = EXCLUDED.name,
               source_kind = EXCLUDED.source_kind,
               base_url = EXCLUDED.base_url,
               attribution = EXCLUDED.attribution
           RETURNING source_id"""
    ).fetchone()[0]


def upsert_taxa(
    connection: psycopg.Connection[Any], taxa: list[dict[str, Any]], source_id: int
) -> dict[str, int]:
    result: dict[str, int] = {}
    for taxon in taxa:
        external_id = str(taxon["sourceTaxonId"])
        taxon_id = connection.execute(
            """INSERT INTO vildaleder.taxon(
                   canonical_source_id, canonical_source_taxon_id, scientific_name,
                   organism_group, redlist_category, redlist_assessment
               ) VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (canonical_source_id, canonical_source_taxon_id) DO UPDATE
               SET scientific_name = EXCLUDED.scientific_name,
                   organism_group = EXCLUDED.organism_group,
                   redlist_category = EXCLUDED.redlist_category,
                   redlist_assessment = EXCLUDED.redlist_assessment,
                   updated_at = now()
               RETURNING taxon_id""",
            (
                source_id,
                external_id,
                taxon.get("scientificName"),
                taxon.get("organismGroup"),
                taxon.get("redlistCategory"),
                taxon.get("redlistAssessment"),
            ),
        ).fetchone()[0]
        result[str(taxon["taxonId"])] = taxon_id
        connection.execute(
            """INSERT INTO vildaleder.taxon_external_id(
                   taxon_id, source_id, external_id, is_accepted
               ) VALUES (%s, %s, %s, true)
               ON CONFLICT (source_id, external_id) DO UPDATE
               SET taxon_id = EXCLUDED.taxon_id, is_accepted = true""",
            (taxon_id, source_id, external_id),
        )
        names = [
            (taxon.get("scientificName"), "zxx", "scientific"),
            *(
                (name, language, "vernacular")
                for language, name in (taxon.get("vernacularNames") or {}).items()
            ),
        ]
        for name, language, kind in names:
            if not name:
                continue
            connection.execute(
                """INSERT INTO vildaleder.taxon_name(
                       taxon_id, language_code, name, name_normalized, name_kind,
                       source_id, is_preferred
                   ) VALUES (%s, %s, %s, %s, %s::vildaleder.taxon_name_kind, %s, true)
                   ON CONFLICT (taxon_id, language_code, name, name_kind, source_id) DO UPDATE
                   SET name_normalized = EXCLUDED.name_normalized, is_preferred = true""",
                (taxon_id, language, name, normalized_name(name), kind, source_id),
            )
    return result


def feature_database_id(connection: psycopg.Connection[Any], public_id: str) -> int:
    source_key, separator, source_feature_id = public_id.partition("-")
    if not separator or source_key not in {"osm", "nvr"}:
        raise RuntimeError(f"Unsupported public feature ID: {public_id}")
    row = connection.execute(
        """SELECT feature.feature_id
           FROM vildaleder.spatial_feature feature
           JOIN vildaleder.data_source source USING (source_id)
           WHERE source.source_key = %s AND feature.source_feature_id = %s""",
        (source_key, source_feature_id),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Feature not loaded before Skandobs import: {public_id}")
    return row[0]


def import_snapshot(snapshot: dict[str, Any], database_url: str) -> dict[str, int]:
    if not database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    meta = snapshot["meta"]
    generated_at = meta["generatedAt"]
    records = snapshot.get("records") or []
    active_source_ids = [str(record["id"]) for record in records]
    with psycopg.connect(database_url) as connection:
        source_id = ensure_source(connection)
        taxon_ids = upsert_taxa(connection, snapshot.get("taxa") or [], source_id)
        observation_ids: dict[str, int] = {}
        for record in records:
            observation_id = connection.execute(
                """INSERT INTO vildaleder.observation(
                       canonical_key, taxon_id, observed_on, individual_count, verified,
                       uncertain_identification, geom, location_is_public,
                       location_is_generalized, data_generalizations,
                       first_seen_at, last_seen_at
                   ) VALUES (
                       %s, %s, %s::date, %s, %s, %s,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326), true, %s, %s, %s, %s
                   )
                   ON CONFLICT (canonical_key) DO UPDATE
                   SET taxon_id = EXCLUDED.taxon_id,
                       observed_on = EXCLUDED.observed_on,
                       individual_count = EXCLUDED.individual_count,
                       verified = EXCLUDED.verified,
                       uncertain_identification = EXCLUDED.uncertain_identification,
                       geom = EXCLUDED.geom,
                       location_is_public = true,
                       location_is_generalized = EXCLUDED.location_is_generalized,
                       data_generalizations = EXCLUDED.data_generalizations,
                       last_seen_at = EXCLUDED.last_seen_at,
                       is_deleted = false,
                       updated_at = now()
                   RETURNING observation_id""",
                (
                    f"skandobs:{record['id']}",
                    taxon_ids.get(str(record["taxonId"])),
                    record["date"],
                    record.get("individualCount"),
                    int(record.get("validationId") or 0) == 5,
                    int(record.get("validationId") or 0) < 0,
                    record["longitude"],
                    record["latitude"],
                    bool(record.get("locationIsGeneralized")),
                    "Generalized by Skandobs" if record.get("locationIsGeneralized") else None,
                    generated_at,
                    generated_at,
                ),
            ).fetchone()[0]
            observation_ids[str(record["id"])] = observation_id
            connection.execute(
                """INSERT INTO vildaleder.observation_source_record(
                       observation_id, source_id, source_record_id, source_url,
                       is_primary, first_seen_at, last_seen_at
                   ) VALUES (%s, %s, %s, %s, true, %s, %s)
                   ON CONFLICT (source_id, source_record_id) DO UPDATE
                   SET observation_id = EXCLUDED.observation_id,
                       source_url = EXCLUDED.source_url,
                       is_primary = true,
                       last_seen_at = EXCLUDED.last_seen_at,
                       is_deleted = false""",
                (
                    observation_id,
                    source_id,
                    str(record["id"]),
                    record.get("sourceUrl"),
                    generated_at,
                    generated_at,
                ),
            )

        connection.execute(
            """UPDATE vildaleder.observation_source_record
               SET is_deleted = true, last_seen_at = %s
               WHERE source_id = %s
                 AND NOT (source_record_id = ANY(%s::text[]))""",
            (generated_at, source_id, active_source_ids),
        )
        connection.execute(
            """UPDATE vildaleder.observation observed
               SET is_deleted = source_record.is_deleted, updated_at = now()
               FROM vildaleder.observation_source_record source_record
               WHERE source_record.observation_id = observed.observation_id
                 AND source_record.source_id = %s""",
            (source_id,),
        )
        connection.execute(
            """DELETE FROM vildaleder.observation_feature matched
               USING vildaleder.observation_source_record source_record
               WHERE source_record.observation_id = matched.observation_id
                 AND source_record.source_id = %s""",
            (source_id,),
        )
        match_count = 0
        for public_feature_id, source_record_ids in (snapshot.get("matches") or {}).items():
            feature_id = feature_database_id(connection, public_feature_id)
            geometry_version = connection.execute(
                "SELECT geometry_version FROM vildaleder.spatial_feature WHERE feature_id = %s",
                (feature_id,),
            ).fetchone()[0]
            for source_record_id in source_record_ids:
                connection.execute(
                    """INSERT INTO vildaleder.observation_feature(
                           observation_id, feature_id, match_method,
                           feature_geometry_version, matched_at
                       ) VALUES (%s, %s, 'skandobs_public_snapshot', %s, %s)
                       ON CONFLICT (observation_id, feature_id) DO UPDATE
                       SET match_method = EXCLUDED.match_method,
                           feature_geometry_version = EXCLUDED.feature_geometry_version,
                           matched_at = EXCLUDED.matched_at""",
                    (
                        observation_ids[str(source_record_id)],
                        feature_id,
                        geometry_version,
                        generated_at,
                    ),
                )
                match_count += 1

        daily = connection.execute(
            "SELECT vildaleder.refresh_daily_feature_taxon(%s::date, %s::date)",
            (meta["windowStart"], meta["windowEnd"]),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO vildaleder.sync_run(
                   source_id, mode, started_at, completed_at, window_start, window_end,
                   status, records_seen, records_inserted
               ) VALUES (%s, 'import', %s, now(), %s, %s, 'complete', %s, %s)""",
            (
                source_id,
                generated_at,
                meta["windowStart"],
                meta["windowEnd"],
                meta.get("publicObservationsInArea", len(records)),
                len(records),
            ),
        )
        connection.commit()
    return {
        "observations": len(records),
        "matches": match_count,
        "taxa": len(taxon_ids),
        "dailyAggregatesRefreshed": daily,
    }


def main() -> int:
    args = parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        stats = import_snapshot(snapshot, args.database_url)
    except (KeyError, OSError, ValueError, RuntimeError, psycopg.Error) as exc:
        print(f"Skandobs PostGIS import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
