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
    // Action cell built with DOM APIs (no innerHTML) so nothing untrusted is injected.
    const actionTd = document.createElement("td");
    if (r.report_type === "annual_sustainability" && (r.status === "draft" || r.status === "review")) {
      const btn = document.createElement("button");
      btn.className = "btn btn-secondary";
      btn.textContent = "Compile ESG";
      btn.addEventListener("click", () => compileEsg(r.id, btn));
      actionTd.appendChild(btn);
    }
    tr.appendChild(actionTd);
    tbody.appendChild(tr);
  }
}

async function compileEsg(reportId, btn) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Compiling...";
  try {
    const resp = await fetch(`${API}/${reportId}/compile-esg`, { method: "POST" });
    const data = await resp.json();
    if (data.ok) {
      const s = data.summary;
      alert(`Compiled ${s.kpi_count} KPI(s) for ${s.reporting_year} `
        + `(${s.rag_counts.red} red / ${s.rag_counts.amber} amber / ${s.rag_counts.green} green).`);
    } else {
      alert(data.message || "Compile failed");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = original;
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
