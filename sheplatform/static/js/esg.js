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

async function csrfToken() {
  const m = document.cookie.match(/she_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

document.getElementById("csv-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const resp = await fetch(`${API}/csv/upload`, {
    method: "POST",
    body: fd,
    headers: {"X-CSRF-Token": await csrfToken()}
  });
  const data = await resp.json();
  const box = document.getElementById("csv-preview");
  if (!data.ok) {
    box.innerHTML = `<p class="red">Upload failed: ${escapeHtml(data.message || "")}</p>`;
    return;
  }
  let html = `<p>Upload ID: ${data.upload_id} | Total rows: ${data.rows_total}</p>`;
  if (data.preview && data.preview.length) {
    html += `<table class="data-table"><thead><tr><th>Row</th><th>Period</th><th>KPI</th><th>Value</th><th>Status</th><th>Note</th></tr></thead><tbody>`;
    for (const r of data.preview) {
      const statusClass = r.status === "valid" ? "green" : r.status === "duplicate" ? "amber" : "red";
      html += `<tr><td>${r.row_number}</td><td>${escapeHtml(r.period)}</td><td>${escapeHtml(r.kpi_code)} ${escapeHtml(r.kpi_name)}</td><td>${r.actual_value}</td><td class="${statusClass}">${r.status}</td><td>${escapeHtml(r.anomaly_reason || "")}</td></tr>`;
    }
    html += `</tbody></table>`;
    html += `<button class="btn btn-primary" id="reconcile-btn">Detect duplicates</button>`;
    html += ` <button class="btn btn-secondary" id="commit-btn">Commit valid rows</button>`;
  }
  box.innerHTML = html;
  box.querySelector("#reconcile-btn")?.addEventListener("click", async () => {
    const r = await fetch(`${API}/csv/${data.upload_id}/reconcile`, {method: "POST", headers: {"X-CSRF-Token": await csrfToken()}});
    const j = await r.json();
    alert(`Duplicates flagged: ${j.duplicates_flagged || 0}`);
    loadSummary();
  });
  box.querySelector("#commit-btn")?.addEventListener("click", async () => {
    const r = await fetch(`${API}/csv/${data.upload_id}/commit`, {method: "POST", headers: {"X-CSRF-Token": await csrfToken()}});
    const j = await r.json();
    alert(j.ok ? `Committed ${j.committed} rows` : `Commit failed: ${j.message}`);
    loadSummary();
  });
});

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

loadKpis();
loadSummary();
