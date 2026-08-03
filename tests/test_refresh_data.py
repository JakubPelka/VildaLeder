import unittest

from shapely.geometry import shape

from scripts.refresh_data import (
    BUFFER_METERS,
    RefreshError,
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


if __name__ == "__main__":
    unittest.main()

