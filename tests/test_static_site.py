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
        self.assertIn('value="national_park"', html)
        self.assertIn('value="observation_infrastructure"', html)
        self.assertIn('value="all"', html)
        self.assertNotIn('value="observation_tower"', html)
        self.assertIn('id="reset-filters"', html)
        self.assertIn('loadJson("data/features.json"', app)
        self.assertIn('loadJson("data/skandobs.json"', app)
        self.assertIn("trail.municipalities", core)
        self.assertIn('LAYER_RESERVES = "nature-reserves-fill"', map_source)
        self.assertIn('LAYER_NATIONAL_PARKS = "national-parks-fill"', map_source)
        self.assertIn('LAYER_DESTINATIONS = "nature-destinations-circle"', map_source)
        self.assertGreaterEqual(translations.count("maximumRangeNote"), 3)
        self.assertGreaterEqual(translations.count("sensitiveSpeciesNote"), 3)
        self.assertIn('data-i18n="trailCoverageNote"', html)
        self.assertGreaterEqual(translations.count("trailCoverageNote"), 3)
        self.assertIn("OpenStreetMap", translations)
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
        translations = (ROOT / "src" / "i18n.js").read_text(encoding="utf-8")
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
        self.assertIn("REDLIST_PRIORITY[taxon.redlistCategory]", app)
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
        self.assertIn('data-i18n="welcomeContribute"', html)
        self.assertGreaterEqual(translations.count("welcomeContribute"), 3)
        self.assertIn("WELCOME_COOKIE", app)
        self.assertIn("SameSite=Lax", app)
        self.assertIn("closeWelcomeDialog({ remember: true })", app)

    def test_species_rows_are_grouped_expandable_and_show_weekly_seasonality(self):
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        core = (ROOT / "src" / "core.js").read_text(encoding="utf-8")
        styles = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn('node("tr", "observation-group-row")', app)
        self.assertIn('node("tr", "observation-details-row")', app)
        self.assertIn("expandedObservationTaxa", app)
        self.assertIn("taxon.observations", app)
        self.assertIn("weeklySeasonality(observations)", app)
        self.assertIn("observationRecordItem", app)
        self.assertIn("export function weeklySeasonality", core)
        self.assertIn("seasonality-chart", styles)

    def test_new_issue_controls_are_wired_without_blocking_startup(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        core = (ROOT / "src" / "core.js").read_text(encoding="utf-8")
        map_source = (ROOT / "src" / "map.js").read_text(encoding="utf-8")
        self.assertIn('<details class="map-key">', html)
        self.assertIn('class="locate-user-icon"', html)
        self.assertIn('id="locate-user" class="locate-user" type="button" aria-pressed="false" disabled', html)
        self.assertIn('id="trail-details-home"', html)
        self.assertIn("appendLocationActions", app)
        self.assertIn("navigator.share", app)
        self.assertIn("fitCurrentAreaAfterRender", app)
        self.assertIn("loadPlaceRankingsForSelection", app)
        self.assertIn("speciesWithRankings", app)
        self.assertIn("suggestions.length >= 15", app)
        self.assertIn("matchesFeatureKind", core)
        self.assertIn("pendingUserLocation", map_source)

    def test_place_type_is_restored_without_changing_the_map_view(self):
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        self.assertIn('FEATURE_KIND_PREFERENCE_KEY = "vildaleder-feature-kind"', app)
        self.assertIn("initialiseFeatureKindControl()", app)
        self.assertIn("localStorage.setItem(FEATURE_KIND_PREFERENCE_KEY", app)
        self.assertIn("localStorage.removeItem(FEATURE_KIND_PREFERENCE_KEY)", app)
        feature_kind_handler = app.split(
            'elements.featureKind.addEventListener("change"', 1
        )[1].split('elements.county.addEventListener("change"', 1)[0]
        self.assertNotIn("fitCurrentAreaAfterRender", feature_kind_handler)

    def test_issue_23_searches_named_places_on_submit_without_autocomplete(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        map_source = (ROOT / "src" / "map.js").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('id="locality-search-form"', html)
        self.assertIn('id="locality-search-results"', html)
        self.assertIn('elements.localitySearchForm.addEventListener("submit", searchLocality)', app)
        self.assertNotIn('elements.localitySearch.addEventListener("input"', app)
        self.assertIn('GEOCODER_ENDPOINT = "https://nominatim.openstreetmap.org/search"', app)
        self.assertIn('countrycodes: "se"', app)
        self.assertIn('limit: "5"', app)
        self.assertIn("PLACE_SEARCH_MIN_INTERVAL_MS = 1_100", app)
        self.assertIn("PLACE_SEARCH_CACHE_MAX_AGE_MS", app)
        self.assertIn("showSearchedPlace", app)
        self.assertIn('SOURCE_SEARCHED_PLACE = "searched-place"', map_source)
        self.assertIn("clearSearchedPlace", map_source)
        self.assertIn("Nominatim usage policy", readme)

    def test_issue_24_sorts_grouped_observations_by_count(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        translations = (ROOT / "src" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('data-sort="count"', html)
        self.assertIn('if (key === "count") return Number(taxon.count || 0)', app)
        self.assertIn('["date", "count"].includes(key) ? "desc" : "asc"', app)
        self.assertIn('node("td", "observation-count", formatNumber(taxon.count))', app)
        self.assertIn("detailsCell.colSpan = 5", app)
        self.assertGreaterEqual(translations.count("observationCountColumn"), 3)

    def test_issue_26_uses_a_three_step_search_drawer_and_replaces_it_with_results(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "styles.css").read_text(encoding="utf-8")
        translations = (ROOT / "src" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('id="menu-toggle"', html)
        self.assertIn('id="search-sidebar"', html)
        self.assertIn('id="sidebar-scrim"', html)
        self.assertEqual(html.count("data-criteria-step="), 3)
        self.assertIn('id="criteria-view"', html)
        self.assertIn('id="results-view" class="results-view" hidden', html)
        self.assertIn('id="show-results"', html)
        self.assertIn('id="back-to-criteria"', html)
        self.assertIn('id="open-tutorial"', html)
        self.assertIn("function setCriteriaStep(step)", app)
        self.assertIn("function validateLocationCriteria()", app)
        self.assertIn("function showSearchResults", app)
        self.assertIn("function showSearchCriteria()", app)
        self.assertIn('showWelcomeDialog({ force: true })', app)
        self.assertIn('document.body.classList.toggle("sidebar-is-open", open)', app)
        self.assertIn("transform: translateX(-105%)", styles)
        self.assertGreaterEqual(translations.count("choosePlaceTypeToContinue"), 3)
        self.assertNotIn('class="mode-tabs"', html)

    def test_issue_27_links_grouped_species_to_artfakta_by_taxon_id(self):
        app = (ROOT / "src" / "app.js").read_text(encoding="utf-8")
        translations = (ROOT / "src" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn("function artfaktaUrl(taxon)", app)
        self.assertIn("https://artfakta.se/taxa/", app)
        self.assertIn('node("a", "artfakta-link"', app)
        self.assertIn('artfaktaLink.target = "_blank"', app)
        self.assertGreaterEqual(translations.count("readOnArtfakta"), 3)

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
