/* Leading indicators ("sites to watch") module JS - vanilla (guide C3). */
const API = "/leading-indicators/api";

function bandClass(band) {
  if (band === "Red") return "red";
  if (band === "Amber") return "amber";
  return "";
}

async function loadSitesToWatch() {
  const resp = await fetch(`${API}/sites-to-watch`);
  const data = await resp.json();
  const tbody = document.querySelector("#watch-table tbody");
  tbody.innerHTML = "";
  for (const s of data.sites) {
    const tr = document.createElement("tr");
    const passRate = s.inspection_pass_rate === null ? "no data" : `${s.inspection_pass_rate}%`;
    tr.innerHTML = `
      <td>${s.rank}</td>
      <td>${s.site_name}</td>
      <td>${(s.near_miss_ratio * 100).toFixed(0)}%</td>
      <td>${s.overdue_cas}</td>
      <td>${passRate}</td>
      <td class="${bandClass(s.band)}">${s.band}</td>
      <td><button class="btn btn-sm" type="button" data-site-id="${s.id}" data-site-name="${s.site_name}">Explain</button></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-site-id]").forEach((btn) => {
    btn.addEventListener("click", () => explainSite(btn.dataset.siteId, btn.dataset.siteName));
  });
}

async function explainSite(siteId, siteName) {
  const card = document.getElementById("explain-card");
  const nameEl = document.getElementById("explain-site-name");
  const textEl = document.getElementById("explain-text");
  card.style.display = "block";
  nameEl.textContent = siteName;
  textEl.textContent = "Thinking...";
  const resp = await fetch(`${API}/explain/${siteId}`, { method: "POST" });
  const data = await resp.json();
  textEl.textContent = data.ok ? data.explanation : (data.message || "Could not generate an explanation");
}

loadSitesToWatch();
