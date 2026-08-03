#!/usr/bin/env python3
"""Export complete PostGIS feature coverage into the lazy static web snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

try:
    from scripts.refresh_data import OBSERVATION_FIELDS, partition_name
except ModuleNotFoundError:  # Direct execution adds scripts/ rather than the repository root.
    from refresh_data import OBSERVATION_FIELDS, partition_name  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
SPECIES_BUCKET_COUNT = 256


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=ROOT / "data" / "features.json")
    parser.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog.json")
    parser.add_argument("--search-index", type=Path, default=ROOT / "data" / "search-index.json")
    parser.add_argument("--observations-dir", type=Path, default=ROOT / "data" / "observations")
    parser.add_argument(
        "--species-observations-dir",
        type=Path,
        default=ROOT / "data" / "species-observations",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def covered_public_ids(connection: psycopg.Connection[Any]) -> set[str]:
    return {
        key.split(":", 2)[1]
        for (key,) in connection.execute(
            "SELECT key FROM vildaleder.metadata WHERE key LIKE 'sos_complete:%%'"
        ).fetchall()
    }


def source_id_value(value: str) -> str | int:
    return int(value) if value.isdigit() else value


def compact_number(value: Any, *, empty: Any = None) -> Any:
    if value is None:
        return empty
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def compact_record(row: tuple[Any, ...]) -> list[Any]:
    (
        source_record_id,
        observed_on,
        taxon_external_id,
        individual_count,
        verified,
        uncertain,
        latitude,
        longitude,
        uncertainty,
    ) = row
    flags = int(bool(verified)) | (int(bool(uncertain)) << 1)
    return [
        source_id_value(str(source_record_id)),
        observed_on.isoformat(),
        source_id_value(str(taxon_external_id)) if taxon_external_id is not None else None,
        compact_number(individual_count, empty=""),
        flags,
        round(float(latitude), 6),
        round(float(longitude), 6),
        compact_number(uncertainty),
    ]


def species_bucket(taxon_external_id: Any) -> str:
    checksum = zlib.crc32(str(taxon_external_id).encode("utf-8"))
    return f"{checksum % SPECIES_BUCKET_COUNT:02x}"


def export_species_partitions(
    connection: psycopg.Connection[Any],
    feature_database_ids: dict[int, str],
    output_root: Path,
    snapshot_end: date,
    start: date,
    end: date,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Export each public SOS point once, with compact matched-feature ordinals."""
    feature_ordinals = {
        database_id: ordinal
        for ordinal, (database_id, _) in enumerate(
            sorted(feature_database_ids.items(), key=lambda item: item[1])
        )
    }
    manifests: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_partition = ""
    bucket_records: dict[str, list[list[Any]]] = defaultdict(list)
    total = 0

    def flush_partition(partition: str) -> None:
        if not partition:
            return
        for bucket, records in sorted(bucket_records.items()):
            records.sort(key=lambda record: (record[1], str(record[0])), reverse=True)
            relative = Path(bucket) / f"{partition}.json"
            write_json(output_root / relative, {"schemaVersion": 1, "records": records})
            manifests[bucket].append(
                {
                    "path": str(Path("data") / "species-observations" / relative),
                    "start": min(record[1] for record in records),
                    "end": max(record[1] for record in records),
                    "count": len(records),
                }
            )
        bucket_records.clear()

    query = """SELECT min(record.source_record_id),
                      observed.observed_on,
                      external.external_id,
                      observed.individual_count,
                      observed.verified,
                      observed.uncertain_identification,
                      ST_Y(observed.geom),
                      ST_X(observed.geom),
                      observed.coordinate_uncertainty_m,
                      array_agg(DISTINCT matched.feature_id ORDER BY matched.feature_id)
               FROM vildaleder.observation_feature matched
               JOIN vildaleder.observation observed USING (observation_id)
               JOIN vildaleder.observation_source_record record USING (observation_id)
               JOIN vildaleder.data_source source ON source.source_id = record.source_id
               LEFT JOIN vildaleder.taxon_external_id external
                 ON external.taxon_id = observed.taxon_id
                AND external.source_id = source.source_id
               WHERE matched.feature_id = ANY(%s)
                 AND source.source_key = 'sos'
                 AND NOT observed.is_deleted
                 AND observed.location_is_public
                 AND NOT record.is_deleted
                 AND observed.observed_on BETWEEN %s AND %s
                 AND external.external_id IS NOT NULL
               GROUP BY observed.observation_id, external.external_id
               ORDER BY observed.observed_on, min(record.source_record_id)"""
    with connection.cursor(name="species_point_export") as cursor:
        cursor.itersize = 10_000
        cursor.execute(query, (list(feature_database_ids), start, end))
        for row in cursor:
            partition = partition_name(row[1].isoformat(), snapshot_end)
            if current_partition and partition != current_partition:
                flush_partition(current_partition)
            current_partition = partition
            compact = compact_record(row[:9])
            compact.append(
                sorted(
                    feature_ordinals[feature_id]
                    for feature_id in row[9]
                    if feature_id in feature_ordinals
                )
            )
            bucket_records[species_bucket(row[2])].append(compact)
            total += 1
    flush_partition(current_partition)
    return dict(sorted(manifests.items())), total


def export_feature_partitions(
    connection: psycopg.Connection[Any],
    feature_database_id: int,
    public_id: str,
    output_root: Path,
    snapshot_end: date,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], int]:
    partitions: dict[str, list[list[Any]]] = defaultdict(list)
    rows = connection.execute(
        """SELECT DISTINCT
                  record.source_record_id,
                  observed.observed_on,
                  external.external_id,
                  observed.individual_count,
                  observed.verified,
                  observed.uncertain_identification,
                  ST_Y(observed.geom),
                  ST_X(observed.geom),
                  observed.coordinate_uncertainty_m
           FROM vildaleder.observation_feature matched
           JOIN vildaleder.observation observed USING (observation_id)
           JOIN vildaleder.observation_source_record record USING (observation_id)
           JOIN vildaleder.data_source source ON source.source_id = record.source_id
           LEFT JOIN vildaleder.taxon_external_id external
             ON external.taxon_id = observed.taxon_id
            AND external.source_id = source.source_id
           WHERE matched.feature_id = %s
             AND source.source_key = 'sos'
             AND NOT observed.is_deleted
             AND observed.location_is_public
             AND NOT record.is_deleted
             AND observed.observed_on BETWEEN %s AND %s
           ORDER BY observed.observed_on, record.source_record_id""",
        (feature_database_id, start, end),
    ).fetchall()
    for row in rows:
        compact = compact_record(row)
        partitions[partition_name(compact[1], snapshot_end)].append(compact)

    manifests = []
    for partition, records in sorted(partitions.items()):
        records.sort(key=lambda record: (record[1], str(record[0])), reverse=True)
        relative = Path(public_id) / f"{partition}.json"
        write_json(output_root / relative, {"schemaVersion": 1, "records": records})
        manifests.append(
            {
                "path": str(Path("data") / "observations" / relative),
                "start": min(record[1] for record in records),
                "end": max(record[1] for record in records),
                "count": len(records),
            }
        )
    return manifests, len(rows)


def search_index(
    connection: psycopg.Connection[Any],
    feature_database_ids: dict[int, str],
    start: date,
    end: date,
    generated_at: str,
) -> dict[str, Any]:
    trail_counts: dict[str, list[list[Any]]] = defaultdict(list)
    taxon_counts: dict[str, dict[str, list[list[Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    counts = connection.execute(
        """SELECT matched.feature_id,
                  external.external_id,
                  observed.observed_on,
                  count(DISTINCT observed.observation_id)::integer
           FROM vildaleder.observation_feature matched
           JOIN vildaleder.observation observed USING (observation_id)
           JOIN vildaleder.observation_source_record record USING (observation_id)
           JOIN vildaleder.data_source source ON source.source_id = record.source_id
           LEFT JOIN vildaleder.taxon_external_id external
             ON external.taxon_id = observed.taxon_id
            AND external.source_id = source.source_id
           WHERE matched.feature_id = ANY(%s)
             AND source.source_key = 'sos'
             AND NOT observed.is_deleted
             AND observed.location_is_public
             AND NOT record.is_deleted
             AND observed.observed_on BETWEEN %s AND %s
           GROUP BY matched.feature_id, external.external_id, observed.observed_on
           ORDER BY matched.feature_id, observed.observed_on""",
        (list(feature_database_ids), start, end),
    ).fetchall()
    for feature_id, taxon_external_id, observed_on, count in counts:
        public_id = feature_database_ids[feature_id]
        dated = [observed_on.isoformat(), count]
        trail_counts[public_id].append(dated)
        if taxon_external_id is not None:
            taxon_counts[str(taxon_external_id)][public_id].append(dated)

    taxa = []
    if taxon_counts:
        metadata_rows = connection.execute(
            """SELECT external.external_id,
                      taxon.scientific_name,
                      scientific.name,
                      vernacular.names,
                      taxon.organism_group,
                      taxon.redlist_category
               FROM vildaleder.taxon_external_id external
               JOIN vildaleder.data_source source USING (source_id)
               JOIN vildaleder.taxon taxon USING (taxon_id)
               LEFT JOIN LATERAL (
                   SELECT name FROM vildaleder.taxon_name
                   WHERE taxon_id = taxon.taxon_id AND name_kind = 'scientific'
                   ORDER BY is_preferred DESC, taxon_name_id LIMIT 1
               ) scientific ON true
               LEFT JOIN LATERAL (
                   SELECT jsonb_object_agg(language_code, name) AS names
                   FROM (
                       SELECT DISTINCT ON (language_code) language_code, name
                       FROM vildaleder.taxon_name
                       WHERE taxon_id = taxon.taxon_id
                         AND name_kind = 'vernacular'
                         AND language_code IN ('sv', 'en', 'pl')
                       ORDER BY language_code, is_preferred DESC, taxon_name_id
                   ) preferred_names
               ) vernacular ON true
               WHERE source.source_key = 'sos' AND external.external_id = ANY(%s)""",
            (list(taxon_counts),),
        ).fetchall()
        for external_id, scientific_name, scientific_alias, names, group, category in metadata_rows:
            vernacular_names = dict(names or {})
            taxa.append(
                {
                    "taxonId": source_id_value(str(external_id)),
                    "scientificName": scientific_name or scientific_alias,
                    "vernacularName": vernacular_names.get("sv"),
                    "vernacularNames": vernacular_names,
                    "organismGroup": group,
                    "redlistCategory": category,
                    "pointBucket": species_bucket(external_id),
                    "trails": dict(sorted(taxon_counts[str(external_id)].items())),
                }
            )
        taxa.sort(key=lambda item: str(item.get("vernacularName") or item.get("scientificName") or "").casefold())
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "trails": dict(sorted(trail_counts.items())),
        "taxa": taxa,
    }


def export(args: argparse.Namespace) -> dict[str, int]:
    if not args.database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    feature_catalog = json.loads(args.features.read_text(encoding="utf-8"))
    feature_by_public_id = {feature["id"]: feature for feature in feature_catalog["features"]}
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with psycopg.connect(args.database_url) as connection:
        public_ids = covered_public_ids(connection) & set(feature_by_public_id)
        database_features = {
            public_id: feature_id
            for feature_id, public_id in connection.execute(
                """SELECT feature.feature_id, source.source_key || '-' || feature.source_feature_id
                   FROM vildaleder.spatial_feature feature
                   JOIN vildaleder.data_source source USING (source_id)
                   WHERE feature.is_active"""
            ).fetchall()
            if public_id in public_ids
        }
        if not database_features:
            raise RuntimeError("PostGIS contains no complete SOS feature coverage")
        has_sos_observations = connection.execute(
            """SELECT EXISTS (
                   SELECT 1
                   FROM vildaleder.observation_source_record record
                   JOIN vildaleder.data_source source USING (source_id)
                   WHERE source.source_key = 'sos' AND NOT record.is_deleted
               )"""
        ).fetchone()[0]
        if not has_sos_observations:
            raise RuntimeError("PostGIS contains no SOS observations")
        start = args.start_date or args.end_date - timedelta(days=3_649)

        temporary_root = Path(tempfile.mkdtemp(prefix="vildaleder-observations-", dir=args.observations_dir.parent))
        temporary_species_root = Path(
            tempfile.mkdtemp(
                prefix="vildaleder-species-observations-",
                dir=args.species_observations_dir.parent,
            )
        )
        exported = []
        total = 0
        try:
            for index, public_id in enumerate(sorted(database_features), 1):
                manifests, observation_total = export_feature_partitions(
                    connection,
                    database_features[public_id],
                    public_id,
                    temporary_root,
                    args.end_date,
                    start,
                    args.end_date,
                )
                feature = dict(feature_by_public_id[public_id])
                feature["corridor"] = feature.pop("analysisGeometry")
                feature["observationFiles"] = manifests
                feature["observationTotal"] = observation_total
                feature["observationLimitReached"] = False
                if feature.get("source") == "osm":
                    feature["osmRelationId"] = int(feature["sourceFeatureId"])
                    feature["osmUrl"] = feature.get("sourceUrl")
                exported.append(feature)
                total += observation_total
                if index % 25 == 0 or index == len(database_features):
                    print(f"Exported {index}/{len(database_features)} features", file=sys.stderr)

            index_data = search_index(
                connection,
                {database_id: public_id for public_id, database_id in database_features.items()},
                start,
                args.end_date,
                generated_at,
            )
            species_manifests, species_observation_total = export_species_partitions(
                connection,
                {database_id: public_id for public_id, database_id in database_features.items()},
                temporary_species_root,
                args.end_date,
                start,
                args.end_date,
            )
            index_data["speciesPointFeatureIds"] = sorted(database_features)
            index_data["speciesObservationFiles"] = species_manifests
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            shutil.rmtree(temporary_species_root, ignore_errors=True)
            raise

    catalog = {
        "schemaVersion": 2,
        "meta": {
            "generatedAt": generated_at,
            "windowStart": start.isoformat(),
            "windowEnd": args.end_date.isoformat(),
            "bufferMeters": feature_catalog["meta"].get("bufferMeters", 200),
            "pilotArea": "Hallands län",
            "observationRecordFields": list(OBSERVATION_FIELDS),
            "sources": {
                "trails": "OpenStreetMap contributors",
                "reserves": "Naturvårdsverket, Naturvårdsregistret",
                "observations": "Artportalen via SLU Species Observation System",
            },
        },
        "trails": sorted(exported, key=lambda item: (item["name"].casefold(), item["id"])),
    }
    backup = args.observations_dir.with_name(args.observations_dir.name + ".previous")
    species_backup = args.species_observations_dir.with_name(
        args.species_observations_dir.name + ".previous"
    )
    if backup.exists():
        shutil.rmtree(backup)
    if species_backup.exists():
        shutil.rmtree(species_backup)
    if args.observations_dir.exists():
        args.observations_dir.replace(backup)
    if args.species_observations_dir.exists():
        args.species_observations_dir.replace(species_backup)
    try:
        temporary_root.replace(args.observations_dir)
        temporary_species_root.replace(args.species_observations_dir)
        write_json(args.catalog, catalog)
        write_json(args.search_index, index_data)
    except Exception:
        if args.observations_dir.exists():
            shutil.rmtree(args.observations_dir)
        if args.species_observations_dir.exists():
            shutil.rmtree(args.species_observations_dir)
        if backup.exists():
            backup.replace(args.observations_dir)
        if species_backup.exists():
            species_backup.replace(args.species_observations_dir)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(species_backup, ignore_errors=True)
    return {
        "features": len(exported),
        "observations": total,
        "speciesObservations": species_observation_total,
        "taxa": len(index_data["taxa"]),
    }


def main() -> int:
    args = parse_args()
    try:
        stats = export(args)
    except (OSError, ValueError, RuntimeError, psycopg.Error) as exc:
        print(f"PostGIS export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
