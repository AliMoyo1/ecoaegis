/* Risk Register module JS - vanilla. */
const API = "/risks/api";

// Deep links from dashboard tiles: ?level=high filters to residual >= 12
const params = new URLSearchParams(location.search);
const levelFilter = params.get("level");

async function loadRisks() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  let risks = data.risks;
  if (levelFilter === "high") {
    risks = risks.filter((r) => r.priority === "High");
  }
  const tbody = document.querySelector("#risk-table tbody");
  tbody.innerHTML = "";
  for (const r of risks) {
    const tr = document.createElement("tr");
    const priorityClass = r.priority === "High" ? "red" : (r.priority === "Medium" ? "amber" : "");
    tr.innerHTML = `
      <td>${r.risk_ref}</td>
      <td>${r.hazard_description}</td>
      <td>${r.risk_category}</td>
      <td>${r.likelihood} x ${r.impact}</td>
      <td>${r.control_effectiveness}</td>
      <td><strong>${r.residual_score}</strong></td>
      <td class="${priorityClass}">${r.priority}</td>
      <td>${r.status}</td>
      <td>${r.origin_module || r.source_type || "manual"}</td>`;
    tbody.appendChild(tr);
  }
  if (levelFilter === "high" && risks.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" style="color:var(--muted)">No high risks (residual >= 12).</td></tr>';
  }
}

document.getElementById("risk-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = new FormData(form);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadRisks();
  } else {
    alert(data.message || "Failed to register risk");
  }
});

loadRisks();
