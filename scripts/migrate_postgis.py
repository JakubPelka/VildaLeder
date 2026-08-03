#!/usr/bin/env python3
"""Apply pending VildaLeder PostgreSQL/PostGIS migrations in order."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
MIGRATION_PATTERN = re.compile(r"^(\d+)_.*\.sql$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DSN; defaults to DATABASE_URL",
    )
    return parser.parse_args()


def migration_files() -> list[tuple[int, Path]]:
    migrations = []
    for path in MIGRATIONS.glob("*.sql"):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            continue
        migrations.append((int(match.group(1)), path))
    return sorted(migrations)


def applied_versions(connection: psycopg.Connection[object]) -> set[int]:
    exists = connection.execute(
        "SELECT to_regclass('vildaleder.schema_migration') IS NOT NULL"
    ).fetchone()[0]
    if not exists:
        return set()
    return {
        row[0]
        for row in connection.execute(
            "SELECT version FROM vildaleder.schema_migration ORDER BY version"
        )
    }


def migrate(database_url: str) -> list[str]:
    if not database_url:
        raise RuntimeError("Set DATABASE_URL or pass --database-url")
    applied = []
    with psycopg.connect(database_url, autocommit=True) as connection:
        completed = applied_versions(connection)
        for version, path in migration_files():
            if version in completed:
                continue
            connection.execute(path.read_text(encoding="utf-8"), prepare=False)
            applied.append(path.name)
    return applied


def main() -> int:
    args = parse_args()
    try:
        applied = migrate(args.database_url)
    except (OSError, RuntimeError, psycopg.Error) as exc:
        print(f"PostGIS migration failed: {exc}", file=sys.stderr)
        return 1
    print("Applied: " + (", ".join(applied) if applied else "nothing; schema is current"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
