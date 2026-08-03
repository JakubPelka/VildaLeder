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
} from "./core.js";
import { translations, translator } from "./i18n.js";
import {
  fitAllTrails,
  fitTrail,
  initMap,
  setObservations,
  setTrails,
  showObservationPopup,
} from "./map.js";

const MAX_TAXA_SHOWN = 100;

const state = {
  catalog: null,
  searchIndex: null,
  taxonById: new Map(),
  partitionCache: new Map(),
  loadedSelection: null,
  detailsRequest: 0,
  disabledRedlistCategories: new Set(),
  language: initialLanguage(),
  mode: "trail",
  county: "",
  municipality: "",
  period: "year",
  customStart: "",
  customEnd: "",
  trailQuery: "",
  speciesQuery: "",
  selectedSpecies: null,
  selectedTrailId: null,
};

const elements = {
  status: document.querySelector("#status"),
  language: document.querySelector("#language"),
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
  redlistFilters: document.querySelector("#redlist-filters"),
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
    county: state.county,
    municipality: state.municipality,
    query,
  });
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
  if (state.catalog) renderAll();
}

function populateAreaFilters() {
  const counties = [...new Set(state.catalog.trails.map((trail) => trail.county))].sort();
  const municipalities = [
    ...new Set(
      state.catalog.trails
        .filter((trail) => !state.county || trail.county === state.county)
        .map((trail) => trail.municipality),
    ),
  ].sort();
  fillSelect(elements.county, counties, t("allCounties"), state.county);
  fillSelect(elements.municipality, municipalities, t("allMunicipalities"), state.municipality);
  if (state.municipality && !municipalities.includes(state.municipality)) {
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
    const option = document.createElement("option");
    option.value = speciesLabel(species);
    elements.speciesSuggestions.append(option);
  });
}

function renderAll() {
  populateAreaFilters();
  elements.snapshotNote.textContent = t("snapshot", {
    start: formatDate(state.catalog.meta.windowStart),
    end: formatDate(state.catalog.meta.windowEnd),
    generated: formatDate(state.catalog.meta.generatedAt),
  });
  renderTrailResults();
  renderSpeciesResults();
  void renderTrailDetails();
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
    button.append(node("span", "result-title", trail.name));
    button.append(
      node(
        "span",
        "result-meta",
        `${t("length", { value: trail.lengthKm })} · ${t("observations", {
          count: formatNumber(stats.observations),
        })} · ${t("species", { count: formatNumber(stats.species) })}`,
      ),
    );
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
    species: speciesLabel(state.selectedSpecies),
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
    button.append(node("span", "result-title", `${index + 1}. ${ranking.trail.name}`));
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
      fetch(file.path).then((response) => {
        if (!response.ok) throw new Error(`Partition request failed: ${response.status}`);
        return response.json();
      }),
    );
  }
  return state.partitionCache.get(file.path);
}

function expandObservation(record) {
  const [sourceId, day, taxonId, individualCount, flags, latitude, longitude, uncertaintyMeters] =
    record;
  const taxon = state.taxonById.get(String(taxonId)) || {};
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
    sourceUrl: hasArtportalenId
      ? `https://www.artportalen.se/sighting/${sourceId}`
      : null,
    dataset: "Artportalen",
  };
}

async function loadTrailObservations(trail, range) {
  const files = observationFilesForRange(trail, range);
  const partitions = await Promise.all(files.map(loadPartition));
  return filterObservations(
    partitions.flatMap((partition) => (partition.records || []).map(expandObservation)),
    range,
  );
}

async function renderTrailDetails() {
  const request = ++state.detailsRequest;
  const trail = state.catalog.trails.find((candidate) => candidate.id === state.selectedTrailId);
  elements.trailDetails.replaceChildren();
  if (!trail) {
    setObservations([]);
    renderRedlistFilters([]);
    renderMapObservationSummary(null, 0);
    elements.trailDetails.append(node("p", "empty-state", t("selectTrail")));
    return;
  }

  const range = currentRange();
  const key = selectionKey(trail, range);
  if (state.loadedSelection?.key === key) {
    renderResolvedTrailDetails(trail, state.loadedSelection.observations);
    return;
  }

  setObservations([]);
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
  renderMapObservationSummary(trail, mappedObservationCount);
  const displayedObservations = state.mode === "species" ? mapObservations : observations;
  const taxa = groupTaxa(displayedObservations);
  const header = node("div", "details-header");
  header.append(node("h2", "", trail.name));
  header.append(
    node(
      "p",
      "details-meta",
      `${trail.municipality}, ${trail.county} · ${t("length", { value: trail.lengthKm })} · ${t(
        "observations",
        { count: formatNumber(displayedObservations.length) },
      )} · ${t("species", { count: formatNumber(taxa.length) })}`,
    ),
  );
  const links = node("div", "details-links");
  const osmLink = node("a", "", t("osmRoute"));
  osmLink.href = trail.osmUrl;
  osmLink.target = "_blank";
  osmLink.rel = "noreferrer";
  links.append(osmLink);
  header.append(links);
  elements.trailDetails.append(header);

  if (!taxa.length) {
    elements.trailDetails.append(node("p", "empty-state", t("noObservations")));
  } else {
    const list = node("div", "taxon-list");
    taxa.slice(0, MAX_TAXA_SHOWN).forEach((taxon) => list.append(taxonRow(taxon)));
    elements.trailDetails.append(list);
    if (taxa.length > MAX_TAXA_SHOWN) {
      elements.trailDetails.append(
        node(
          "p",
          "help-text",
          t("moreSpecies", { shown: MAX_TAXA_SHOWN, total: formatNumber(taxa.length) }),
        ),
      );
    }
  }
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

function taxonRow(taxon) {
  const row = node("div", "taxon-row");
  const badge = node("span", "redlist-badge", taxon.redlistCategory || t("unknownCategory"));
  badge.dataset.category = taxon.redlistCategory || "unknown";
  const name = node("div", "taxon-name", taxon.vernacularName || taxon.scientificName || "—");
  if (taxon.scientificName && taxon.scientificName !== taxon.vernacularName) {
    name.append(node("span", "scientific-name", taxon.scientificName));
  }
  const meta = node(
    "div",
    "taxon-count",
    `${formatNumber(taxon.count)} · ${formatDate(taxon.lastSeen)}`,
  );
  row.append(badge, name, meta);
  return row;
}

function renderMapObservationSummary(trail, count) {
  elements.mapObservationSummary.dataset.count = String(count);
  if (!trail) {
    elements.mapObservationSummary.textContent = t("mapSelectTrail");
    return;
  }
  if (state.mode === "species" && state.selectedSpecies) {
    elements.mapObservationSummary.textContent = t("mapSpeciesPoints", {
      count: formatNumber(count),
      species: speciesLabel(state.selectedSpecies),
      trail: trail.name,
    });
    return;
  }
  elements.mapObservationSummary.textContent = t("mapTrailPoints", {
    count: formatNumber(count),
    trail: trail.name,
  });
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

function setMode(mode) {
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
  elements.language.addEventListener("change", () => {
    state.language = elements.language.value;
    localStorage.setItem("vildaleder-language", state.language);
    applyLanguage();
  });
  elements.modeTabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
  elements.county.addEventListener("change", () => {
    state.county = elements.county.value;
    state.municipality = "";
    renderAll();
  });
  elements.municipality.addEventListener("change", () => {
    state.municipality = elements.municipality.value;
    renderAll();
  });
  elements.period.addEventListener("change", () => {
    state.period = elements.period.value;
    elements.customDates.hidden = state.period !== "custom";
    renderAll();
  });
  elements.dateFrom.addEventListener("change", () => {
    state.customStart = elements.dateFrom.value;
    renderAll();
  });
  elements.dateTo.addEventListener("change", () => {
    state.customEnd = elements.dateTo.value;
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
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${label} request failed: ${response.status}`);
  return response.json();
}

async function start() {
  applyLanguage();
  bindEvents();
  try {
    const [catalog, searchIndex] = await Promise.all([
      loadJson("data/catalog.json", "Catalog"),
      loadJson("data/search-index.json", "Search index"),
      initMap({
        onTrailClick: selectTrail,
        onObservationClick: (observation, lngLat) =>
          showObservationPopup(observation, lngLat, observationPopup(observation)),
      }),
    ]);
    state.catalog = catalog;
    state.searchIndex = searchIndex;
    state.taxonById = new Map(
      (searchIndex.taxa || []).map((taxon) => [String(taxon.taxonId), taxon]),
    );
    elements.dateFrom.min = state.catalog.meta.windowStart;
    elements.dateFrom.max = state.catalog.meta.windowEnd;
    elements.dateFrom.value = state.catalog.meta.windowStart;
    elements.dateTo.min = state.catalog.meta.windowStart;
    elements.dateTo.max = state.catalog.meta.windowEnd;
    elements.dateTo.value = state.catalog.meta.windowEnd;
    state.customStart = state.catalog.meta.windowStart;
    state.customEnd = state.catalog.meta.windowEnd;
    elements.status.classList.add("is-ready");
    populateSpeciesSuggestions();
    drawMap();
    renderAll();
  } catch (error) {
    console.error(error);
    elements.status.classList.add("is-error");
    elements.status.replaceChildren(node("span", "", t("loadError")));
  }
}

start();
