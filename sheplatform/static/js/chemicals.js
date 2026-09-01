/* Chemicals module JS - vanilla. */
const API = "/chemicals/api";

async function loadSites() {
  const resp = await fetch(`${API}/sites`);
  const data = await resp.json();
  const sel = document.getElementById("chem-site");
  for (const s of (data.sites || [])) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.site_name;
    sel.appendChild(opt);
  }
}

function statusBadge(status) {
  const map = {
    current: "green",
    draft: "amber",
    expiring: "amber",
    expired: "red",
  };
  return `<span class="badge badge-${map[status] || 'gray'}">${status || "-"}</span>`;
}

async function loadChemicals() {
  const hazard = document.getElementById("f-hazard").value;
  const qs = new URLSearchParams();
  if (hazard) qs.set("hazard_class", hazard);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#chem-table tbody");
  tbody.innerHTML = "";
  for (const c of data.chemicals) {
    const tr = document.createElement("tr");
    const hazardClass = c.hazard_class === "flammable" || c.hazard_class === "explosive" || c.hazard_class === "toxic" ? "red" : "";
    tr.innerHTML = `
      <td>${c.chem_ref}</td>
      <td>${c.name}</td>
      <td style="font-family:var(--font);font-size:12px;font-variant-numeric:tabular-nums">${c.cas_number || "-"}</td>
      <td class="${hazardClass}"><strong>${c.hazard_class || "-"}</strong></td>
      <td>${c.supplier || "-"}</td>
      <td>${c.site_name || "-"}</td>
      <td>${c.storage_location || "-"}</td>
      <td>${statusBadge(c.sds_status)} ${c.sds_attachment_id ? `<a href="/attachments/api/serve/${c.sds_attachment_id}" target="_blank">PDF</a>` : ""}</td>
      <td><button class="btn btn-sm" data-id="${c.id}" onclick="openSdsModal(this)">SDS</button></td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("chem-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    e.target.reset();
    loadChemicals();
  } else {
    alert(data.message || "Failed to register");
  }
});

document.getElementById("f-hazard").addEventListener("change", loadChemicals);

/* SDS upload + review modal */
let currentChemId = null;
let currentExtraction = null;

function openSdsModal(btn) {
  currentChemId = btn.dataset.id;
  currentExtraction = null;
  document.getElementById("sds-file").value = "";
  document.getElementById("sds-review-date").value = "";
  document.getElementById("sds-preview").innerHTML = "<p class=\"text-muted\">Upload an SDS PDF to see extracted fields.</p>";
  document.getElementById("sds-apply-form").classList.add("hidden");
  document.getElementById("sds-modal").classList.remove("hidden");
}

function closeSdsModal() {
  document.getElementById("sds-modal").classList.add("hidden");
}

document.getElementById("sds-upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = new FormData(form);
  body.append("sds_review_date", document.getElementById("sds-review-date").value);
  const resp = await fetch(`${API}/${currentChemId}/sds-upload`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    currentExtraction = data.extraction.fields || {};
    renderSdsPreview(currentExtraction);
    document.getElementById("sds-apply-form").classList.remove("hidden");
  } else {
    document.getElementById("sds-preview").innerHTML = `<p class="text-danger">${data.message || "Extraction failed"}</p>`;
  }
});

function renderSdsPreview(fields) {
  const container = document.getElementById("sds-preview");
  if (!fields || Object.keys(fields).length === 0) {
    container.innerHTML = "<p class=\"text-muted\">No fields extracted.</p>";
    return;
  }
  const rows = Object.entries(fields).map(([k, v]) => {
    let display = v;
    if (Array.isArray(v)) display = v.join(", ");
    return `<tr><td style="font-weight:600;text-transform:capitalize">${k.replace(/_/g, " ")}</td><td>${display || "-"}</td></tr>`;
  }).join("");
  container.innerHTML = `<table class="data-table"><tbody>${rows}</tbody></table>`;

  // Pre-fill apply form from extracted values
  const map = {
    "supplier": "a-supplier",
    "cas_number": "a-cas",
  };
  if (fields.cas_number) document.getElementById("a-cas").value = fields.cas_number;
  if (fields.manufacturer) document.getElementById("a-supplier").value = fields.manufacturer;
  if (fields.supplier) document.getElementById("a-supplier").value = fields.supplier;
}

document.getElementById("sds-apply-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  body.append("extracted_json", JSON.stringify(currentExtraction || {}));
  const resp = await fetch(`${API}/${currentChemId}/sds-apply`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    closeSdsModal();
    loadChemicals();
  } else {
    alert(data.message || "Failed to apply SDS");
  }
});

loadSites();
loadChemicals();
