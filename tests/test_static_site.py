import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "stylesheet":
            self.assets.append(values.get("href"))
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"])


class StaticSiteTests(unittest.TestCase):
    def test_all_local_assets_exist(self):
        parser = AssetParser()
        parser.feed((ROOT / "index.html").read_text(encoding="utf-8"))
        local_assets = [
            urlparse(asset).path
            for asset in parser.assets
            if not urlparse(asset).scheme
        ]
        self.assertIn("styles.css", local_assets)
        self.assertIn("src/app.js", local_assets)
        for asset in local_assets:
            self.assertTrue((ROOT / asset).is_file(), asset)

    def test_frontend_does_not_contain_sos_credentials_or_endpoint(self):
        frontend = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [ROOT / "index.html", ROOT / "styles.css", *sorted((ROOT / "src").glob("*.js"))]
        )
        self.assertNotIn("Ocp-Apim-Subscription-Key", frontend)
        self.assertNotIn("api.artdatabanken.se", frontend)
        self.assertIsNone(re.search(r"[a-f0-9]{32}", frontend, flags=re.IGNORECASE))

    def test_map_uses_resilient_maplibre_embedding_and_redlist_layer(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        map_source = (ROOT / "src" / "map.js").read_text(encoding="utf-8")
        self.assertIn("maplibre-gl@5.11.0", html)
        self.assertNotIn("leaflet", html.lower())
        self.assertIn("ResizeObserver", map_source)
        self.assertIn("forceSeveralMapRefreshes", map_source)
        self.assertIn('LAYER_OBSERVATIONS = "observations-circle"', map_source)
        for category in ("CR", "EN", "VU", "NT", "DD", "LC"):
            self.assertIn(f'{category}: "#', map_source)

    def test_frontend_loads_points_lazily_and_has_redlist_toggles(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="redlist-filters"', html)
        self.assertIn('loadJson("data/search-index.json"', app)
        self.assertIn("observationFilesForRange", app)
        self.assertIn("state.partitionCache", app)
        self.assertIn("disabledRedlistCategories", app)

    def test_refresh_workflow_uses_named_secret_without_embedding_a_key(self):
        workflow = (ROOT / ".github" / "workflows" / "refresh-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SOS_SUBSCRIPTION_KEY: ${{ secrets.SOS_SUBSCRIPTION_KEY }}", workflow)
        self.assertIn('cron: "17 4 * * *"', workflow)
        self.assertIn("scripts/refresh_data.py --incremental", workflow)
        self.assertIn("data/search-index.json data/observations", workflow)
        self.assertIsNone(re.search(r"[a-f0-9]{32}", workflow, flags=re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
