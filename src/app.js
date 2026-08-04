import {
  dateOnly,
  filterObservations,
  filteredTrails,
  groupTaxa,
  indexedTrailStats,
  matchesFeatureKind,
  observationFilesForRange,
  periodRange,
  rankTrailsForSpecies,
  rankTrailsForMultipleSpecies,
  REDLIST_PRIORITY,
  resolveSpecies,
  speciesCatalog,
  speciesLabel,
  weeklySeasonality,
} from "./core.js?v=20260804-map-menu-v24";
import { translations, translator } from "./i18n.js?v=20260804-map-menu-v24";
import {
  clearSearchedPlace,
  clearUserLocation,
  fitAllTrails,
  fitTrail,
  initMap,
  focusObservation,
  refreshMapSize,
  setObservationColors,
  setObservations,
  setTrails,
  setHoveredTrail,
  setUserLocation,
  showFeaturePopup,
  showObservationPopup,
  showSearchedPlace,
} from "./map.js?v=20260804-map-menu-v24";

const OBSERVATION_TABLE_PAGE_SIZE = 100;
const LOCATION_REFRESH_MS = 2_000;
const SNAPSHOT_POLL_MS = 15 * 60 * 1_000;
const MAP_TABLE_RATIO_KEY = "vildaleder-map-table-ratio";
const DEFAULT_MAP_TABLE_RATIO = 0.25;
const MIN_MAP_HEIGHT = 240;
const MIN_TABLE_HEIGHT = 160;
const PERIOD_PREFERENCE_KEY = "vildaleder-period";
const CUSTOM_START_PREFERENCE_KEY = "vildaleder-custom-start";
const CUSTOM_END_PREFERENCE_KEY = "vildaleder-custom-end";
const PLACE_SEARCH_CACHE_PREFIX = "vildaleder-place-search:";
const PLACE_SEARCH_CACHE_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1_000;
const PLACE_SEARCH_MIN_INTERVAL_MS = 1_100;
const GEOCODER_ENDPOINT = "https://nominatim.openstreetmap.org/search";
const PERIOD_VALUES = new Set(["day", "month", "quarter", "year", "custom"]);
const WELCOME_COOKIE = "vildaleder_welcome_dismissed";
const WELCOME_COOKIE_MAX_AGE = 365 * 24 * 60 * 60;
const FEATURE_KIND_TRANSLATIONS = Object.freeze({
  trail: "trail",
  reserve: "natureReserve",
  national_park: "nationalPark",
  observation_tower: "observationTower",
  bird_hide: "birdHide",
  observation_site: "observationSite",
});

const state = {
  catalog: null,
  searchIndex: null,
  skandobs: null,
  skandobsRecordById: new Map(),
  speciesPointFeatureIndex: new Map(),
  taxonById: new Map(),
  partitionCache: new Map(),
  placeRankingCache: new Map(),
  loadedPlaceRankingGroups: new Set(),
  speciesRankingCache: new Map(),
  speciesSearchEntries: [],
  speciesSearchTimer: null,
  speciesRankingRequest: 0,
  placeSearchRequest: 0,
  lastPlaceSearchAt: 0,
  loadedSelection: null,
  detailsRequest: 0,
  disabledRedlistCategories: new Set(),
  selectedObjectObservations: [],
  expandedObservationTaxa: new Set(),
  observationTablePage: 0,
  observationTableSort: { key: "date", direction: "desc" },
  language: initialLanguage(),
  mode: "trail",
  criteriaStep: 1,
  searchView: "criteria",
  featureKind: "",
  county: "",
  municipality: "",
  period: "year",
  customStart: "",
  customEnd: "",
  trailQuery: "",
  speciesQuery: "",
  selectedSpeciesList: [],
  speciesSortBy: "days",
  speciesSortDir: "desc",
  selectedTrailId: null,
  locationTracking: false,
  locationRequestPending: false,
  locationHasFix: false,
  locationTimer: null,
  appReady: false,
};

const elements = {
  welcomeDialog: document.querySelector("#welcome-dialog"),
  welcomeClose: document.querySelector("#welcome-close"),
  welcomeStart: document.querySelector("#welcome-start"),
  welcomeDismiss: document.querySelector("#welcome-dismiss"),
  sidebar: document.querySelector("#search-sidebar"),
  menuToggle: document.querySelector("#menu-toggle"),
  sidebarClose: document.querySelector("#sidebar-close"),
  sidebarScrim: document.querySelector("#sidebar-scrim"),
  criteriaView: document.querySelector("#criteria-view"),
  resultsView: document.querySelector("#results-view"),
  criteriaSteps: [...document.querySelectorAll("[data-criteria-step]")],
  criteriaProgress: [...document.querySelectorAll("[data-criteria-progress]")],
  criteriaValidation: document.querySelector("#criteria-validation"),
  nextStepButtons: [...document.querySelectorAll("[data-next-step]")],
  previousStepButtons: [...document.querySelectorAll("[data-previous-step]")],
  showResults: document.querySelector("#show-results"),
  backToCriteria: document.querySelector("#back-to-criteria"),
  newSearch: document.querySelector("#new-search"),
  openTutorial: document.querySelector("#open-tutorial"),
  status: document.querySelector("#status"),
  locateUser: document.querySelector("#locate-user"),
  locationStatus: document.querySelector("#location-status"),
  resetFilters: document.querySelector("#reset-filters"),
  localitySearchForm: document.querySelector("#locality-search-form"),
  localitySearch: document.querySelector("#locality-search"),
  localitySearchSubmit: document.querySelector("#locality-search-submit"),
  localitySearchStatus: document.querySelector("#locality-search-status"),
  localitySearchResults: document.querySelector("#locality-search-results"),
  language: document.querySelector("#language"),
  featureKind: document.querySelector("#feature-kind"),
  county: document.querySelector("#county"),
  municipality: document.querySelector("#municipality"),
  period: document.querySelector("#period"),
  customDates: document.querySelector("#custom-dates"),
  dateFrom: document.querySelector("#date-from"),
  dateTo: document.querySelector("#date-to"),
  snapshotNote: document.querySelector("#snapshot-note"),
  trailPanel: document.querySelector("#trail-panel"),
  speciesPanel: document.querySelector("#species-panel"),
  trailSearch: document.querySelector("#trail-search"),
  speciesSearch: document.querySelector("#species-search"),
  selectedSpeciesList: document.querySelector("#selected-species-list"),
  speciesSuggestions: document.querySelector("#species-suggestions"),
  speciesSummary: document.querySelector("#species-summary"),
  speciesSortControls: document.querySelector("#species-sort-controls"),
  speciesSortBy: document.querySelector("#species-sort-by"),
  speciesSortDir: document.querySelector("#species-sort-dir"),
  trailResults: document.querySelector("#trail-results"),
  speciesResults: document.querySelector("#species-results"),
  trailDetailsHome: document.querySelector("#trail-details-home"),
  trailDetails: document.querySelector("#trail-details"),
  mapObservationSummary: document.querySelector("#map-observation-summary"),
  mapPanel: document.querySelector(".map-panel"),
  mapTableResizer: document.querySelector("#map-table-resizer"),
  redlistFilters: document.querySelector("#redlist-filters"),
  observationTablePanel: document.querySelector("#observation-table-panel"),
  observationTableTitle: document.querySelector("#observation-table-title"),
  observationTableSummary: document.querySelector("#observation-table-summary"),
  observationTablePagination: document.querySelector("#observation-table-pagination"),
  observationTableScroll: document.querySelector(".observation-table-scroll"),
  observationTableBody: document.querySelector("#observation-table-body"),
  observationTableEmpty: document.querySelector("#observation-table-empty"),
  observationSortButtons: [...document.querySelectorAll(".observation-sort-button")],
};

function initialLanguage() {
  const saved = localStorage.getItem("vildaleder-language");
  if (saved && translations[saved]) return saved;
  const browserLanguage = navigator.language.slice(0, 2).toLowerCase();
  return translations[browserLanguage] ? browserLanguage : "en";
}

function t(key, values) {
  return translator(state.language)(key, values);
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatDate(value) {
  const day = dateOnly(value);
  if (!day) return "—";
  return new Intl.DateTimeFormat(state.language, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(`${day}T12:00:00Z`));
}

function formatNumber(value) {
  return new Intl.NumberFormat(state.language).format(value);
}

function featureKindLabel(feature) {
  return t(FEATURE_KIND_TRANSLATIONS[feature.featureKind] || "trail");
}

function featureDimension(feature) {
  if (["reserve", "national_park"].includes(feature.featureKind)) {
    return t("areaHectares", { value: formatNumber(Math.round(feature.areaHa || 0)) });
  }
  if (feature.featureKind === "trail") {
    return t("length", { value: feature.lengthKm });
  }
  return t("bufferedDestination");
}

function featureSourceLabel(feature) {
  if (feature.source === "nvl") return t("nvlSource");
  if (["reserve", "national_park"].includes(feature.featureKind)) {
    return t("protectedAreaSource");
  }
  return feature.featureKind === "trail" ? t("osmRoute") : t("osmPlace");
}

function coordinatePairs(coordinates, pairs = []) {
  if (!Array.isArray(coordinates)) return pairs;
  if (
    coordinates.length >= 2 &&
    Number.isFinite(Number(coordinates[0])) &&
    Number.isFinite(Number(coordinates[1]))
  ) {
    pairs.push([Number(coordinates[0]), Number(coordinates[1])]);
    return pairs;
  }
  coordinates.forEach((coordinate) => coordinatePairs(coordinate, pairs));
  return pairs;
}

function featureLocation(feature) {
  const pairs = coordinatePairs(feature?.geometry?.coordinates);
  if (!pairs.length) return null;
  const longitudes = pairs.map(([longitude]) => longitude);
  const latitudes = pairs.map(([, latitude]) => latitude);
  return {
    longitude: (Math.min(...longitudes) + Math.max(...longitudes)) / 2,
    latitude: (Math.min(...latitudes) + Math.max(...latitudes)) / 2,
  };
}

function navigationUrl(feature) {
  const location = featureLocation(feature);
  if (!location) return "";
  const destination = `${location.latitude.toFixed(6)},${location.longitude.toFixed(6)}`;
  return `https://www.google.com/maps/dir/?api=1&travelmode=walking&destination=${encodeURIComponent(destination)}`;
}

function shareLocationButton(feature) {
  const url = navigationUrl(feature);
  if (!url) return null;
  const button = node("button", "share-place-location", t("sharePlaceLocation"));
  button.type = "button";
  button.addEventListener("click", async () => {
    const shareData = {
      title: feature.name,
      text: t("shareLocationText", { place: feature.name }),
      url,
    };
    try {
      if (navigator.share) {
        await navigator.share(shareData);
      } else {
        await navigator.clipboard.writeText(url);
        button.textContent = t("locationCopied");
        window.setTimeout(() => { button.textContent = t("sharePlaceLocation"); }, 2_000);
      }
    } catch (error) {
      if (error?.name !== "AbortError") console.warn("Location sharing failed", error);
    }
  });
  return button;
}

function appendLocationActions(container, feature) {
  const url = navigationUrl(feature);
  if (!url) return;
  const navigate = node("a", "navigate-to-place", t("navigateToPlace"));
  navigate.href = url;
  navigate.target = "_blank";
  navigate.rel = "noreferrer";
  const share = shareLocationButton(feature);
  container.append(navigate);
  if (share) container.append(share);
}

function currentRange() {
  return periodRange(
    state.period,
    state.customStart,
    state.customEnd,
    state.catalog.meta.windowStart,
    state.catalog.meta.windowEnd,
  );
}

function areaTrails(query = "") {
  return filteredTrails(state.catalog.trails, {
    featureKind: state.featureKind,
    county: state.county,
    municipality: state.municipality,
    query,
  });
}

function localizedSpecies(species) {
  return {
    ...species,
    vernacularName:
      species?.vernacularNames?.[state.language] || species?.vernacularName,
  };
}

function localizedSpeciesLabel(species) {
  return speciesLabel(localizedSpecies(species));
}

function applyLanguage() {
  document.documentElement.lang = state.language;
  document.title = `VildaLeder — ${t("tagline")}`;
  elements.language.value = state.language;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
  });
  elements.mapTableResizer.setAttribute("aria-label", t("resizeMapTable"));
  elements.welcomeClose.setAttribute("aria-label", t("welcomeClose"));
  elements.sidebarClose.setAttribute("aria-label", t("closeSearchMenu"));
  elements.sidebarScrim.setAttribute("aria-label", t("closeSearchMenu"));
  syncSidebarState();
  updateLocationButton();
  if (state.catalog) renderAll();
}

function cookieValue(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

function rememberWelcomeDismissal() {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${encodeURIComponent(WELCOME_COOKIE)}=1; Max-Age=${WELCOME_COOKIE_MAX_AGE}; Path=/; SameSite=Lax${secure}`;
}

function closeWelcomeDialog({ remember = false } = {}) {
  if (remember) rememberWelcomeDismissal();
  elements.welcomeDialog.hidden = true;
  document.body.classList.remove("welcome-is-open");
}

function showWelcomeDialog({ force = false } = {}) {
  if (!force && cookieValue(WELCOME_COOKIE) === "1") return;
  elements.welcomeDialog.hidden = false;
  document.body.classList.add("welcome-is-open");
  window.requestAnimationFrame(() => elements.welcomeClose.focus());
}

function mobileSidebar() {
  return window.matchMedia("(max-width: 800px)").matches;
}

function sidebarIsOpen() {
  return mobileSidebar()
    ? document.body.classList.contains("sidebar-is-open")
    : !document.body.classList.contains("sidebar-is-collapsed");
}

function syncSidebarState() {
  const open = sidebarIsOpen();
  const label = t(open ? "closeSearchMenu" : "openSearchMenu");
  elements.menuToggle.setAttribute("aria-expanded", String(open));
  elements.menuToggle.setAttribute("aria-label", label);
  elements.menuToggle.setAttribute("title", label);
  const menuLabel = elements.menuToggle.querySelector(".sr-only");
  if (menuLabel) menuLabel.textContent = label;
  elements.sidebarScrim.hidden = !mobileSidebar() || !open;
}

function setSidebarOpen(open) {
  if (mobileSidebar()) {
    document.body.classList.toggle("sidebar-is-open", open);
  } else {
    document.body.classList.toggle("sidebar-is-collapsed", !open);
  }
  syncSidebarState();
  if (open) {
    window.requestAnimationFrame(() => elements.sidebar.focus({ preventScroll: true }));
  }
}

function clearCriteriaValidation() {
  elements.criteriaValidation.hidden = true;
  elements.criteriaValidation.textContent = "";
}

function setCriteriaStep(step) {
  const nextStep = Math.min(3, Math.max(1, Number(step) || 1));
  state.criteriaStep = nextStep;
  clearCriteriaValidation();
  elements.criteriaSteps.forEach((section) => {
    section.hidden = Number(section.dataset.criteriaStep) !== nextStep;
  });
  elements.criteriaProgress.forEach((item) => {
    const itemStep = Number(item.dataset.criteriaProgress);
    item.classList.toggle("is-active", itemStep === nextStep);
    item.classList.toggle("is-complete", itemStep < nextStep);
    if (itemStep === nextStep) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  elements.sidebar.scrollTo({ top: 0, behavior: "smooth" });
}

function validateLocationCriteria() {
  if (state.featureKind) return true;
  setCriteriaStep(3);
  elements.criteriaValidation.textContent = t("choosePlaceTypeToContinue");
  elements.criteriaValidation.hidden = false;
  window.requestAnimationFrame(() => elements.featureKind.focus());
  return false;
}

function showSearchResults({ validate = true } = {}) {
  if (validate && !validateLocationCriteria()) return false;
  state.speciesQuery = elements.speciesSearch.value;
  const mode = state.selectedSpeciesList.length > 0 || state.speciesQuery.trim() ? "species" : "trail";
  setMode(mode);
  state.searchView = "results";
  elements.criteriaView.hidden = true;
  elements.resultsView.hidden = false;
  elements.sidebar.scrollTo({ top: 0, behavior: "smooth" });
  if (state.catalog) renderAll();
  return true;
}

function showSearchCriteria() {
  state.searchView = "criteria";
  elements.resultsView.hidden = true;
  elements.criteriaView.hidden = false;
  setCriteriaStep(3);
}

function populateAreaFilters() {
  const counties = [...new Set(state.catalog.trails.map((trail) => trail.county))].sort();
  const municipalities = [
    ...new Set(
      state.catalog.trails
        .filter(
          (trail) =>
            (!state.featureKind || matchesFeatureKind(trail.featureKind, state.featureKind)) &&
            (!state.county || trail.county === state.county),
        )
        .flatMap((trail) => trail.municipalities || [trail.municipality].filter(Boolean)),
    ),
  ].sort();
  const availableMunicipalities = state.catalog.featureMeta?.municipalities;
  const filteredMunicipalities = availableMunicipalities
    ? municipalities.filter((municipality) => availableMunicipalities.includes(municipality))
    : municipalities;
  fillSelect(elements.county, counties, t("allCounties"), state.county);
  fillSelect(
    elements.municipality,
    filteredMunicipalities,
    t("allMunicipalities"),
    state.municipality,
  );
  if (state.municipality && !filteredMunicipalities.includes(state.municipality)) {
    state.municipality = "";
    elements.municipality.value = "";
  }
}

function fillSelect(select, values, emptyLabel, selected) {
  select.replaceChildren();
  const empty = node("option", "", emptyLabel);
  empty.value = "";
  select.append(empty);
  values.forEach((value) => {
    const option = node("option", "", value);
    option.value = value;
    option.selected = value === selected;
    select.append(option);
  });
}

function normalizedSearchText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .trim();
}

function buildSpeciesSearchEntries() {
  state.speciesSearchEntries = speciesCatalog(state.searchIndex).map((species) => {
    const labels = [
      species.scientificName,
      species.vernacularName,
      ...Object.values(species.vernacularNames || {}),
    ].filter(Boolean);
    return {
      species,
      searchText: normalizedSearchText(labels.join(" ")),
      labels,
    };
  });
}

function populateSpeciesSuggestions(query = "") {
  elements.speciesSuggestions.replaceChildren();
  const needle = normalizedSearchText(query).replace(/\s+—\s+.*/, "");
  if (needle.length < 2) return;
  const suggestions = [];
  const seen = new Set();
  state.speciesSearchEntries
    .filter((entry) => entry.searchText.includes(needle))
    .sort((left, right) => {
      const leftStarts = left.searchText.startsWith(needle) ? 0 : 1;
      const rightStarts = right.searchText.startsWith(needle) ? 0 : 1;
      return leftStarts - rightStarts || localizedSpeciesLabel(left.species).localeCompare(
        localizedSpeciesLabel(right.species),
        state.language,
      );
    })
    .some((entry) => {
      const label = localizedSpeciesLabel(entry.species);
      if (seen.has(label)) return false;
      seen.add(label);
      suggestions.push(label);
      return suggestions.length >= 15;
    });
  suggestions.forEach((label) => {
      const option = document.createElement("li");
      option.textContent = label;
      option.addEventListener("click", () => {
        elements.speciesSearch.value = label;
        // Trigger the input event to add the pill
        elements.speciesSearch.dispatchEvent(new Event("input"));
        elements.speciesSuggestions.replaceChildren();
      });
      elements.speciesSuggestions.append(option);
  });
}

function placeSearchCacheKey(query) {
  return `${PLACE_SEARCH_CACHE_PREFIX}${state.language}:${encodeURIComponent(query.trim().toLocaleLowerCase())}`;
}

function cachedPlaceSearch(query) {
  const key = placeSearchCacheKey(query);
  try {
    const cached = JSON.parse(localStorage.getItem(key));
    if (
      cached &&
      Array.isArray(cached.results) &&
      Date.now() - Number(cached.cachedAt || 0) <= PLACE_SEARCH_CACHE_MAX_AGE_MS
    ) {
      return cached.results;
    }
    localStorage.removeItem(key);
  } catch {
    localStorage.removeItem(key);
  }
  return null;
}

function cachePlaceSearch(query, results) {
  try {
    localStorage.setItem(
      placeSearchCacheKey(query),
      JSON.stringify({ cachedAt: Date.now(), results }),
    );
  } catch {
    // Search still works when browser storage is unavailable or full.
  }
}

function setPlaceSearchStatus(key = "") {
  elements.localitySearchStatus.hidden = !key;
  elements.localitySearchStatus.textContent = key ? t(key) : "";
}

function renderPlaceSearchResults(results) {
  elements.localitySearchResults.replaceChildren();
  elements.localitySearchResults.hidden = results.length === 0;
  if (!results.length) {
    setPlaceSearchStatus("noPlacesFound");
    return;
  }
  setPlaceSearchStatus();
  results.forEach((result) => {
    const button = node("button", "locality-search-result");
    button.type = "button";
    button.append(node("span", "locality-result-name", result.display_name));
    const kind = result.addresstype || result.type;
    if (kind) button.append(node("span", "locality-result-kind", kind.replaceAll("_", " ")));
    button.addEventListener("click", () => {
      showSearchedPlace({
        name: result.display_name,
        latitude: Number(result.lat),
        longitude: Number(result.lon),
        boundingBox: result.boundingbox,
      });
    });
    elements.localitySearchResults.append(button);
  });
}

async function searchLocality(event) {
  event.preventDefault();
  const query = elements.localitySearch.value.trim();
  if (query.length < 2 || !state.appReady) return;
  const request = ++state.placeSearchRequest;
  elements.localitySearchSubmit.disabled = true;
  elements.localitySearchResults.hidden = true;
  setPlaceSearchStatus("searchingPlaces");
  try {
    let results = cachedPlaceSearch(query);
    if (!results) {
      const waitMs = Math.max(
        0,
        PLACE_SEARCH_MIN_INTERVAL_MS - (Date.now() - state.lastPlaceSearchAt),
      );
      if (waitMs) await new Promise((resolve) => window.setTimeout(resolve, waitMs));
      state.lastPlaceSearchAt = Date.now();
      const parameters = new URLSearchParams({
        q: query,
        format: "jsonv2",
        countrycodes: "se",
        addressdetails: "1",
        limit: "5",
        "accept-language": state.language,
      });
      const response = await fetch(`${GEOCODER_ENDPOINT}?${parameters}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`Place search failed: ${response.status}`);
      results = (await response.json())
        .filter(
          (result) =>
            Number.isFinite(Number(result.lat)) && Number.isFinite(Number(result.lon)),
        )
        .slice(0, 5);
      cachePlaceSearch(query, results);
    }
    if (request !== state.placeSearchRequest) return;
    renderPlaceSearchResults(results);
  } catch (error) {
    if (request !== state.placeSearchRequest) return;
    console.error(error);
    elements.localitySearchResults.replaceChildren();
    elements.localitySearchResults.hidden = true;
    setPlaceSearchStatus("placeSearchError");
  } finally {
    if (request === state.placeSearchRequest) {
      elements.localitySearchSubmit.disabled = !state.appReady;
    }
  }
}

function placeRankingGroup(feature) {
  return ["bird_hide", "observation_tower", "observation_site"].includes(feature.featureKind)
    ? "observation_infrastructure"
    : feature.featureKind;
}

function selectedPlaceRankingGroups() {
  const files = state.searchIndex?.placeRankingFiles || {};
  if (state.featureKind === "all") return Object.keys(files);
  return state.featureKind && files[state.featureKind] ? [state.featureKind] : [];
}

function placeRankingReady(feature) {
  if (!feature.fullObservationCoverage) return true;
  return state.loadedPlaceRankingGroups.has(placeRankingGroup(feature));
}

async function loadPlaceRankingsForSelection() {
  const selection = state.featureKind;
  const groups = selectedPlaceRankingGroups();
  const payloads = await Promise.all(
    groups.map(async (group) => {
      if (!state.placeRankingCache.has(group)) {
        const path = state.searchIndex.placeRankingFiles[group];
        state.placeRankingCache.set(group, loadJson(path, `Place rankings: ${group}`));
      }
      return [group, await state.placeRankingCache.get(group)];
    }),
  );
  payloads.forEach(([group, payload]) => {
    if (state.loadedPlaceRankingGroups.has(group)) return;
    Object.entries(payload.trails || {}).forEach(([featureId, dated]) => {
      state.searchIndex.trails[featureId] = mergeDated(
        dated,
        state.searchIndex.trails[featureId] || [],
      );
    });
    state.loadedPlaceRankingGroups.add(group);
  });
  if (selection === state.featureKind) renderTrailResults();
}

async function speciesWithRankings(species) {
  let trails = species.trails || null;
  if (!trails) {
    const bucket = species.rankingBucket;
    const path = state.searchIndex.speciesRankingFiles?.[bucket];
    if (!path) trails = {};
    else {
      if (!state.speciesRankingCache.has(bucket)) {
        state.speciesRankingCache.set(bucket, loadJson(path, `Species rankings: ${bucket}`));
      }
      const payload = await state.speciesRankingCache.get(bucket);
      trails = payload.taxa?.[String(species.taxonId)] || {};
    }
  }
  const combined = { ...trails };
  (state.searchIndex.taxa || [])
    .filter(
      (candidate) =>
        candidate !== species &&
        candidate.trails &&
        String(candidate.taxonId) === String(species.taxonId),
    )
    .forEach((candidate) => {
      Object.entries(candidate.trails).forEach(([featureId, dated]) => {
        combined[featureId] = mergeDated(combined[featureId] || [], dated);
      });
    });
  return { ...species, trails: combined };
}

function renderAll() {
  populateAreaFilters();
  if (
    state.selectedTrailId &&
    !areaTrails().some((feature) => feature.id === state.selectedTrailId)
  ) {
    state.selectedTrailId = null;
    state.loadedSelection = null;
  }
  elements.snapshotNote.textContent = t("snapshot", {
    start: formatDate(state.catalog.meta.windowStart),
    end: formatDate(state.catalog.meta.windowEnd),
    generated: formatDate(state.catalog.meta.generatedAt),
  });
  renderTrailResults();
  void renderSpeciesResults();
  void renderTrailDetails();
  renderObservationTable();
  updateMapStyles();
}

function renderTrailResults() {
  elements.trailResults.replaceChildren();
  if (!state.featureKind) {
    elements.trailResults.append(node("p", "empty-state", t("choosePlaceTypePrompt")));
    return;
  }
  const range = currentRange();
  let trails = areaTrails(state.trailQuery);
  if (!trails.length) {
    elements.trailResults.append(node("p", "empty-state", t("noTrails")));
    return;
  }
  
  trails.sort((a, b) => {
    const statsA = indexedTrailStats(state.searchIndex, a.id, range);
    const statsB = indexedTrailStats(state.searchIndex, b.id, range);
    if (statsB.observations !== statsA.observations) {
      return (statsB.observations || 0) - (statsA.observations || 0);
    }
    return a.name.localeCompare(b.name);
  });
  
  trails.forEach((trail) => {
    const stats = indexedTrailStats(state.searchIndex, trail.id, range);
    const button = node("button", "result-card");
    button.type = "button";
    button.classList.toggle("is-selected", trail.id === state.selectedTrailId);
    const title = node("span", "result-title", trail.name);
    title.prepend(featureKindBadge(trail));
    button.append(title);
    const dimension = featureDimension(trail);
    const evidence = trail.observationCoverage
      ? !placeRankingReady(trail)
        ? t("loadingCounts")
        : `${t("observations", { count: formatNumber(stats.observations) })}${
            stats.species ? ` · ${t("species", { count: formatNumber(stats.species) })}` : ""
          }${trail.fullObservationCoverage ? "" : ` · ${t("skandobsOnly")}`}`
      : t("observationSyncPending");
    button.append(node("span", "result-meta", `${dimension} · ${evidence}`));
    button.addEventListener("click", () => selectTrail(trail.id));
    button.addEventListener("mouseenter", () => setHoveredTrail(trail.id));
    button.addEventListener("mouseleave", () => setHoveredTrail(null));
    elements.trailResults.append(button);
  });
}

async function renderSpeciesResults() {
  const request = ++state.speciesRankingRequest;
  if (elements.trailDetails.parentElement !== elements.trailDetailsHome) {
    elements.trailDetailsHome.append(elements.trailDetails);
  }
  elements.speciesResults.replaceChildren();
  elements.speciesSummary.replaceChildren();
  if (elements.speciesSortControls) elements.speciesSortControls.hidden = true;
  
  if (state.selectedSpeciesList.length === 0 && !state.speciesQuery.trim()) {
    elements.speciesResults.append(node("p", "empty-state", t("noSpecies")));
    return;
  }
  
  let targetList = [...state.selectedSpeciesList];
  if (state.speciesQuery.trim() && targetList.length === 0) {
    const allSpecies = speciesCatalog(state.searchIndex);
    const resolved = resolveSpecies(allSpecies, state.speciesQuery);
    if (!resolved) {
      elements.speciesResults.append(node("p", "empty-state", t("speciesNotFound")));
      return;
    }
    targetList = [resolved];
  }

  if (targetList.length === 0) {
    elements.speciesResults.append(node("p", "empty-state", t("noSpecies")));
    return;
  }

  if (!state.featureKind) {
    const names = targetList.map(localizedSpeciesLabel).join(" + ");
    elements.speciesSummary.textContent = t("rankedFor", { species: names });
    elements.speciesResults.append(node("p", "empty-state", t("choosePlaceTypePrompt")));
    return;
  }

  const query = state.speciesQuery;
  elements.speciesSortControls.hidden = false;
  elements.speciesResults.append(node("p", "empty-state", t("loadingRankings")));
  
  try {
    targetList = await Promise.all(targetList.map(s => speciesWithRankings(s)));
  } catch (error) {
    if (request !== state.speciesRankingRequest) return;
    console.error(error);
    elements.speciesResults.replaceChildren(node("p", "empty-state error-text", t("rankingLoadError")));
    return;
  }
  
  if (request !== state.speciesRankingRequest || (targetList.length === 0 && query !== state.speciesQuery)) return;
  
  elements.speciesResults.replaceChildren();
  const names = targetList.map(localizedSpeciesLabel).join(" + ");
  elements.speciesSummary.textContent = t("rankedFor", { species: names });
  
  const rankings = rankTrailsForMultipleSpecies(
    areaTrails(),
    targetList,
    currentRange(),
    state.searchIndex,
    { by: state.speciesSortBy, dir: state.speciesSortDir }
  );
  
  if (!rankings.length) {
    elements.speciesResults.append(node("p", "empty-state", t("noObservations")));
    return;
  }
  
  rankings.forEach((ranking, index) => {
    const item = node("div", "species-result-item");
    const button = node("button", "result-card");
    button.type = "button";
    button.classList.toggle("is-selected", ranking.trail.id === state.selectedTrailId);
    
    const titleText = `${index + 1}. ${ranking.trail.name} (Score: ${ranking.combinedScore.toFixed(2)})`;
    const title = node("span", "result-title", titleText);
    title.prepend(featureKindBadge(ranking.trail));
    button.append(title);
    
    const dimension = featureDimension(ranking.trail);
    let evidenceText = "";
    
    ranking.perSpeciesStats.forEach((stats, i) => {
      const speciesColor = targetList[i].color || "#000";
      evidenceText += `<span style="color:${speciesColor}; font-weight:bold;">${formatNumber(stats.count)}</span> `;
      evidenceText += `(<span style="color:${speciesColor};">${formatNumber(stats.days)}d</span>) `;
    });
    
    button.append(node("span", "result-meta", `${dimension} · Counts (Days): `));
    const evidenceSpan = document.createElement("span");
    evidenceSpan.innerHTML = evidenceText.trim();
    button.lastChild.append(evidenceSpan);
    
    button.addEventListener("click", () => selectTrail(ranking.trail.id));
    button.addEventListener("mouseenter", () => setHoveredTrail(ranking.trail.id));
    button.addEventListener("mouseleave", () => setHoveredTrail(null));
    item.append(button);
    if (state.mode === "species" && ranking.trail.id === state.selectedTrailId) {
      item.append(elements.trailDetails);
    }
    elements.speciesResults.append(item);
  });
}

function selectionKey(trail, range) {
  return `${trail.id}|${range.start}|${range.end}`;
}

async function loadPartition(file) {
  if (!state.partitionCache.has(file.path)) {
    state.partitionCache.set(
      file.path,
      fetch(file.path, { cache: "no-cache" }).then((response) => {
        if (!response.ok) throw new Error(`Partition request failed: ${response.status}`);
        return response.json();
      }),
    );
  }
  return state.partitionCache.get(file.path);
}

function expandObservation(record) {
  const [
    sourceId,
    day,
    taxonId,
    individualCount,
    flags,
    latitude,
    longitude,
    uncertaintyMeters,
    featureIndexes,
  ] = record;
  const taxon = localizedSpecies(state.taxonById.get(String(taxonId)) || {});
  const hasArtportalenId = /^\d+$/.test(String(sourceId));
  return {
    id: hasArtportalenId ? `urn:lsid:artportalen.se:sighting:${sourceId}` : String(sourceId),
    date: day,
    taxonId,
    scientificName: taxon.scientificName,
    vernacularName: taxon.vernacularName,
    organismGroup: taxon.organismGroup,
    redlistCategory: taxon.redlistCategory,
    individualCount,
    verified: Boolean(flags & 1),
    uncertainIdentification: Boolean(flags & 2),
    latitude,
    longitude,
    uncertaintyMeters,
    featureIndexes: featureIndexes || [],
    sourceUrl: hasArtportalenId
      ? `https://www.artportalen.se/sighting/${sourceId}`
      : null,
    dataset: "Artportalen",
  };
}

async function loadTrailObservations(trail, range) {
  const files = observationFilesForRange(trail, range);
  const partitions = await Promise.all(files.map(loadPartition));
  const skandobsIds = state.skandobs?.matches?.[trail.id] || [];
  const skandobsObservations = skandobsIds
    .map((observationId) => state.skandobsRecordById.get(observationId))
    .filter(Boolean)
    .map(expandSkandobsObservation);
  return filterObservations(
    [
      ...partitions.flatMap((partition) => (partition.records || []).map(expandObservation)),
      ...skandobsObservations,
    ],
    range,
  );
}

function expandSkandobsObservation(record) {
  const taxon = localizedSpecies(state.taxonById.get(String(record.taxonId)) || {});
  return {
    id: `skandobs:${record.id}`,
    date: record.date,
    taxonId: record.taxonId,
    scientificName: taxon.scientificName,
    vernacularName: taxon.vernacularName,
    organismGroup: taxon.organismGroup,
    redlistCategory: taxon.redlistCategory,
    individualCount: record.individualCount,
    verified: Number(record.validationId) === 5,
    uncertainIdentification: Number(record.validationId) < 0,
    validationStatus: record.validationStatus,
    activity: record.activity,
    latitude: Number(record.latitude),
    longitude: Number(record.longitude),
    locationIsGeneralized: Boolean(record.locationIsGeneralized),
    sourceUrl: record.sourceUrl,
    dataset: "Skandobs",
  };
}

async function areaSpeciesObservations() {
  if (!state.selectedSpeciesList.length || !state.skandobs) return [];
  const range = currentRange();
  const allowedFeatureIndexes = new Set(
    areaTrails()
      .map((feature) => state.speciesPointFeatureIndex.get(feature.id))
      .filter((index) => index !== undefined),
  );
  
  let allObservations = [];
  for (const species of state.selectedSpeciesList) {
    const selectedTaxonId = String(species.taxonId);
    const bucketFiles = species.pointBucket
      ? state.searchIndex.speciesObservationFiles?.[species.pointBucket] || []
      : [];
    const files = observationFilesForRange({ observationFiles: bucketFiles }, range);
    const partitions = await Promise.all(files.map(loadPartition));
    const sosObservations = partitions
      .flatMap((partition) => (partition.records || []).map(expandObservation))
      .filter((observation) => String(observation.taxonId) === selectedTaxonId)
      .filter((observation) =>
        observation.featureIndexes.some((index) => allowedFeatureIndexes.has(index)),
      );
    const observationIds = new Set();
    areaTrails().forEach((feature) => {
      (state.skandobs.matches?.[feature.id] || []).forEach((observationId) =>
        observationIds.add(observationId),
      );
    });
    const skandobsObservations = [...observationIds]
        .map((observationId) => state.skandobsRecordById.get(observationId))
        .filter(Boolean)
        .filter((record) => String(record.taxonId) === selectedTaxonId)
        .map(expandSkandobsObservation);
    allObservations.push(...sosObservations, ...skandobsObservations);
  }
  return filterObservations(allObservations, range);
}

async function renderAreaSpeciesObservations(request) {
  setObservations([]);
  setObservationTableRows([]);
  renderRedlistFilters([]);
  elements.mapObservationSummary.textContent = t("loadingSpeciesMap");
  
  if (!state.selectedSpeciesList.length) {
    setObservations([]);
    renderMapObservationSummary(null, 0);
    return;
  }
  
  let observations;
  try {
    observations = await areaSpeciesObservations();
  } catch (error) {
    if (request !== state.detailsRequest) return;
    console.error(error);
    elements.mapObservationSummary.textContent = t("speciesMapLoadError");
    return;
  }
  if (request !== state.detailsRequest) return;
  renderRedlistFilters(observations);
  
  const visibleObservations = observations.filter(
    (observation) =>
      !state.disabledRedlistCategories.has(observation.redlistCategory || "unknown"),
  );
  
  const colorMap = {};
  state.selectedSpeciesList.forEach(s => {
    if (s.color) colorMap[s.taxonId] = s.color;
  });
  setObservationColors(colorMap);
  
  const mappedObservationCount = setObservations(visibleObservations) ?? 0;
  setObservationTableRows([]);
  renderMapObservationSummary(null, mappedObservationCount);
  elements.speciesSummary.append(
    seasonalityPanel(
      observations,
      t("weeklySeasonalityFor", { species: localizedSpeciesLabel(state.selectedSpecies) }),
    ),
  );
}

async function renderTrailDetails() {
  if (!state.catalog) return;
  const request = ++state.detailsRequest;
  const trail = state.catalog.trails.find((candidate) => candidate.id === state.selectedTrailId);
  elements.trailDetails.replaceChildren();
  if (!trail) {
    elements.trailDetails.append(node("p", "empty-state", t("selectTrail")));
    if (state.mode === "species" && state.selectedSpeciesList.length > 0 && state.featureKind) {
      await renderAreaSpeciesObservations(request);
    } else {
      setObservationColors(null);
      setObservations([]);
      setObservationTableRows([]);
      renderRedlistFilters([]);
      renderMapObservationSummary(null, 0);
    }
    return;
  }
  if (!trail.observationCoverage) {
    setObservationColors(null);
    setObservations([]);
    setObservationTableRows([]);
    renderRedlistFilters([]);
    renderMapObservationSummary(trail, 0);
    renderResolvedTrailDetails(trail, []);
    return;
  }

  const range = currentRange();
  const key = selectionKey(trail, range);
  if (state.loadedSelection?.key === key) {
    renderResolvedTrailDetails(trail, state.loadedSelection.observations);
    return;
  }

  setObservationColors(null);
  setObservations([]);
  setObservationTableRows([]);
  renderRedlistFilters([]);
  renderMapObservationSummary(trail, 0);
  elements.trailDetails.append(node("p", "empty-state loading-observations", t("loadingTrail")));
  try {
    const observations = await loadTrailObservations(trail, range);
    if (request !== state.detailsRequest) return;
    state.loadedSelection = { key, observations };
    renderResolvedTrailDetails(trail, observations);
  } catch (error) {
    if (request !== state.detailsRequest) return;
    console.error(error);
    elements.trailDetails.replaceChildren(node("p", "empty-state error-text", t("trailLoadError")));
  }
}

function renderResolvedTrailDetails(trail, observations) {
  elements.trailDetails.replaceChildren();
  const mapObservations = state.mode === "species"
    ? observations.filter(
        (observation) =>
          state.selectedSpeciesList.some(s => String(observation.taxonId) === String(s.taxonId))
      )
    : observations;
  renderRedlistFilters(mapObservations);
  const visibleMapObservations = mapObservations.filter(
    (observation) =>
      !state.disabledRedlistCategories.has(observation.redlistCategory || "unknown"),
  );
  
  if (state.mode === "species" && state.selectedSpeciesList.length > 0) {
    const colorMap = {};
    state.selectedSpeciesList.forEach(s => {
      if (s.color) colorMap[s.taxonId] = s.color;
    });
    setObservationColors(colorMap);
  } else {
    setObservationColors(null);
  }
  
  const mappedObservationCount = setObservations(visibleMapObservations) ?? 0;
  setObservationTableRows(visibleMapObservations);
  renderMapObservationSummary(trail, mappedObservationCount);
  const displayedObservations = state.mode === "species" ? mapObservations : observations;
  const taxa = groupTaxa(displayedObservations);
  const header = node("div", "details-header");
  const heading = node("h2", "", trail.name);
  heading.prepend(featureKindBadge(trail));
  header.append(heading);
  const municipalities = (trail.municipalities || [trail.municipality].filter(Boolean)).join(", ");
  const dimension = featureDimension(trail);
  header.append(
    node(
      "p",
      "details-meta",
      trail.observationCoverage
        ? `${municipalities}, ${trail.county} · ${dimension} · ${t("observations", {
            count: formatNumber(displayedObservations.length),
          })} · ${t("species", { count: formatNumber(taxa.length) })}`
        : `${municipalities}, ${trail.county} · ${dimension} · ${t("observationSyncPending")}`,
    ),
  );
  const links = node("div", "details-links");
  const osmLink = node("a", "", featureSourceLabel(trail));
  osmLink.href = trail.sourceUrl || trail.osmUrl;
  osmLink.target = "_blank";
  osmLink.rel = "noreferrer";
  const clearSelection = node("button", "clear-place-selection", t("clearPlaceSelection"));
  clearSelection.type = "button";
  clearSelection.addEventListener("click", clearTrailSelection);
  links.append(osmLink, clearSelection);
  appendLocationActions(links, trail);
  header.append(links);
  if (trail.description) header.append(node("p", "feature-description", trail.description));
  if (trail.marking) {
    header.append(node("p", "feature-description", trail.marking));
  }
  elements.trailDetails.append(header);

  if (!trail.observationCoverage) {
    elements.trailDetails.append(node("p", "data-caveat", t("observationCoverageNote")));
  } else {
    if (!trail.fullObservationCoverage) {
      elements.trailDetails.append(
        node("p", "data-caveat", t("partialObservationCoverageNote")),
      );
    }
    if (!taxa.length) elements.trailDetails.append(node("p", "empty-state", t("noObservations")));
  }
}

function setObservationTableRows(observations) {
  state.selectedObjectObservations = [...observations];
  state.expandedObservationTaxa.clear();
  state.observationTablePage = 0;
  renderObservationTable();
}

function observationTaxonKey(taxon) {
  return String(
    taxon.taxonId ?? taxon.scientificName ?? taxon.vernacularName ?? "unknown",
  ).toLocaleLowerCase();
}

function artfaktaUrl(taxon) {
  const identifier = String(taxon?.sourceTaxonId ?? taxon?.taxonId ?? "");
  const match = identifier.match(/(?:^|:)(\d+)$/);
  return match ? `https://artfakta.se/taxa/${match[1]}` : "";
}

function observationGroupSources(taxon) {
  return [...new Set(taxon.observations.map((observation) => observation.dataset).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, state.language));
}

function observationGroupSortValue(taxon, key) {
  if (key === "redlist") return REDLIST_PRIORITY[taxon.redlistCategory] ?? 99;
  if (key === "count") return Number(taxon.count || 0);
  if (key === "species") {
    return taxon.vernacularName || taxon.scientificName || "";
  }
  if (key === "source") return observationGroupSources(taxon).join(" ");
  return taxon.lastSeen || "";
}

function groupedObservationTableRows() {
  const { key, direction } = state.observationTableSort;
  const multiplier = direction === "asc" ? 1 : -1;
  return groupTaxa(state.selectedObjectObservations).sort((left, right) => {
    const leftValue = observationGroupSortValue(left, key);
    const rightValue = observationGroupSortValue(right, key);
    const comparison = typeof leftValue === "number"
      ? leftValue - rightValue
      : String(leftValue).localeCompare(String(rightValue), state.language, {
          sensitivity: "base",
          numeric: true,
        });
    return (
      comparison * multiplier ||
      (right.lastSeen || "").localeCompare(left.lastSeen || "") ||
      observationTaxonKey(left).localeCompare(observationTaxonKey(right))
    );
  });
}

function updateObservationSortHeaders() {
  elements.observationSortButtons.forEach((button) => {
    const active = button.dataset.sort === state.observationTableSort.key;
    const header = button.closest("th");
    header?.setAttribute(
      "aria-sort",
      active
        ? state.observationTableSort.direction === "asc" ? "ascending" : "descending"
        : "none",
    );
    button.dataset.direction = active ? state.observationTableSort.direction : "";
  });
}

function sortObservationTable(key) {
  const current = state.observationTableSort;
  if (current.key === key) {
    current.direction = current.direction === "asc" ? "desc" : "asc";
  } else {
    state.observationTableSort = {
      key,
      direction: ["date", "count"].includes(key) ? "desc" : "asc",
    };
  }
  state.observationTablePage = 0;
  renderObservationTable();
}

function renderObservationTable() {
  const hasSelection = Boolean(state.catalog && state.selectedTrailId);
  elements.observationTablePanel.hidden = !hasSelection;
  elements.mapTableResizer.hidden = !hasSelection;
  if (!hasSelection) return;

  const selectedObject = state.catalog.trails.find(
    (feature) => feature.id === state.selectedTrailId,
  );
  elements.observationTableTitle.textContent = t("visibleObservationsTitle", {
    place: selectedObject?.name || "",
  });

  const observations = state.selectedObjectObservations;
  const taxa = groupedObservationTableRows();
  updateObservationSortHeaders();
  const pageCount = Math.max(1, Math.ceil(taxa.length / OBSERVATION_TABLE_PAGE_SIZE));
  state.observationTablePage = Math.min(state.observationTablePage, pageCount - 1);
  const start = state.observationTablePage * OBSERVATION_TABLE_PAGE_SIZE;
  const pageTaxa = taxa.slice(start, start + OBSERVATION_TABLE_PAGE_SIZE);

  elements.observationTableBody.replaceChildren();
  elements.observationTablePagination.replaceChildren();
  elements.observationTableEmpty.hidden = observations.length > 0;
  elements.observationTableScroll.hidden = observations.length === 0;

  if (!observations.length) {
    elements.observationTableSummary.textContent = t("visibleObservationCount", { count: 0 });
    return;
  }

  elements.observationTableSummary.textContent = t("visibleSpeciesRange", {
    from: formatNumber(start + 1),
    to: formatNumber(start + pageTaxa.length),
    species: formatNumber(taxa.length),
    observations: formatNumber(observations.length),
  });
  pageTaxa.forEach((taxon) => {
    const rows = observationTaxonRows(taxon);
    elements.observationTableBody.append(...rows);
  });

  if (pageCount > 1) {
    const previous = node("button", "table-page-button", t("previousPage"));
    previous.type = "button";
    previous.disabled = state.observationTablePage === 0;
    previous.addEventListener("click", () => {
      state.observationTablePage -= 1;
      renderObservationTable();
    });
    const page = node(
      "span",
      "table-page-status",
      t("pageOf", {
        page: formatNumber(state.observationTablePage + 1),
        pages: formatNumber(pageCount),
      }),
    );
    const next = node("button", "table-page-button", t("nextPage"));
    next.type = "button";
    next.disabled = state.observationTablePage >= pageCount - 1;
    next.addEventListener("click", () => {
      state.observationTablePage += 1;
      renderObservationTable();
    });
    elements.observationTablePagination.append(previous, page, next);
  }
}

function mapTableRatioBounds() {
  const panelHeight = Math.max(1, elements.mapPanel.clientHeight);
  const resizerHeight = elements.mapTableResizer.offsetHeight || 13;
  const min = Math.max(0.18, MIN_TABLE_HEIGHT / panelHeight);
  return {
    min,
    max: Math.max(
      min,
      Math.min(0.65, (panelHeight - MIN_MAP_HEIGHT - resizerHeight) / panelHeight),
    ),
  };
}

function applyMapTableRatio(requestedRatio, persist = false) {
  const bounds = mapTableRatioBounds();
  const ratio = Math.min(Math.max(requestedRatio, bounds.min), Math.max(bounds.min, bounds.max));
  const percentage = Math.round(ratio * 1000) / 10;
  elements.mapPanel.style.setProperty("--observation-table-ratio", `${percentage}%`);
  elements.mapTableResizer.setAttribute("aria-valuemin", String(Math.round(bounds.min * 100)));
  elements.mapTableResizer.setAttribute("aria-valuemax", String(Math.round(bounds.max * 100)));
  elements.mapTableResizer.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
  if (persist) localStorage.setItem(MAP_TABLE_RATIO_KEY, String(ratio));
  return ratio;
}

function setupMapTableResizer() {
  const savedRatio = Number(localStorage.getItem(MAP_TABLE_RATIO_KEY));
  let ratio = applyMapTableRatio(
    Number.isFinite(savedRatio) && savedRatio > 0 ? savedRatio : DEFAULT_MAP_TABLE_RATIO,
  );
  let dragging = false;

  const finishDrag = (event) => {
    if (!dragging) return;
    dragging = false;
    elements.mapPanel.classList.remove("is-resizing");
    document.body.classList.remove("is-resizing-map-table");
    if (event?.pointerId !== undefined && elements.mapTableResizer.hasPointerCapture(event.pointerId)) {
      elements.mapTableResizer.releasePointerCapture(event.pointerId);
    }
    ratio = applyMapTableRatio(ratio, true);
  };

  elements.mapTableResizer.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(max-width: 800px)").matches) return;
    dragging = true;
    elements.mapTableResizer.setPointerCapture(event.pointerId);
    elements.mapPanel.classList.add("is-resizing");
    document.body.classList.add("is-resizing-map-table");
    event.preventDefault();
  });
  elements.mapTableResizer.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const panel = elements.mapPanel.getBoundingClientRect();
    ratio = applyMapTableRatio((panel.bottom - event.clientY) / panel.height);
  });
  elements.mapTableResizer.addEventListener("pointerup", finishDrag);
  elements.mapTableResizer.addEventListener("pointercancel", finishDrag);
  elements.mapTableResizer.addEventListener("dblclick", () => {
    ratio = applyMapTableRatio(DEFAULT_MAP_TABLE_RATIO, true);
  });
  elements.mapTableResizer.addEventListener("keydown", (event) => {
    let nextRatio = ratio;
    if (event.key === "ArrowUp") nextRatio += 0.04;
    else if (event.key === "ArrowDown") nextRatio -= 0.04;
    else if (event.key === "Home") nextRatio = mapTableRatioBounds().min;
    else if (event.key === "End") nextRatio = mapTableRatioBounds().max;
    else return;
    event.preventDefault();
    ratio = applyMapTableRatio(nextRatio, true);
  });
  window.addEventListener("resize", () => {
    ratio = applyMapTableRatio(ratio);
  });
}

function focusObservationRecord(observation) {
  focusObservation(observation);
  showObservationPopup(
    observation,
    [observation.longitude, observation.latitude],
    observationPopup(observation),
  );
}

function observationRecordItem(observation, speciesLabel) {
  const record = node("div", "observation-record-item");
  const zoom = node("button", "observation-record-zoom", formatDate(observation.date));
  zoom.type = "button";
  zoom.setAttribute(
    "aria-label",
    t("zoomToObservation", { species: speciesLabel, date: formatDate(observation.date) }),
  );
  zoom.addEventListener("click", () => focusObservationRecord(observation));
  const metadata = node("span", "observation-record-meta", observation.dataset || "—");
  record.append(zoom, metadata);
  if (observation.sourceUrl) {
    const source = node("a", "observation-record-source", t("openObservation"));
    source.href = observation.sourceUrl;
    source.target = "_blank";
    source.rel = "noreferrer";
    record.append(source);
  }
  return record;
}

function observationTaxonRows(taxon) {
  const key = observationTaxonKey(taxon);
  const expanded = state.expandedObservationTaxa.has(key);
  const label = taxon.vernacularName || taxon.scientificName || t("observation");
  const row = node("tr", "observation-group-row");
  const speciesCell = document.createElement("td");
  const toggle = node("button", "observation-group-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute(
    "aria-label",
    t(expanded ? "collapseObservationGroup" : "expandObservationGroup", { species: label }),
  );
  toggle.append(
    node("span", "observation-group-chevron", expanded ? "−" : "+"),
    node("span", "observation-species-name", label),
  );
  if (taxon.scientificName && taxon.scientificName !== taxon.vernacularName) {
    toggle.append(node("span", "scientific-name", taxon.scientificName));
  }
  toggle.addEventListener("click", () => {
    if (expanded) state.expandedObservationTaxa.delete(key);
    else state.expandedObservationTaxa.add(key);
    renderObservationTable();
  });
  speciesCell.append(toggle);
  const taxonUrl = artfaktaUrl(taxon);
  if (taxonUrl) {
    const artfaktaLink = node("a", "artfakta-link", `${t("readOnArtfakta")} ↗`);
    artfaktaLink.href = taxonUrl;
    artfaktaLink.target = "_blank";
    artfaktaLink.rel = "noreferrer";
    speciesCell.append(artfaktaLink);
  }
  const countCell = node("td", "observation-count", formatNumber(taxon.count));
  const dateCell = node("td", "", formatDate(taxon.lastSeen));
  const categoryCell = document.createElement("td");
  const category = taxon.redlistCategory || "unknown";
  const badge = node(
    "span",
    "redlist-badge",
    category === "unknown" ? t("unknownCategory") : category,
  );
  badge.dataset.category = category;
  categoryCell.append(badge);
  const sourceCell = node("td", "", observationGroupSources(taxon).join(" · ") || "—");
  row.append(speciesCell, countCell, dateCell, categoryCell, sourceCell);
  if (!expanded) return [row];

  const detailsRow = node("tr", "observation-details-row");
  const detailsCell = document.createElement("td");
  detailsCell.colSpan = 5;
  detailsCell.append(
    seasonalityPanel(taxon.observations, t("weeklySeasonalityFor", { species: label })),
    node("h3", "observation-record-heading", t("recentTaxonRecords")),
  );
  const records = node("div", "observation-record-list");
  [...taxon.observations]
    .sort(
      (left, right) =>
        (right.date || "").localeCompare(left.date || "") ||
        String(left.id || "").localeCompare(String(right.id || "")),
    )
    .forEach((observation) => records.append(observationRecordItem(observation, label)));
  detailsCell.append(records);
  detailsRow.append(detailsCell);
  return [row, detailsRow];
}

function renderRedlistFilters(observations) {
  elements.redlistFilters.replaceChildren();
  const counts = new Map();
  observations.forEach((observation) => {
    const category = observation.redlistCategory || "unknown";
    counts.set(category, (counts.get(category) || 0) + 1);
  });
  const categories = [...counts.keys()].sort(
    (left, right) =>
      (REDLIST_PRIORITY[left] ?? 99) - (REDLIST_PRIORITY[right] ?? 99) ||
      left.localeCompare(right),
  );
  categories.forEach((category) => {
    const active = !state.disabledRedlistCategories.has(category);
    const button = node("button", "redlist-filter");
    button.type = "button";
    button.dataset.category = category;
    button.classList.toggle("is-disabled", !active);
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute("aria-label", t(active ? "hideCategory" : "showCategory", {
      category: category === "unknown" ? t("otherCategory") : category,
      count: formatNumber(counts.get(category)),
    }));
    const swatch = node("i", "key-observation");
    swatch.dataset.category = category;
    button.append(
      swatch,
      node(
        "span",
        "",
        `${category === "unknown" ? t("otherCategory") : category} (${formatNumber(
          counts.get(category),
        )})`,
      ),
    );
    button.addEventListener("click", () => {
      if (state.disabledRedlistCategories.has(category)) {
        state.disabledRedlistCategories.delete(category);
      } else {
        state.disabledRedlistCategories.add(category);
      }
      void renderTrailDetails();
    });
    elements.redlistFilters.append(button);
  });
}

function svgNode(tag, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

function seasonalityPanel(observations, titleText = t("weeklySeasonality")) {
  const panel = node("section", "seasonality-panel");
  panel.append(
    node("h3", "seasonality-title", titleText),
    node("p", "seasonality-description", t("seasonalityDescription", currentRange())),
  );
  const series = weeklySeasonality(observations);
  const maximum = Math.max(1, ...series.map((item) => item.count));
  const width = 636;
  const height = 178;
  const plotTop = 12;
  const plotBottom = 142;
  const barStep = 11.4;
  const barWidth = 8.2;
  const chart = svgNode("svg", {
    class: "seasonality-chart",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `${titleText}. ${t("seasonalityDescription", currentRange())}`,
  });
  chart.append(svgNode("line", {
    x1: 22,
    y1: plotBottom,
    x2: width - 7,
    y2: plotBottom,
    class: "seasonality-axis",
  }));
  const maximumLabel = svgNode("text", {
    x: 20,
    y: plotTop + 5,
    class: "seasonality-label",
    "text-anchor": "end",
  });
  maximumLabel.textContent = formatNumber(maximum);
  const zeroLabel = svgNode("text", {
    x: 20,
    y: plotBottom + 3,
    class: "seasonality-label",
    "text-anchor": "end",
  });
  zeroLabel.textContent = "0";
  chart.append(maximumLabel, zeroLabel);
  series.forEach(({ week, count }, index) => {
    const barHeight = count ? Math.max(2, (count / maximum) * (plotBottom - plotTop)) : 0;
    const bar = svgNode("rect", {
      x: 24 + index * barStep,
      y: plotBottom - barHeight,
      width: barWidth,
      height: barHeight,
      class: "seasonality-bar",
      tabindex: count ? 0 : -1,
    });
    bar.append(svgNode("title"));
    bar.firstChild.textContent = t("weekCount", { week, count: formatNumber(count) });
    chart.append(bar);
  });
  [1, 13, 26, 39, 52].forEach((week) => {
    const label = svgNode("text", {
      x: 28 + (week - 1) * barStep,
      y: 163,
      class: "seasonality-label",
      "text-anchor": "middle",
    });
    label.textContent = t("weekShort", { week });
    chart.append(label);
  });
  panel.append(chart);
  return panel;
}

function renderMapObservationSummary(trail, count) {
  elements.mapObservationSummary.dataset.count = String(count);
  if (!trail) {
    if (state.mode === "species" && state.selectedSpecies) {
      elements.mapObservationSummary.textContent = t("mapSpeciesAreaPoints", {
        count: formatNumber(count),
        species: localizedSpeciesLabel(state.selectedSpecies),
      });
      return;
    }
    elements.mapObservationSummary.textContent = t(
      state.featureKind ? "mapSelectTrail" : "mapChoosePlaceType",
    );
    return;
  }
  if (!trail.observationCoverage) {
    elements.mapObservationSummary.textContent = t("mapObservationSyncPending", {
      trail: trail.name,
    });
    return;
  }
  if (state.mode === "species" && state.selectedSpecies) {
    elements.mapObservationSummary.textContent = t("mapSpeciesPoints", {
      count: formatNumber(count),
      species: localizedSpeciesLabel(state.selectedSpecies),
      trail: trail.name,
    });
    return;
  }
  elements.mapObservationSummary.textContent = t("mapTrailPoints", {
    count: formatNumber(count),
    trail: trail.name,
  });
}

function featureKindBadge(feature) {
  const badge = node(
    "span",
    "feature-kind-badge",
    featureKindLabel(feature),
  );
  badge.dataset.kind = feature.featureKind || "trail";
  return badge;
}

function observationPopup(observation) {
  const wrapper = node("div", "observation-popup");
  wrapper.append(
    node("div", "popup-title", observation.vernacularName || observation.scientificName || "—"),
  );
  if (observation.scientificName) {
    wrapper.append(node("div", "popup-scientific", observation.scientificName));
  }
  const parts = [formatDate(observation.date)];
  if (observation.redlistCategory) parts.push(observation.redlistCategory);
  if (observation.uncertaintyMeters) parts.push(`±${observation.uncertaintyMeters} m`);
  if (observation.locationIsGeneralized) parts.push(t("generalizedLocation"));
  if (observation.validationStatus) parts.push(observation.validationStatus);
  wrapper.append(node("div", "popup-meta", parts.join(" · ")));
  if (observation.sourceUrl) {
    const link = node("a", "", t("openObservation"));
    link.href = observation.sourceUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    wrapper.append(link);
  }
  return wrapper;
}

function featurePopup(feature) {
  const wrapper = node("div", "feature-popup");
  wrapper.append(node("div", "popup-title", feature.name));
  const municipalities = (feature.municipalities || [feature.municipality].filter(Boolean)).join(", ");
  const dimension = featureDimension(feature);
  wrapper.append(
    node(
      "div",
      "popup-meta",
      [featureKindLabel(feature), municipalities, dimension].filter(Boolean).join(" · "),
    ),
  );
  if (feature.description) wrapper.append(node("div", "popup-description", feature.description));
  if (feature.marking) wrapper.append(node("div", "popup-description", feature.marking));
  if (feature.sourceUrl || feature.osmUrl) {
    const link = node(
      "a",
      "",
      featureSourceLabel(feature),
    );
    link.href = feature.sourceUrl || feature.osmUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    wrapper.append(link);
  }
  appendLocationActions(wrapper, feature);
  return wrapper;
}

function drawMap() {
  const visibleIds = areaTrails().map((trail) => trail.id);
  setTrails(state.catalog.trails, visibleIds, state.selectedTrailId);
  fitAllTrails(areaTrails());
}

function updateMapStyles() {
  if (!state.catalog) return;
  setTrails(
    state.catalog.trails,
    areaTrails().map((trail) => trail.id),
    state.selectedTrailId,
  );
}

function currentMapExtentPlaces() {
  const visiblePlaces = areaTrails();
  if (visiblePlaces.length) return visiblePlaces;
  const areaContext = filteredTrails(state.catalog.trails, {
    featureKind: "all",
    county: state.county,
    municipality: state.municipality,
  });
  if (areaContext.length) return areaContext;
  return filteredTrails(state.catalog.trails, { featureKind: "all" });
}

function fitCurrentAreaAfterRender() {
  window.requestAnimationFrame(() => {
    const extentPlaces = currentMapExtentPlaces();
    if (extentPlaces.length) fitAllTrails(extentPlaces);
  });
}

function selectTrail(trailId) {
  if (state.selectedTrailId === trailId) {
    clearTrailSelection();
    return;
  }
  state.selectedTrailId = trailId;
  renderTrailResults();
  void renderSpeciesResults();
  void renderTrailDetails();
  updateMapStyles();
  fitTrail(state.catalog.trails.find((trail) => trail.id === trailId));
  if (window.innerWidth <= 800) {
    setSidebarOpen(true);
    elements.sidebar.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function clearTrailSelection() {
  if (!state.selectedTrailId) return;
  state.selectedTrailId = null;
  renderTrailResults();
  void renderSpeciesResults();
  void renderTrailDetails();
  updateMapStyles();
  fitAllTrails(areaTrails());
}

function resetFilters() {
  if (!state.catalog) return;
  state.featureKind = "";
  state.county = "";
  state.municipality = "";
  state.period = "year";
  state.customStart = state.catalog.meta.windowStart;
  state.customEnd = state.catalog.meta.windowEnd;
  state.trailQuery = "";
  state.speciesQuery = "";
  state.selectedSpeciesList = [];
  state.speciesSortBy = "days";
  state.speciesSortDir = "desc";
  if (elements.speciesSortBy) elements.speciesSortBy.value = "days";
  if (elements.speciesSortDir) elements.speciesSortDir.textContent = "⬇️";
  state.selectedTrailId = null;
  state.loadedSelection = null;
  state.placeSearchRequest += 1;
  state.disabledRedlistCategories.clear();
  if (elements.selectedSpeciesList) elements.selectedSpeciesList.replaceChildren();
  if (elements.speciesSearch) elements.speciesSearch.style.display = "";
  elements.featureKind.value = "";
  elements.localitySearch.value = "";
  elements.localitySearchResults.replaceChildren();
  elements.localitySearchResults.hidden = true;
  elements.localitySearchSubmit.disabled = !state.appReady;
  setPlaceSearchStatus();
  clearSearchedPlace();
  elements.period.value = "year";
  elements.customDates.hidden = true;
  elements.dateFrom.value = state.customStart;
  elements.dateTo.value = state.customEnd;
  persistPeriodControls();
  elements.trailSearch.value = "";
  elements.speciesSearch.value = "";
  setMode("trail");
  showSearchCriteria();
  renderAll();
  fitAllTrails(areaTrails());
}

function initialiseFeatureKindControl() {
  state.featureKind = "";
  elements.featureKind.value = "";
}

function activateCustomPeriod() {
  state.period = "custom";
  elements.period.value = "custom";
  elements.customDates.hidden = false;
}

function persistPeriodControls() {
  localStorage.setItem(PERIOD_PREFERENCE_KEY, state.period);
  if (state.customStart) localStorage.setItem(CUSTOM_START_PREFERENCE_KEY, state.customStart);
  if (state.customEnd) localStorage.setItem(CUSTOM_END_PREFERENCE_KEY, state.customEnd);
}

function initialisePeriodControls() {
  const savedPeriod = localStorage.getItem(PERIOD_PREFERENCE_KEY);
  if (PERIOD_VALUES.has(savedPeriod)) elements.period.value = savedPeriod;
  state.period = PERIOD_VALUES.has(elements.period.value) ? elements.period.value : "year";
  state.customStart = localStorage.getItem(CUSTOM_START_PREFERENCE_KEY) || elements.dateFrom.value;
  state.customEnd = localStorage.getItem(CUSTOM_END_PREFERENCE_KEY) || elements.dateTo.value;
  elements.customDates.hidden = state.period !== "custom";
}

function syncPeriodControlsFromDom({ persist = true, render = true } = {}) {
  const period = PERIOD_VALUES.has(elements.period.value) ? elements.period.value : "year";
  state.period = period;
  elements.customDates.hidden = period !== "custom";
  if (elements.dateFrom.value) state.customStart = elements.dateFrom.value;
  if (elements.dateTo.value) state.customEnd = elements.dateTo.value;
  if (persist) persistPeriodControls();
  if (render && state.catalog) renderAll();
}

function datePreferenceInWindow(value, windowStart, windowEnd, fallback) {
  const day = dateOnly(value);
  return day && day >= windowStart && day <= windowEnd ? day : fallback;
}

function updateLocationButton() {
  if (!elements.locateUser) return;
  const key = state.locationTracking ? "stopLocationTracking" : "showMyLocation";
  elements.locateUser.setAttribute("aria-label", t(key));
  elements.locateUser.setAttribute("title", t(key));
  elements.locateUser.setAttribute("aria-pressed", String(state.locationTracking));
  elements.locateUser.disabled = !state.appReady;
  elements.locateUser.classList.toggle("is-active", state.locationTracking);
  const label = elements.locateUser.querySelector(".sr-only");
  if (label) label.textContent = t(key);
}

function setLocationMessage(key = "") {
  elements.locationStatus.hidden = !key;
  elements.locationStatus.textContent = key ? t(key) : "";
}

function stopLocationTracking({ clear = true } = {}) {
  state.locationTracking = false;
  state.locationRequestPending = false;
  state.locationHasFix = false;
  window.clearInterval(state.locationTimer);
  state.locationTimer = null;
  if (clear) clearUserLocation();
  setLocationMessage();
  updateLocationButton();
}

function requestUserLocation() {
  if (!state.locationTracking || state.locationRequestPending) return;
  state.locationRequestPending = true;
  navigator.geolocation.getCurrentPosition(
    (position) => {
      state.locationRequestPending = false;
      if (!state.locationTracking) return;
      setUserLocation(
        {
          longitude: position.coords.longitude,
          latitude: position.coords.latitude,
          accuracy: position.coords.accuracy,
        },
        !state.locationHasFix,
      );
      state.locationHasFix = true;
      setLocationMessage();
    },
    (error) => {
      console.warn("Geolocation failed", error);
      stopLocationTracking();
      setLocationMessage(error?.code === 1 ? "locationDenied" : "locationUnavailable");
    },
    { enableHighAccuracy: true, maximumAge: 1_000, timeout: 10_000 },
  );
}

function toggleLocationTracking() {
  if (state.locationTracking) {
    stopLocationTracking();
    return;
  }
  if (!navigator.geolocation) {
    setLocationMessage("locationUnsupported");
    return;
  }
  state.locationTracking = true;
  state.locationHasFix = false;
  setLocationMessage("locationWaiting");
  updateLocationButton();
  requestUserLocation();
  state.locationTimer = window.setInterval(requestUserLocation, LOCATION_REFRESH_MS);
}

function setMode(mode) {
  if (mode === "species" && state.mode !== "species") {
    state.selectedTrailId = null;
    state.loadedSelection = null;
  }
  state.mode = mode;
  elements.trailPanel.hidden = mode !== "trail";
  elements.speciesPanel.hidden = mode !== "species";
  if (mode !== "species" && elements.trailDetails.parentElement !== elements.trailDetailsHome) {
    elements.trailDetailsHome.append(elements.trailDetails);
  }
  void renderTrailDetails();
}

function bindEvents() {
  elements.welcomeClose.addEventListener("click", () => closeWelcomeDialog());
  elements.welcomeStart.addEventListener("click", () => closeWelcomeDialog());
  elements.welcomeDismiss.addEventListener("click", () => closeWelcomeDialog({ remember: true }));
  elements.welcomeDialog.addEventListener("click", (event) => {
    if (event.target === elements.welcomeDialog) closeWelcomeDialog();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!elements.welcomeDialog.hidden) closeWelcomeDialog();
    else if (sidebarIsOpen()) setSidebarOpen(false);
  });
  elements.menuToggle.addEventListener("click", () => setSidebarOpen(!sidebarIsOpen()));
  elements.sidebarClose.addEventListener("click", () => setSidebarOpen(false));
  elements.sidebarScrim.addEventListener("click", () => setSidebarOpen(false));
  elements.nextStepButtons.forEach((button) =>
    button.addEventListener("click", () => {
      const step = Number(button.dataset.nextStep);
      setCriteriaStep(step);
    }),
  );
  elements.previousStepButtons.forEach((button) =>
    button.addEventListener("click", () => setCriteriaStep(button.dataset.previousStep)),
  );
  elements.criteriaProgress.forEach((item) => {
    item.addEventListener("click", () => {
      const step = Number(item.dataset.criteriaProgress);
      setCriteriaStep(step);
    });
  });
  elements.showResults.addEventListener("click", () => showSearchResults());
  elements.backToCriteria.addEventListener("click", showSearchCriteria);
  if (elements.newSearch) {
    elements.newSearch.addEventListener("click", () => {
      resetFilters();
      showSearchCriteria();
      setCriteriaStep(1);
    });
  }
  elements.openTutorial.addEventListener("click", () => showWelcomeDialog({ force: true }));
  elements.language.addEventListener("change", () => {
    state.language = elements.language.value;
    localStorage.setItem("vildaleder-language", state.language);
    state.loadedSelection = null;
    applyLanguage();
  });
  elements.locateUser.addEventListener("click", toggleLocationTracking);
  elements.localitySearchForm.addEventListener("submit", searchLocality);
  elements.speciesSearch.addEventListener("focus", () => {
    setTimeout(() => {
      elements.speciesSearch.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 250);
  });
  elements.resetFilters.addEventListener("click", resetFilters);
  elements.observationSortButtons.forEach((button) =>
    button.addEventListener("click", () => sortObservationTable(button.dataset.sort)),
  );
  elements.featureKind.addEventListener("change", () => {
    state.featureKind = elements.featureKind.value;
    if (state.featureKind) clearCriteriaValidation();
    renderAll();
    void loadPlaceRankingsForSelection().catch((error) => {
      console.error("Place rankings could not be loaded", error);
    });
  });
  elements.county.addEventListener("change", () => {
    state.county = elements.county.value;
    state.municipality = "";
    renderAll();
    fitCurrentAreaAfterRender();
  });
  elements.municipality.addEventListener("change", () => {
    state.municipality = elements.municipality.value;
    renderAll();
    fitCurrentAreaAfterRender();
  });
  elements.period.addEventListener("change", () => {
    syncPeriodControlsFromDom();
  });
  elements.dateFrom.addEventListener("change", () => {
    activateCustomPeriod();
    state.customStart = elements.dateFrom.value;
    persistPeriodControls();
    renderAll();
  });
  elements.dateTo.addEventListener("change", () => {
    activateCustomPeriod();
    state.customEnd = elements.dateTo.value;
    persistPeriodControls();
    renderAll();
  });
  elements.trailSearch.addEventListener("input", () => {
    state.trailQuery = elements.trailSearch.value;
    renderTrailResults();
  });
  elements.speciesSearch.addEventListener("input", () => {
    state.speciesQuery = elements.speciesSearch.value;
    const allSpecies = speciesCatalog(state.searchIndex);
    const resolved = resolveSpecies(allSpecies, state.speciesQuery);
    
    // Auto-select exact match species pill logic
    if (resolved && state.selectedSpeciesList.length < 3 && !state.selectedSpeciesList.some(s => s.taxonId === resolved.taxonId)) {
      state.selectedSpeciesList.push(resolved);
      elements.speciesSearch.value = "";
      state.speciesQuery = "";
      renderSelectedSpeciesPills();
    }
    
    populateSpeciesSuggestions(state.speciesQuery);
    window.clearTimeout(state.speciesSearchTimer);
    state.speciesSearchTimer = window.setTimeout(() => {
      void renderSpeciesResults();
      void renderTrailDetails();
    }, 140);
  });
  
  if (elements.speciesSortBy) {
    elements.speciesSortBy.addEventListener("change", (e) => {
      state.speciesSortBy = e.target.value;
      void renderSpeciesResults();
    });
  }

  if (elements.speciesSortDir) {
    elements.speciesSortDir.addEventListener("click", () => {
      state.speciesSortDir = state.speciesSortDir === "desc" ? "asc" : "desc";
      elements.speciesSortDir.textContent = state.speciesSortDir === "desc" ? "⬇️" : "⬆️";
      void renderSpeciesResults();
    });
  }

  // Close autocomplete when clicking outside
  document.addEventListener("click", (e) => {
    if (elements.speciesSearch && elements.speciesSuggestions) {
      if (!elements.speciesSearch.contains(e.target) && !elements.speciesSuggestions.contains(e.target)) {
        elements.speciesSuggestions.replaceChildren();
      }
    }
  });
  
  window.addEventListener("resize", syncSidebarState);
}

function renderSelectedSpeciesPills() {
  if (!elements.selectedSpeciesList) return;
  elements.selectedSpeciesList.replaceChildren();
  const colors = ["#e41a1c", "#377eb8", "#4daf4a"];
  state.selectedSpeciesList.forEach((species, index) => {
    const pill = node("div", "species-pill");
    const dot = node("span", "species-color-dot");
    const color = colors[index % colors.length];
    dot.style.backgroundColor = color;
    species.color = color;
    pill.append(dot);
    pill.append(document.createTextNode(localizedSpeciesLabel(species)));
    const removeBtn = node("button");
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", () => {
      state.selectedSpeciesList.splice(index, 1);
      renderSelectedSpeciesPills();
      void renderSpeciesResults();
      void renderTrailDetails();
    });
    pill.append(removeBtn);
    elements.selectedSpeciesList.append(pill);
  });
  
  if (state.selectedSpeciesList.length >= 3) {
    elements.speciesSearch.style.display = "none";
  } else {
    elements.speciesSearch.style.display = "";
  }
}

async function loadJson(path, label) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${label} request failed: ${response.status}`);
  return response.json();
}

function startSnapshotWatcher(generatedAt) {
  window.setInterval(async () => {
    try {
      const catalog = await loadJson(
        `data/catalog.json?refresh=${Date.now()}`,
        "Catalog refresh check",
      );
      if (catalog.meta?.generatedAt && catalog.meta.generatedAt !== generatedAt) {
        window.location.reload();
      }
    } catch (error) {
      console.warn("Snapshot refresh check failed", error);
    }
  }, SNAPSHOT_POLL_MS);
}

function mergedCatalog(observationCatalog, featureCatalog) {
  const observationsByFeature = new Map(
    observationCatalog.trails.map((trail) => [trail.id, trail]),
  );
  const features = featureCatalog.features.map((feature) => {
    const observationFeature = observationsByFeature.get(feature.id) || {};
    const skandobsMatchCount = state.skandobs?.matches?.[feature.id]?.length || 0;
    const fullObservationCoverage = Boolean(observationFeature.id);
    return {
      ...feature,
      ...observationFeature,
      featureKind: feature.featureKind,
      municipalities: feature.municipalities,
      municipality: feature.municipality,
      geometry: feature.geometry,
      corridor: feature.analysisGeometry,
      sourceUrl: feature.sourceUrl,
      areaHa: feature.areaHa,
      observationFiles: observationFeature.observationFiles || [],
      observationTotal: observationFeature.observationTotal || 0,
      fullObservationCoverage,
      skandobsMatchCount,
      observationCoverage: fullObservationCoverage || skandobsMatchCount > 0,
    };
  });
  return {
    ...observationCatalog,
    trails: features,
    featureMeta: featureCatalog.meta,
  };
}

function mergeDated(left = [], right = []) {
  const counts = new Map();
  [...left, ...right].forEach(([day, count]) =>
    counts.set(day, (counts.get(day) || 0) + Number(count || 0)),
  );
  return [...counts].sort(([leftDay], [rightDay]) => leftDay.localeCompare(rightDay));
}

function mergedSearchIndex(primary, additional) {
  const trails = { ...primary.trails };
  Object.entries(additional.trails || {}).forEach(([featureId, values]) => {
    trails[featureId] = mergeDated(trails[featureId], values);
  });
  return {
    ...primary,
    trails,
    taxa: [...(primary.taxa || []), ...(additional.taxa || [])],
  };
}

async function start() {
  applyLanguage();
  initialisePeriodControls();
  initialiseFeatureKindControl();
  setCriteriaStep(1);
  bindEvents();
  showWelcomeDialog();
  setupMapTableResizer();
  window.addEventListener("pageshow", () => {
    requestAnimationFrame(() => {
      initialiseFeatureKindControl();
      syncPeriodControlsFromDom();
    });
  });
  try {
    const [observationCatalog, featureCatalog, searchIndex, skandobs] = await Promise.all([
      loadJson("data/catalog.json", "Catalog"),
      loadJson("data/features.json", "Feature catalog"),
      loadJson("data/search-index.json", "Search index"),
      loadJson("data/skandobs.json", "Skandobs snapshot"),
      initMap({
        onTrailClick: (trailId, lngLat) => {
          if (state.searchView === "criteria") showSearchResults();
          selectTrail(trailId);
          if (window.innerWidth <= 800) setSidebarOpen(true);
          const feature = state.catalog?.trails.find((candidate) => candidate.id === trailId);
          if (feature) showFeaturePopup(lngLat, featurePopup(feature));
        },
        onObservationClick: (observation, lngLat) =>
          showObservationPopup(observation, lngLat, observationPopup(observation)),
      }),
    ]);
    state.skandobs = skandobs;
    state.skandobsRecordById = new Map(
      (skandobs.records || []).map((record) => [record.id, record]),
    );
    state.catalog = mergedCatalog(observationCatalog, featureCatalog);
    state.searchIndex = mergedSearchIndex(searchIndex, skandobs);
    state.appReady = true;
    updateLocationButton();
    elements.localitySearchSubmit.disabled = false;
    elements.showResults.disabled = false;
    state.speciesPointFeatureIndex = new Map(
      (searchIndex.speciesPointFeatureIds || []).map((featureId, index) => [featureId, index]),
    );
    state.taxonById = new Map(
      (state.searchIndex.taxa || []).map((taxon) => [String(taxon.taxonId), taxon]),
    );
    elements.dateFrom.min = state.catalog.meta.windowStart;
    elements.dateFrom.max = state.catalog.meta.windowEnd;
    state.customStart = datePreferenceInWindow(
      state.customStart,
      state.catalog.meta.windowStart,
      state.catalog.meta.windowEnd,
      state.catalog.meta.windowStart,
    );
    state.customEnd = datePreferenceInWindow(
      state.customEnd,
      state.catalog.meta.windowStart,
      state.catalog.meta.windowEnd,
      state.catalog.meta.windowEnd,
    );
    elements.dateFrom.value = state.customStart;
    elements.dateTo.min = state.catalog.meta.windowStart;
    elements.dateTo.max = state.catalog.meta.windowEnd;
    elements.dateTo.value = state.customEnd;
    elements.period.value = state.period;
    elements.customDates.hidden = state.period !== "custom";
    persistPeriodControls();
    elements.status.classList.add("is-ready");
    buildSpeciesSearchEntries();
    populateSpeciesSuggestions();
    drawMap();
    renderAll();
    startSnapshotWatcher(state.catalog.meta.generatedAt);
  } catch (error) {
    console.error(error);
    elements.status.classList.add("is-error");
    elements.status.replaceChildren(node("span", "", t("loadError")));
  }
}

start();
