/* Geographic map module JS - vanilla, Leaflet + Leaflet.markercluster (guide C1). */
const mapEl = document.getElementById("map");
const TILE_URL = mapEl.dataset.tileUrl;
const TILE_ATTRIBUTION = mapEl.dataset.tileAttribution;

// Reuse the app's own theme colors so markers match light/dark mode instead
// of a second, hardcoded palette drifting out of sync with app.css.
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

const map = L.map("map").setView([-19.0154, 29.1549], 6); // Zimbabwe

L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(map);

const clusterGroup = L.markerClusterGroup();
map.addLayer(clusterGroup);

function incidentPopup(inc) {
  const date = inc.occurred_at ? inc.occurred_at.slice(0, 10) : "";
  return `<strong><a href="/incidents/${inc.id}">${inc.incident_ref}</a></strong><br>
    ${inc.title}<br>
    <span style="text-transform:capitalize">${inc.severity}</span> &middot;
    <span style="text-transform:capitalize">${inc.incident_type.replace(/_/g, " ")}</span><br>
    ${date}`;
}

function sitePopup(site) {
  return `<strong>${site.site_name}</strong> (${site.site_code})<br>
    <span style="text-transform:capitalize">${site.site_type}</span><br>
    ${site.city || ""}${site.city && site.region ? ", " : ""}${site.region || ""}`;
}

async function loadPoints() {
  const severity = document.getElementById("f-severity").value;
  const type = document.getElementById("f-type").value;
  const since = document.getElementById("f-since").value;
  const qs = new URLSearchParams();
  if (severity) qs.set("severity", severity);
  if (type) qs.set("type", type);
  if (since) qs.set("since", new Date(since).toISOString());

  const resp = await fetch(`/map/api/points?${qs}`);
  const data = await resp.json();

  clusterGroup.clearLayers();

  for (const inc of data.incidents) {
    const marker = L.marker([inc.latitude, inc.longitude], { icon: dotIcon(SEVERITY_COLOR[inc.severity] || COLOR_MUTED) });
    marker.bindPopup(incidentPopup(inc));
    clusterGroup.addLayer(marker);
  }
  for (const site of data.sites) {
    const marker = L.marker([site.latitude, site.longitude], { icon: dotIcon(COLOR_SITE) });
    marker.bindPopup(sitePopup(site));
    clusterGroup.addLayer(marker);
  }

  const all = [...data.incidents, ...data.sites];
  if (all.length) {
    const bounds = L.latLngBounds(all.map((p) => [p.latitude, p.longitude]));
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  }
}

["f-severity", "f-type", "f-since"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadPoints);
});

loadPoints();
