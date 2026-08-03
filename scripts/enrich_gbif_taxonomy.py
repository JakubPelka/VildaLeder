#!/usr/bin/env python3
"""Enrich PostGIS taxa with English and Polish vernacular names from GBIF."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import psycopg
import requests

try:
    from scripts.import_postgis import normalized_name
except ModuleNotFoundError:  # Direct execution adds scripts/ rather than the repository root.
    from import_postgis import normalized_name  # type: ignore[no-redef]


GBIF_API = "https://api.gbif.org/v1"
LANGUAGES = {"eng": "en", "pol": "pl"}
DEFAULT_CACHE = Path.home() / ".cache" / "vildaleder" / "gbif-taxonomy.json"
USER_AGENT = "VildaLeder/0.1 (+https://github.com/JakubPelka/VildaLeder)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def request_json(session: requests.Session, url: str, **params: Any) -> dict[str, Any]:
    last_error = ""
    for attempt in range(6):
        try:
            response = session.get(url, params=params, timeout=60)
            if response.ok:
                return response.json()
            last_error = f"HTTP {response.status_code}: {response.text[:160]}"
            retryable = response.status_code == 429 or response.status_code >= 500
            retry_after = response.headers.get("Retry-After")
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
            retryable = True
            retry_after = None
        if not retryable or attempt == 5:
            break
        delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30, 2**attempt)
        time.sleep(delay + random.random())
    raise RuntimeError(f"GBIF request failed for {url}: {last_error}")


def preferred_name(rows: list[dict[str, Any]], language: str) -> str | None:
    candidates = [
        " ".join(str(row.get("vernacularName") or "").split())
        for row in rows
        if row.get("language") == language and row.get("vernacularName")
    ]
    if not candidates:
        return None
    normalized_counts = Counter(value.casefold() for value in candidates)
    winner = max(normalized_counts, key=lambda value: (normalized_counts[value], -len(value), value))
    variants = Counter(value for value in candidates if value.casefold() == winner)
    return max(variants, key=lambda value: (variants[value], value[:1].isupper(), value))


def lookup(scientific_name: str) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    match = request_json(session, f"{GBIF_API}/species/match", name=scientific_name)
    confidence = int(match.get("confidence") or 0)
    if not match.get("usageKey") or confidence < 90 or match.get("matchType") == "NONE":
        return {"usageKey": None, "names": {}, "confidence": confidence}
    usage_key = match.get("acceptedUsageKey") or match["usageKey"]
    names_response = request_json(
        session,
        f"{GBIF_API}/species/{usage_key}/vernacularNames",
        limit=1_000,
    )
    rows = names_response.get("results") or []
    names = {
        language_code: name
        for gbif_language, language_code in LANGUAGES.items()
        if (name := preferred_name(rows, gbif_language))
    }
    return {
        "usageKey": usage_key,
        "names": names,
        "confidence": confidence,
        "matchType": match.get("matchType"),
    }


def atomic_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    temporary.replace(path)


def load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def taxa_to_enrich(connection: psycopg.Connection[Any], limit: int | None) -> list[tuple[int, str]]:
    query = """SELECT taxon.taxon_id, taxon.scientific_name, count(observed.observation_id) AS records
               FROM vildaleder.taxon taxon
               JOIN vildaleder.observation observed USING (taxon_id)
               WHERE taxon.scientific_name IS NOT NULL
                 AND NOT observed.is_deleted
               GROUP BY taxon.taxon_id, taxon.scientific_name
               ORDER BY records DESC, taxon.taxon_id"""
    params: tuple[Any, ...] = ()
    if limit:
        query += " LIMIT %s"
        params = (limit,)
    return [(taxon_id, scientific_name) for taxon_id, scientific_name, _ in connection.execute(query, params)]


def gbif_source_id(connection: psycopg.Connection[Any]) -> int:
    return connection.execute(
        """INSERT INTO vildaleder.data_source(
               source_key, name, source_kind, base_url, licence, attribution, terms_checked_at
           ) VALUES (
               'gbif', 'Global Biodiversity Information Facility', 'taxonomy',
               'https://www.gbif.org/', 'Source-dependent', 'GBIF.org', current_date
           )
           ON CONFLICT (source_key) DO UPDATE
           SET name = EXCLUDED.name, base_url = EXCLUDED.base_url,
               attribution = EXCLUDED.attribution, terms_checked_at = EXCLUDED.terms_checked_at
           RETURNING source_id"""
    ).fetchone()[0]


def store_names(
    connection: psycopg.Connection[Any],
    source_id: int,
    taxa: list[tuple[int, str]],
    cache: dict[str, Any],
) -> dict[str, int]:
    inserted = 0
    named_taxa = 0
    for taxon_id, scientific_name in taxa:
        result = cache[scientific_name]
        names = result.get("names") or {}
        if names:
            named_taxa += 1
        for language, name in names.items():
            connection.execute(
                """UPDATE vildaleder.taxon_name
                   SET is_preferred = false
                   WHERE taxon_id = %s AND language_code = %s
                     AND name_kind = 'vernacular' AND source_id = %s""",
                (taxon_id, language, source_id),
            )
            inserted += connection.execute(
                """INSERT INTO vildaleder.taxon_name(
                       taxon_id, language_code, name, name_normalized, name_kind,
                       source_id, source_name_id, is_preferred
                   ) VALUES (%s, %s, %s, %s, 'vernacular', %s, %s, true)
                   ON CONFLICT (taxon_id, language_code, name, name_kind, source_id) DO UPDATE
                   SET name_normalized = EXCLUDED.name_normalized,
                       source_name_id = EXCLUDED.source_name_id,
                       is_preferred = true""",
                (
                    taxon_id,
                    language,
                    name,
                    normalized_name(name),
                    source_id,
                    str(result.get("usageKey") or ""),
                ),
            ).rowcount
    connection.commit()
    return {"taxa": len(taxa), "namedTaxa": named_taxa, "names": inserted}


def enrich(args: argparse.Namespace) -> dict[str, int]:
    if not args.database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    if args.workers < 1 or args.workers > 12:
        raise RuntimeError("--workers must be between 1 and 12")
    cache = load_cache(args.cache)
    with psycopg.connect(args.database_url) as connection:
        taxa = taxa_to_enrich(connection, args.limit)
        source_id = gbif_source_id(connection)
        connection.commit()
    names_to_taxa: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for taxon in taxa:
        names_to_taxa[taxon[1]].append(taxon)
    missing = [name for name in names_to_taxa if name not in cache]
    print(
        f"GBIF taxonomy: {len(taxa)} taxa, {len(missing)} uncached lookups",
        file=sys.stderr,
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(lookup, scientific_name): scientific_name for scientific_name in missing}
        completed = 0
        for future in as_completed(futures):
            scientific_name = futures[future]
            cache[scientific_name] = future.result()
            completed += 1
            if completed % 100 == 0 or completed == len(missing):
                atomic_cache(args.cache, cache)
                print(f"GBIF taxonomy lookups: {completed}/{len(missing)}", file=sys.stderr, flush=True)
    with psycopg.connect(args.database_url) as connection:
        return store_names(connection, source_id, taxa, cache)


def main() -> int:
    args = parse_args()
    try:
        stats = enrich(args)
    except (OSError, ValueError, RuntimeError, requests.RequestException, psycopg.Error) as exc:
        print(f"GBIF taxonomy enrichment failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
