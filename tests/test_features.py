import unittest
from unittest.mock import patch
import json
from pathlib import Path

from shapely import wkt
from shapely.geometry import shape

from scripts.sync_features import (
    BUFFER_METERS,
    build_catalog,
    overpass_query,
    reserve_feature,
    reserve_municipalities,
)


ROOT = Path(__file__).resolve().parents[1]


class FeatureSyncTests(unittest.TestCase):
    def test_halland_route_queries_use_bounded_municipal_areas(self):
        query = overpass_query("1380")
        self.assertIn('ref:scb"="1380', query)
        self.assertIn('route"~"^(hiking|foot)$', query)
        self.assertIn("out body geom", query)

    def test_reserve_municipalities_are_normalized_and_deduplicated(self):
        self.assertEqual(
            reserve_municipalities("Halmstads kommun, Laholms kommun, Halmstads kommun"),
            ["Halmstad", "Laholm"],
        )

    @patch("scripts.sync_features.request_text")
    def test_reserve_uses_full_polygon_plus_200_metre_buffer(self, request_text):
        projected = wkt.loads(
            "POLYGON ((350000 6300000, 351000 6300000, 351000 6301000, "
            "350000 6301000, 350000 6300000))"
        )
        request_text.return_value = projected.wkt

        feature = reserve_feature(
            {
                "id": "test-1",
                "namn": "Testreservat",
                "kommunerAsText": "Halmstads kommun",
                "beslutsstatus": "Gällande",
                "areaHa": 100,
            }
        )

        reserve = shape(feature["geometry"])
        analysis = shape(feature["analysisGeometry"])
        self.assertEqual(feature["featureKind"], "reserve")
        self.assertEqual(feature["municipalities"], ["Halmstad"])
        self.assertTrue(analysis.contains(reserve))
        self.assertGreater(analysis.area, reserve.area)
        self.assertEqual(BUFFER_METERS, 200)

    def test_catalog_records_halland_and_ten_year_limit(self):
        catalog = build_catalog([])
        self.assertEqual(catalog["meta"]["area"], "Halland")
        self.assertEqual(catalog["meta"]["bufferMeters"], 200)
        self.assertEqual(catalog["meta"]["maximumObservationYears"], 10)


class GeneratedFeatureCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "data" / "features.json").read_text(encoding="utf-8"))

    def test_catalog_covers_halland_trails_reserves_and_municipalities(self):
        features = self.catalog["features"]
        trails = [feature for feature in features if feature["featureKind"] == "trail"]
        reserves = [feature for feature in features if feature["featureKind"] == "reserve"]
        self.assertGreaterEqual(len(features), 370)
        self.assertGreaterEqual(len(trails), 170)
        self.assertGreaterEqual(len(reserves), 200)
        self.assertEqual(
            self.catalog["meta"]["municipalities"],
            ["Falkenberg", "Halmstad", "Hylte", "Kungsbacka", "Laholm", "Varberg"],
        )
        self.assertEqual(len({feature["id"] for feature in features}), len(features))
        self.assertGreaterEqual(
            len([feature for feature in features if len(feature["municipalities"]) > 1]),
            20,
        )

    def test_every_reserve_has_valid_polygon_and_larger_analysis_area(self):
        for feature in self.catalog["features"]:
            geometry = shape(feature["geometry"])
            analysis = shape(feature["analysisGeometry"])
            self.assertFalse(geometry.is_empty, feature["id"])
            self.assertTrue(geometry.is_valid, feature["id"])
            self.assertTrue(analysis.is_valid, feature["id"])
            if feature["featureKind"] == "reserve":
                self.assertIn(geometry.geom_type, {"Polygon", "MultiPolygon"})
                self.assertGreater(analysis.area, geometry.area)


if __name__ == "__main__":
    unittest.main()
