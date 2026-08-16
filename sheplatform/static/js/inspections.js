/* Inspections module JS - vanilla. */
const API = "/inspections/api";
let currentInspectionId = null;

async function loadUsers() {
  const resp = await fetch("/capa/api/users");
  const data = await resp.json();
  const sel = document.getElementById("inspector-select");
  sel.innerHTML = "";
  for (const u of (data.users || [])) {
    const opt = document.createElement("option");
    opt.value = u.id;
    opt.textContent = `${u.first_name || ""} ${u.last_name || ""} (${u.role_key})`;
    sel.appendChild(opt);
  }
}

async function loadInspections() {
  const status = document.getElementById("f-status").value;
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#insp-table tbody");
  tbody.innerHTML = "";
  for (const i of data.inspections) {
    const tr = document.createElement("tr");
    let btns = "";
    if (i.status === "scheduled") {
      btns += `<button class="btn btn-sm" onclick="startInspection(${i.id})">Start</button>`;
    }
    if (i.status === "in_progress") {
      btns += `<button class="btn btn-sm btn-primary" onclick="runInspection(${i.id}, '${i.title}', '${i.inspection_type}')">Run checklist</button>`;
    }
    tr.innerHTML = `
      <td>${i.inspection_ref}</td>
      <td>${i.title}</td>
      <td>${i.inspection_type}</td>
      <td class="site-cell"></td>
      <td>${i.site_location || "-"}</td>
      <td style="font-family:var(--mono);font-size:12px">${i.scheduled_date ? i.scheduled_date.slice(0, 10) : "-"}</td>
      <td>${i.inspector_email || "-"}</td>
      <td><strong>${i.status}</strong></td>
      <td>${btns || "-"}</td>`;
    tr.querySelector(".site-cell").textContent = i.site_name
      ? `${i.site_code} — ${i.site_name}`
      : "Unlinked";
    tbody.appendChild(tr);
  }
}

async function startInspection(id) {
  const resp = await fetch(`${API}/${id}/start`, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) alert(data.message || "Failed");
  loadInspections();
}

async function runInspection(id, title, type) {
  currentInspectionId = id;
  const resp = await fetch(`${API}/checklist?inspection_type=${type}`);
  const data = await resp.json();
  const box = document.getElementById("checklist-items");
  box.innerHTML = "";
  for (const item of data.items) {
    const div = document.createElement("div");
    div.style.cssText = "margin:8px 0;display:flex;align-items:center;gap:10px;flex-wrap:wrap";
    div.innerHTML = `
      <span style="flex:1;min-width:200px">${item}</span>
      <select class="result-select" style="width:110px">
        <option value="pass">Pass</option>
        <option value="fail">Fail</option>
        <option value="na">N/A</option>
      </select>
      <input type="text" class="result-comment" placeholder="Comment" style="flex:1;min-width:140px">`;
    div.dataset.item = item;
    box.appendChild(div);
  }
  document.getElementById("run-title").textContent = `Run inspection: ${title}`;
  document.getElementById("run-card").style.display = "block";
  document.getElementById("run-card").scrollIntoView({ behavior: "smooth" });
}

async function completeInspection() {
  const results = [];
  document.querySelectorAll("#checklist-items > div").forEach((div) => {
    results.push({
      item: div.dataset.item,
      result: div.querySelector(".result-select").value,
      comment: div.querySelector(".result-comment").value,
    });
  });
  const fd = new FormData();
  fd.append("findings", document.getElementById("findings").value);
  fd.append("results_json", JSON.stringify(results));
  const resp = await fetch(`${API}/${currentInspectionId}/complete`, { method: "POST", body: fd });
  const data = await resp.json();
  if (data.ok) {
    const capaMsg = data.capa_created.length ? ` - created CAPA: ${data.capa_created.join(", ")}` : "";
    alert(`Inspection completed${capaMsg}`);
    document.getElementById("run-card").style.display = "none";
    document.getElementById("findings").value = "";
    loadInspections();
  } else {
    alert(data.message || "Failed to complete");
  }
}

document.getElementById("insp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    e.target.reset();
    loadInspections();
  } else {
    alert(data.message || "Failed to schedule");
  }
});

document.getElementById("f-status").addEventListener("change", loadInspections);
loadUsers();
loadInspections();
