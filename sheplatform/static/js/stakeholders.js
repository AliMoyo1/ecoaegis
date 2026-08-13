/* Stakeholder module JS - vanilla. */
const API = "/stakeholders/api";

async function loadStakeholders() {
  const resp = await fetch(`${API}/stakeholders`);
  const data = await resp.json();
  const tbody = document.querySelector("#stakeholder-table tbody");
  tbody.innerHTML = "";
  for (const s of data.stakeholders) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${s.name}</td>
      <td>${s.category}</td>
      <td>${s.contact_person || "-"}</td>
      <td>${s.phone || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("stakeholder-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/stakeholders`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadStakeholders();
  } else {
    alert(data.message || "Failed to register stakeholder");
  }
});

loadStakeholders();
