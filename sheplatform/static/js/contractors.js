/* Contractors module JS - vanilla. */
const API = "/contractors/api";

async function loadOptions() {
  const [vResp, sResp] = await Promise.all([
    fetch(`${API}/vendors`),
    fetch(`${API}/sites`),
  ]);
  const vendors = (await vResp.json()).vendors || [];
  const sites = (await sResp.json()).sites || [];
  const vs = document.getElementById("vendor-select");
  const ss = document.getElementById("site-select");
  vs.innerHTML = "";
  ss.innerHTML = "";
  for (const v of vendors) {
    const opt = document.createElement("option");
    opt.value = v.id;
    opt.textContent = `${v.company_name} (${v.vendor_ref})`;
    vs.appendChild(opt);
  }
  for (const s of sites) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.site_name} (${s.city || ""})`;
    ss.appendChild(opt);
  }
}

async function loadReadiness() {
  const resp = await fetch(`${API}/vendors`);
  const data = await resp.json();
  const tbody = document.querySelector("#readiness-table tbody");
  tbody.innerHTML = "";
  for (const v of data.vendors) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${v.company_name}</td>
      <td>${v.status}</td>
      <td style="font-family:var(--font);font-size:12px;font-variant-numeric:tabular-nums">${v.insurance_expiry ? v.insurance_expiry.slice(0, 10) : "-"}</td>
      <td>${v.certification_status}</td>
      <td>${v.ptw_eligible ? "Yes" : "No"}</td>
      <td id="ready-${v.id}" style="font-weight:600">Checking...</td>
      <td><button class="btn btn-sm" onclick="checkReadiness(${v.id})">Check</button></td>`;
    tbody.appendChild(tr);
  }
}

async function checkReadiness(vendorId) {
  const siteId = document.getElementById("site-select").value;
  const resp = await fetch(`${API}/readiness?vendor_id=${vendorId}&site_id=${siteId}`);
  const data = await resp.json();
  const cell = document.getElementById(`ready-${vendorId}`);
  if (data.ready) {
    cell.textContent = "SITE READY";
    cell.style.color = "var(--good, #2ecc71)";
  } else {
    cell.textContent = data.reasons.join("; ");
    cell.style.color = "var(--red)";
  }
}

async function loadInductions() {
  const resp = await fetch(`${API}/inductions`);
  const data = await resp.json();
  const tbody = document.querySelector("#ind-table tbody");
  tbody.innerHTML = "";
  for (const i of data.inductions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${i.vendor_name || "-"}</td>
      <td>${i.site_name || "-"}</td>
      <td>${i.induction_type}</td>
      <td style="font-family:var(--font);font-size:12px;font-variant-numeric:tabular-nums">${i.induction_date ? i.induction_date.slice(0, 10) : "-"}</td>
      <td style="font-family:var(--font);font-size:12px;font-variant-numeric:tabular-nums">${i.valid_until ? i.valid_until.slice(0, 10) : "-"}</td>
      <td>${i.trainer_email || "-"}</td>
      <td><strong>${i.status}</strong></td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("ind-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/induction`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    alert("Induction recorded");
    e.target.reset();
    loadInductions();
  } else {
    alert(data.message || "Failed");
  }
});

loadOptions();
loadReadiness();
loadInductions();
