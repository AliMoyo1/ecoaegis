/* ESG KPI module JS - vanilla. */
const API = "/esg/api";

async function loadKpis() {
  const resp = await fetch(`${API}/kpis`);
  const data = await resp.json();
  const select = document.getElementById("kpi-select");
  select.innerHTML = "";
  const tbody = document.querySelector("#kpi-table tbody");
  tbody.innerHTML = "";
  for (const k of data.kpis) {
    const opt = document.createElement("option");
    opt.value = k.id;
    opt.textContent = `${k.kpi_code} - ${k.name}`;
    select.appendChild(opt);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${k.kpi_code}</td>
      <td>${k.name}</td>
      <td>${k.category}</td>
      <td>${k.unit}</td>
      <td>${k.alert_threshold === null ? "-" : "non-zero triggers incident"}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadSummary() {
  const resp = await fetch(`${API}/summary`);
  const data = await resp.json();
  document.getElementById("rag-summary").innerHTML = `
    <div class="kpi-card"><span class="kpi-value red">${data.red}</span><span class="kpi-label">Red (off-track)</span></div>
    <div class="kpi-card"><span class="kpi-value amber">${data.amber}</span><span class="kpi-label">Amber (at risk)</span></div>
    <div class="kpi-card"><span class="kpi-value">${data.green}</span><span class="kpi-label">Green (on-track)</span></div>`;
}

document.getElementById("esg-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  if (!fd.get("target_value")) fd.delete("target_value");
  const resp = await fetch(`${API}/entries`, { method: "POST", body: fd });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadSummary();
    alert(data.entry.linked_incident_id ? "Entry recorded - non-zero critical KPI auto-created an incident!" : "Entry recorded");
  } else {
    alert(data.message || "Failed to record entry");
  }
});

loadKpis();
loadSummary();
