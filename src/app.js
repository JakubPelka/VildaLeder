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
  clearUserLocation,
  fitAllTrails,
  fitTrail,
  focusObservation,
  initMap,
  setObservations,
  setTrails,
  setUserLocation,
  showObservationPopup,
} from "./map.js";

const MAX_TAXA_SHOWN = 100;
const OBSERVATION_TABLE_PAGE_SIZE = 100;
const LOCATION_REFRESH_MS = 2_000;

const state = {
  catalog: null,
  searchIndex: null,
  skandobs: null,
  skandobsRecordById: new Map(),
  taxonById: new Map(),
  partitionCache: new Map(),
  loadedSelection: null,
  detailsRequest: 0,
  disabledRedlistCategories: new Set(),
  visibleTableObservations: [],
  observationTablePage: 0,
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
  redlistFilters: document.querySelector("#redlist-filters"),
  observationTablePanel: document.querySelector("#observation-table-panel"),
  observationTableSummary: document.querySelector("#observation-table-summary"),
  observationTablePagination: document.querySelector("#observation-table-pagination"),
  observationTableScroll: document.querySelector(".observation-table-scroll"),
  observationTableBody: document.querySelector("#observation-table-body"),
  observationTableEmpty: document.querySelector("#observation-table-empty"),
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
  updateLocationButton();
  if (state.catalog) renderAll();
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
    const dimension = trail.featureKind === "reserve"
      ? t("areaHectares", { value: formatNumber(Math.round(trail.areaHa || 0)) })
      : t("length", { value: trail.lengthKm });
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
  if (!trail.observationCoverage) {
    setObservations([]);
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
  const heading = node("h2", "", trail.name);
  heading.prepend(featureKindBadge(trail));
  header.append(heading);
  const municipalities = (trail.municipalities || [trail.municipality].filter(Boolean)).join(", ");
  const dimension = trail.featureKind === "reserve"
    ? t("areaHectares", { value: formatNumber(Math.round(trail.areaHa || 0)) })
    : t("length", { value: trail.lengthKm });
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
  const osmLink = node("a", "", t(trail.featureKind === "reserve" ? "reserveSource" : "osmRoute"));
  osmLink.href = trail.sourceUrl || trail.osmUrl;
  osmLink.target = "_blank";
  osmLink.rel = "noreferrer";
  links.append(osmLink);
  header.append(links);
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
}

function handleViewportChange(observations) {
  state.visibleTableObservations = [...observations].sort(
    (left, right) =>
      (right.date || "").localeCompare(left.date || "") ||
      (REDLIST_PRIORITY[left.redlistCategory] ?? 99) -
        (REDLIST_PRIORITY[right.redlistCategory] ?? 99) ||
      String(left.vernacularName || left.scientificName || "").localeCompare(
        String(right.vernacularName || right.scientificName || ""),
      ),
  );
  state.observationTablePage = 0;
  renderObservationTable();
}

function renderObservationTable() {
  const hasSelection = Boolean(state.catalog && state.selectedTrailId);
  elements.observationTablePanel.hidden = !hasSelection;
  if (!hasSelection) return;

  const observations = state.visibleTableObservations;
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
    t(feature.featureKind === "reserve" ? "natureReserve" : "trail"),
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
    state.loadedSelection = null;
    applyLanguage();
  });
  elements.locateUser.addEventListener("click", toggleLocationTracking);
  elements.resetFilters.addEventListener("click", resetFilters);
  elements.modeTabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
  elements.featureKind.addEventListener("change", () => {
    state.featureKind = elements.featureKind.value;
    renderAll();
    fitAllTrails(areaTrails());
  });
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
    activateCustomPeriod();
    state.customStart = elements.dateFrom.value;
    renderAll();
  });
  elements.dateTo.addEventListener("change", () => {
    activateCustomPeriod();
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
  bindEvents();
  try {
    const [observationCatalog, featureCatalog, searchIndex, skandobs] = await Promise.all([
      loadJson("data/catalog.json", "Catalog"),
      loadJson("data/features.json", "Feature catalog"),
      loadJson("data/search-index.json", "Search index"),
      loadJson("data/skandobs.json", "Skandobs snapshot"),
      initMap({
        onTrailClick: selectTrail,
        onObservationClick: (observation, lngLat) =>
          showObservationPopup(observation, lngLat, observationPopup(observation)),
        onViewportChange: handleViewportChange,
      }),
    ]);
    state.skandobs = skandobs;
    state.skandobsRecordById = new Map(
      (skandobs.records || []).map((record) => [record.id, record]),
    );
    state.catalog = mergedCatalog(observationCatalog, featureCatalog);
    state.searchIndex = mergedSearchIndex(searchIndex, skandobs);
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
