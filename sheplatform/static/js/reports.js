/* Reporting module JS - vanilla. */
const API = "/reports/api";

async function loadReports() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#report-table tbody");
  tbody.innerHTML = "";
  for (const r of data.reports) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.report_ref}</td>
      <td>${r.title}</td>
      <td>${r.report_type}</td>
      <td>${r.status}</td>
      <td>${r.submission_deadline || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("report-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  for (const key of ["period_start", "period_end", "submission_deadline"]) {
    const v = fd.get(key);
    if (v) fd.set(key, new Date(v).toISOString());
  }
  const resp = await fetch(`${API}/create`, { method: "POST", body: fd });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadReports();
  } else {
    alert(data.message || "Failed to create report");
  }
});

loadReports();
