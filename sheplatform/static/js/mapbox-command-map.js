/* EcoAegis Phase 4 command map: admitted Mapbox renderer over private BBOX APIs. */
(function () {
  "use strict";

  const mapElement = document.getElementById("map");
  if (!mapElement || mapElement.dataset.engine !== "mapbox") return;

  const providerPanel = document.getElementById("map-provider-status");
  const dataStatus = document.getElementById("map-data-status");
  const budgetAdmin = document.getElementById("map-budget-admin");
  const layerOptions = document.getElementById("map-layer-options");
  const continuityPanel = document.getElementById("map-continuity");
  const continuityMessage = document.getElementById("map-continuity-message");
  const continuityList = document.getElementById("map-continuity-list");
  const detailPanel = document.getElementById("map-feature-detail");
  const detailProperties = document.getElementById("map-feature-properties");
  const detailLink = document.getElementById("map-feature-link");
  const center = [Number(mapElement.dataset.centerLng), Number(mapElement.dataset.centerLat)];
  const debounceMs = Number(mapElement.dataset.debounceMs) || 300;
  const pageNonce = mapElement.dataset.providerPageNonce || "";

  const STATE_TRANSITIONS = Object.freeze({
    idle: ["requesting", "unsupported", "failed"],
    requesting: ["admitted", "denied", "failed"],
    admitted: ["initializing", "failed"],
    initializing: ["ready", "failed"],
    ready: [], denied: [], unsupported: [], failed: [],
  });
  let lifecycleState = "idle";
  let map = null;
  let mapWasConstructed = false;
  let manifest = null;
  let styleConfig = null;
  let activeStyle = "standard";
  let loadGeneration = 0;
  let loadTimer = null;
  let lastQuerySignature = "";
  let continuityReason = "The basemap is unavailable.";
  let selectionHandler = null;
  let draftMarker = null;
  const layerCache = new Map();
  const layerControllers = new Map();
  const enabledLayers = new Set(["facilities", "incidents"]);
  const wiredLayers = new Set();

  const LAYER_COLORS = Object.freeze({
    facilities: "#2563eb", incidents: "#dc2626", permits: "#7c3aed",
    inspections: "#0891b2", environmental: "#16a34a", emergencies: "#ea580c",
    contractors: "#a16207", corrective_actions: "#be123c", assets: "#475569",
    observations: "#0f766e", risks: "#9333ea",
  });

  function transition(next) {
    if (!STATE_TRANSITIONS[lifecycleState].includes(next)) {
      throw new Error(`Invalid map lifecycle transition ${lifecycleState} to ${next}`);
    }
    lifecycleState = next;
    mapElement.dataset.lifecycleState = next;
  }

  function providerStatus(title, message, visible) {
    providerPanel.querySelector("h2").textContent = title;
    providerPanel.querySelector("p").textContent = message;
    providerPanel.style.display = visible ? "block" : "none";
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)she_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function setDataStatus(message) {
    if (dataStatus) dataStatus.textContent = message;
  }

  async function loadAdminBudget() {
    if (!budgetAdmin) return;
    try {
      const response = await fetch("/map/api/provider-budget");
      if (!response.ok) throw new Error("Provider budget status unavailable");
      const data = await response.json();
      budgetAdmin.textContent = `Mapbox budget: ${data.admitted_loads.toLocaleString()} of ` +
        `${data.monthly_limit.toLocaleString()} loads admitted, approximately ` +
        `US$${Number(data.estimated_cost_usd).toFixed(2)} (${data.state}).`;
    } catch (error) {
      budgetAdmin.textContent = error.message || "Provider budget status unavailable.";
    }
  }

  function filters() {
    const values = {
      severity: document.getElementById("f-severity")?.value || "",
      type: document.getElementById("f-type")?.value || "",
      since: document.getElementById("f-since")?.value || "",
    };
    if (values.since) values.since = new Date(values.since).toISOString();
    return values;
  }

  function queryBounds() {
    if (!map) return [-180, -90, 180, 90];
    const bounds = map.getBounds();
    let west = Math.max(-180, bounds.getWest());
    let east = Math.min(180, bounds.getEast());
    if (west > east || bounds.getWest() < -180 || bounds.getEast() > 180) {
      west = -180;
      east = 180;
    }
    return [west, Math.max(-90, bounds.getSouth()), east, Math.min(90, bounds.getNorth())]
      .map((value) => Number(value.toFixed(6)));
  }

  function manifestUrl(bounds) {
    const query = new URLSearchParams({ bbox: bounds.join(",") });
    return `/map/api/manifest?${query}`;
  }

  function layerUrl(spec, bounds) {
    const query = new URLSearchParams({ bbox: bounds.join(",") });
    const currentFilters = filters();
    for (const key of spec.supported_filters) {
      if (currentFilters[key]) query.set(key, currentFilters[key]);
    }
    return `${spec.endpoint}?${query}`;
  }

  function sourceId(key) { return `ecoaegis-${key}`; }
  function clusterId(key) { return `${sourceId(key)}-clusters`; }
  function countId(key) { return `${sourceId(key)}-cluster-count`; }
  function pointId(key) { return `${sourceId(key)}-points`; }

  function addOperationalLayer(spec, collection) {
    if (!map || !map.isStyleLoaded()) return;
    const id = sourceId(spec.key);
    const existing = map.getSource(id);
    if (existing) {
      existing.setData(collection);
      setLayerVisibility(spec.key, true);
      return;
    }
    map.addSource(id, {
      type: "geojson", data: collection, cluster: true,
      clusterMaxZoom: 14, clusterRadius: 48,
    });
    const color = LAYER_COLORS[spec.key] || "#334155";
    map.addLayer({
      id: clusterId(spec.key), type: "circle", source: id,
      filter: ["has", "point_count"],
      paint: {
        "circle-color": color,
        "circle-radius": ["step", ["get", "point_count"], 18, 30, 23, 100, 29],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
      },
    });
    map.addLayer({
      id: countId(spec.key), type: "symbol", source: id,
      filter: ["has", "point_count"],
      layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 12 },
      paint: { "text-color": "#ffffff" },
    });
    map.addLayer({
      id: pointId(spec.key), type: "circle", source: id,
      filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": color, "circle-radius": 7,
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
      },
    });
    wireLayerInteractions(spec);
  }

  function setLayerVisibility(key, visible) {
    if (!map) return;
    const visibility = visible ? "visible" : "none";
    for (const id of [clusterId(key), countId(key), pointId(key)]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visibility);
    }
  }

  function wireLayerInteractions(spec) {
    if (wiredLayers.has(spec.key)) return;
    wiredLayers.add(spec.key);
    map.on("click", clusterId(spec.key), async function (event) {
      const feature = event.features?.[0];
      const source = map.getSource(sourceId(spec.key));
      if (!feature || !source) return;
      const zoom = await source.getClusterExpansionZoom(feature.properties.cluster_id);
      map.easeTo({ center: feature.geometry.coordinates, zoom });
    });
    map.on("click", pointId(spec.key), function (event) {
      const feature = event.features?.[0];
      if (feature) showFeature(spec, feature).catch(showDataError);
    });
    for (const layerId of [clusterId(spec.key), pointId(spec.key)]) {
      map.on("mouseenter", layerId, function () { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", layerId, function () {
        map.getCanvas().style.cursor = selectionHandler ? "crosshair" : "";
      });
    }
  }

  function appendDetail(term, value) {
    if (value === null || value === undefined || value === "") return;
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term.replace(/_/g, " ");
    dd.textContent = String(value);
    detailProperties.append(dt, dd);
  }

  function safeLocalUrl(value) {
    return typeof value === "string" && value.startsWith("/") && !value.startsWith("//")
      ? value : "/map";
  }

  async function showFeature(spec, feature) {
    let properties = feature.properties || {};
    if (spec.key === "facilities" && properties.id) {
      const response = await fetch(`/map/api/facility/${encodeURIComponent(properties.id)}`);
      if (response.ok) {
        const detail = await response.json();
        properties = { ...(detail.properties || {}), counts: detail.counts || {} };
      }
    }
    detailProperties.replaceChildren();
    for (const key of ["ref", "label", "site_name", "status", "severity", "type", "timestamp"]) {
      appendDetail(key, properties[key]);
    }
    if (spec.key === "facilities" && properties.counts) {
      for (const [key, value] of Object.entries(properties.counts)) appendDetail(key, value);
    }
    detailLink.href = safeLocalUrl(properties.url || "/map");
    detailPanel.hidden = false;
    detailPanel.focus?.();
  }

  function restoreCachedSources() {
    if (!manifest) return;
    for (const spec of manifest.layers) {
      if (enabledLayers.has(spec.key) && layerCache.has(spec.key)) {
        addOperationalLayer(spec, layerCache.get(spec.key));
      }
    }
  }

  async function fetchLayer(spec, bounds, generation) {
    layerControllers.get(spec.key)?.abort();
    const controller = new AbortController();
    layerControllers.set(spec.key, controller);
    const response = await fetch(layerUrl(spec, bounds), { signal: controller.signal });
    if (!response.ok) throw new Error(`${spec.label} could not be loaded`);
    const collection = await response.json();
    if (generation !== loadGeneration) return null;
    layerCache.set(spec.key, collection);
    if (map) addOperationalLayer(spec, collection);
    return collection;
  }

  function renderLayerControls() {
    layerOptions.replaceChildren();
    for (const spec of manifest.layers) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = spec.key;
      input.checked = enabledLayers.has(spec.key);
      input.addEventListener("change", function () {
        if (input.checked) {
          enabledLayers.add(spec.key);
          setLayerVisibility(spec.key, true);
        } else {
          enabledLayers.delete(spec.key);
          setLayerVisibility(spec.key, false);
        }
        scheduleLoad(true);
      });
      label.append(input, document.createTextNode(spec.label));
      layerOptions.appendChild(label);
    }
  }

  async function loadVisibleData(force) {
    const bounds = queryBounds();
    const signature = JSON.stringify([bounds, filters(), [...enabledLayers].sort()]);
    if (!force && signature === lastQuerySignature) return;
    lastQuerySignature = signature;
    const generation = ++loadGeneration;
    setDataStatus("Loading authorized records in this view...");
    const manifestResponse = await fetch(manifestUrl(bounds));
    if (!manifestResponse.ok) throw new Error("Authorized map layers could not be loaded");
    manifest = await manifestResponse.json();
    renderLayerControls();
    const active = manifest.layers.filter((spec) => enabledLayers.has(spec.key));
    const results = await Promise.allSettled(active.map((spec) => fetchLayer(spec, bounds, generation)));
    if (generation !== loadGeneration) return;
    const failures = results.filter((result) => result.status === "rejected" &&
      result.reason?.name !== "AbortError");
    const collections = results.filter((result) => result.status === "fulfilled" && result.value)
      .map((result) => result.value);
    const returned = collections.reduce((total, item) => total + item.meta.returned, 0);
    const unlocated = collections.reduce((total, item) => total + item.meta.unlocated, 0);
    const truncated = collections.some((item) => item.meta.truncated);
    setDataStatus(`${returned} located records in view, ${unlocated} unlocated` +
      `${truncated ? "; one or more layers reached the display limit" : ""}` +
      `${failures.length ? `; ${failures.length} layer request failed` : ""}.`);
  }

  function scheduleLoad(force) {
    window.clearTimeout(loadTimer);
    loadTimer = window.setTimeout(function () {
      const request = !map && continuityPanel && !continuityPanel.hidden
        ? showProviderFreeMode(continuityReason)
        : loadVisibleData(Boolean(force));
      request.catch(showDataError);
    }, debounceMs);
  }

  function showDataError(error) {
    if (error?.name === "AbortError") return;
    setDataStatus(error?.message || "Operational map data could not be loaded.");
  }

  function renderContinuityRecords(spec, collection) {
    for (const feature of collection.features.slice(0, 50)) {
      const item = document.createElement("li");
      const props = feature.properties || {};
      const link = document.createElement("a");
      link.href = safeLocalUrl(props.url);
      link.textContent = props.ref || props.label || `${spec.label} record`;
      item.appendChild(link);
      if (props.label && props.label !== props.ref) {
        item.appendChild(document.createTextNode(`: ${props.label}`));
      }
      continuityList.appendChild(item);
    }
  }

  async function showProviderFreeMode(reason) {
    continuityReason = reason;
    mapElement.hidden = true;
    continuityPanel.hidden = false;
    continuityMessage.textContent = `${reason} Authorized EcoAegis records remain available below.`;
    continuityList.replaceChildren();
    try {
      const bounds = [-180, -90, 180, 90];
      const response = await fetch(manifestUrl(bounds));
      if (!response.ok) throw new Error("Operational data could not be loaded");
      manifest = await response.json();
      renderLayerControls();
      const active = manifest.layers.filter((spec) => enabledLayers.has(spec.key));
      for (const spec of active) {
        const layerResponse = await fetch(layerUrl(spec, bounds));
        if (!layerResponse.ok) continue;
        const collection = await layerResponse.json();
        layerCache.set(spec.key, collection);
        renderContinuityRecords(spec, collection);
      }
      setDataStatus(`${continuityList.children.length} operational records listed without a basemap.`);
      if (!continuityList.children.length) {
        const item = document.createElement("li");
        item.textContent = "No located records match the current filters.";
        continuityList.appendChild(item);
      }
    } catch (error) {
      showDataError(error);
    }
  }

  async function admit() {
    const body = new FormData();
    body.append("page_nonce", pageNonce);
    const response = await fetch("/map/api/provider-session", {
      method: "POST", body, headers: { "X-CSRF-Token": csrfToken() },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.detail || "The secure basemap could not start");
      error.decision = data.decision;
      throw error;
    }
    return data;
  }

  function constructMap(admission) {
    if (mapWasConstructed) throw new Error("Mapbox was already initialized for this page");
    mapWasConstructed = true;
    mapboxgl.accessToken = admission.token;
    mapboxgl.workerUrl = admission.worker_url;
    styleConfig = admission;
    map = new mapboxgl.Map({
      container: mapElement,
      style: admission.style,
      center,
      zoom: Number(mapElement.dataset.defaultZoom),
      minZoom: Number(mapElement.dataset.minZoom),
      maxZoom: Number(mapElement.dataset.maxZoom),
      attributionControl: true,
      cooperativeGestures: true,
    });
    map.addControl(new mapboxgl.NavigationControl(), "top-right");
    map.on("style.load", restoreCachedSources);
    map.on("load", function () {
      transition("ready");
      providerStatus("Secure basemap ready",
        "Mapbox Standard is active. EcoAegis operational data stays on the private application API.", false);
      loadVisibleData(true).catch(showDataError);
    });
    map.on("moveend", function () { scheduleLoad(false); });
    map.on("click", function (event) {
      if (selectionHandler) selectionHandler(event.lngLat.lat, event.lngLat.lng);
    });
    map.on("error", function (event) {
      providerStatus("Basemap degraded",
        "Operational data remains available while the provider connection is unavailable.", true);
      fetch("/map/api/metrics/provider-failure", {
        method: "POST", headers: { "X-CSRF-Token": csrfToken() },
      }).catch(function () {});
      if (event?.error) console.warn("Mapbox renderer error", event.error.message);
    });
  }

  async function start() {
    if (!window.mapboxgl || !mapboxgl.supported({ failIfMajorPerformanceCaveat: true })) {
      transition("unsupported");
      providerStatus("Basemap unavailable on this browser",
        "This browser does not provide the required WebGL 2 support.", true);
      await showProviderFreeMode("The basemap is unsupported on this browser.");
      return;
    }
    if (!pageNonce) {
      transition("failed");
      await showProviderFreeMode("A secure provider session could not be requested.");
      return;
    }
    transition("requesting");
    try {
      const admission = await admit();
      transition("admitted");
      transition("initializing");
      constructMap(admission);
    } catch (error) {
      transition(error.decision === "denied" ? "denied" : "failed");
      providerStatus(error.decision === "denied" ? "Basemap monthly limit reached" : "Basemap unavailable",
        error.message || "The secure basemap could not start.", true);
      await showProviderFreeMode(error.message || "The basemap could not start.");
    }
  }

  for (const id of ["f-severity", "f-type", "f-since"]) {
    document.getElementById(id)?.addEventListener("change", function () { scheduleLoad(true); });
  }
  document.querySelectorAll('input[name="map-style"]').forEach(function (input) {
    input.addEventListener("change", function () {
      if (!map || !styleConfig || !input.checked || input.value === activeStyle) return;
      activeStyle = input.value;
      map.setStyle(activeStyle === "satellite" ? styleConfig.satellite_style : styleConfig.style);
    });
  });
  document.getElementById("map-feature-close")?.addEventListener("click", function () {
    detailPanel.hidden = true;
  });

  window.ecoMapRenderer = {
    refresh: function () { return loadVisibleData(true); },
    isReady: function () { return lifecycleState === "ready"; },
    setSelectionHandler: function (handler) {
      selectionHandler = lifecycleState === "ready" && typeof handler === "function" ? handler : null;
      if (map) map.getCanvas().style.cursor = selectionHandler ? "crosshair" : "";
      return lifecycleState === "ready";
    },
    setDraft: function (latitude, longitude, onDrag, moveMap) {
      if (!map || lifecycleState !== "ready") return false;
      const coordinates = [Number(longitude), Number(latitude)];
      if (!draftMarker) {
        draftMarker = new mapboxgl.Marker({ draggable: true }).setLngLat(coordinates).addTo(map);
        draftMarker.on("dragend", function () {
          const point = draftMarker.getLngLat();
          if (onDrag) onDrag(point.lat, point.lng);
        });
      } else {
        draftMarker.setLngLat(coordinates);
      }
      if (moveMap !== false) map.easeTo({ center: coordinates });
      return true;
    },
    removeDraft: function () {
      draftMarker?.remove();
      draftMarker = null;
    },
  };

  start().catch(function (error) {
    console.error("Command map startup failed", error);
    showDataError(error);
  });
  loadAdminBudget();
})();
