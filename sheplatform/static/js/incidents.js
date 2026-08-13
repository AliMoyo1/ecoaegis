/* Incidents module JS - vanilla, no framework (guide 2). */
const API = "/incidents/api";
let currentSuggestion = null;

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
    let actions = "-";
    if (inc.status === "under_review" && inc.pending_step) {
      const role = inc.pending_step.role_required.replace(/_/g, " ");
      actions = `<span style="font-size:11px;color:var(--muted)">review: ${role}</span> ` +
                `<button class="btn btn-sm btn-primary" onclick="approveReport(${inc.id}, ${inc.pending_step.id}, 'approved')">Approve</button> ` +
                `<button class="btn btn-sm" onclick="approveReport(${inc.id}, ${inc.pending_step.id}, 'rejected')">Reject</button>`;
    }
    tr.innerHTML = `
      <td><a href="/incidents/${inc.id}">${inc.incident_ref}</a></td>
      <td>${inc.title}</td>
      <td>${inc.severity}</td>
      <td>${inc.incident_type}</td>
      <td>${inc.status}</td>
      <td>${deadline}</td>
      <td>${inc.reported_at || ""}</td>
      <td>
        <a class="btn btn-sm" href="/incidents/${inc.id}">Open</a>
        ${actions}
      </td>`;
    tbody.appendChild(tr);
  }
}

async function approveReport(incidentId, stepId, decision) {
  const comments = decision === "rejected" ? (prompt("Rejection reason:") || "rejected") : "";
  if (comments === null) return;
  const fd = new FormData();
  fd.append("step_id", stepId);
  fd.append("decision", decision);
  fd.append("comments", comments);
  const resp = await fetch(`${API}/${incidentId}/approve-report`, { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) alert(data.message || "Approval failed");
  else if (data.result.complete) alert("Report approval complete - incident can be closed");
  loadIncidents();
}

document.getElementById("incident-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = new FormData(form);
  if (currentSuggestion) body.append("ai_classify", "true");
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    currentSuggestion = null;
    document.getElementById("ai-suggestion-card").style.display = "none";
    loadIncidents();
  } else {
    alert(data.message || "Failed to create incident");
  }
});

async function classifyIncident() {
  const description = document.querySelector("[name='description']").value.trim();
  const status = document.getElementById("ai-suggestion-status");
  const card = document.getElementById("ai-suggestion-card");
  if (!description) { status.textContent = "Enter a description first"; return; }
  status.textContent = "Analysing...";
  const fd = new FormData();
  fd.append("description", description);
  const resp = await fetch("/ai/api/classify-incident", { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) { status.textContent = "AI classification failed"; return; }
  currentSuggestion = data.suggestion;
  const s = data.suggestion;
  document.getElementById("ai-suggestion-body").innerHTML = `
    <strong>Title:</strong> ${s.title}<br>
    <strong>Severity:</strong> ${s.severity}<br>
    <strong>Type:</strong> ${s.incident_type}<br>
    <strong>Summary:</strong> ${s.summary}`;
  card.style.display = "block";
  status.textContent = "Suggestion ready";
}

document.getElementById("ai-classify-btn").addEventListener("click", classifyIncident);

document.getElementById("accept-ai").addEventListener("change", (e) => {
  if (!currentSuggestion) return;
  if (e.target.checked) {
    document.querySelector("[name='title']").value = currentSuggestion.title || "";
    document.querySelector("[name='severity']").value = currentSuggestion.severity || "medium";
    document.querySelector("[name='incident_type']").value = currentSuggestion.incident_type || "accident";
  }
});

["f-status", "f-severity", "f-type"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadIncidents);
});

loadIncidents();
