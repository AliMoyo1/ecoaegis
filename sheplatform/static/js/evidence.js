/* Evidence vault JS - vanilla. */
const API = "/evidence/api";

async function loadEvidence() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#evidence-table tbody");
  tbody.innerHTML = "";
  for (const e of data.evidence) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${e.id}</td>
      <td>${e.original_name}</td>
      <td>${e.entity_type}#${e.entity_id}</td>
      <td>${e.file_size} bytes</td>
      <td>${(e.file_hash || "").slice(0, 16)}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("evidence-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const resp = await fetch(`${API}/upload`, { method: "POST", body: fd });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadEvidence();
  } else {
    alert(data.message || "Upload failed");
  }
});

loadEvidence();
