import unittest
from unittest.mock import patch
import json
from pathlib import Path

from shapely import wkt
from shapely.geometry import shape

from scripts.sync_features import (
    BUFFER_METERS,
    build_catalog,
    fetch_nvl_trails,
    nvl_county_label,
    nvl_destination_feature,
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
        self.assertIn("Naturvårdsverket", catalog["meta"]["sources"]["trails"])

    def test_nvl_county_labels_cover_grammar_variants(self):
        self.assertEqual(nvl_county_label("Halland"), "Hallands Län")
        self.assertEqual(nvl_county_label("Kronoberg"), "Kronobergs Län")
        self.assertEqual(nvl_county_label("Dalarna"), "Dalarnas Län")

    @patch("scripts.sync_features.fetch_nvl_rows")
    def test_nvl_segments_are_grouped_as_one_buffered_walking_trail(self, fetch_rows):
        fetch_rows.return_value = [
            {
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[12.80, 56.70], [12.81, 56.71]]],
                },
                "properties": {
                    "Led_ID": "42",
                    "Lednamn": "Testleden",
                    "Typ_av_led": "Vandringsled",
                    "Kommun": "Halmstad",
                },
            },
            {
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[12.81, 56.71], [12.82, 56.72]]],
                },
                "properties": {
                    "Led_ID": "42",
                    "Lednamn": "Testleden",
                    "Typ_av_led": "Vandringsled",
                    "Kommun": "Halmstad",
                },
            },
            {
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[12.70, 56.70], [12.71, 56.71]]],
                },
                "properties": {
                    "Led_ID": "99",
                    "Lednamn": "Cykelleden",
                    "Typ_av_led": "Cykelled",
                    "Kommun": "Halmstad",
                },
            },
        ]
        trails = fetch_nvl_trails("Halland")
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0]["id"], "nvl-led-42")
        self.assertEqual(trails[0]["source"], "nvl")
        self.assertGreater(trails[0]["lengthKm"], 0)
        self.assertTrue(shape(trails[0]["analysisGeometry"]).contains(shape(trails[0]["geometry"])))

    def test_nvl_bird_destination_gets_point_and_200_metre_buffer(self):
        feature = nvl_destination_feature(
            {
                "geometry": {"type": "MultiPoint", "coordinates": [[12.9, 56.7]]},
                "properties": {
                    "Anordning_ID": "77",
                    "Anordningsnamn": "Testtornet",
                    "Typ": "Fågeltorn",
                    "Kommun": "Halmstad",
                },
            },
            "Halland",
        )
        self.assertEqual(feature["featureKind"], "observation_tower")
        self.assertEqual(feature["sourceFeatureId"], "site-77")
        self.assertEqual(shape(feature["geometry"]).geom_type, "Point")
        self.assertTrue(shape(feature["analysisGeometry"]).contains(shape(feature["geometry"])))

    def test_geometry_changes_invalidate_observation_coverage(self):
        source = (ROOT / "scripts" / "sync_features.py").read_text(encoding="utf-8")
        self.assertIn("previous_geometry_version != new_geometry_version", source)
        self.assertIn('f"sos_window:{feature_id}:%"', source)
        self.assertIn('f"sos_complete:{feature_id}:%"', source)


class GeneratedFeatureCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "data" / "features.json").read_text(encoding="utf-8"))

    def test_catalog_covers_halland_destinations_and_municipalities(self):
        features = self.catalog["features"]
        trails = [feature for feature in features if feature["featureKind"] == "trail"]
        reserves = [feature for feature in features if feature["featureKind"] == "reserve"]
        destinations = [
            feature
            for feature in features
            if feature["featureKind"]
            in {"bird_hide", "observation_tower", "observation_site"}
        ]
        self.assertGreaterEqual(len(features), 475)
        self.assertGreaterEqual(len(trails), 240)
        self.assertGreaterEqual(len(reserves), 210)
        self.assertGreaterEqual(len(destinations), 10)
        self.assertGreaterEqual(
            len([feature for feature in trails if feature["source"] == "nvl"]), 130
        )
        self.assertEqual(
            self.catalog["meta"]["municipalities"],
            ["Falkenberg", "Halmstad", "Hylte", "Kungsbacka", "Laholm", "Varberg"],
        )
        self.assertEqual(len({feature["id"] for feature in features}), len(features))
        self.assertGreaterEqual(
            len([feature for feature in features if len(feature["municipalities"]) > 1]),
            20,
        )

    def test_every_destination_has_valid_geometry_and_analysis_area(self):
        for feature in self.catalog["features"]:
            geometry = shape(feature["geometry"])
            analysis = shape(feature["analysisGeometry"])
            self.assertFalse(geometry.is_empty, feature["id"])
            self.assertTrue(geometry.is_valid, feature["id"])
            self.assertTrue(analysis.is_valid, feature["id"])
            if feature["featureKind"] == "reserve":
                self.assertIn(geometry.geom_type, {"Polygon", "MultiPolygon"})
                self.assertGreater(analysis.area, geometry.area)
            if feature["featureKind"] in {
                "bird_hide",
                "observation_tower",
                "observation_site",
            }:
                self.assertEqual(geometry.geom_type, "Point")
                self.assertTrue(analysis.contains(geometry))


if __name__ == "__main__":
    unittest.main()
