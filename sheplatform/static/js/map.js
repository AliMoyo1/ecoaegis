/* Geographic map module - provider-neutral data rendered with Leaflet (guide C1). */
const mapEl = document.getElementById("map");
const TILE_URL = mapEl.dataset.tileUrl;
const TILE_ATTRIBUTION = mapEl.dataset.tileAttribution;
const CENTER = [Number(mapEl.dataset.centerLat), Number(mapEl.dataset.centerLng)];
const DEFAULT_ZOOM = Number(mapEl.dataset.defaultZoom);
const MIN_ZOOM = Number(mapEl.dataset.minZoom);
const MAX_ZOOM = Number(mapEl.dataset.maxZoom);
const DEBOUNCE_MS = Number(mapEl.dataset.debounceMs);

const theme = getComputedStyle(document.documentElement);
const COLOR_RED = theme.getPropertyValue("--red").trim() || "#e74c3c";
const COLOR_AMBER = theme.getPropertyValue("--amber").trim() || "#f1c40f";
const COLOR_MUTED = theme.getPropertyValue("--muted").trim() || "#888";
const COLOR_SITE = "#4a89dc";

const SEVERITY_COLOR = {
  critical: COLOR_RED,
  high: COLOR_RED,
  medium: COLOR_AMBER,
  low: COLOR_MUTED,
  near_miss: COLOR_MUTED,
};

function dotIcon(color) {
  return L.divIcon({
    className: "",
    html: `<span style="display:block;width:14px;height:14px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 2px rgba(0,0,0,0.5)"></span>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
    popupAnchor: [0, -7],
  });
}

const map = L.map("map", { minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM }).setView(CENTER, DEFAULT_ZOOM);
const providerStatus = document.getElementById("map-provider-status");

if (TILE_URL) {
  const tileLayer = L.tileLayer(TILE_URL, {
    attribution: TILE_ATTRIBUTION,
    minZoom: MIN_ZOOM,
    maxZoom: MAX_ZOOM,
  }).addTo(map);
  let providerFailed = false;
  tileLayer.on("tileerror", () => {
    if (providerFailed) return;
    providerFailed = true;
    providerStatus.style.display = "block";
    providerStatus.querySelector("h2").textContent = "Basemap temporarily unavailable";
    providerStatus.querySelector("p").textContent =
      "Operational markers and coordinate editing remain available. Check the configured tile provider and network connection.";
  });
}

const clusterGroup = L.markerClusterGroup();
map.addLayer(clusterGroup);

function appendLine(container, values) {
  container.appendChild(document.createElement("br"));
  container.appendChild(document.createTextNode(values.filter(Boolean).join("")));
}

function incidentPopup(incident) {
  const content = document.createElement("div");
  const strong = document.createElement("strong");
  const link = document.createElement("a");
  link.href = `/incidents/${Number(incident.id)}`;
  link.textContent = incident.incident_ref || "Incident";
  strong.appendChild(link);
  content.appendChild(strong);
  appendLine(content, [incident.title || ""]);
  appendLine(content, [
    (incident.severity || "").replace(/_/g, " "),
    incident.severity && incident.incident_type ? " · " : "",
    (incident.incident_type || "").replace(/_/g, " "),
  ]);
  appendLine(content, [incident.occurred_at ? String(incident.occurred_at).slice(0, 10) : ""]);
  return content;
}

function sitePopup(site) {
  const content = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = site.site_name || "Site";
  content.appendChild(strong);
  content.appendChild(document.createTextNode(site.site_code ? ` (${site.site_code})` : ""));
  appendLine(content, [(site.site_type || "site").replace(/_/g, " ")]);
  appendLine(content, [site.city || "", site.city && site.region ? ", " : "", site.region || ""]);
  return content;
}

async function loadPoints(options = {}) {
  const severity = document.getElementById("f-severity").value;
  const type = document.getElementById("f-type").value;
  const since = document.getElementById("f-since").value;
  const qs = new URLSearchParams();
  if (severity) qs.set("severity", severity);
  if (type) qs.set("type", type);
  if (since) qs.set("since", new Date(since).toISOString());

  const response = await fetch(`/map/api/points?${qs}`);
  if (!response.ok) throw new Error("Map points could not be loaded");
  const data = await response.json();
  clusterGroup.clearLayers();

  for (const incident of data.incidents) {
    const marker = L.marker([incident.latitude, incident.longitude], {
      icon: dotIcon(SEVERITY_COLOR[incident.severity] || COLOR_MUTED),
    });
    marker.bindPopup(incidentPopup(incident));
    clusterGroup.addLayer(marker);
  }
  for (const site of data.sites) {
    const marker = L.marker([site.latitude, site.longitude], { icon: dotIcon(COLOR_SITE) });
    marker.bindPopup(sitePopup(site));
    clusterGroup.addLayer(marker);
  }

  const all = [...data.incidents, ...data.sites];
  if (all.length && options.fit !== false) {
    const bounds = L.latLngBounds(all.map((point) => [point.latitude, point.longitude]));
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: Math.min(12, MAX_ZOOM) });
  }
}

let filterTimer;
for (const id of ["f-severity", "f-type", "f-since"]) {
  document.getElementById(id).addEventListener("change", () => {
    window.clearTimeout(filterTimer);
    filterTimer = window.setTimeout(() => loadPoints().catch(reportMapError), DEBOUNCE_MS);
  });
}

function reportMapError(error) {
  providerStatus.style.display = "block";
  providerStatus.querySelector("h2").textContent = "Map data unavailable";
  providerStatus.querySelector("p").textContent = error.message || "Map data could not be loaded.";
}

const editor = document.getElementById("coordinate-editor");
if (editor) {
  const form = document.getElementById("coordinate-form");
  const siteSelect = document.getElementById("coordinate-site");
  const latitudeInput = document.getElementById("coordinate-latitude");
  const longitudeInput = document.getElementById("coordinate-longitude");
  const accuracyInput = document.getElementById("coordinate-accuracy");
  const sourceInput = document.getElementById("coordinate-source");
  const status = document.getElementById("coordinate-status");
  const selectButton = document.getElementById("coordinate-map-select");
  const deviceButton = document.getElementById("coordinate-device");
  const clearButton = document.getElementById("coordinate-clear");
  const sites = new Map();
  let draftMarker = null;
  let selectingOnMap = false;

  function hasCoordinates(site) {
    return site && site.latitude !== null && site.latitude !== undefined &&
      site.longitude !== null && site.longitude !== undefined;
  }

  function selectedSite() {
    return sites.get(Number(siteSelect.value));
  }

  function setStatus(message) {
    status.textContent = message;
  }

  function removeDraftMarker() {
    if (draftMarker) map.removeLayer(draftMarker);
    draftMarker = null;
  }

  function setDraft(latitude, longitude, source, accuracy, panToMarker = true) {
    if (latitude === "" || longitude === "" || latitude === null || longitude === null) return false;
    const lat = Number(latitude);
    const lng = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
      return false;
    }
    latitudeInput.value = lat.toFixed(6);
    longitudeInput.value = lng.toFixed(6);
    sourceInput.value = source;
    accuracyInput.value = accuracy === null || accuracy === undefined ? "" : Number(accuracy).toFixed(1);
    if (!draftMarker) {
      draftMarker = L.marker([lat, lng], { draggable: true }).addTo(map);
      draftMarker.on("dragend", () => {
        const position = draftMarker.getLatLng();
        setDraft(position.lat, position.lng, "manual", null, false);
        setStatus("Draft marker moved. Save to apply this location.");
      });
    } else {
      draftMarker.setLatLng([lat, lng]);
    }
    if (panToMarker) map.panTo([lat, lng]);
    return true;
  }

  function stopMapSelection() {
    selectingOnMap = false;
    selectButton.textContent = "Select on map";
    mapEl.style.cursor = "";
  }

  siteSelect.addEventListener("change", () => {
    stopMapSelection();
    const site = selectedSite();
    removeDraftMarker();
    if (!site) {
      form.reset();
      setStatus("Choose a site to begin.");
      return;
    }
    if (hasCoordinates(site)) {
      setDraft(site.latitude, site.longitude, site.coordinate_source || "manual",
        site.coordinate_accuracy_m, true);
      setStatus(`Current source: ${(site.coordinate_source || "legacy").replace(/_/g, " ")}.`);
    } else {
      latitudeInput.value = "";
      longitudeInput.value = "";
      accuracyInput.value = "";
      sourceInput.value = "manual";
      setStatus("This site has no recorded location.");
    }
  });

  for (const input of [latitudeInput, longitudeInput]) {
    input.addEventListener("input", () => {
      sourceInput.value = "manual";
      accuracyInput.value = "";
      setDraft(latitudeInput.value, longitudeInput.value, "manual", null, false);
    });
  }

  selectButton.addEventListener("click", () => {
    if (!selectedSite()) {
      setStatus("Choose a site before selecting a map location.");
      return;
    }
    selectingOnMap = !selectingOnMap;
    selectButton.textContent = selectingOnMap ? "Cancel map selection" : "Select on map";
    mapEl.style.cursor = selectingOnMap ? "crosshair" : "";
    setStatus(selectingOnMap ? "Click the map to position the draft marker." : "Map selection cancelled.");
  });

  map.on("click", (event) => {
    if (!selectingOnMap) return;
    setDraft(event.latlng.lat, event.latlng.lng, "manual", null, false);
    stopMapSelection();
    setStatus("Map location selected. Save to apply it.");
  });

  deviceButton.addEventListener("click", () => {
    if (!selectedSite()) {
      setStatus("Choose a site before using device location.");
      return;
    }
    if (!navigator.geolocation) {
      setStatus("This browser does not provide device location. Enter coordinates or select the map.");
      return;
    }
    setStatus("Requesting device location...");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const accuracy = position.coords.accuracy;
        setDraft(position.coords.latitude, position.coords.longitude, "device_gps", accuracy, true);
        setStatus(accuracy > 100
          ? `Device location captured with low accuracy (±${Math.round(accuracy)} m). Review before saving.`
          : `Device location captured (±${Math.round(accuracy)} m). Review and save.`);
      },
      (error) => {
        const message = error.code === error.PERMISSION_DENIED
          ? "Device location permission was denied. Enter coordinates or select the map."
          : "Device location was unavailable. Enter coordinates or select the map.";
        setStatus(message);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    );
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const site = selectedSite();
    if (!site || !form.reportValidity()) return;
    const replacing = hasCoordinates(site) &&
      (Number(site.latitude) !== Number(latitudeInput.value) || Number(site.longitude) !== Number(longitudeInput.value));
    if (replacing && !window.confirm("Replace this site's existing coordinates? The previous values remain in the audit trail.")) return;
    setStatus("Saving location...");
    try {
      const response = await fetch(`/map/api/sites/${site.id}/coords`, {
        method: "POST",
        body: new FormData(form),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Location could not be saved");
      sites.set(site.id, data.site);
      setDraft(data.site.latitude, data.site.longitude, data.site.coordinate_source,
        data.site.coordinate_accuracy_m, false);
      setStatus("Site location saved and audited.");
      await loadPoints({ fit: false });
    } catch (error) {
      setStatus(error.message || "Location could not be saved.");
    }
  });

  clearButton.addEventListener("click", async () => {
    const site = selectedSite();
    if (!site || !hasCoordinates(site)) {
      setStatus("The selected site has no saved location to clear.");
      return;
    }
    if (!window.confirm("Clear this site's saved coordinates? The previous values remain in the audit trail.")) return;
    setStatus("Clearing location...");
    try {
      const response = await fetch(`/map/api/sites/${site.id}/coords`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || "Location could not be cleared");
      sites.set(site.id, data.site);
      removeDraftMarker();
      latitudeInput.value = "";
      longitudeInput.value = "";
      accuracyInput.value = "";
      sourceInput.value = "manual";
      setStatus("Site location cleared and audited.");
      await loadPoints({ fit: false });
    } catch (error) {
      setStatus(error.message || "Location could not be cleared.");
    }
  });

  async function loadCoordinateSites() {
    try {
      const response = await fetch("/map/api/sites");
      if (!response.ok) throw new Error("Sites could not be loaded");
      const data = await response.json();
      siteSelect.replaceChildren(new Option("Choose a site", ""));
      for (const site of data.sites) {
        sites.set(site.id, site);
        const locationState = hasCoordinates(site) ? "located" : "unlocated";
        siteSelect.add(new Option(`${site.site_name} (${site.site_code}) — ${locationState}`, String(site.id)));
      }
      setStatus(data.sites.length ? "Choose a site to begin." : "No active sites are available.");
    } catch (error) {
      siteSelect.replaceChildren(new Option("Sites unavailable", ""));
      setStatus(error.message || "Sites could not be loaded.");
    }
  }

  loadCoordinateSites();
}

loadPoints().catch(reportMapError);
