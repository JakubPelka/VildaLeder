import unittest
from datetime import date
from pathlib import Path

from scripts.export_postgis_snapshot import compact_record
from scripts.sync_halland_postgis import complete_feature_ids, ordered_features, year_windows


class HallandSyncTests(unittest.TestCase):
    def test_postgis_export_uses_the_existing_compact_browser_contract(self):
        record = compact_record(
            ("123", date(2026, 8, 3), "100100", 2, True, False, 56.7, 12.8, 25)
        )
        self.assertEqual(record, [123, "2026-08-03", 100100, 2, 1, 56.7, 12.8, 25])
        empty_count = compact_record(
            ("124", date(2026, 8, 3), "100100", None, False, False, 56.7, 12.8, None)
        )
        self.assertEqual(empty_count[3], "")
        self.assertIsNone(empty_count[7])

    def test_sos_import_normalises_empty_numeric_fields(self):
        source = Path("scripts/sync_halland_postgis.py").read_text(encoding="utf-8")
        self.assertIn('if value is None or value == "":', source)
        self.assertIn('number_or_none(item.get("individualCount"))', source)

    def test_static_export_requires_a_complete_feature_window(self):
        source = Path("scripts/export_postgis_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("WHERE key LIKE 'sos_complete:%%'", source)
        self.assertNotIn("matched | completed", source)

    def test_halland_sync_skips_already_complete_features(self):
        source = Path("scripts/sync_halland_postgis.py").read_text(encoding="utf-8")
        self.assertIn('feature["id"] not in complete', source)

    def test_daily_bootstrap_accepts_an_existing_full_length_window(self):
        keys = {
            "sos_complete:osm-1:2016-08-06:2026-08-03",
            "sos_complete:nvr-2:2026-05-06:2026-08-03",
            "invalid",
        }
        self.assertEqual(complete_feature_ids(keys, 3_650), {"osm-1"})
        self.assertEqual(complete_feature_ids(keys, 90), {"osm-1", "nvr-2"})

    def test_year_windows_are_newest_first_complete_and_non_overlapping(self):
        windows = year_windows(date(2023, 8, 6), date(2026, 8, 3))
        self.assertEqual(
            windows,
            [
                (date(2026, 1, 1), date(2026, 8, 3)),
                (date(2025, 1, 1), date(2025, 12, 31)),
                (date(2024, 1, 1), date(2024, 12, 31)),
                (date(2023, 8, 6), date(2023, 12, 31)),
            ],
        )

    def test_kungsbacka_features_are_filtered_and_prioritised(self):
        features = [
            {"id": "osm-2", "name": "Beta", "municipalities": ["Halmstad"]},
            {"id": "osm-1", "name": "Alpha", "municipalities": ["Kungsbacka"]},
            {"id": "nvr-3", "name": "Gamma", "municipalities": ["Kungsbacka", "Varberg"]},
        ]
        ordered = ordered_features(features, None, "Kungsbacka")
        self.assertEqual([feature["id"] for feature in ordered], ["osm-1", "nvr-3", "osm-2"])
        selected = ordered_features(features, "Varberg", "Kungsbacka")
        self.assertEqual([feature["id"] for feature in selected], ["nvr-3"])


if __name__ == "__main__":
    unittest.main()
