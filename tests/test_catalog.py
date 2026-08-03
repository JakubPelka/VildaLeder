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

    def partition_records(self, trail):
        for manifest in trail["observationFiles"]:
            path = ROOT / manifest["path"]
            partition = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(partition["schemaVersion"], 1)
            self.assertEqual(len(partition["records"]), manifest["count"])
            yield from partition["records"]

    def test_catalog_contains_all_named_halmstad_routes(self):
        trails = self.catalog["trails"]
        self.assertEqual(self.catalog["schemaVersion"], 2)
        self.assertGreaterEqual(len(trails), 64)
        self.assertTrue(
            {8_394_095, 8_394_110, 8_394_180, 9_158_828, 13_262_342}.issubset(
                {trail["osmRelationId"] for trail in trails}
            )
        )
        for trail in trails:
            self.assertEqual(trail["municipality"], "Halmstad")
            self.assertEqual(trail["county"], "Halland")
            self.assertGreater(trail["lengthKm"], 0)
            self.assertFalse(shape(trail["geometry"]).is_empty)
            self.assertTrue(shape(trail["corridor"]).is_valid)
            self.assertNotIn("observations", trail)
            self.assertFalse(trail["observationLimitReached"])

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
        self.assertGreater(total, 270_000)

    def test_daily_index_matches_manifest_totals_and_has_redlist_metadata(self):
        indexed_total = 0
        for trail in self.catalog["trails"]:
            daily = self.index["trails"].get(trail["id"], [])
            count = sum(value for _, value in daily)
            indexed_total += count
            self.assertEqual(count, trail["observationTotal"])
        self.assertGreater(indexed_total, 270_000)
        categories = {taxon["redlistCategory"] for taxon in self.index["taxa"]}
        self.assertTrue({"CR", "EN", "VU", "NT"}.issubset(categories))

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

    def test_snapshot_spans_about_ten_years(self):
        start = date.fromisoformat(self.catalog["meta"]["windowStart"])
        end = date.fromisoformat(self.catalog["meta"]["windowEnd"])
        self.assertEqual((end - start).days + 1, 3_650)


if __name__ == "__main__":
    unittest.main()
