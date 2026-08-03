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

    def test_catalog_contains_expected_real_pilot_routes(self):
        trails = self.catalog["trails"]
        self.assertEqual(len(trails), 5)
        self.assertEqual(
            {trail["osmRelationId"] for trail in trails},
            {8_394_095, 8_394_110, 8_394_180, 9_158_828, 13_262_342},
        )
        for trail in trails:
            self.assertEqual(trail["municipality"], "Halmstad")
            self.assertEqual(trail["county"], "Halland")
            self.assertGreater(trail["lengthKm"], 1)
            self.assertFalse(shape(trail["geometry"]).is_empty)
            self.assertTrue(shape(trail["corridor"]).is_valid)

    def test_observations_are_inside_snapshot_and_have_public_fields_only(self):
        start = date.fromisoformat(self.catalog["meta"]["windowStart"])
        end = date.fromisoformat(self.catalog["meta"]["windowEnd"])
        total = 0
        prohibited = {"reportedBy", "recordedBy", "subscriptionKey", "apiKey"}
        for trail in self.catalog["trails"]:
            occurrence_ids = set()
            for observation in trail["observations"]:
                total += 1
                self.assertFalse(prohibited.intersection(observation))
                observed = date.fromisoformat(observation["date"][:10])
                self.assertGreaterEqual(observed, start)
                self.assertLessEqual(observed, end)
                self.assertIsInstance(observation["taxonId"], int)
                self.assertIsNotNone(observation["scientificName"])
                self.assertGreaterEqual(observation["latitude"], 55)
                self.assertLessEqual(observation["latitude"], 70)
                self.assertGreaterEqual(observation["longitude"], 10)
                self.assertLessEqual(observation["longitude"], 25)
                if observation["id"]:
                    self.assertNotIn(observation["id"], occurrence_ids)
                    occurrence_ids.add(observation["id"])
        self.assertGreater(total, 9_000)

    def test_snapshot_has_redlisted_observations(self):
        categories = {
            observation["redlistCategory"]
            for trail in self.catalog["trails"]
            for observation in trail["observations"]
        }
        self.assertTrue({"CR", "EN", "VU", "NT"}.issubset(categories))


if __name__ == "__main__":
    unittest.main()

