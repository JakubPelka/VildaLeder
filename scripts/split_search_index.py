#!/usr/bin/env python3
"""Split the browser search index into small, lazily loaded ranking buckets."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPECIES_BUCKET_COUNT = 256
INFRASTRUCTURE_KINDS = {"bird_hide", "observation_tower", "observation_site"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "data" / "search-index.json")
    parser.add_argument("--features", type=Path, default=ROOT / "data" / "features.json")
    parser.add_argument(
        "--place-rankings-dir",
        type=Path,
        default=ROOT / "data" / "place-rankings",
    )
    parser.add_argument(
        "--species-rankings-dir",
        type=Path,
        default=ROOT / "data" / "species-rankings",
    )
    return parser.parse_args()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def species_bucket(taxon_id: Any) -> str:
    checksum = zlib.crc32(str(taxon_id).encode("utf-8"))
    return f"{checksum % SPECIES_BUCKET_COUNT:02x}"


def place_group(feature_kind: str) -> str:
    if feature_kind in INFRASTRUCTURE_KINDS:
        return "observation_infrastructure"
    return feature_kind or "trail"


def split_index(
    index: dict[str, Any],
    features: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    kind_by_id = {
        str(feature["id"]): place_group(str(feature.get("featureKind") or "trail"))
        for feature in features
    }
    generated_at = index.get("generatedAt")
    place_payloads: dict[str, dict[str, Any]] = {}
    for feature_id, dated in index.get("trails", {}).items():
        group = kind_by_id.get(str(feature_id), "trail")
        payload = place_payloads.setdefault(
            group,
            {"schemaVersion": 1, "generatedAt": generated_at, "trails": {}},
        )
        payload["trails"][feature_id] = dated

    species_payloads: dict[str, dict[str, Any]] = {}
    lightweight_taxa = []
    for source_taxon in index.get("taxa", []):
        taxon = dict(source_taxon)
        trails = taxon.pop("trails", {})
        bucket = str(taxon.get("rankingBucket") or taxon.get("pointBucket") or species_bucket(taxon["taxonId"]))
        taxon["rankingBucket"] = bucket
        lightweight_taxa.append(taxon)
        payload = species_payloads.setdefault(
            bucket,
            {"schemaVersion": 1, "generatedAt": generated_at, "taxa": {}},
        )
        payload["taxa"][str(taxon["taxonId"])] = trails

    lightweight = {
        key: value
        for key, value in index.items()
        if key not in {"trails", "taxa", "placeRankingFiles", "speciesRankingFiles"}
    }
    lightweight["schemaVersion"] = 2
    lightweight["taxaRankingsLazy"] = True
    lightweight["taxa"] = lightweight_taxa
    lightweight["placeRankingFiles"] = {
        group: f"data/place-rankings/{group}.json" for group in sorted(place_payloads)
    }
    lightweight["speciesRankingFiles"] = {
        bucket: f"data/species-rankings/{bucket}.json" for bucket in sorted(species_payloads)
    }
    return lightweight, place_payloads, species_payloads


def replace_directory(target: Path, payloads: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f"{target.name}-", dir=target.parent))
    backup = target.with_name(f"{target.name}.previous")
    try:
        for name, payload in payloads.items():
            write_json(temporary / f"{name}.json", payload)
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.replace(backup)
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if not target.exists() and backup.exists():
            backup.replace(target)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def write_search_bundle(
    index: dict[str, Any],
    features: list[dict[str, Any]],
    index_path: Path,
    place_rankings_dir: Path,
    species_rankings_dir: Path,
) -> dict[str, int]:
    lightweight, place_payloads, species_payloads = split_index(index, features)
    replace_directory(place_rankings_dir, place_payloads)
    replace_directory(species_rankings_dir, species_payloads)
    temporary_index = index_path.with_suffix(index_path.suffix + ".tmp")
    write_json(temporary_index, lightweight)
    temporary_index.replace(index_path)
    return {
        "taxa": len(lightweight["taxa"]),
        "placeRankingFiles": len(place_payloads),
        "speciesRankingFiles": len(species_payloads),
    }


def main() -> int:
    args = parse_args()
    index = json.loads(args.index.read_text(encoding="utf-8"))
    if "trails" not in index or any("trails" not in taxon for taxon in index.get("taxa", [])):
        raise SystemExit("The input index is already split or does not contain ranking data")
    feature_document = json.loads(args.features.read_text(encoding="utf-8"))
    features = feature_document.get("features") or feature_document.get("trails") or []
    stats = write_search_bundle(
        index,
        features,
        args.index,
        args.place_rankings_dir,
        args.species_rankings_dir,
    )
    print(json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
