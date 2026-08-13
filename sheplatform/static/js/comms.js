/* External comms module JS - vanilla. */
const API = "/comms/api";

async function loadComms() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#comms-table tbody");
  tbody.innerHTML = "";
  for (const c of data.comms) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${c.comms_ref}</td>
      <td>${c.concern_description}</td>
      <td>${c.target_segment || "-"}</td>
      <td>${c.medium}</td>
      <td>${c.status}</td>
      <td>${c.hod_approved ? "Yes" : "No"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("comms-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/create`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadComms();
  } else {
    alert(data.message || "Failed to save draft");
  }
});

loadComms();
