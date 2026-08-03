import unittest

from scripts.split_search_index import split_index


class SearchIndexSplitTests(unittest.TestCase):
    def test_rankings_are_partitioned_by_place_group_and_taxon_bucket(self):
        index = {
            "schemaVersion": 1,
            "generatedAt": "2026-08-03T12:00:00Z",
            "trails": {
                "osm-1": [["2026-08-01", 2]],
                "nvl-2": [["2026-08-01", 1]],
            },
            "taxa": [
                {
                    "taxonId": 100,
                    "scientificName": "Example species",
                    "pointBucket": "2a",
                    "trails": {"osm-1": [["2026-08-01", 2]]},
                }
            ],
            "speciesPointFeatureIds": ["nvl-2", "osm-1"],
        }
        features = [
            {"id": "osm-1", "featureKind": "trail"},
            {"id": "nvl-2", "featureKind": "bird_hide"},
        ]

        lightweight, place_payloads, species_payloads = split_index(index, features)

        self.assertNotIn("trails", lightweight)
        self.assertNotIn("trails", lightweight["taxa"][0])
        self.assertEqual(lightweight["taxa"][0]["rankingBucket"], "2a")
        self.assertEqual(
            place_payloads["observation_infrastructure"]["trails"]["nvl-2"],
            [["2026-08-01", 1]],
        )
        self.assertEqual(
            species_payloads["2a"]["taxa"]["100"]["osm-1"],
            [["2026-08-01", 2]],
        )


if __name__ == "__main__":
    unittest.main()
