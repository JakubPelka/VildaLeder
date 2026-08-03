const SOURCE_CORRIDORS = "trail-corridors";
const SOURCE_TRAILS = "trails";
const SOURCE_OBSERVATIONS = "observations";
const LAYER_CORRIDORS = "trail-corridors-fill";
const LAYER_CORRIDOR_OUTLINES = "trail-corridors-outline";
const LAYER_RESERVES = "nature-reserves-fill";
const LAYER_TRAILS = "trails-line";
const LAYER_OBSERVATION_CLUSTERS = "observations-clusters";
const LAYER_OBSERVATION_CLUSTER_COUNT = "observations-cluster-count";
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
    maxZoom: 20,
    attributionControl: true,
    style: {
      version: 8,
      glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
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
    cluster: true,
    clusterRadius: 44,
    clusterMaxZoom: 20,
    maxzoom: 21,
  });

  map.addLayer({
    id: LAYER_CORRIDORS,
    type: "fill",
    source: SOURCE_CORRIDORS,
    filter: ["==", ["get", "visible"], true],
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
    filter: ["==", ["get", "visible"], true],
    paint: {
      "line-color": ["case", ["==", ["get", "selected"], true], "#d56a13", "#176b48"],
      "line-width": ["case", ["==", ["get", "selected"], true], 1.8, 1],
      "line-opacity": ["case", ["==", ["get", "visible"], false], 0.05, 0.35],
    },
  });
  map.addLayer({
    id: LAYER_RESERVES,
    type: "fill",
    source: SOURCE_TRAILS,
    filter: [
      "all",
      ["==", ["get", "featureKind"], "reserve"],
      ["==", ["get", "visible"], true],
    ],
    paint: {
      "fill-color": ["case", ["==", ["get", "selected"], true], "#d56a13", "#2f855a"],
      "fill-opacity": [
        "case",
        ["==", ["get", "visible"], false],
        0.01,
        ["==", ["get", "selected"], true],
        0.32,
        0.18,
      ],
    },
  });
  map.addLayer({
    id: LAYER_TRAILS,
    type: "line",
    source: SOURCE_TRAILS,
    filter: ["==", ["get", "visible"], true],
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
    id: LAYER_OBSERVATION_CLUSTERS,
    type: "circle",
    source: SOURCE_OBSERVATIONS,
    filter: ["has", "point_count"],
    paint: {
      "circle-color": [
        "step",
        ["get", "point_count"],
        "#176b48",
        10,
        "#0f5c48",
        50,
        "#174b66",
        250,
        "#303b70",
      ],
      "circle-radius": [
        "step",
        ["get", "point_count"],
        16,
        10,
        20,
        50,
        24,
        250,
        29,
      ],
      "circle-opacity": 0.92,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: LAYER_OBSERVATION_CLUSTER_COUNT,
    type: "symbol",
    source: SOURCE_OBSERVATIONS,
    filter: ["has", "point_count"],
    layout: {
      "text-field": ["get", "point_count_abbreviated"],
      "text-font": ["Open Sans Semibold"],
      "text-size": 12,
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": "#ffffff",
      "text-halo-color": "rgba(0, 0, 0, 0.22)",
      "text-halo-width": 0.5,
    },
  });
  map.addLayer({
    id: LAYER_OBSERVATIONS,
    type: "circle",
    source: SOURCE_OBSERVATIONS,
    filter: ["!", ["has", "point_count"]],
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
  map.on("click", async (event) => {
    const features = map.queryRenderedFeatures(event.point, {
      layers: [
        LAYER_OBSERVATION_CLUSTERS,
        LAYER_OBSERVATIONS,
        LAYER_TRAILS,
        LAYER_RESERVES,
      ],
    });
    const clusterFeature = features.find(
      (feature) => feature.layer.id === LAYER_OBSERVATION_CLUSTERS,
    );
    if (clusterFeature) {
      const clusterId = Number(clusterFeature.properties.cluster_id);
      const source = map.getSource(SOURCE_OBSERVATIONS);
      const expansionZoom = await source.getClusterExpansionZoom(clusterId);
      map.easeTo({
        center: clusterFeature.geometry.coordinates,
        zoom: Math.min(expansionZoom, map.getMaxZoom()),
      });
      return;
    }
    const observationFeature = features.find((feature) => feature.layer.id === LAYER_OBSERVATIONS);
    if (observationFeature) {
      const observation = observationByMarkerId.get(String(observationFeature.properties.markerId));
      if (observation) callbacks.onObservationClick?.(observation, event.lngLat);
      return;
    }
    const trailFeature = features.find((feature) =>
      [LAYER_TRAILS, LAYER_RESERVES].includes(feature.layer.id),
    );
    if (trailFeature) callbacks.onTrailClick?.(trailFeature.properties.trailId);
  });

  [LAYER_OBSERVATION_CLUSTERS, LAYER_OBSERVATIONS, LAYER_TRAILS, LAYER_RESERVES].forEach((layerId) => {
    map.on("mouseenter", layerId, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layerId, () => {
      map.getCanvas().style.cursor = "";
    });
  });

  map.on("moveend", notifyViewportChange);
}

export function setTrails(trails, visibleTrailIds, selectedTrailId) {
  if (!mapReady) return;
  const visible = new Set(visibleTrailIds);
  const properties = (trail) => ({
    trailId: trail.id,
    name: trail.name,
    featureKind: trail.featureKind || "trail",
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
  notifyViewportChange();
  return features.length;
}

export function getVisibleObservations() {
  if (!mapReady || !map) return [];
  const bounds = map.getBounds();
  return [...observationByMarkerId.values()].filter(
    (observation) =>
      Number.isFinite(observation.latitude) &&
      Number.isFinite(observation.longitude) &&
      bounds.contains([observation.longitude, observation.latitude]),
  );
}

function notifyViewportChange() {
  callbacks.onViewportChange?.(getVisibleObservations());
}

export function focusObservation(observation) {
  if (!mapReady || !map) return;
  if (!Number.isFinite(observation?.latitude) || !Number.isFinite(observation?.longitude)) return;
  map.easeTo({
    center: [observation.longitude, observation.latitude],
    zoom: Math.max(map.getZoom(), Math.min(17, map.getMaxZoom())),
  });
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
  const renderedClusters = renderedClusterDebug();
  return {
    loaded: mapReady,
    tilesLoaded: map ? map.areTilesLoaded() : false,
    observations: observationByMarkerId.size,
    firstObservationPoint: firstObservationPoint
      ? { x: firstObservationPoint.x, y: firstObservationPoint.y }
      : null,
    featuresAtFirstObservation: firstObservationPoint && map
      ? map.queryRenderedFeatures(firstObservationPoint, {
          layers: [LAYER_OBSERVATION_CLUSTERS, LAYER_OBSERVATIONS],
        }).length
      : 0,
    visibleObservations: getVisibleObservations().length,
    renderedClusters,
    renderedClusteredObservations: renderedClusters.reduce(
      (total, cluster) => total + cluster.count,
      0,
    ),
    renderedIndividualPoints: map
      ? map.queryRenderedFeatures({ layers: [LAYER_OBSERVATIONS] }).length
      : 0,
    center: map ? map.getCenter().toArray() : null,
    zoom: map ? map.getZoom() : null,
  };
}

function renderedClusterDebug() {
  if (!mapReady || !map) return [];
  const clusters = new Map();
  map.queryRenderedFeatures({ layers: [LAYER_OBSERVATION_CLUSTERS] }).forEach((feature) => {
    const clusterId = String(feature.properties.cluster_id);
    if (clusters.has(clusterId)) return;
    const point = map.project(feature.geometry.coordinates);
    clusters.set(clusterId, {
      id: clusterId,
      count: Number(feature.properties.point_count),
      x: point.x,
      y: point.y,
    });
  });
  return [...clusters.values()];
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
