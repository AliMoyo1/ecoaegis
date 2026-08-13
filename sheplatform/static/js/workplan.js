/* Workplan module JS - vanilla. */
const API = "/workplan/api";

async function loadWorkplans() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#workplan-table tbody");
  tbody.innerHTML = "";
  for (const w of data.workplans) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${w.plan_ref}</td>
      <td>${w.fiscal_year}</td>
      <td>${w.status}</td>
      <td>${w.preventive_pct === null ? "-" : w.preventive_pct + "%"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("workplan-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/create`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadWorkplans();
  } else {
    alert(data.message || "Failed to create workplan");
  }
});

loadWorkplans();
