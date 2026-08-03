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

    def test_halland_feature_filters_reserves_and_data_caveats_are_in_the_ui(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        core = (ROOT / "src" / "core.js").read_text(encoding="utf-8")
        map_source = (ROOT / "src" / "map.js").read_text(encoding="utf-8")
        translations = (ROOT / "src" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('id="feature-kind"', html)
        self.assertIn('value="reserve"', html)
        self.assertIn('id="reset-filters"', html)
        self.assertIn('loadJson("data/features.json"', app)
        self.assertIn('loadJson("data/skandobs.json"', app)
        self.assertIn("trail.municipalities", core)
        self.assertIn('LAYER_RESERVES = "nature-reserves-fill"', map_source)
        self.assertGreaterEqual(translations.count("maximumRangeNote"), 3)
        self.assertGreaterEqual(translations.count("sensitiveSpeciesNote"), 3)
        self.assertGreaterEqual(translations.count("resetFilters"), 3)

    def test_custom_dates_location_tracking_and_skandobs_are_wired(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        map_source = (ROOT / "src" / "map.js").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "refresh-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="custom-dates" class="two-columns custom-dates" hidden', html)
        self.assertGreaterEqual(app.count("activateCustomPeriod();"), 2)
        self.assertIn('id="locate-user"', html)
        self.assertIn("LOCATION_REFRESH_MS = 2_000", app)
        self.assertIn("navigator.geolocation.getCurrentPosition", app)
        self.assertIn("setUserLocation", map_source)
        self.assertIn('SOURCE_USER_LOCATION = "user-location"', map_source)
        self.assertIn("scripts/server_refresh.sh", workflow)
        self.assertNotIn("schedule:", workflow)

    def test_map_clusters_overlapping_points_and_observation_table_tracks_selection(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        map_source = (ROOT / "src" / "map.js").read_text(encoding="utf-8")
        self.assertIn("cluster: true", map_source)
        self.assertIn('LAYER_OBSERVATION_CLUSTERS = "observations-clusters"', map_source)
        self.assertIn("point_count_abbreviated", map_source)
        self.assertIn("getClusterExpansionZoom", map_source)
        self.assertIn('id="observation-table-panel"', html)
        self.assertIn('id="observation-table-body"', html)
        self.assertIn('id="map-table-resizer"', html)
        self.assertIn('role="separator"', html)
        self.assertIn("setupMapTableResizer()", app)
        self.assertIn("vildaleder-map-table-ratio", app)
        self.assertIn('addEventListener("pointermove"', app)
        self.assertIn('addEventListener("keydown"', app)
        self.assertIn("vildaleder-period", app)
        self.assertIn('addEventListener("pageshow"', app)
        self.assertIn('elements.customDates.hidden = state.period !== "custom"', app)
        self.assertIn("setObservationTableRows(visibleMapObservations)", app)
        self.assertNotIn("onViewportChange: handleViewportChange", app)
        self.assertIn('data-sort="redlist"', html)
        self.assertIn("REDLIST_PRIORITY[observation.redlistCategory]", app)
        self.assertIn("sortObservationTable", app)
        self.assertIn("focusObservation(observation)", app)
        self.assertIn("OBSERVATION_TABLE_PAGE_SIZE", app)
        self.assertIn("function areaSpeciesObservations()", app)
        self.assertIn("state.skandobs.matches", app)
        self.assertIn("mapSpeciesAreaPoints", app)
        self.assertIn("(state.searchIndex.taxa || [])", app)
        self.assertIn("speciesObservationFiles", app)
        self.assertIn("speciesPointFeatureIndex", app)
        self.assertIn("async function areaSpeciesObservations()", app)
        self.assertIn("function featurePopup(feature)", app)
        self.assertIn("showFeaturePopup(lngLat, featurePopup(feature))", app)
        self.assertIn("showFeatureTooltip", map_source)
        self.assertIn("feature-name-tooltip", map_source)
        self.assertIn("function clearTrailSelection()", app)
        self.assertIn("state.selectedTrailId === trailId", app)
        self.assertIn("clearPlaceSelection", app)
        self.assertIn('id="welcome-dialog"', html)
        self.assertIn("WELCOME_COOKIE", app)
        self.assertIn("SameSite=Lax", app)
        self.assertIn("closeWelcomeDialog({ remember: true })", app)

    def test_refresh_is_owned_by_the_local_server_without_embedding_a_key(self):
        workflow = (ROOT / ".github" / "workflows" / "refresh-data.yml").read_text(
            encoding="utf-8"
        )
        refresh = (ROOT / "scripts" / "server_refresh.sh").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "vildaleder-refresh.timer").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("schedule:", workflow)
        self.assertIn("03:00:00 Europe/Stockholm", timer)
        self.assertIn("scripts/sync_features.py", refresh)
        self.assertIn("scripts/sync_halland_postgis.py", refresh)
        self.assertIn("scripts/sync_skandobs.py", refresh)
        self.assertIn("scripts/enrich_gbif_taxonomy.py", refresh)
        self.assertIn("scripts/export_postgis_snapshot.py", refresh)
        self.assertIn('git push origin "HEAD:${REFRESH_BRANCH}"', refresh)
        self.assertIn("speciesPointFeatureIds", refresh)
        self.assertIn("data/species-observations", refresh)
        self.assertIn("SNAPSHOT_POLL_MS", (ROOT / "src" / "app.js").read_text(encoding="utf-8"))
        self.assertIsNone(re.search(r"[a-f0-9]{32}", workflow, flags=re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
