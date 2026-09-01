/* Compliance module JS - vanilla. */
const API = "/compliance/api";

async function loadUsers() {
  const resp = await fetch("/capa/api/users");
  const data = await resp.json();
  const sel = document.getElementById("owner-select");
  sel.innerHTML = "";
  for (const u of (data.users || [])) {
    const opt = document.createElement("option");
    opt.value = u.id;
    opt.textContent = `${u.first_name || ""} ${u.last_name || ""} (${u.role_key})`;
    sel.appendChild(opt);
  }
}

async function loadObligations() {
  const status = document.getElementById("f-status").value;
  const regulator = document.getElementById("f-regulator").value;
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (regulator) qs.set("regulator", regulator);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#obl-table tbody");
  tbody.innerHTML = "";
  for (const o of data.obligations) {
    const tr = document.createElement("tr");
    const dueClass = o.status === "overdue" ? "red" : "";
    let btns = "";
    if (o.status === "active" || o.status === "overdue") {
      btns += `<button class="btn btn-sm btn-primary" onclick="markCompliant(${o.id})">Mark compliant</button>`;
    }
    tr.innerHTML = `
      <td>${o.obligation_ref}</td>
      <td>${o.regulation}</td>
      <td>${o.obligation}</td>
      <td>${o.regulator}</td>
      <td>${o.owner_email || "-"}</td>
      <td>${o.frequency}</td>
      <td class="${dueClass}" style="font-family:var(--font);font-size:12px;font-variant-numeric:tabular-nums">${o.next_due_date ? o.next_due_date.slice(0, 10) : "-"}</td>
      <td><strong>${o.status}</strong></td>
      <td>${btns || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

async function markCompliant(id) {
  const evidence = prompt("Evidence reference (e.g. submission receipt no.):") || "";
  const fd = new FormData();
  fd.append("evidence", evidence);
  const resp = await fetch(`${API}/${id}/compliant`, { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) alert(data.message || "Failed");
  loadObligations();
}

document.getElementById("obl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    e.target.reset();
    loadObligations();
  } else {
    alert(data.message || "Failed to register");
  }
});

["f-status", "f-regulator"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadObligations);
});
loadUsers();
loadObligations();
