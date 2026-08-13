/* Permits module JS - vanilla. */
const API = "/permits/api";

async function loadPermits() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#permit-table tbody");
  tbody.innerHTML = "";
  for (const p of data.permits) {
    const tr = document.createElement("tr");
    let actions = "-";
    if (p.status === "pending_approval" && p.pending_step) {
      const role = p.pending_step.role_required.replace(/_/g, " ");
      actions = `<span style="font-size:11px;color:var(--muted)">awaiting: ${role}</span> ` +
                `<button class="btn btn-sm btn-primary" onclick="approvePermit(${p.id}, ${p.pending_step.id}, 'approved')">Approve</button> ` +
                `<button class="btn btn-sm" onclick="approvePermit(${p.id}, ${p.pending_step.id}, 'rejected')">Reject</button>`;
    }
    tr.innerHTML = `
      <td>${p.permit_ref}</td>
      <td>${p.title}</td>
      <td>${p.permit_type}</td>
      <td>${p.vendor_id || "-"}</td>
      <td><strong>${p.status}</strong></td>
      <td>${p.valid_until ? p.valid_until.slice(0, 10) : "-"}</td>
      <td>${actions}</td>`;
    tbody.appendChild(tr);
  }
}

async function approvePermit(permitId, stepId, decision) {
  const comments = decision === "rejected" ? (prompt("Rejection reason:") || "rejected") : "";
  if (comments === null) return;
  const fd = new FormData();
  fd.append("step_id", stepId);
  fd.append("decision", decision);
  fd.append("comments", comments);
  const resp = await fetch(`${API}/${permitId}/approve`, { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) {
    alert(data.message || "Approval failed");
  } else if (data.result.complete) {
    alert(`Permit ${data.permit.permit_ref} ${data.permit.status === "active" ? "ACTIVATED" : data.permit.status}`);
  }
  loadPermits();
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
