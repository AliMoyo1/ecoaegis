/* Incidents module JS - vanilla, no framework (guide 2). */
const API = "/incidents/api";

// Deep links from dashboard tiles: ?status=open, ?type=near_miss
const params = new URLSearchParams(location.search);
if (params.get("status") && document.getElementById("f-status")) {
  document.getElementById("f-status").value = params.get("status");
}
if (params.get("severity") && document.getElementById("f-severity")) {
  document.getElementById("f-severity").value = params.get("severity");
}
if (params.get("type") && document.getElementById("f-type")) {
  document.getElementById("f-type").value = params.get("type");
}

async function loadIncidents() {
  const status = document.getElementById("f-status").value;
  const severity = document.getElementById("f-severity").value;
  const type = document.getElementById("f-type").value;
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (severity) qs.set("severity", severity);
  if (type) qs.set("type", type);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#incident-table tbody");
  tbody.innerHTML = "";
  for (const inc of data.incidents) {
    const tr = document.createElement("tr");
    const deadline = inc.statutory_deadline ? `<span class="${inc.statutory_deadline < new Date().toISOString() ? 'red' : ''}">${inc.statutory_deadline}</span>` : "-";
    tr.innerHTML = `
      <td>${inc.incident_ref}</td>
      <td>${inc.title}</td>
      <td>${inc.severity}</td>
      <td>${inc.incident_type}</td>
      <td>${inc.status}</td>
      <td>${deadline}</td>
      <td>${inc.reported_at || ""}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("incident-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = new FormData(form);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadIncidents();
  } else {
    alert(data.message || "Failed to create incident");
  }
});

["f-status", "f-severity", "f-type"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadIncidents);
});

loadIncidents();
