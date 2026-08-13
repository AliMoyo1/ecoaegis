/* EIA module JS - vanilla. */
const API = "/eia/api";

async function loadProjects() {
  const resp = await fetch(`${API}/projects`);
  const data = await resp.json();
  const tbody = document.querySelector("#eia-table tbody");
  tbody.innerHTML = "";
  for (const p of data.projects) {
    const tr = document.createElement("tr");
    const blocked = p.blocked ? `<strong class="red">BLOCKED</strong>` : "No";
    tr.innerHTML = `
      <td>${p.project_ref}</td>
      <td>${p.project_name}</td>
      <td>${p.status}</td>
      <td>${p.eia_required === null ? "-" : (p.eia_required ? "Yes" : "No")}</td>
      <td>${blocked}</td>
      <td>${p.ema_decision || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("eia-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/projects`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadProjects();
  } else {
    alert(data.message || "Failed to register project");
  }
});

loadProjects();
