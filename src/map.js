const SOURCE_CORRIDORS = "trail-corridors";
const SOURCE_TRAILS = "trails";
const SOURCE_OBSERVATIONS = "observations";
const SOURCE_USER_LOCATION = "user-location";
const SOURCE_SEARCHED_PLACE = "searched-place";
const LAYER_CORRIDORS = "trail-corridors-fill";
const LAYER_CORRIDOR_OUTLINES = "trail-corridors-outline";
const LAYER_RESERVES = "nature-reserves-fill";
const LAYER_NATIONAL_PARKS = "national-parks-fill";
const LAYER_URBAN_GREEN = "urban-green-fill";
const LAYER_TRAILS = "trails-line";
const LAYER_DESTINATIONS = "nature-destinations-circle";
const LAYER_OBSERVATION_CLUSTERS = "observations-clusters";
const LAYER_OBSERVATION_CLUSTER_COUNT = "observations-cluster-count";
const LAYER_OBSERVATIONS = "observations-circle";
const LAYER_USER_ACCURACY = "user-location-accuracy";
const LAYER_USER_LOCATION = "user-location-point";
const LAYER_SEARCHED_PLACE = "searched-place-point";

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
let hoverPopup;
let searchedPlacePopup;
let observationByMarkerId = new Map();
let callbacks = {};
let pendingUserLocation = null;

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
      if (pendingUserLocation) {
        const { position, focus } = pendingUserLocation;
        pendingUserLocation = null;
        setUserLocation(position, focus);
      }
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
  map.addSource(SOURCE_USER_LOCATION, { type: "geojson", data: emptyFeatureCollection() });
  map.addSource(SOURCE_SEARCHED_PLACE, { type: "geojson", data: emptyFeatureCollection() });
  map.addSource(SOURCE_CORRIDORS, { type: "geojson", data: emptyFeatureCollection(), promoteId: "id" });
  map.addSource(SOURCE_TRAILS, { type: "geojson", data: emptyFeatureCollection(), promoteId: "id" });
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
    id: LAYER_USER_ACCURACY,
    type: "fill",
    source: SOURCE_USER_LOCATION,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: {
      "fill-color": "#2475d0",
      "fill-opacity": 0.14,
      "fill-outline-color": "#2475d0",
    },
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
      "line-color": ["case", ["==", ["get", "selected"], true], "#d56a13", ["boolean", ["feature-state", "hover"], false], "#ff8c00", "#176b48"],
      "line-width": ["case", ["==", ["get", "selected"], true], 3, ["boolean", ["feature-state", "hover"], false], 2.5, 1],
      "line-opacity": ["case", ["==", ["get", "visible"], false], 0.05, ["boolean", ["feature-state", "hover"], false], 0.6, 0.35],
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
      "fill-color": ["case", ["==", ["get", "selected"], true], "#d56a13", ["boolean", ["feature-state", "hover"], false], "#ff8c00", "#2f855a"],
      "fill-opacity": [
        "case",
        ["==", ["get", "visible"], false],
        0.01,
        ["==", ["get", "selected"], true],
        0.45,
        ["boolean", ["feature-state", "hover"], false],
        0.40,
        0.18,
      ],
    },
  });
  map.addLayer({
    id: LAYER_NATIONAL_PARKS,
    type: "fill",
    source: SOURCE_TRAILS,
    filter: [
      "all",
      ["==", ["get", "featureKind"], "national_park"],
      ["==", ["get", "visible"], true],
    ],
    paint: {
      "fill-color": ["case", ["==", ["get", "selected"], true], "#d56a13", ["boolean", ["feature-state", "hover"], false], "#ff8c00", "#287552"],
      "fill-opacity": ["case", ["==", ["get", "selected"], true], 0.5, ["boolean", ["feature-state", "hover"], false], 0.45, 0.24],
    },
  });

  map.addLayer({
    id: LAYER_URBAN_GREEN,
    type: "fill",
    source: SOURCE_TRAILS,
    filter: [
      "all",
      ["==", ["get", "featureKind"], "urban_green"],
      ["==", ["get", "visible"], true],
    ],
    paint: {
      "fill-color": ["case", ["==", ["get", "selected"], true], "#d56a13", ["boolean", ["feature-state", "hover"], false], "#ff8c00", "#7cb342"],
      "fill-opacity": [
        "case",
        ["==", ["get", "visible"], false],
        0.01,
        ["==", ["get", "selected"], true],
        0.45,
        ["boolean", ["feature-state", "hover"], false],
        0.40,
        0.18,
      ],
    },
  });

  map.addLayer({
    id: LAYER_TRAILS,
    type: "line",
    source: SOURCE_TRAILS,
    filter: [
      "all",
      ["==", ["get", "featureKind"], "trail"],
      ["==", ["get", "visible"], true],
    ],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["case", ["==", ["get", "selected"], true], "#d56a13", ["boolean", ["feature-state", "hover"], false], "#ff8c00", "#176b48"],
      "line-width": ["case", ["==", ["get", "selected"], true], 8.5, ["boolean", ["feature-state", "hover"], false], 7.5, 4],
      "line-opacity": [
        "case",
        ["==", ["get", "visible"], false],
        0.1,
        ["==", ["get", "selected"], true],
        1,
        ["boolean", ["feature-state", "hover"], false],
        1,
        0.82,
      ],
    },
  });
  map.addLayer({
    id: LAYER_DESTINATIONS,
    type: "circle",
    source: SOURCE_TRAILS,
    filter: [
      "all",
      ["in", ["get", "featureKind"], ["literal", [
        "bird_hide",
        "observation_tower",
        "observation_site",
      ]]],
      ["==", ["get", "visible"], true],
    ],
    paint: {
      "circle-color": [
        "match",
        ["get", "featureKind"],
        "bird_hide", "#65558f",
        "observation_tower", "#176b8c",
        "#ad6b19",
      ],
      "circle-radius": ["case", ["==", ["get", "selected"], true], 10, ["boolean", ["feature-state", "hover"], false], 9, 6],
      "circle-stroke-color": ["case", ["==", ["get", "selected"], true], "#d56a13", ["boolean", ["feature-state", "hover"], false], "#ff8c00", "#ffffff"],
      "circle-stroke-width": ["case", ["==", ["get", "selected"], true], 3.5, ["boolean", ["feature-state", "hover"], false], 3, 2],
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
  map.addLayer({
    id: LAYER_SEARCHED_PLACE,
    type: "circle",
    source: SOURCE_SEARCHED_PLACE,
    paint: {
      "circle-color": "#e2842c",
      "circle-radius": 8,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 3,
    },
  });
  map.addLayer({
    id: LAYER_USER_LOCATION,
    type: "circle",
    source: SOURCE_USER_LOCATION,
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-color": "#2475d0",
      "circle-radius": 7,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 3,
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

export function setObservationColors(mapping) {
  if (!mapReady || !map.getLayer(LAYER_OBSERVATIONS)) return;
  if (!mapping) {
    map.setPaintProperty(LAYER_OBSERVATIONS, "circle-color", redlistColorExpression());
    return;
  }
  const expression = ["match", ["get", "taxonId"]];
  Object.entries(mapping).forEach(([taxonId, color]) => {
    expression.push(Number(taxonId), color);
  });
  expression.push(REDLIST_COLORS.unknown); // default
  map.setPaintProperty(LAYER_OBSERVATIONS, "circle-color", expression);
}

function bindMapInteractions() {
  map.on("click", async (event) => {
    const features = map.queryRenderedFeatures(event.point, {
      layers: [
        LAYER_OBSERVATION_CLUSTERS,
        LAYER_OBSERVATIONS,
        LAYER_TRAILS,
        LAYER_RESERVES,
        LAYER_NATIONAL_PARKS,
        LAYER_URBAN_GREEN,
        LAYER_DESTINATIONS,
        LAYER_SEARCHED_PLACE,
      ],
    });
    const searchedPlaceFeature = features.find(
      (feature) => feature.layer.id === LAYER_SEARCHED_PLACE,
    );
    if (searchedPlaceFeature) {
      showSearchedPlacePopup(
        searchedPlaceFeature.geometry.coordinates,
        searchedPlaceFeature.properties.name,
      );
      return;
    }
    const clusterFeature = features.find(
      (feature) => feature.layer.id === LAYER_OBSERVATION_CLUSTERS,
    );
    if (clusterFeature) {
      const clusterId = Number(clusterFeature.properties.cluster_id);
      const source = map.getSource(SOURCE_OBSERVATIONS);
      const leaves = await source.getClusterLeaves(clusterId, 50, 0);
      const observations = leaves.map((f) => observationByMarkerId.get(String(f.properties.markerId))).filter(Boolean);
      if (observations.length > 0) callbacks.onClusterClick?.(observations, event.lngLat);
      return;
    }
    const observationFeature = features.find((feature) => feature.layer.id === LAYER_OBSERVATIONS);
    if (observationFeature) {
      const observation = observationByMarkerId.get(String(observationFeature.properties.markerId));
      if (observation) callbacks.onObservationClick?.(observation, event.lngLat);
      return;
    }
    const trailFeature = features.find((feature) =>
      [LAYER_TRAILS, LAYER_RESERVES, LAYER_NATIONAL_PARKS, LAYER_URBAN_GREEN, LAYER_DESTINATIONS]
        .includes(feature.layer.id),
    );
    if (trailFeature) {
      callbacks.onTrailClick?.(trailFeature.properties.trailId, event.lngLat);
    }
  });

  [
    LAYER_OBSERVATION_CLUSTERS,
    LAYER_OBSERVATIONS,
    LAYER_TRAILS,
    LAYER_RESERVES,
    LAYER_NATIONAL_PARKS,
    LAYER_URBAN_GREEN,
    LAYER_DESTINATIONS,
    LAYER_SEARCHED_PLACE,
  ].forEach((layerId) => {
    map.on("mouseenter", layerId, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layerId, () => {
      map.getCanvas().style.cursor = "";
    });
  });
  [LAYER_TRAILS, LAYER_RESERVES, LAYER_NATIONAL_PARKS, LAYER_URBAN_GREEN, LAYER_DESTINATIONS].forEach((layerId) => {
    map.on("mouseenter", layerId, showFeatureTooltip);
    map.on("mousemove", layerId, moveFeatureTooltip);
    map.on("mouseleave", layerId, hideFeatureTooltip);
  });

  map.on("moveend", notifyViewportChange);
}

function showFeatureTooltip(event) {
  const name = event.features?.[0]?.properties?.name;
  if (!name) return;
  hoverPopup?.remove();
  hoverPopup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    className: "feature-name-tooltip",
    offset: 10,
  })
    .setLngLat(event.lngLat)
    .setText(name)
    .addTo(map);
}

function moveFeatureTooltip(event) {
  hoverPopup?.setLngLat(event.lngLat);
}

function hideFeatureTooltip() {
  hoverPopup?.remove();
  hoverPopup = null;
}

export function setTrails(trails, visibleTrailIds, selectedTrailId) {
  if (!mapReady) return;
  const visible = new Set(visibleTrailIds);
  const properties = (trail) => ({
    id: trail.id,
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
      id: trail.id,
      properties: properties(trail),
      geometry: trail.corridor,
    })),
  });
  map.getSource(SOURCE_TRAILS).setData({
    type: "FeatureCollection",
    features: trails.map((trail) => ({
      type: "Feature",
      id: trail.id,
      properties: properties(trail),
      geometry: trail.geometry,
    })),
  });
}

let hoveredTrailId = null;

export function setHoveredTrail(trailId) {
  if (!mapReady) return;
  if (hoveredTrailId) {
    map.setFeatureState({ source: SOURCE_TRAILS, id: hoveredTrailId }, { hover: false });
    map.setFeatureState({ source: SOURCE_CORRIDORS, id: hoveredTrailId }, { hover: false });
  }
  hoveredTrailId = trailId;
  if (hoveredTrailId) {
    map.setFeatureState({ source: SOURCE_TRAILS, id: hoveredTrailId }, { hover: true });
    map.setFeatureState({ source: SOURCE_CORRIDORS, id: hoveredTrailId }, { hover: true });
  }
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

function showSearchedPlacePopup(coordinates, name) {
  searchedPlacePopup?.remove();
  searchedPlacePopup = new maplibregl.Popup({ closeButton: true, closeOnClick: false })
    .setLngLat(coordinates)
    .setText(name)
    .addTo(map);
}

export function showSearchedPlace(result) {
  if (!mapReady || !map) return;
  const longitude = Number(result?.longitude);
  const latitude = Number(result?.latitude);
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return;
  const name = String(result?.name || "");
  map.getSource(SOURCE_SEARCHED_PLACE).setData({
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      properties: { name },
      geometry: { type: "Point", coordinates: [longitude, latitude] },
    }],
  });
  const boundingBox = (result.boundingBox || []).map(Number);
  if (boundingBox.length === 4 && boundingBox.every(Number.isFinite)) {
    const [south, north, west, east] = boundingBox;
    map.fitBounds([[west, south], [east, north]], { padding: 55, maxZoom: 13 });
  } else {
    map.easeTo({ center: [longitude, latitude], zoom: 13 });
  }
  showSearchedPlacePopup([longitude, latitude], name);
  forceSeveralMapRefreshes();
}

export function clearSearchedPlace() {
  searchedPlacePopup?.remove();
  searchedPlacePopup = null;
  if (!mapReady || !map) return;
  map.getSource(SOURCE_SEARCHED_PLACE).setData(emptyFeatureCollection());
}

function accuracyPolygon(longitude, latitude, accuracyMeters) {
  const radius = Math.max(1, Number(accuracyMeters) || 1);
  const latitudeScale = radius / 110_540;
  const longitudeScale = radius / (111_320 * Math.max(0.2, Math.cos((latitude * Math.PI) / 180)));
  const coordinates = [];
  for (let step = 0; step <= 48; step += 1) {
    const angle = (step / 48) * Math.PI * 2;
    coordinates.push([
      longitude + Math.cos(angle) * longitudeScale,
      latitude + Math.sin(angle) * latitudeScale,
    ]);
  }
  return { type: "Polygon", coordinates: [coordinates] };
}

export function setUserLocation(position, focus = false) {
  if (!mapReady || !map) {
    pendingUserLocation = { position, focus };
    return;
  }
  const longitude = Number(position?.longitude);
  const latitude = Number(position?.latitude);
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return;
  const point = {
    type: "Feature",
    properties: { kind: "position" },
    geometry: { type: "Point", coordinates: [longitude, latitude] },
  };
  const accuracy = {
    type: "Feature",
    properties: { kind: "accuracy", accuracyMeters: Number(position.accuracy) || 0 },
    geometry: accuracyPolygon(longitude, latitude, position.accuracy),
  };
  map.getSource(SOURCE_USER_LOCATION).setData({
    type: "FeatureCollection",
    features: [accuracy, point],
  });
  if (focus) {
    map.easeTo({ center: [longitude, latitude], zoom: Math.max(map.getZoom(), 15) });
  }
}

export function clearUserLocation() {
  pendingUserLocation = null;
  if (!mapReady || !map) return;
  map.getSource(SOURCE_USER_LOCATION).setData(emptyFeatureCollection());
}

export function showObservationPopup(observation, lngLat, content) {
  showMapPopup(lngLat, content);
}

export function showFeaturePopup(lngLat, content) {
  showMapPopup(lngLat, content);
}

function showMapPopup(lngLat, content) {
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
  const userLocationData = mapReady ? map.getSource(SOURCE_USER_LOCATION)._data : null;
  const searchedPlaceData = mapReady ? map.getSource(SOURCE_SEARCHED_PLACE)._data : null;
  const userLocationPoint = userLocationData?.features?.find(
    (feature) => feature.geometry?.type === "Point",
  );
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
    userLocationFeatures: userLocationData?.features?.length || 0,
    userLocation: userLocationPoint?.geometry?.coordinates || null,
    searchedPlaceFeatures: searchedPlaceData?.features?.length || 0,
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
