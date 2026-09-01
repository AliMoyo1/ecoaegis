/* CAPA module JS - vanilla. */
const API = "/capa/api";

async function loadUsers() {
  const resp = await fetch("/capa/api/users");
  const data = await resp.json();
  const sel = document.getElementById("assignee-select");
  sel.innerHTML = "";
  for (const u of (data.users || [])) {
    const opt = document.createElement("option");
    opt.value = u.id;
    opt.textContent = `${u.first_name || ""} ${u.last_name || ""} (${u.email || u.role_key})`;
    sel.appendChild(opt);
  }
}

async function loadActions() {
  const status = document.getElementById("f-status").value;
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#capa-table tbody");
  tbody.innerHTML = "";
  for (const a of data.actions) {
    const tr = document.createElement("tr");
    const pclass = a.priority === "critical" ? "red" : (a.priority === "high" ? "amber" : "");
    let btns = "";
    if (a.status === "open" || a.status === "overdue") {
      btns += `<button class="btn btn-sm" onclick="act(${a.id},'start')">Start</button> `;
    }
    if (a.status === "in_progress" || a.status === "overdue") {
      btns += `<button class="btn btn-sm btn-primary" onclick="act(${a.id},'complete')">Complete</button> `;
    }
    if (a.status === "completed") {
      btns += `<button class="btn btn-sm" onclick="act(${a.id},'verify')">Verify</button>`;
    }
    if (!btns) btns = "-";
    tr.innerHTML = `
      <td>${a.action_ref}</td>
      <td>${a.title}</td>
      <td>${a.source_type}${a.source_id ? " #" + a.source_id : ""}</td>
      <td class="${pclass}">${a.priority}</td>
      <td>${a.assignee_first || ""} ${a.assignee_last || ""}</td>
      <td style="font-family:var(--font);font-size:12px;font-variant-numeric:tabular-nums">${a.due_date ? a.due_date.slice(0, 10) : "-"}</td>
      <td><strong>${a.status}</strong></td>
      <td>${a.verifier_email || "-"}</td>
      <td>${btns}</td>`;
    tbody.appendChild(tr);
  }
}

async function act(id, action) {
  const note = action === "verify" ? prompt("Verification note (optional):") : "";
  if (note === null) return;
  const fd = new FormData();
  if (note) fd.append("note", note);
  const resp = await fetch(`${API}/${id}/${action}`, { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) alert(data.message || "Action failed");
  loadActions();
}

document.getElementById("capa-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = new FormData(form);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadActions();
  } else {
    alert(data.message || "Failed to create action");
  }
});

document.getElementById("f-status").addEventListener("change", loadActions);
loadUsers();
loadActions();
