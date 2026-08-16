/* Emergency module JS - vanilla. */
const API = "/emergency/api";

async function loadEvents() {
  const resp = await fetch(`${API}/events`);
  const data = await resp.json();
  const tbody = document.querySelector("#emergency-table tbody");
  tbody.innerHTML = "";
  for (const e of data.emergencies) {
    const tr = document.createElement("tr");
    const safe = e.site_safe_certificate ? "Yes" : "No";
    tr.innerHTML = `
      <td>${e.event_ref}</td>
      <td>${e.title}</td>
      <td class="site-cell"></td>
      <td>${e.site_location || "-"}</td>
      <td>${e.severity}</td>
      <td>${e.status}</td>
      <td>${safe}</td>`;
    tr.querySelector(".site-cell").textContent = e.site_name
      ? `${e.site_code} — ${e.site_name}`
      : "Unlinked";
    tbody.appendChild(tr);
  }
}

document.getElementById("emergency-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/events`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadEvents();
  } else {
    alert(data.message || "Failed to log emergency");
  }
});

loadEvents();
