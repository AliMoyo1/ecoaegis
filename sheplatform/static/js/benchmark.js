/* Benchmark module JS - vanilla. */
const API = "/benchmark/api";

async function loadBenchmark() {
  const resp = await fetch(`${API}/summary`);
  const data = await resp.json();
  document.getElementById("total-sites").textContent = data.total_sites;
  document.getElementById("red-sites").textContent = data.red_sites;
  document.getElementById("amber-sites").textContent = data.amber_sites;

  const tbody = document.querySelector("#bench-table tbody");
  tbody.innerHTML = "";
  for (const s of data.sites) {
    const tr = document.createElement("tr");
    const bandClass = s.band === "Red" ? "red" : (s.band === "Amber" ? "amber" : "");
    tr.innerHTML = `
      <td style="font-weight:700">#${s.rank}</td>
      <td>${s.site_name}</td>
      <td>${s.city || "-"}</td>
      <td>${s.site_type}</td>
      <td>${s.incidents}</td>
      <td>${s.observations}</td>
      <td>${s.overdue_inspections}</td>
      <td>${s.open_cas}</td>
      <td>${s.chemicals}</td>
      <td class="${bandClass}"><strong>${s.band}</strong></td>`;
    tbody.appendChild(tr);
  }
}

loadBenchmark();
