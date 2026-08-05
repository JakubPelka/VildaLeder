#!/usr/bin/env python3
"""Synchronise ten years of public SOS observations for Halland features into PostGIS.

The upstream API is queried in bounded one-year feature windows. Each completed
window is committed independently and recorded in database metadata, so an
interrupted run can resume without repeating successful requests. Observation
records are canonicalised once and linked to every queried trail or reserve.

The requested date range controls upstream reconciliation and public snapshot
coverage; it is not a database-retention boundary. Observations already stored
outside the range remain in PostGIS so the historical archive grows over time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
import requests

try:
    from scripts.import_postgis import normalized_name, source_ids
    from scripts.refresh_data import (
        RefreshError,
        iso_timestamp,
        new_session,
        read_subscription_key,
        search_observations,
        simplify_observation,
        source_id,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/ rather than the repository root.
    from import_postgis import normalized_name, source_ids  # type: ignore[no-redef]
    from refresh_data import (  # type: ignore[no-redef]
        RefreshError,
        iso_timestamp,
        new_session,
        read_subscription_key,
        search_observations,
        simplify_observation,
        source_id,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAYS = 3_650
DEFAULT_WORKERS = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=ROOT / "data" / "features.json")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--municipality",
        help="Synchronise only features assigned to this municipality (for staged rollout).",
    )
    parser.add_argument(
        "--priority-municipality",
        default="Kungsbacka",
        help="Process this municipality first during a Halland-wide run.",
    )
    parser.add_argument("--force", action="store_true", help="Repeat completed windows")
    return parser.parse_args()


def year_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = end
    while cursor >= start:
        window_start = max(start, date(cursor.year, 1, 1))
        windows.append((window_start, cursor))
        cursor = window_start - timedelta(days=1)
    return windows


def feature_municipalities(feature: dict[str, Any]) -> list[str]:
    values = feature.get("municipalities") or []
    if not values and feature.get("municipality"):
        values = [feature["municipality"]]
    return [str(value) for value in values]


def ordered_features(
    features: Iterable[dict[str, Any]],
    municipality: str | None,
    priority_municipality: str,
) -> list[dict[str, Any]]:
    selected = [
        feature
        for feature in features
        if not municipality or municipality in feature_municipalities(feature)
    ]
    return sorted(
        selected,
        key=lambda feature: (
            priority_municipality not in feature_municipalities(feature),
            str(feature.get("name") or "").casefold(),
            str(feature["id"]),
        ),
    )


def coverage_key(feature_id: str, start: date, end: date) -> str:
    return f"sos_window:{feature_id}:{start.isoformat()}:{end.isoformat()}"


def complete_key(feature_id: str, start: date, end: date) -> str:
    return f"sos_complete:{feature_id}:{start.isoformat()}:{end.isoformat()}"


def completed_windows(connection: psycopg.Connection[Any]) -> set[str]:
    return {
        key
        for (key,) in connection.execute(
            "SELECT key FROM vildaleder.metadata WHERE key LIKE 'sos_window:%%'"
        ).fetchall()
    }


def completed_features(connection: psycopg.Connection[Any]) -> set[str]:
    return {
        key
        for (key,) in connection.execute(
            "SELECT key FROM vildaleder.metadata WHERE key LIKE 'sos_complete:%%'"
        ).fetchall()
    }


def complete_feature_ids(keys: Iterable[str], minimum_days: int) -> set[str]:
    """Return features with a recorded complete window at least this long."""
    completed: set[str] = set()
    for key in keys:
        parts = key.split(":")
        if len(parts) != 4 or parts[0] != "sos_complete":
            continue
        try:
            start = date.fromisoformat(parts[2])
            end = date.fromisoformat(parts[3])
        except ValueError:
            continue
        if (end - start).days + 1 >= minimum_days:
            completed.add(parts[1])
    return completed


def public_feature_ids(connection: psycopg.Connection[Any]) -> dict[str, int]:
    return {
        f"{source_key}-{source_feature_id}": feature_id
        for feature_id, source_key, source_feature_id in connection.execute(
            """SELECT feature.feature_id, source.source_key, feature.source_feature_id
               FROM vildaleder.spatial_feature feature
               JOIN vildaleder.data_source source USING (source_id)
               WHERE feature.is_active"""
        ).fetchall()
    }


def fetch_window(
    feature: dict[str, Any],
    start: date,
    end: date,
    subscription_key: str,
) -> tuple[str, date, date, list[dict[str, Any]], int]:
    raw, source_total = search_observations(
        new_session(),
        subscription_key,
        feature["analysisGeometry"],
        start,
        end,
    )
    observations = [simplify_observation(item) for item in raw]
    observations = [
        observation
        for observation in observations
        if observation.get("date")
        and observation.get("latitude") is not None
        and observation.get("longitude") is not None
    ]
    return feature["id"], start, end, observations, source_total


def upsert_taxa(
    connection: psycopg.Connection[Any],
    observations: Iterable[dict[str, Any]],
    sos_source_id: int,
) -> None:
    taxa: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if observation.get("taxonId") is None:
            continue
        taxa.setdefault(str(observation["taxonId"]), observation)
    for external_id, item in taxa.items():
        taxon_id = connection.execute(
            """INSERT INTO vildaleder.taxon(
                   canonical_source_id, canonical_source_taxon_id, scientific_name,
                   organism_group, redlist_category
               ) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (canonical_source_id, canonical_source_taxon_id) DO UPDATE
               SET scientific_name = COALESCE(EXCLUDED.scientific_name, vildaleder.taxon.scientific_name),
                   organism_group = COALESCE(EXCLUDED.organism_group, vildaleder.taxon.organism_group),
                   redlist_category = COALESCE(EXCLUDED.redlist_category, vildaleder.taxon.redlist_category),
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
        connection.execute(
            """INSERT INTO vildaleder.taxon_external_id(
                   taxon_id, source_id, external_id, is_accepted
               ) VALUES (%s, %s, %s, true)
               ON CONFLICT (source_id, external_id) DO UPDATE
               SET taxon_id = EXCLUDED.taxon_id, is_accepted = true""",
            (taxon_id, sos_source_id, external_id),
        )
        for name, language, kind in (
            (item.get("scientificName"), "zxx", "scientific"),
            (item.get("vernacularName"), "sv", "vernacular"),
        ):
            if not name:
                continue
            connection.execute(
                """INSERT INTO vildaleder.taxon_name(
                       taxon_id, language_code, name, name_normalized, name_kind,
                       source_id, is_preferred
                   ) VALUES (%s, %s, %s, %s, %s::vildaleder.taxon_name_kind, %s, true)
                   ON CONFLICT (taxon_id, language_code, name, name_kind, source_id) DO UPDATE
                   SET name_normalized = EXCLUDED.name_normalized, is_preferred = true""",
                (taxon_id, language, name, normalized_name(name), kind, sos_source_id),
            )


def stage_observations(
    connection: psycopg.Connection[Any], observations: Iterable[dict[str, Any]]
) -> int:
    connection.execute(
        """CREATE TEMP TABLE sos_window_observation (
               source_record_id text PRIMARY KEY,
               taxon_external_id text,
               observed_on date NOT NULL,
               individual_count double precision,
               verified boolean NOT NULL,
               uncertain_identification boolean NOT NULL,
               latitude double precision NOT NULL,
               longitude double precision NOT NULL,
               coordinate_uncertainty_m double precision,
               source_url text
           ) ON COMMIT DROP"""
    )
    count = 0

    def number_or_none(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    with connection.cursor().copy(
        """COPY sos_window_observation(
               source_record_id, taxon_external_id, observed_on, individual_count,
               verified, uncertain_identification, latitude, longitude,
               coordinate_uncertainty_m, source_url
           ) FROM STDIN"""
    ) as copy:
        for item in observations:
            copy.write_row(
                (
                    str(source_id(item.get("id"))),
                    str(item["taxonId"]) if item.get("taxonId") is not None else None,
                    str(item["date"])[:10],
                    number_or_none(item.get("individualCount")),
                    bool(item.get("verified")),
                    bool(item.get("uncertainIdentification")),
                    float(item["latitude"]),
                    float(item["longitude"]),
                    number_or_none(item.get("uncertaintyMeters")),
                    item.get("sourceUrl"),
                )
            )
            count += 1
    return count


def import_window(
    database_url: str,
    feature_database_id: int,
    feature_public_id: str,
    window_start: date,
    window_end: date,
    observations: list[dict[str, Any]],
    source_total: int,
) -> dict[str, int]:
    generated_at = iso_timestamp()
    with psycopg.connect(database_url) as connection:
        sources = source_ids(connection)
        sos_source_id = sources["sos"]
        upsert_taxa(connection, observations, sos_source_id)
        staged = stage_observations(connection, observations)
        inserted = connection.execute(
            """INSERT INTO vildaleder.observation(
                   canonical_key, taxon_id, observed_on, individual_count, verified,
                   uncertain_identification, geom, coordinate_uncertainty_m,
                   first_seen_at, last_seen_at
               )
               SELECT
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
               FROM sos_window_observation staged
               LEFT JOIN vildaleder.taxon_external_id external
                 ON external.source_id = %s
                AND external.external_id = staged.taxon_external_id
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
                   observation_id, source_id, source_record_id, source_url,
                   is_primary, first_seen_at, last_seen_at
               )
               SELECT observed.observation_id, %s, staged.source_record_id,
                      staged.source_url, true, %s::timestamptz, %s::timestamptz
               FROM sos_window_observation staged
               JOIN vildaleder.observation observed
                 ON observed.canonical_key = 'sos:' || staged.source_record_id
               ON CONFLICT (source_id, source_record_id) DO UPDATE
               SET observation_id = EXCLUDED.observation_id,
                   source_url = EXCLUDED.source_url,
                   is_primary = true,
                   last_seen_at = EXCLUDED.last_seen_at,
                   is_deleted = false
               RETURNING observation_id""",
            (sos_source_id, generated_at, generated_at),
        ).rowcount
        # Replace only the feature matches covered by this correction window.
        # The canonical observations themselves are intentionally append-only
        # with respect to age: an observation falling outside a later ten-year
        # refresh remains available in PostGIS for historical use.
        connection.execute(
            """DELETE FROM vildaleder.observation_feature matched
               USING vildaleder.observation observed,
                     vildaleder.observation_source_record source_record
               WHERE matched.feature_id = %s
                 AND matched.observation_id = observed.observation_id
                 AND source_record.observation_id = observed.observation_id
                 AND source_record.source_id = %s
                 AND observed.observed_on BETWEEN %s AND %s""",
            (feature_database_id, sos_source_id, window_start, window_end),
        )
        matches = connection.execute(
            """INSERT INTO vildaleder.observation_feature(
                   observation_id, feature_id, match_method,
                   feature_geometry_version, matched_at
               )
               SELECT observed.observation_id, feature.feature_id,
                      'sos_geometry_query', feature.geometry_version, %s::timestamptz
               FROM sos_window_observation staged
               JOIN vildaleder.observation observed
                 ON observed.canonical_key = 'sos:' || staged.source_record_id
               JOIN vildaleder.spatial_feature feature ON feature.feature_id = %s
               ON CONFLICT (observation_id, feature_id) DO UPDATE
               SET match_method = EXCLUDED.match_method,
                   feature_geometry_version = EXCLUDED.feature_geometry_version,
                   matched_at = EXCLUDED.matched_at
               RETURNING observation_id""",
            (generated_at, feature_database_id),
        ).rowcount
        connection.execute(
            """INSERT INTO vildaleder.metadata(key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            (
                coverage_key(feature_public_id, window_start, window_end),
                json.dumps(
                    {
                        "completedAt": generated_at,
                        "records": staged,
                        "sourceRecords": source_total,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        connection.commit()
    return {
        "staged": staged,
        "upserted": inserted,
        "sourceRecords": source_records,
        "matches": matches,
    }


def mark_complete(
    database_url: str,
    feature_ids: Iterable[str],
    start: date,
    end: date,
    expected_windows: list[tuple[date, date]],
) -> int:
    marked = 0
    with psycopg.connect(database_url) as connection:
        existing = completed_windows(connection)
        complete = completed_features(connection)
        complete_ids = complete_feature_ids(complete, (end - start).days + 1)
        for feature_id in feature_ids:
            target = complete_key(feature_id, start, end)
            if feature_id in complete_ids:
                marked += 1
            elif all(
                coverage_key(feature_id, left, right) in existing
                for left, right in expected_windows
            ):
                connection.execute(
                    """INSERT INTO vildaleder.metadata(key, value) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                    (target, iso_timestamp()),
                )
                marked += 1
        connection.commit()
    return marked


def sync(args: argparse.Namespace) -> dict[str, int]:
    if not args.database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    if args.days < 1 or args.days > 3_660:
        raise RuntimeError("--days must be between 1 and 3660")
    if args.workers < 1 or args.workers > 6:
        raise RuntimeError("--workers must be between 1 and 6")
    catalog = json.loads(args.features.read_text(encoding="utf-8"))
    features = ordered_features(
        catalog["features"], args.municipality, args.priority_municipality
    )
    if not features:
        raise RuntimeError("No features match the requested municipality")
    subscription_key = read_subscription_key()
    window_start = args.end_date - timedelta(days=args.days - 1)
    windows = year_windows(window_start, args.end_date)

    with psycopg.connect(args.database_url) as connection:
        database_ids = public_feature_ids(connection)
        done = set() if args.force else completed_windows(connection)
        complete = set() if args.force else complete_feature_ids(
            completed_features(connection), args.days
        )
    missing_features = [feature["id"] for feature in features if feature["id"] not in database_ids]
    if missing_features:
        raise RuntimeError(f"Features missing from PostGIS: {', '.join(missing_features[:5])}")

    tasks = [
        (feature, left, right)
        for feature in features
        for left, right in windows
        if args.force
        or (
            feature["id"] not in complete
            and coverage_key(feature["id"], left, right) not in done
        )
    ]
    stats = {"features": len(features), "windows": len(tasks), "records": 0, "matches": 0}
    print(
        f"Synchronising {len(tasks)} feature/year windows for {len(features)} features "
        f"({window_start}–{args.end_date})",
        file=sys.stderr,
        flush=True,
    )
    executor = ThreadPoolExecutor(max_workers=args.workers)
    try:
        futures = {
            executor.submit(fetch_window, feature, left, right, subscription_key): (
                feature,
                left,
                right,
            )
            for feature, left, right in tasks
        }
        completed = 0
        for future in as_completed(futures):
            feature, left, right = futures[future]
            try:
                feature_id, fetched_start, fetched_end, observations, source_total = future.result()
                imported = import_window(
                    args.database_url,
                    database_ids[feature_id],
                    feature_id,
                    fetched_start,
                    fetched_end,
                    observations,
                    source_total,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed {feature['name']} ({left}–{right}): {exc}"
                ) from exc
            completed += 1
            stats["records"] += imported["staged"]
            stats["matches"] += imported["matches"]
            print(
                f"[{completed}/{len(tasks)}] {feature['name']} {left.year}: "
                f"{imported['staged']} observations",
                file=sys.stderr,
                flush=True,
            )
    except Exception:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    stats["completeFeatures"] = mark_complete(
        args.database_url,
        (feature["id"] for feature in features),
        window_start,
        args.end_date,
        windows,
    )
    with psycopg.connect(args.database_url) as connection:
        stats["dailyAggregates"] = 0
        if tasks and not getattr(args, 'skip_aggregates', False):
            stats["dailyAggregates"] = connection.execute(
                "SELECT vildaleder.refresh_daily_feature_taxon(%s, %s)",
                (window_start, args.end_date),
            ).fetchone()[0]
        connection.execute(
            """INSERT INTO vildaleder.sync_run(
                   source_id, mode, started_at, completed_at, window_start, window_end,
                   status, records_seen, records_inserted
               )
               SELECT source_id, 'full', %s::timestamptz, now(), %s, %s,
                      'complete', %s, %s
               FROM vildaleder.data_source WHERE source_key = 'sos'""",
            (iso_timestamp(), window_start, args.end_date, stats["records"], stats["matches"]),
        )
        connection.commit()
    return stats


def main() -> int:
    args = parse_args()
    try:
        stats = sync(args)
    except (OSError, ValueError, RuntimeError, RefreshError, requests.RequestException, psycopg.Error) as exc:
        print(f"Halland SOS sync failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
