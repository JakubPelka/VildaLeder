#!/usr/bin/env python3
"""Backfill Swedish nature destinations and SOS observations into private PostGIS.

The job works one county at a time, stores county feature catalogs outside the
public site, and relies on the existing feature/year SOS checkpoints. It never
exports browser data or publishes to GitHub Pages.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import requests

try:
    from scripts.refresh_data import RefreshError, iso_timestamp
    from scripts.sync_features import (
        build_catalog,
        fetch_national_parks,
        fetch_nvl_destinations,
        fetch_nvl_trails,
        fetch_reserves,
        fetch_routes,
        upsert_postgis,
    )
    from scripts.sync_halland_postgis import sync as sync_sos
except ModuleNotFoundError:  # Direct execution adds scripts/ rather than the repository root.
    from refresh_data import RefreshError, iso_timestamp  # type: ignore[no-redef]
    from sync_features import (  # type: ignore[no-redef]
        build_catalog,
        fetch_national_parks,
        fetch_nvl_destinations,
        fetch_nvl_trails,
        fetch_reserves,
        fetch_routes,
        upsert_postgis,
    )
    from sync_halland_postgis import sync as sync_sos  # type: ignore[no-redef]


SCB_MUNICIPALITY_URL = (
    "https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/"
    "regionala-indelningar/lan-och-kommuner/kommuner-i-bokstavsordning?menu=open"
)
DEFAULT_WORKSPACE = Path.home() / ".local" / "state" / "vildaleder-sweden"


@dataclass(frozen=True)
class County:
    name: str
    nvr_code: str
    scb_prefix: str


# Smaller southern counties go first so the end-to-end pipeline produces useful
# checkpoints early. Halland is already present in the cloned national database.
COUNTIES = (
    County("Blekinge", "K", "10"),
    County("Kronoberg", "G", "07"),
    County("Jönköping", "F", "06"),
    County("Kalmar", "H", "08"),
    County("Skåne", "M", "12"),
    County("Västra Götaland", "O", "14"),
    County("Östergötland", "E", "05"),
    County("Gotland", "I", "09"),
    County("Södermanland", "D", "04"),
    County("Örebro", "T", "18"),
    County("Värmland", "S", "17"),
    County("Västmanland", "U", "19"),
    County("Uppsala", "C", "03"),
    County("Stockholm", "AB", "01"),
    County("Dalarna", "W", "20"),
    County("Gävleborg", "X", "21"),
    County("Västernorrland", "Y", "22"),
    County("Jämtland", "Z", "23"),
    County("Västerbotten", "AC", "24"),
    County("Norrbotten", "BD", "25"),
)
VALID_SCB_PREFIXES = {county.scb_prefix for county in COUNTIES} | {"13"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--days", type=int, default=3_650)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument(
        "--county",
        action="append",
        help="Limit the backfill to a county name, NVR code, or two-digit SCB prefix.",
    )
    parser.add_argument(
        "--refresh-features",
        action="store_true",
        help="Discard cached county catalogs and query OSM/NVR again.",
    )
    parser.add_argument("--skip-observations", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def parse_scb_municipalities(document: str) -> dict[str, str]:
    plain = html.unescape(re.sub(r"<[^>]+>", "\n", document))
    rows = {}
    for name, code in re.findall(
        r"([A-ZÅÄÖ][A-Za-zÅÄÖåäöÉé .\-]+?)\s+(\d{4})(?=\s)", plain
    ):
        if code[:2] in VALID_SCB_PREFIXES:
            rows[code] = " ".join(name.split())
    return rows


def fetch_scb_municipalities() -> dict[str, str]:
    response = requests.get(SCB_MUNICIPALITY_URL, timeout=120)
    response.raise_for_status()
    rows = parse_scb_municipalities(response.text)
    if len(rows) < 290:
        raise RefreshError(f"SCB municipality list is incomplete: {len(rows)}/290")
    return rows


def selected_counties(filters: list[str] | None) -> list[County]:
    if not filters:
        return list(COUNTIES)
    wanted = {value.casefold() for value in filters}
    selected = [
        county
        for county in COUNTIES
        if county.name.casefold() in wanted
        or county.nvr_code.casefold() in wanted
        or county.scb_prefix in wanted
    ]
    if len(selected) != len(wanted):
        matched = {
            value.casefold()
            for county in selected
            for value in (county.name, county.nvr_code, county.scb_prefix)
        }
        unknown = sorted(wanted - matched)
        raise RuntimeError(f"Unknown county selector: {', '.join(unknown)}")
    return selected


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    temporary.replace(path)


def load_progress(path: Path) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"startedAt": iso_timestamp(), "counties": {}}


def update_progress(
    path: Path,
    progress: dict[str, Any],
    county: County,
    status: str,
    **details: Any,
) -> None:
    progress["updatedAt"] = iso_timestamp()
    progress["currentCounty"] = county.name
    progress.setdefault("counties", {})[county.name] = {
        **progress.get("counties", {}).get(county.name, {}),
        "status": status,
        "updatedAt": progress["updatedAt"],
        **details,
    }
    atomic_json(path, progress)


def database_name(database_url: str) -> str:
    with psycopg.connect(database_url) as connection:
        return connection.execute("SELECT current_database()").fetchone()[0]


def build_county_catalog(
    county: County,
    municipalities: dict[str, str],
    output: Path,
) -> dict[str, Any]:
    trails = [
        *fetch_routes(county.name, municipalities),
        *fetch_nvl_trails(county.name),
    ]
    reserves = fetch_reserves(county.name, county.nvr_code)
    national_parks = fetch_national_parks(county.name, county.nvr_code)
    destinations = fetch_nvl_destinations(county.name)
    features = sorted(
        [*trails, *reserves, *national_parks, *destinations],
        key=lambda item: (item["featureKind"], item["name"].casefold(), item["id"]),
    )
    catalog = build_catalog(features, county.name, municipalities)
    atomic_json(output, catalog)
    return catalog


def process_county(
    args: argparse.Namespace,
    county: County,
    all_municipalities: dict[str, str],
    progress_path: Path,
    progress: dict[str, Any],
) -> None:
    municipalities = {
        code: name
        for code, name in all_municipalities.items()
        if code.startswith(county.scb_prefix)
    }
    if not municipalities:
        raise RuntimeError(f"SCB returned no municipalities for {county.name}")
    catalog_path = args.workspace / "features" / f"{county.scb_prefix}.json"
    update_progress(
        progress_path,
        progress,
        county,
        "features",
        municipalities=len(municipalities),
    )
    if catalog_path.is_file() and not args.refresh_features:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    else:
        catalog = build_county_catalog(county, municipalities, catalog_path)
    features = catalog["features"]
    feature_stats = upsert_postgis(
        args.database_url,
        features,
        county.name,
        deactivate_missing=False,
    )
    update_progress(
        progress_path,
        progress,
        county,
        "observations" if not args.skip_observations else "complete",
        catalog=str(catalog_path),
        trails=len([feature for feature in features if feature["featureKind"] == "trail"]),
        reserves=len([feature for feature in features if feature["featureKind"] == "reserve"]),
        nationalParks=len(
            [feature for feature in features if feature["featureKind"] == "national_park"]
        ),
        destinations=len(
            [
                feature
                for feature in features
                if feature["featureKind"]
                in {"bird_hide", "observation_tower", "observation_site"}
            ]
        ),
        postgis=feature_stats,
    )
    if args.skip_observations:
        return
    sos_args = argparse.Namespace(
        features=catalog_path,
        database_url=args.database_url,
        days=args.days,
        end_date=args.end_date,
        workers=args.workers,
        municipality=None,
        priority_municipality="",
        force=False,
    )
    stats = sync_sos(sos_args)
    update_progress(progress_path, progress, county, "complete", sos=stats)


def main() -> int:
    args = parse_args()
    if not args.database_url:
        print("Set DATABASE_URL or pass --database-url", file=sys.stderr)
        return 1
    if args.retries < 1:
        print("--retries must be at least 1", file=sys.stderr)
        return 1
    args.workspace.mkdir(parents=True, exist_ok=True)
    progress_path = args.workspace / "progress.json"
    progress = load_progress(progress_path)
    try:
        current_database = database_name(args.database_url)
        if current_database == "vildaleder":
            raise RuntimeError(
                "Refusing a national backfill into the Halland database; use an isolated database"
            )
        progress["database"] = current_database
        progress["windowEnd"] = args.end_date.isoformat()
        progress["windowDays"] = args.days
        all_municipalities = fetch_scb_municipalities()
        counties = selected_counties(args.county)
        for county in counties:
            for attempt in range(1, args.retries + 1):
                try:
                    print(
                        f"[{iso_timestamp()}] {county.name}: pass {attempt}/{args.retries}",
                        file=sys.stderr,
                        flush=True,
                    )
                    process_county(
                        args,
                        county,
                        all_municipalities,
                        progress_path,
                        progress,
                    )
                    break
                except Exception as exc:
                    update_progress(
                        progress_path,
                        progress,
                        county,
                        "retrying" if attempt < args.retries else "failed",
                        attempt=attempt,
                        error=str(exc),
                    )
                    print(f"{county.name} failed: {exc}", file=sys.stderr, flush=True)
                    if attempt == args.retries:
                        break
                    time.sleep(min(900, 30 * 2 ** (attempt - 1)))
        progress["currentCounty"] = None
        progress["completedAt"] = iso_timestamp()
        atomic_json(progress_path, progress)
    except (OSError, ValueError, RuntimeError, RefreshError, requests.RequestException, psycopg.Error) as exc:
        print(f"Sweden PostGIS backfill failed: {exc}", file=sys.stderr)
        return 1
    failed = [
        name
        for name, state in progress.get("counties", {}).items()
        if state.get("status") == "failed"
    ]
    print(json.dumps({"database": progress["database"], "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
