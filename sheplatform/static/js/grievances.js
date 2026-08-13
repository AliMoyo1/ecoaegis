/* Grievances module JS - vanilla. */
const API = "/grievances/api";

async function loadGrievances() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#grievance-table tbody");
  tbody.innerHTML = "";
  for (const g of data.grievances) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${g.case_ref}</td>
      <td>${g.description}</td>
      <td>${g.source_channel}</td>
      <td>${g.severity}</td>
      <td>${g.status}</td>
      <td>${g.complainant_notified ? "Yes" : "No"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("grievance-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/create`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadGrievances();
  } else {
    alert(data.message || "Failed to log complaint");
  }
});

loadGrievances();
