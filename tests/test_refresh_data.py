import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from shapely.geometry import shape

from scripts.refresh_data import (
    BUFFER_METERS,
    RefreshError,
    compact_observation,
    partition_name,
    prune_observation_partitions,
    relation_lines,
    simplify_observation,
    sos_polygon_geometry,
    trail_geometry,
)


class GeometryTests(unittest.TestCase):
    def test_relation_is_converted_to_metric_trail_and_corridor(self):
        relation = {
            "id": 123,
            "members": [
                {
                    "type": "way",
                    "role": "",
                    "geometry": [
                        {"lon": 12.9, "lat": 56.67},
                        {"lon": 12.91, "lat": 56.67},
                    ],
                }
            ],
        }

        lines = relation_lines(relation)
        geometry, corridor, length_km = trail_geometry(lines)

        self.assertEqual(geometry["type"], "MultiLineString")
        self.assertIn(corridor["type"], {"Polygon", "MultiPolygon"})
        self.assertGreater(length_km, 0.5)
        self.assertGreater(shape(corridor).area, 0)
        self.assertEqual(sos_polygon_geometry(corridor)["type"], corridor["type"].lower())
        self.assertEqual(BUFFER_METERS, 200)

    def test_relation_without_way_geometry_is_rejected(self):
        with self.assertRaises(RefreshError):
            relation_lines({"id": 123, "members": []})


class ObservationTests(unittest.TestCase):
    def test_simplifies_sos_record_without_personal_reporter_data(self):
        record = {
            "datasetName": "Artportalen",
            "occurrence": {
                "occurrenceId": "urn:test:1",
                "organismQuantityInt": 3,
                "url": "https://example.test/1",
                "reportedBy": "must not be copied",
            },
            "event": {"startDate": "2026-07-31T12:00:00+02:00"},
            "identification": {"verified": True},
            "location": {
                "decimalLatitude": 56.67,
                "decimalLongitude": 12.91,
                "coordinateUncertaintyInMeters": 100,
            },
            "taxon": {
                "id": 100,
                "scientificName": "Haliaeetus albicilla",
                "vernacularName": "havsörn",
                "attributes": {
                    "organismGroup": "Fåglar",
                    "redlistCategory": "NT",
                    "isRedlisted": True,
                },
            },
        }

        simplified = simplify_observation(record)

        self.assertEqual(simplified["individualCount"], 3)
        self.assertEqual(simplified["redlistCategory"], "NT")
        self.assertNotIn("reportedBy", simplified)
        self.assertNotIn("recordedBy", simplified)

        compact = compact_observation(simplified)
        self.assertEqual(len(compact), 8)
        self.assertEqual(compact[1], "2026-07-31")
        self.assertEqual(compact[2], 100)
        self.assertEqual(compact[3], 3)
        self.assertEqual(compact[4], 1)

    def test_current_year_is_partitioned_by_month(self):
        self.assertEqual(partition_name("2025-12-31", date(2026, 8, 3)), "2025")
        self.assertEqual(partition_name("2026-07-31", date(2026, 8, 3)), "2026-07")

    def test_rolling_snapshot_prunes_only_out_of_window_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            observations_dir = Path(temporary) / "observations"
            trail_dir = observations_dir / "osm-1"
            trail_dir.mkdir(parents=True)
            path = trail_dir / "2016.json"
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "records": [[1, "2016-08-06"], [2, "2016-08-07"]],
                    }
                ),
                encoding="utf-8",
            )
            manifests = [
                {
                    "path": "data/observations/osm-1/2016.json",
                    "start": "2016-08-06",
                    "end": "2016-08-07",
                    "count": 2,
                }
            ]

            retained = prune_observation_partitions(
                observations_dir,
                "osm-1",
                manifests,
                date(2016, 8, 7),
                date(2026, 8, 4),
            )

            self.assertEqual(retained[0]["count"], 1)
            records = json.loads(path.read_text(encoding="utf-8"))["records"]
            self.assertEqual(records, [[2, "2016-08-07"]])


if __name__ == "__main__":
    unittest.main()
