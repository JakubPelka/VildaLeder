import {
  dateOnly,
  filterObservations,
  filteredTrails,
  groupTaxa,
  indexedTrailStats,
  observationFilesForRange,
  periodRange,
  rankTrailsForSpecies,
  REDLIST_PRIORITY,
  resolveSpecies,
  speciesCatalog,
  speciesLabel,
  weeklySeasonality,
} from "./core.js?v=20260803-seasonality-destinations-v12";
import { translations, translator } from "./i18n.js?v=20260803-seasonality-destinations-v12";
import {
  clearUserLocation,
  fitAllTrails,
  fitTrail,
  focusObservation,
  initMap,
  setObservations,
  setTrails,
  setUserLocation,
  showFeaturePopup,
  showObservationPopup,
} from "./map.js?v=20260803-seasonality-destinations-v12";

const MAX_TAXA_SHOWN = 100;
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
  loadedSelection: null,
  detailsRequest: 0,
  disabledRedlistCategories: new Set(),
  selectedObjectObservations: [],
  observationTablePage: 0,
  observationTableSort: { key: "date", direction: "desc" },
  language: initialLanguage(),
  mode: "trail",
  featureKind: "",
  county: "",
  municipality: "",
  period: "year",
  customStart: "",
  customEnd: "",
  trailQuery: "",
  speciesQuery: "",
  selectedSpecies: null,
  selectedTrailId: null,
  locationTracking: false,
  locationRequestPending: false,
  locationHasFix: false,
  locationTimer: null,
};

const elements = {
  welcomeDialog: document.querySelector("#welcome-dialog"),
  welcomeClose: document.querySelector("#welcome-close"),
  welcomeStart: document.querySelector("#welcome-start"),
  welcomeDismiss: document.querySelector("#welcome-dismiss"),
  status: document.querySelector("#status"),
  locateUser: document.querySelector("#locate-user"),
  locationStatus: document.querySelector("#location-status"),
  resetFilters: document.querySelector("#reset-filters"),
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
  speciesSuggestions: document.querySelector("#species-suggestions"),
  speciesSummary: document.querySelector("#species-summary"),
  trailResults: document.querySelector("#trail-results"),
  speciesResults: document.querySelector("#species-results"),
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
  modeTabs: [...document.querySelectorAll(".mode-tab")],
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
  return t("osmRoute");
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
  elements.mapTableResizer.setAttribute("aria-label", t("resizeMapTable"));
  elements.welcomeClose.setAttribute("aria-label", t("welcomeClose"));
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

function showWelcomeDialog() {
  if (cookieValue(WELCOME_COOKIE) === "1") return;
  elements.welcomeDialog.hidden = false;
  document.body.classList.add("welcome-is-open");
  window.requestAnimationFrame(() => elements.welcomeClose.focus());
}

function populateAreaFilters() {
  const counties = [...new Set(state.catalog.trails.map((trail) => trail.county))].sort();
  const municipalities = [
    ...new Set(
      state.catalog.trails
        .filter(
          (trail) =>
            (!state.featureKind || trail.featureKind === state.featureKind) &&
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

function populateSpeciesSuggestions() {
  elements.speciesSuggestions.replaceChildren();
  speciesCatalog(state.searchIndex).forEach((species) => {
    const labels = new Set([
      localizedSpeciesLabel(species),
      ...Object.values(species.vernacularNames || {}).map((name) =>
        species.scientificName ? `${name} — ${species.scientificName}` : name,
      ),
    ]);
    labels.forEach((label) => {
      const option = document.createElement("option");
      option.value = label;
      elements.speciesSuggestions.append(option);
    });
  });
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
  renderSpeciesResults();
  void renderTrailDetails();
  renderObservationTable();
  updateMapStyles();
}

function renderTrailResults() {
  elements.trailResults.replaceChildren();
  const range = currentRange();
  const trails = areaTrails(state.trailQuery);
  if (!trails.length) {
    elements.trailResults.append(node("p", "empty-state", t("noTrails")));
    return;
  }
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
      ? `${t("observations", { count: formatNumber(stats.observations) })} · ${t("species", {
          count: formatNumber(stats.species),
        })}${trail.fullObservationCoverage ? "" : ` · ${t("skandobsOnly")}`}`
      : t("observationSyncPending");
    button.append(node("span", "result-meta", `${dimension} · ${evidence}`));
    button.addEventListener("click", () => selectTrail(trail.id));
    elements.trailResults.append(button);
  });
}

function renderSpeciesResults() {
  elements.speciesResults.replaceChildren();
  elements.speciesSummary.replaceChildren();
  state.selectedSpecies = null;
  if (!state.speciesQuery.trim()) {
    elements.speciesResults.append(node("p", "empty-state", t("noSpecies")));
    return;
  }
  const allSpecies = speciesCatalog(state.searchIndex);
  state.selectedSpecies = resolveSpecies(allSpecies, state.speciesQuery);
  if (!state.selectedSpecies) {
    elements.speciesResults.append(node("p", "empty-state", t("speciesNotFound")));
    return;
  }
  elements.speciesSummary.textContent = t("rankedFor", {
    species: localizedSpeciesLabel(state.selectedSpecies),
  });
  const rankings = rankTrailsForSpecies(
    areaTrails(),
    state.selectedSpecies,
    currentRange(),
    state.searchIndex,
  );
  if (!rankings.length) {
    elements.speciesResults.append(node("p", "empty-state", t("noObservations")));
    return;
  }
  rankings.forEach((ranking, index) => {
    const button = node("button", "result-card");
    button.type = "button";
    button.classList.toggle("is-selected", ranking.trail.id === state.selectedTrailId);
    const title = node("span", "result-title", `${index + 1}. ${ranking.trail.name}`);
    title.prepend(featureKindBadge(ranking.trail));
    button.append(title);
    button.append(
      node(
        "span",
        "result-meta",
        `${t("observations", { count: formatNumber(ranking.count) })} · ${t("lastSeen", {
          date: formatDate(ranking.lastSeen),
        })}`,
      ),
    );
    button.addEventListener("click", () => selectTrail(ranking.trail.id));
    elements.speciesResults.append(button);
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
  if (!state.selectedSpecies || !state.skandobs) return [];
  const selectedTaxonId = String(state.selectedSpecies.taxonId);
  const range = currentRange();
  const allowedFeatureIndexes = new Set(
    areaTrails()
      .map((feature) => state.speciesPointFeatureIndex.get(feature.id))
      .filter((index) => index !== undefined),
  );
  const bucketFiles = state.selectedSpecies.pointBucket
    ? state.searchIndex.speciesObservationFiles?.[state.selectedSpecies.pointBucket] || []
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
      .filter(
        (record) => String(record.taxonId) === selectedTaxonId,
      )
      .map(expandSkandobsObservation);
  return filterObservations(
    [...sosObservations, ...skandobsObservations],
    range,
  );
}

async function renderAreaSpeciesObservations(request) {
  setObservations([]);
  setObservationTableRows([]);
  renderRedlistFilters([]);
  elements.mapObservationSummary.textContent = t("loadingSpeciesMap");
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
    if (state.mode === "species" && state.selectedSpecies) {
      await renderAreaSpeciesObservations(request);
    } else {
      setObservations([]);
      setObservationTableRows([]);
      renderRedlistFilters([]);
      renderMapObservationSummary(null, 0);
    }
    return;
  }
  if (!trail.observationCoverage) {
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
          state.selectedSpecies &&
          String(observation.taxonId) === String(state.selectedSpecies.taxonId),
      )
    : observations;
  renderRedlistFilters(mapObservations);
  const visibleMapObservations = mapObservations.filter(
    (observation) =>
      !state.disabledRedlistCategories.has(observation.redlistCategory || "unknown"),
  );
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
    if (!taxa.length) {
      elements.trailDetails.append(node("p", "empty-state", t("noObservations")));
    } else {
      const list = node("div", "taxon-list");
      taxa.slice(0, MAX_TAXA_SHOWN).forEach((taxon) => {
        list.append(taxonRow(taxon, { expanded: state.mode === "species" }));
      });
      elements.trailDetails.append(list);
      if (taxa.length > MAX_TAXA_SHOWN) {
        const showAll = node(
          "button",
          "show-all-species",
          t("showAllSpecies", { count: formatNumber(taxa.length) }),
        );
        showAll.type = "button";
        showAll.addEventListener("click", () => {
          taxa.slice(MAX_TAXA_SHOWN).forEach((taxon) => list.append(taxonRow(taxon)));
          showAll.remove();
        });
        elements.trailDetails.append(showAll);
      }
    }
  }
}

function setObservationTableRows(observations) {
  state.selectedObjectObservations = [...observations];
  sortObservationTableRows();
  state.observationTablePage = 0;
  renderObservationTable();
}

function observationSortValue(observation, key) {
  if (key === "redlist") return REDLIST_PRIORITY[observation.redlistCategory] ?? 99;
  if (key === "species") {
    return observation.vernacularName || observation.scientificName || "";
  }
  if (key === "source") return observation.dataset || "";
  return observation.date || "";
}

function sortObservationTableRows() {
  const { key, direction } = state.observationTableSort;
  const multiplier = direction === "asc" ? 1 : -1;
  state.selectedObjectObservations.sort((left, right) => {
    const leftValue = observationSortValue(left, key);
    const rightValue = observationSortValue(right, key);
    const comparison = typeof leftValue === "number"
      ? leftValue - rightValue
      : String(leftValue).localeCompare(String(rightValue), state.language, {
          sensitivity: "base",
          numeric: true,
        });
    return (
      comparison * multiplier ||
      (right.date || "").localeCompare(left.date || "") ||
      String(left.id || "").localeCompare(String(right.id || ""))
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
      direction: key === "date" ? "desc" : "asc",
    };
  }
  sortObservationTableRows();
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
  updateObservationSortHeaders();
  const pageCount = Math.max(1, Math.ceil(observations.length / OBSERVATION_TABLE_PAGE_SIZE));
  state.observationTablePage = Math.min(state.observationTablePage, pageCount - 1);
  const start = state.observationTablePage * OBSERVATION_TABLE_PAGE_SIZE;
  const pageObservations = observations.slice(start, start + OBSERVATION_TABLE_PAGE_SIZE);

  elements.observationTableBody.replaceChildren();
  elements.observationTablePagination.replaceChildren();
  elements.observationTableEmpty.hidden = observations.length > 0;
  elements.observationTableScroll.hidden = observations.length === 0;

  if (!observations.length) {
    elements.observationTableSummary.textContent = t("visibleObservationCount", { count: 0 });
    return;
  }

  elements.observationTableSummary.textContent = t("visibleObservationRange", {
    from: formatNumber(start + 1),
    to: formatNumber(start + pageObservations.length),
    count: formatNumber(observations.length),
  });
  pageObservations.forEach((observation) =>
    elements.observationTableBody.append(observationTableRow(observation)),
  );

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

function observationTableRow(observation) {
  const row = document.createElement("tr");
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  const label = observation.vernacularName || observation.scientificName || t("observation");
  row.setAttribute(
    "aria-label",
    t("zoomToObservation", { species: label, date: formatDate(observation.date) }),
  );

  const speciesCell = document.createElement("td");
  speciesCell.append(node("span", "observation-species-name", label));
  if (observation.scientificName && observation.scientificName !== observation.vernacularName) {
    speciesCell.append(node("span", "scientific-name", observation.scientificName));
  }
  const dateCell = node("td", "", formatDate(observation.date));
  const categoryCell = document.createElement("td");
  const category = observation.redlistCategory || "unknown";
  const badge = node(
    "span",
    "redlist-badge",
    category === "unknown" ? t("unknownCategory") : category,
  );
  badge.dataset.category = category;
  categoryCell.append(badge);
  const sourceCell = document.createElement("td");
  if (observation.sourceUrl) {
    const link = node("a", "", observation.dataset || t("openObservation"));
    link.href = observation.sourceUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.addEventListener("click", (event) => event.stopPropagation());
    sourceCell.append(link);
  } else {
    sourceCell.textContent = observation.dataset || "—";
  }
  row.append(speciesCell, dateCell, categoryCell, sourceCell);

  const focus = () => {
    focusObservation(observation);
    showObservationPopup(
      observation,
      [observation.longitude, observation.latitude],
      observationPopup(observation),
    );
  };
  row.addEventListener("click", focus);
  row.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    focus();
  });
  return row;
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

function recentTaxonObservations(taxon) {
  const wrapper = node("div", "taxon-observation-explorer");
  wrapper.append(node("h4", "", t("recentTaxonRecords")));
  [...taxon.observations]
    .sort((left, right) => (right.date || "").localeCompare(left.date || ""))
    .slice(0, 20)
    .forEach((observation) => {
      const record = node("button", "taxon-observation-record");
      record.type = "button";
      record.append(
        node("span", "", formatDate(observation.date)),
        node("span", "", observation.dataset || "—"),
      );
      record.addEventListener("click", () => {
        if (!Number.isFinite(observation.latitude) || !Number.isFinite(observation.longitude)) return;
        focusObservation(observation);
        showObservationPopup(
          observation,
          [observation.longitude, observation.latitude],
          observationPopup(observation),
        );
      });
      wrapper.append(record);
    });
  return wrapper;
}

function taxonRow(taxon, { expanded = false } = {}) {
  const row = node("details", "taxon-row");
  row.open = expanded;
  const summary = node("summary", "taxon-summary");
  const badge = node("span", "redlist-badge", taxon.redlistCategory || t("unknownCategory"));
  badge.dataset.category = taxon.redlistCategory || "unknown";
  const name = node("div", "taxon-name", taxon.vernacularName || taxon.scientificName || "—");
  if (taxon.scientificName && taxon.scientificName !== taxon.vernacularName) {
    name.append(node("span", "scientific-name", taxon.scientificName));
  }
  const meta = node(
    "div",
    "taxon-count",
    `${t("observations", { count: formatNumber(taxon.count) })} · ${t("lastSeen", {
      date: formatDate(taxon.lastSeen),
    })}`,
  );
  summary.append(badge, name, meta);
  const label = taxon.vernacularName || taxon.scientificName || "—";
  summary.setAttribute("aria-label", t(expanded ? "collapseTaxon" : "expandTaxon", {
    species: label,
  }));
  row.addEventListener("toggle", () => {
    summary.setAttribute("aria-label", t(row.open ? "collapseTaxon" : "expandTaxon", {
      species: label,
    }));
    if (row.open) renderExpanded();
  });
  const content = node("div", "taxon-expanded");
  let expandedRendered = false;
  const renderExpanded = () => {
    if (expandedRendered) return;
    expandedRendered = true;
    content.append(
      seasonalityPanel(taxon.observations, t("weeklySeasonalityFor", { species: label })),
      recentTaxonObservations(taxon),
    );
  };
  if (expanded) renderExpanded();
  row.append(summary, content);
  return row;
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
    elements.mapObservationSummary.textContent = t("mapSelectTrail");
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

function selectTrail(trailId) {
  if (state.selectedTrailId === trailId) {
    clearTrailSelection();
    return;
  }
  state.selectedTrailId = trailId;
  renderTrailResults();
  renderSpeciesResults();
  void renderTrailDetails();
  updateMapStyles();
  fitTrail(state.catalog.trails.find((trail) => trail.id === trailId));
  if (window.innerWidth <= 800) {
    document.querySelector(".sidebar").scrollIntoView({ behavior: "smooth" });
  }
}

function clearTrailSelection() {
  if (!state.selectedTrailId) return;
  state.selectedTrailId = null;
  renderTrailResults();
  renderSpeciesResults();
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
  state.selectedSpecies = null;
  state.selectedTrailId = null;
  state.loadedSelection = null;
  state.disabledRedlistCategories.clear();
  elements.featureKind.value = "";
  elements.period.value = "year";
  elements.customDates.hidden = true;
  elements.dateFrom.value = state.customStart;
  elements.dateTo.value = state.customEnd;
  persistPeriodControls();
  elements.trailSearch.value = "";
  elements.speciesSearch.value = "";
  setMode("trail");
  renderAll();
  fitAllTrails(areaTrails());
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
  elements.locateUser.classList.toggle("is-active", state.locationTracking);
  const label = elements.locateUser.querySelector("span");
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
  elements.modeTabs.forEach((tab) => {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  elements.trailPanel.hidden = mode !== "trail";
  elements.speciesPanel.hidden = mode !== "species";
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
    if (event.key === "Escape" && !elements.welcomeDialog.hidden) closeWelcomeDialog();
  });
  elements.language.addEventListener("change", () => {
    state.language = elements.language.value;
    localStorage.setItem("vildaleder-language", state.language);
    state.loadedSelection = null;
    applyLanguage();
  });
  elements.locateUser.addEventListener("click", toggleLocationTracking);
  elements.resetFilters.addEventListener("click", resetFilters);
  elements.modeTabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
  elements.observationSortButtons.forEach((button) =>
    button.addEventListener("click", () => sortObservationTable(button.dataset.sort)),
  );
  elements.featureKind.addEventListener("change", () => {
    state.featureKind = elements.featureKind.value;
    renderAll();
    fitAllTrails(areaTrails());
  });
  elements.county.addEventListener("change", () => {
    state.county = elements.county.value;
    state.municipality = "";
    renderAll();
    fitAllTrails(areaTrails());
  });
  elements.municipality.addEventListener("change", () => {
    state.municipality = elements.municipality.value;
    renderAll();
    fitAllTrails(areaTrails());
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
    renderSpeciesResults();
    void renderTrailDetails();
  });
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
  bindEvents();
  showWelcomeDialog();
  setupMapTableResizer();
  window.addEventListener("pageshow", () => {
    requestAnimationFrame(() => syncPeriodControlsFromDom());
  });
  try {
    const [observationCatalog, featureCatalog, searchIndex, skandobs] = await Promise.all([
      loadJson("data/catalog.json", "Catalog"),
      loadJson("data/features.json", "Feature catalog"),
      loadJson("data/search-index.json", "Search index"),
      loadJson("data/skandobs.json", "Skandobs snapshot"),
      initMap({
        onTrailClick: (trailId, lngLat) => {
          selectTrail(trailId);
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
