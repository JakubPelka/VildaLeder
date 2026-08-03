#!/usr/bin/env python3
"""Verify core PostGIS counts, spatial indexes, and known Halmstad evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DSN; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def scalar(connection: psycopg.Connection[object], query: str, parameters=()):
    return connection.execute(query, parameters).fetchone()[0]


def verify(database_url: str) -> dict[str, int]:
    if not database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    with psycopg.connect(database_url) as connection:
        stats = {
            "features": scalar(
                connection,
                "SELECT count(*) FROM vildaleder.spatial_feature WHERE is_active",
            ),
            "taxa": scalar(connection, "SELECT count(*) FROM vildaleder.taxon"),
            "taxonNames": scalar(connection, "SELECT count(*) FROM vildaleder.taxon_name"),
            "uniqueObservations": scalar(connection, "SELECT count(*) FROM vildaleder.observation"),
            "snapshotMatches": scalar(connection, "SELECT count(*) FROM vildaleder.observation_feature"),
            "dailyAggregates": scalar(connection, "SELECT count(*) FROM vildaleder.daily_feature_taxon"),
            "prinsDecade": scalar(
                connection,
                """SELECT sum(daily.observation_count)::bigint
                   FROM vildaleder.daily_feature_taxon daily
                   JOIN vildaleder.spatial_feature feature USING (feature_id)
                   WHERE feature.canonical_name = 'Prins Bertils stig'""",
            ),
            "prinsBivrak": scalar(
                connection,
                """SELECT sum(daily.observation_count)::bigint
                   FROM vildaleder.daily_feature_taxon daily
                   JOIN vildaleder.spatial_feature feature USING (feature_id)
                   JOIN vildaleder.taxon taxon USING (taxon_id)
                   WHERE feature.canonical_name = 'Prins Bertils stig'
                     AND taxon.canonical_source_taxon_id = '100100'""",
            ),
            "spatialIndexes": scalar(
                connection,
                """SELECT count(*) FROM pg_indexes
                   WHERE schemaname = 'vildaleder'
                     AND indexdef ILIKE '%%USING gist%%'""",
            ),
        }
    expectations = (
        (stats["features"] >= 64, "expected all Halmstad features"),
        (stats["taxa"] >= 8_000, "expected full taxonomy index"),
        (stats["taxonNames"] >= 10_000, "expected scientific and vernacular names"),
        (stats["uniqueObservations"] > 100_000, "expected deduplicated observations"),
        (stats["uniqueObservations"] < stats["snapshotMatches"], "matches must reuse observations"),
        (stats["snapshotMatches"] > 270_000, "expected full 10-year trail matches"),
        (stats["prinsDecade"] > 77_000, "expected full Prins Bertils decade"),
        (stats["prinsBivrak"] > 500, "expected bivråk evidence on Prins Bertils stig"),
        (stats["spatialIndexes"] >= 3, "expected PostGIS GiST indexes"),
    )
    failures = [message for passed, message in expectations if not passed]
    if failures:
        raise RuntimeError("; ".join(failures))
    return stats


def main() -> int:
    args = parse_args()
    try:
        stats = verify(args.database_url)
    except (RuntimeError, psycopg.Error) as exc:
        print(f"PostGIS verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
