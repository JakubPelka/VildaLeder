import json
import unittest
from datetime import date
from pathlib import Path

from shapely.geometry import shape


ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
        cls.index = json.loads((ROOT / "data" / "search-index.json").read_text(encoding="utf-8"))
        feature_catalog = json.loads(
            (ROOT / "data" / "features.json").read_text(encoding="utf-8")
        )
        cls.feature_ids = {feature["id"] for feature in feature_catalog["features"]}

    def place_daily_counts(self):
        counts = {}
        for path in self.index["placeRankingFiles"].values():
            payload = json.loads((ROOT / path).read_text(encoding="utf-8"))
            counts.update(payload["trails"])
        return counts

    def species_trails(self, taxon):
        path = self.index["speciesRankingFiles"][taxon["rankingBucket"]]
        payload = json.loads((ROOT / path).read_text(encoding="utf-8"))
        return payload["taxa"][str(taxon["taxonId"])]

    def partition_records(self, trail):
        for manifest in trail["observationFiles"]:
            self.assertLessEqual(manifest["start"], manifest["end"])
            path = ROOT / manifest["path"]
            partition = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(partition["schemaVersion"], 1)
            self.assertEqual(len(partition["records"]), manifest["count"])
            yield from partition["records"]

    def test_catalog_contains_complete_halland_features(self):
        features = self.catalog["trails"]
        trails = [feature for feature in features if feature["featureKind"] == "trail"]
        reserves = [feature for feature in features if feature["featureKind"] == "reserve"]
        destinations = [
            feature
            for feature in features
            if feature["featureKind"]
            in {"bird_hide", "observation_tower", "observation_site"}
        ]
        self.assertEqual(self.catalog["schemaVersion"], 2)
        self.assertEqual({feature["id"] for feature in features}, self.feature_ids)
        self.assertGreaterEqual(len(trails), 240)
        self.assertGreaterEqual(len(reserves), 210)
        self.assertGreaterEqual(len(destinations), 10)
        self.assertTrue(
            {8_394_095, 8_394_180, 9_158_828}.issubset(
                {
                    trail["osmRelationId"]
                    for trail in trails
                    if "osmRelationId" in trail
                }
            )
        )
        self.assertTrue(
            any("Kungsbacka" in feature.get("municipalities", []) for feature in features)
        )
        for feature in features:
            self.assertEqual(feature["county"], "Halland")
            if feature["featureKind"] == "trail":
                self.assertGreater(feature["lengthKm"], 0)
            elif feature["featureKind"] in {"reserve", "national_park"}:
                self.assertGreater(feature["areaHa"], 0)
            self.assertFalse(shape(feature["geometry"]).is_empty)
            self.assertTrue(shape(feature["corridor"]).is_valid)
            self.assertNotIn("observations", feature)
            self.assertFalse(feature["observationLimitReached"])

    def test_compact_observations_are_complete_public_and_in_window(self):
        start = self.catalog["meta"]["windowStart"]
        end = self.catalog["meta"]["windowEnd"]
        self.assertEqual(
            self.catalog["meta"]["observationRecordFields"],
            [
                "sourceId",
                "date",
                "taxonId",
                "individualCount",
                "flags",
                "latitude",
                "longitude",
                "uncertaintyMeters",
            ],
        )
        total = 0
        for trail in self.catalog["trails"]:
            source_ids = set()
            records = list(self.partition_records(trail))
            self.assertEqual(len(records), trail["observationTotal"])
            for record in records:
                total += 1
                self.assertEqual(len(record), 8)
                source_id, observed, taxon_id, _, flags, latitude, longitude, _ = record
                self.assertGreaterEqual(observed, start)
                self.assertLessEqual(observed, end)
                self.assertIsInstance(taxon_id, int)
                self.assertIsInstance(flags, int)
                self.assertGreaterEqual(latitude, 55)
                self.assertLessEqual(latitude, 70)
                self.assertGreaterEqual(longitude, 10)
                self.assertLessEqual(longitude, 25)
                self.assertNotIn(source_id, source_ids)
                source_ids.add(source_id)
        self.assertGreater(total, 1_900_000)

    def test_daily_index_matches_manifest_totals_and_has_redlist_metadata(self):
        indexed_total = 0
        place_counts = self.place_daily_counts()
        for trail in self.catalog["trails"]:
            daily = place_counts.get(trail["id"], [])
            count = sum(value for _, value in daily)
            indexed_total += count
            self.assertEqual(count, trail["observationTotal"])
        self.assertGreater(indexed_total, 1_900_000)
        categories = {taxon["redlistCategory"] for taxon in self.index["taxa"]}
        self.assertTrue({"CR", "EN", "VU", "NT"}.issubset(categories))

    def test_species_point_index_deduplicates_havsorn_and_tracks_feature_matches(self):
        feature_ids = self.index["speciesPointFeatureIds"]
        self.assertEqual(set(feature_ids), self.feature_ids)
        self.assertEqual(len(set(feature_ids)), len(feature_ids))
        havsorn = next(taxon for taxon in self.index["taxa"] if taxon["taxonId"] == 100067)
        manifests = self.index["speciesObservationFiles"][havsorn["pointBucket"]]
        source_ids = set()
        havsorn_count = 0
        for manifest in manifests:
            partition = json.loads((ROOT / manifest["path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(partition["records"]), manifest["count"])
            for record in partition["records"]:
                self.assertEqual(len(record), 9)
                self.assertTrue(record[8])
                self.assertTrue(all(0 <= index < len(feature_ids) for index in record[8]))
                if record[2] != 100067:
                    continue
                self.assertNotIn(record[0], source_ids)
                source_ids.add(record[0])
                havsorn_count += 1
        self.assertGreater(havsorn_count, 1_000)

    def test_prins_bertils_has_full_decade_and_bivrak_evidence(self):
        trail = next(
            trail for trail in self.catalog["trails"] if trail["name"] == "Prins Bertils stig"
        )
        records = list(self.partition_records(trail))
        self.assertGreater(len(records), 77_000)
        bivrak = [record for record in records if record[2] == 100100]
        self.assertGreater(len(bivrak), 500)
        taxon = next(taxon for taxon in self.index["taxa"] if taxon["taxonId"] == 100100)
        self.assertEqual(taxon["scientificName"], "Pernis apivorus")
        self.assertEqual(taxon["redlistCategory"], "NT")

    def test_storspov_ranking_matches_paarp_map_records(self):
        trail = next(
            (
                trail
                for trail in self.catalog["trails"]
                if trail["name"] == "Hallandsleden Etappen Påarp - Mellbystrand"
            ),
            None,
        )
        if trail is None:
            self.skipTest("Påarp trail not found in current data snapshot")
        records = [record for record in self.partition_records(trail) if record[2] == 100091]
        taxon = next(taxon for taxon in self.index["taxa"] if taxon["taxonId"] == 100091)
        ranked_count = sum(value for _, value in self.species_trails(taxon)[trail["id"]])
        self.assertEqual(ranked_count, 252)
        self.assertEqual(len(records), ranked_count)

    def test_search_bootstrap_is_lightweight_and_rankings_are_lazy(self):
        index_path = ROOT / "data" / "search-index.json"
        self.assertLess(index_path.stat().st_size, 5_000_000)
        self.assertEqual(self.index["schemaVersion"], 2)
        self.assertTrue(self.index["taxaRankingsLazy"])
        self.assertNotIn("trails", self.index)
        self.assertTrue(all("trails" not in taxon for taxon in self.index["taxa"]))
        self.assertGreaterEqual(len(self.index["speciesRankingFiles"]), 200)
        self.assertTrue(
            all((ROOT / path).is_file() for path in self.index["placeRankingFiles"].values())
        )
        self.assertTrue(
            all((ROOT / path).is_file() for path in self.index["speciesRankingFiles"].values())
        )

    def test_snapshot_spans_about_ten_years(self):
        start = date.fromisoformat(self.catalog["meta"]["windowStart"])
        end = date.fromisoformat(self.catalog["meta"]["windowEnd"])
        self.assertEqual((end - start).days + 1, 3_650)


if __name__ == "__main__":
    unittest.main()
