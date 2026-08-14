/* Asset register module JS - vanilla (guide C4). */
const API = "/assets/api";

async function loadAssets() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#asset-table tbody");
  tbody.innerHTML = "";
  for (const a of data.assets) {
    const sinceService = (a.total_run_hours - a.hours_at_last_service).toFixed(1);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${a.asset_ref}</td>
      <td>${a.name}</td>
      <td>${a.asset_type.replace(/_/g, " ")}</td>
      <td>${a.total_run_hours}</td>
      <td>${sinceService}${a.service_interval_hours ? ` / ${a.service_interval_hours}` : ""}</td>
      <td>${a.status}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadMaintenance() {
  const resp = await fetch(`${API}/maintenance?status=open`);
  const data = await resp.json();
  const tbody = document.querySelector("#maintenance-table tbody");
  tbody.innerHTML = "";
  for (const t of data.tasks) {
    const tr = document.createElement("tr");
    const completeBtn = document.getElementById("asset-form")
      ? `<button class="btn btn-sm" onclick="completeMaintenance(${t.id})">Mark complete</button>`
      : "";
    tr.innerHTML = `
      <td>${t.asset_name} (${t.asset_ref})</td>
      <td>${t.reason}</td>
      <td>${new Date(t.created_at).toLocaleDateString()}</td>
      <td>${completeBtn}</td>`;
    tbody.appendChild(tr);
  }
}

async function completeMaintenance(taskId) {
  const resp = await fetch(`${API}/maintenance/${taskId}/complete`, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) { alert(data.message || "Could not complete"); return; }
  loadMaintenance();
  loadAssets();
}

document.getElementById("asset-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (!data.ok) { alert(data.message || "Could not register asset"); return; }
  e.target.reset();
  loadAssets();
});

document.getElementById("api-key-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/api-keys`, { method: "POST", body });
  const data = await resp.json();
  const box = document.getElementById("api-key-result");
  box.style.display = "block";
  if (!data.ok) { box.textContent = data.message || "Could not create key"; return; }
  box.textContent = `${data.api_key} (send as X-Asset-API-Key header)`;
  e.target.reset();
});

loadAssets();
loadMaintenance();
