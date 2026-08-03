import {
  dateOnly,
  filterObservations,
  filteredTrails,
  groupTaxa,
  periodRange,
  rankTrailsForSpecies,
  resolveSpecies,
  speciesCatalog,
  speciesLabel,
} from "./core.js";
import { translations, translator } from "./i18n.js";

const MAX_TAXA_SHOWN = 100;
const MAX_MAP_OBSERVATIONS = 700;

const state = {
  catalog: null,
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
  modeTabs: [...document.querySelectorAll(".mode-tab")],
};

const map = L.map("map", { zoomControl: true }).setView([56.68, 12.95], 10);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const corridorLayer = L.geoJSON(null).addTo(map);
const trailLayer = L.geoJSON(null).addTo(map);
const observationLayer = L.layerGroup().addTo(map);
const trailLayers = new Map();

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
  speciesCatalog(state.catalog.trails).forEach((species) => {
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
  renderTrailDetails();
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
    const observations = filterObservations(trail.observations, range);
    const taxa = groupTaxa(observations);
    const button = node("button", "result-card");
    button.type = "button";
    button.classList.toggle("is-selected", trail.id === state.selectedTrailId);
    button.append(node("span", "result-title", trail.name));
    button.append(
      node(
        "span",
        "result-meta",
        `${t("length", { value: trail.lengthKm })} · ${t("observations", {
          count: formatNumber(observations.length),
        })} · ${t("species", { count: formatNumber(taxa.length) })}`,
      ),
    );
    button.addEventListener("click", () => selectTrail(trail.id));
    elements.trailResults.append(button);
  });
}

function renderSpeciesResults() {
  elements.speciesResults.replaceChildren();
  elements.speciesSummary.replaceChildren();
  if (!state.speciesQuery.trim()) {
    elements.speciesResults.append(node("p", "empty-state", t("noSpecies")));
    state.selectedSpecies = null;
    return;
  }
  const allSpecies = speciesCatalog(state.catalog.trails);
  state.selectedSpecies = resolveSpecies(allSpecies, state.speciesQuery);
  if (!state.selectedSpecies) {
    elements.speciesResults.append(node("p", "empty-state", t("speciesNotFound")));
    return;
  }
  elements.speciesSummary.textContent = t("rankedFor", {
    species: speciesLabel(state.selectedSpecies),
  });
  const rankings = rankTrailsForSpecies(areaTrails(), state.selectedSpecies, currentRange());
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

function renderTrailDetails() {
  elements.trailDetails.replaceChildren();
  const trail = state.catalog.trails.find((candidate) => candidate.id === state.selectedTrailId);
  observationLayer.clearLayers();
  if (!trail) {
    elements.trailDetails.append(node("p", "empty-state", t("selectTrail")));
    return;
  }
  const observations = filterObservations(trail.observations, currentRange());
  const taxa = groupTaxa(observations);
  const header = node("div", "details-header");
  header.append(node("h2", "", trail.name));
  header.append(
    node(
      "p",
      "details-meta",
      `${trail.municipality}, ${trail.county} · ${t("length", { value: trail.lengthKm })} · ${t(
        "observations",
        { count: formatNumber(observations.length) },
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
  if (trail.observationLimitReached) {
    header.append(node("p", "help-text", t("partialData")));
  }
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
  addObservationMarkers(observations);
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

function addObservationMarkers(observations) {
  const selected = state.mode === "species" && state.selectedSpecies
    ? observations.filter((observation) => observation.taxonId === state.selectedSpecies.taxonId)
    : observations;
  selected.slice(0, MAX_MAP_OBSERVATIONS).forEach((observation) => {
    if (!Number.isFinite(observation.latitude) || !Number.isFinite(observation.longitude)) return;
    const marker = L.circleMarker([observation.latitude, observation.longitude], {
      radius: 4,
      color: "#fff",
      weight: 1.5,
      fillColor: "#e2842c",
      fillOpacity: 0.86,
    });
    marker.bindPopup(() => observationPopup(observation));
    marker.addTo(observationLayer);
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
  corridorLayer.clearLayers();
  trailLayer.clearLayers();
  trailLayers.clear();
  const bounds = [];
  state.catalog.trails.forEach((trail) => {
    const corridor = L.geoJSON(trail.corridor, {
      style: { color: "#176b48", weight: 1, fillOpacity: 0.08, opacity: 0.35 },
      interactive: false,
    });
    corridor.eachLayer((layer) => corridorLayer.addLayer(layer));
    const line = L.geoJSON(trail.geometry, {
      style: { color: "#176b48", weight: 4, opacity: 0.82 },
    });
    line.eachLayer((layer) => {
      layer.on("click", () => selectTrail(trail.id));
      layer.bindTooltip(trail.name, { sticky: true });
      trailLayer.addLayer(layer);
      bounds.push(layer.getBounds());
    });
    trailLayers.set(trail.id, line);
  });
  if (bounds.length) {
    const combined = bounds.reduce((result, current) => result.extend(current), bounds[0]);
    map.fitBounds(combined, { padding: [20, 20] });
  }
}

function updateMapStyles() {
  if (!state.catalog) return;
  const visibleIds = new Set(areaTrails().map((trail) => trail.id));
  trailLayer.eachLayer((layer) => {
    const trail = state.catalog.trails.find((candidate) => layer.getTooltip()?.getContent() === candidate.name);
    if (!trail) return;
    const isSelected = trail.id === state.selectedTrailId;
    layer.setStyle({
      color: isSelected ? "#d56a13" : "#176b48",
      weight: isSelected ? 7 : 4,
      opacity: visibleIds.has(trail.id) ? (isSelected ? 1 : 0.82) : 0.12,
    });
  });
}

function selectTrail(trailId) {
  state.selectedTrailId = trailId;
  renderTrailResults();
  renderSpeciesResults();
  renderTrailDetails();
  updateMapStyles();
  const layer = [...trailLayer.getLayers()].find((candidate) => {
    const trail = state.catalog.trails.find((item) => item.id === trailId);
    return candidate.getTooltip()?.getContent() === trail?.name;
  });
  if (layer) map.fitBounds(layer.getBounds(), { padding: [35, 35], maxZoom: 14 });
  if (window.innerWidth <= 800) document.querySelector(".sidebar").scrollIntoView({ behavior: "smooth" });
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
  renderTrailDetails();
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
    renderTrailDetails();
  });
}

async function loadCatalog() {
  const response = await fetch("data/catalog.json");
  if (!response.ok) throw new Error(`Catalog request failed: ${response.status}`);
  return response.json();
}

async function start() {
  applyLanguage();
  bindEvents();
  try {
    state.catalog = await loadCatalog();
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

