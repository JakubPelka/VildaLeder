import unittest

from scripts.enrich_gbif_taxonomy import preferred_name
from scripts.sync_sweden_postgis import parse_scb_municipalities, selected_counties


class GbifTaxonomyTests(unittest.TestCase):
    def test_preferred_name_uses_the_most_repeated_language_name(self):
        rows = [
            {"language": "eng", "vernacularName": "Grey Sea Eagle"},
            {"language": "eng", "vernacularName": "White-tailed Eagle"},
            {"language": "eng", "vernacularName": "White-tailed Eagle"},
            {"language": "pol", "vernacularName": "Bielik"},
        ]
        self.assertEqual(preferred_name(rows, "eng"), "White-tailed Eagle")
        self.assertEqual(preferred_name(rows, "pol"), "Bielik")

    def test_scb_parser_keeps_official_municipality_codes(self):
        document = "<p>Karlskrona 1080</p><p>Ronneby 1081</p><p>Not a municipality 9999</p>"
        self.assertEqual(
            parse_scb_municipalities(document),
            {"1080": "Karlskrona", "1081": "Ronneby"},
        )

    def test_county_selector_accepts_name_code_and_prefix(self):
        self.assertEqual([item.name for item in selected_counties(["Blekinge"])], ["Blekinge"])
        self.assertEqual([item.name for item in selected_counties(["K"])], ["Blekinge"])
        self.assertEqual([item.name for item in selected_counties(["10"])], ["Blekinge"])
