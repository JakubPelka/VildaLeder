const SOURCE_CORRIDORS = "trail-corridors";
const SOURCE_TRAILS = "trails";
const SOURCE_OBSERVATIONS = "observations";
const LAYER_CORRIDORS = "trail-corridors-fill";
const LAYER_CORRIDOR_OUTLINES = "trail-corridors-outline";
const LAYER_TRAILS = "trails-line";
const LAYER_OBSERVATIONS = "observations-circle";

export const REDLIST_COLORS = Object.freeze({
  EX: "#3f0b0b",
  RE: "#681717",
  CR: "#a71919",
  EN: "#dc3528",
  VU: "#f07c22",
  NT: "#f2c94c",
  DD: "#7856a8",
  LC: "#3a9d5d",
  NE: "#718096",
  NA: "#a0aec0",
  unknown: "#52606d",
});

let map;
let mapReady = false;
let resizeObserver;
let resizeTimer;
let activePopup;
let observationByMarkerId = new Map();
let callbacks = {};

const emptyFeatureCollection = () => ({ type: "FeatureCollection", features: [] });

export function initMap(options = {}) {
  callbacks = options;
  map = new maplibregl.Map({
    container: "map",
    bearing: 0,
    pitch: 0,
    maxPitch: 0,
    dragRotate: false,
    pitchWithRotate: false,
    touchPitch: false,
    center: [12.95, 56.68],
    zoom: 10,
    attributionControl: true,
    style: {
      version: 8,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap contributors",
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
  });

  disableRotation();
  map.addControl(
    new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }),
    "top-left",
  );
  setupResizeHandling();

  return new Promise((resolve) => {
    map.on("load", () => {
      addDataLayers();
      bindMapInteractions();
      mapReady = true;
      window.__vildaMapDebug = getMapDebugState;
      forceSeveralMapRefreshes();
      resolve(map);
    });
  });
}

function disableRotation() {
  map.dragRotate?.disable?.();
  map.touchPitch?.disable?.();
  map.touchZoomRotate?.disableRotation?.();
  map.setBearing(0);
  map.setPitch(0);
  map.on("rotate", () => map.getBearing() && map.setBearing(0));
  map.on("pitch", () => map.getPitch() && map.setPitch(0));
}

function addDataLayers() {
  map.addSource(SOURCE_CORRIDORS, { type: "geojson", data: emptyFeatureCollection() });
  map.addSource(SOURCE_TRAILS, { type: "geojson", data: emptyFeatureCollection() });
  map.addSource(SOURCE_OBSERVATIONS, {
    type: "geojson",
    data: emptyFeatureCollection(),
    promoteId: "markerId",
  });

  map.addLayer({
    id: LAYER_CORRIDORS,
    type: "fill",
    source: SOURCE_CORRIDORS,
    paint: {
      "fill-color": ["case", ["==", ["get", "selected"], true], "#d56a13", "#176b48"],
      "fill-opacity": [
        "case",
        ["==", ["get", "visible"], false],
        0.01,
        ["==", ["get", "selected"], true],
        0.15,
        0.07,
      ],
    },
  });
  map.addLayer({
    id: LAYER_CORRIDOR_OUTLINES,
    type: "line",
    source: SOURCE_CORRIDORS,
    paint: {
      "line-color": ["case", ["==", ["get", "selected"], true], "#d56a13", "#176b48"],
      "line-width": ["case", ["==", ["get", "selected"], true], 1.8, 1],
      "line-opacity": ["case", ["==", ["get", "visible"], false], 0.05, 0.35],
    },
  });
  map.addLayer({
    id: LAYER_TRAILS,
    type: "line",
    source: SOURCE_TRAILS,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["case", ["==", ["get", "selected"], true], "#d56a13", "#176b48"],
      "line-width": ["case", ["==", ["get", "selected"], true], 7, 4],
      "line-opacity": [
        "case",
        ["==", ["get", "visible"], false],
        0.1,
        ["==", ["get", "selected"], true],
        1,
        0.82,
      ],
    },
  });
  map.addLayer({
    id: LAYER_OBSERVATIONS,
    type: "circle",
    source: SOURCE_OBSERVATIONS,
    paint: {
      "circle-color": redlistColorExpression(),
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 2.5, 11, 4.5, 15, 7],
      "circle-opacity": 0.88,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 7, 0.5, 13, 1.4],
    },
  });
}

function redlistColorExpression() {
  const expression = ["match", ["get", "redlistCategory"]];
  Object.entries(REDLIST_COLORS)
    .filter(([category]) => category !== "unknown")
    .forEach(([category, color]) => expression.push(category, color));
  expression.push(REDLIST_COLORS.unknown);
  return expression;
}

function bindMapInteractions() {
  map.on("click", (event) => {
    const features = map.queryRenderedFeatures(event.point, {
      layers: [LAYER_OBSERVATIONS, LAYER_TRAILS],
    });
    const observationFeature = features.find((feature) => feature.layer.id === LAYER_OBSERVATIONS);
    if (observationFeature) {
      const observation = observationByMarkerId.get(String(observationFeature.properties.markerId));
      if (observation) callbacks.onObservationClick?.(observation, event.lngLat);
      return;
    }
    const trailFeature = features.find((feature) => feature.layer.id === LAYER_TRAILS);
    if (trailFeature) callbacks.onTrailClick?.(trailFeature.properties.trailId);
  });

  [LAYER_OBSERVATIONS, LAYER_TRAILS].forEach((layerId) => {
    map.on("mouseenter", layerId, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layerId, () => {
      map.getCanvas().style.cursor = "";
    });
  });
}

export function setTrails(trails, visibleTrailIds, selectedTrailId) {
  if (!mapReady) return;
  const visible = new Set(visibleTrailIds);
  const properties = (trail) => ({
    trailId: trail.id,
    name: trail.name,
    selected: trail.id === selectedTrailId,
    visible: visible.has(trail.id),
  });
  map.getSource(SOURCE_CORRIDORS).setData({
    type: "FeatureCollection",
    features: trails.map((trail) => ({
      type: "Feature",
      properties: properties(trail),
      geometry: trail.corridor,
    })),
  });
  map.getSource(SOURCE_TRAILS).setData({
    type: "FeatureCollection",
    features: trails.map((trail) => ({
      type: "Feature",
      properties: properties(trail),
      geometry: trail.geometry,
    })),
  });
}

export function setObservations(observations) {
  if (!mapReady) return;
  observationByMarkerId = new Map();
  const features = [];
  observations.forEach((observation, index) => {
    if (!Number.isFinite(observation.latitude) || !Number.isFinite(observation.longitude)) return;
    const markerId = String(index);
    observationByMarkerId.set(markerId, observation);
    features.push({
      type: "Feature",
      id: markerId,
      properties: {
        markerId,
        redlistCategory: observation.redlistCategory || "unknown",
        taxonId: observation.taxonId,
      },
      geometry: {
        type: "Point",
        coordinates: [observation.longitude, observation.latitude],
      },
    });
  });
  map.getSource(SOURCE_OBSERVATIONS).setData({ type: "FeatureCollection", features });
  return features.length;
}

export function showObservationPopup(observation, lngLat, content) {
  activePopup?.remove();
  activePopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: "300px" })
    .setLngLat(lngLat)
    .setDOMContent(content)
    .addTo(map);
}

export function fitAllTrails(trails) {
  fitGeometries(trails.map((trail) => trail.geometry), { padding: 30, maxZoom: 12 });
}

export function fitTrail(trail) {
  if (!trail) return;
  fitGeometries([trail.geometry], { padding: 45, maxZoom: 14 });
}

function fitGeometries(geometries, options) {
  const bounds = new maplibregl.LngLatBounds();
  geometries.forEach((geometry) => extendBounds(bounds, geometry.coordinates));
  if (!bounds.isEmpty()) map.fitBounds(bounds, options);
  forceSeveralMapRefreshes();
}

function extendBounds(bounds, coordinates) {
  if (!Array.isArray(coordinates)) return;
  if (
    coordinates.length >= 2 &&
    Number.isFinite(coordinates[0]) &&
    Number.isFinite(coordinates[1])
  ) {
    bounds.extend([coordinates[0], coordinates[1]]);
    return;
  }
  coordinates.forEach((coordinate) => extendBounds(bounds, coordinate));
}

function setupResizeHandling() {
  const mapElement = document.querySelector("#map");
  if (window.ResizeObserver && mapElement) {
    resizeObserver = new ResizeObserver(() => debouncedRefreshMapSize());
    resizeObserver.observe(mapElement);
  }
  window.addEventListener("load", forceSeveralMapRefreshes);
  window.addEventListener("resize", debouncedRefreshMapSize);
  window.addEventListener("orientationchange", forceSeveralMapRefreshes);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) forceSeveralMapRefreshes();
  });
}

function debouncedRefreshMapSize() {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(refreshMapSize, 80);
}

export function forceSeveralMapRefreshes() {
  [0, 80, 180, 400, 900].forEach((delay) => window.setTimeout(refreshMapSize, delay));
}

export function refreshMapSize() {
  if (!map) return;
  requestAnimationFrame(() => map.resize());
}

export function getMapDebugState() {
  const firstObservationPoint = findClickableObservationPoint();
  return {
    loaded: mapReady,
    tilesLoaded: map ? map.areTilesLoaded() : false,
    observations: observationByMarkerId.size,
    firstObservationPoint: firstObservationPoint
      ? { x: firstObservationPoint.x, y: firstObservationPoint.y }
      : null,
    featuresAtFirstObservation: firstObservationPoint && map
      ? map.queryRenderedFeatures(firstObservationPoint, { layers: [LAYER_OBSERVATIONS] }).length
      : 0,
    center: map ? map.getCenter().toArray() : null,
    zoom: map ? map.getZoom() : null,
  };
}

function findClickableObservationPoint() {
  if (!map) return null;
  const canvas = map.getCanvas();
  const rect = canvas.getBoundingClientRect();
  for (const observation of observationByMarkerId.values()) {
    const point = map.project([observation.longitude, observation.latitude]);
    if (point.x < 15 || point.y < 15 || point.x > rect.width - 15 || point.y > rect.height - 15) {
      continue;
    }
    if (document.elementFromPoint(rect.left + point.x, rect.top + point.y) === canvas) {
      return point;
    }
  }
  return null;
}
