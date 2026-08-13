/* Permits module JS - vanilla. */
const API = "/permits/api";

async function loadPermits() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#permit-table tbody");
  tbody.innerHTML = "";
  for (const p of data.permits) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.permit_ref}</td>
      <td>${p.title}</td>
      <td>${p.permit_type}</td>
      <td>${p.vendor_id || "-"}</td>
      <td>${p.status}</td>
      <td>${p.valid_until || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("permit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  // convert datetime-local to ISO
  for (const key of ["valid_from", "valid_until"]) {
    const v = fd.get(key);
    if (v) fd.set(key, new Date(v).toISOString());
  }
  const resp = await fetch(`${API}/create`, { method: "POST", body: fd });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadPermits();
  } else {
    alert((data.code || "") + " " + (data.message || "Failed to create permit"));
  }
});

loadPermits();
