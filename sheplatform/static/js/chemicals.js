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
      <td style="font-family:var(--mono);font-size:12px">${c.cas_number || "-"}</td>
      <td class="${hazardClass}"><strong>${c.hazard_class || "-"}</strong></td>
      <td>${c.supplier || "-"}</td>
      <td>${c.site_name || "-"}</td>
      <td>${c.storage_location || "-"}</td>
      <td>${c.sds_path ? `<a href="/${c.sds_path}" target="_blank">view</a>` : "-"}</td>`;
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
loadSites();
loadChemicals();
