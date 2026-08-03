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
        local_assets = [asset for asset in parser.assets if not urlparse(asset).scheme]
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


if __name__ == "__main__":
    unittest.main()

