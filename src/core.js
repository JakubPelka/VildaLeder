export const REDLIST_PRIORITY = Object.freeze({
  EX: 0,
  RE: 1,
  CR: 2,
  EN: 3,
  VU: 4,
  NT: 5,
  DD: 6,
  LC: 7,
  NE: 8,
  NA: 9,
});

export function normalize(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toLocaleLowerCase();
}

export function dateOnly(value) {
  return value ? String(value).slice(0, 10) : "";
}

export function periodRange(period, customStart, customEnd, snapshotStart, snapshotEnd) {
  if (period === "custom") {
    return {
      start: customStart || snapshotStart,
      end: customEnd || snapshotEnd,
    };
  }
  const days = { day: 1, month: 30, quarter: 90, year: 365 }[period] || 365;
  const end = new Date(`${snapshotEnd}T12:00:00Z`);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  const startText = start.toISOString().slice(0, 10);
  return { start: startText < snapshotStart ? snapshotStart : startText, end: snapshotEnd };
}

export function filterObservations(observations, range) {
  return observations.filter((observation) => {
    const day = dateOnly(observation.date);
    return day && day >= range.start && day <= range.end;
  });
}

export function countDated(entries, range) {
  return (entries || []).reduce(
    (total, [day, count]) => (day >= range.start && day <= range.end ? total + count : total),
    0,
  );
}

export function lastDated(entries, range) {
  return (entries || []).reduce(
    (latest, [day, count]) =>
      count > 0 && day >= range.start && day <= range.end && day > latest ? day : latest,
    "",
  );
}

export function observationFilesForRange(trail, range) {
  return (trail.observationFiles || []).filter(
    (file) => file.start <= range.end && file.end >= range.start,
  );
}

export function filteredTrails(trails, filters) {
  const query = normalize(filters.query);
  return trails.filter((trail) => {
    if (filters.featureKind && trail.featureKind !== filters.featureKind) return false;
    if (filters.county && trail.county !== filters.county) return false;
    const municipalities = trail.municipalities || [trail.municipality].filter(Boolean);
    if (filters.municipality && !municipalities.includes(filters.municipality)) return false;
    return !query || normalize(trail.name).includes(query);
  });
}

function categoryPriority(category) {
  return REDLIST_PRIORITY[category] ?? 99;
}

export function groupTaxa(observations) {
  const grouped = new Map();
  for (const observation of observations) {
    const key = observation.taxonId ?? observation.scientificName ?? observation.vernacularName;
    if (!key) continue;
    const current = grouped.get(key) || {
      taxonId: observation.taxonId,
      vernacularName: observation.vernacularName,
      scientificName: observation.scientificName,
      organismGroup: observation.organismGroup,
      redlistCategory: observation.redlistCategory,
      count: 0,
      individuals: 0,
      lastSeen: "",
      observations: [],
    };
    current.count += 1;
    const individuals = Number(observation.individualCount);
    if (Number.isFinite(individuals)) current.individuals += individuals;
    if ((observation.date || "") > current.lastSeen) current.lastSeen = observation.date;
    if (categoryPriority(observation.redlistCategory) < categoryPriority(current.redlistCategory)) {
      current.redlistCategory = observation.redlistCategory;
    }
    current.observations.push(observation);
    grouped.set(key, current);
  }
  return [...grouped.values()].sort((left, right) => {
    return (
      categoryPriority(left.redlistCategory) - categoryPriority(right.redlistCategory) ||
      right.count - left.count ||
      (right.lastSeen || "").localeCompare(left.lastSeen || "") ||
      normalize(left.vernacularName || left.scientificName).localeCompare(
        normalize(right.vernacularName || right.scientificName),
      )
    );
  });
}

export function speciesCatalog(source) {
  if (source?.taxa) {
    return [...source.taxa].sort((left, right) =>
      normalize(left.vernacularName || left.scientificName).localeCompare(
        normalize(right.vernacularName || right.scientificName),
      ),
    );
  }
  const trails = source || [];
  const catalog = new Map();
  for (const trail of trails) {
    for (const observation of trail.observations) {
      if (!observation.taxonId) continue;
      const existing = catalog.get(observation.taxonId) || {
        taxonId: observation.taxonId,
        vernacularName: observation.vernacularName,
        scientificName: observation.scientificName,
        organismGroup: observation.organismGroup,
      };
      if (!existing.vernacularName && observation.vernacularName) {
        existing.vernacularName = observation.vernacularName;
      }
      catalog.set(observation.taxonId, existing);
    }
  }
  return [...catalog.values()].sort((left, right) =>
    normalize(left.vernacularName || left.scientificName).localeCompare(
      normalize(right.vernacularName || right.scientificName),
    ),
  );
}

export function speciesLabel(species) {
  if (species.vernacularName && species.scientificName) {
    return `${species.vernacularName} — ${species.scientificName}`;
  }
  return species.vernacularName || species.scientificName || String(species.taxonId);
}

export function resolveSpecies(catalog, query) {
  const needle = normalize(query).replace(/\s+—\s+.*/, "");
  if (!needle) return null;
  const exact = catalog.find((species) => {
    const values = [
      species.vernacularName,
      species.scientificName,
      speciesLabel(species),
      ...Object.values(species.vernacularNames || {}),
    ];
    return values.some((value) => normalize(value) === normalize(query) || normalize(value) === needle);
  });
  if (exact) return exact;
  const matches = catalog.filter((species) =>
    [
      species.vernacularName,
      species.scientificName,
      ...Object.values(species.vernacularNames || {}),
    ]
      .map(normalize)
      .some((value) => value.includes(needle)),
  );
  return matches.length === 1 ? matches[0] : null;
}

export function indexedTrailStats(searchIndex, trailId, range) {
  let species = 0;
  for (const taxon of searchIndex.taxa || []) {
    if (countDated(taxon.trails?.[trailId], range) > 0) species += 1;
  }
  return {
    observations: countDated(searchIndex.trails?.[trailId], range),
    species,
  };
}

export function rankTrailsForSpecies(trails, species, range, searchIndex) {
  if (!species) return [];
  if (searchIndex) {
    const indexedSpecies = (searchIndex.taxa || []).find(
      (candidate) => String(candidate.taxonId) === String(species.taxonId),
    );
    if (!indexedSpecies) return [];
    return trails
      .map((trail) => ({
        trail,
        count: countDated(indexedSpecies.trails?.[trail.id], range),
        lastSeen: lastDated(indexedSpecies.trails?.[trail.id], range),
      }))
      .filter((result) => result.count > 0)
      .sort(
        (left, right) =>
          right.count - left.count ||
          right.lastSeen.localeCompare(left.lastSeen) ||
          left.trail.name.localeCompare(right.trail.name),
      );
  }
  return trails
    .map((trail) => {
      const observations = filterObservations(trail.observations, range).filter(
        (observation) => observation.taxonId === species.taxonId,
      );
      return {
        trail,
        count: observations.length,
        lastSeen: observations.reduce(
          (latest, observation) => (observation.date > latest ? observation.date : latest),
          "",
        ),
        observations,
      };
    })
    .filter((result) => result.count > 0)
    .sort(
      (left, right) =>
        right.count - left.count ||
        right.lastSeen.localeCompare(left.lastSeen) ||
        left.trail.name.localeCompare(right.trail.name),
    );
}
