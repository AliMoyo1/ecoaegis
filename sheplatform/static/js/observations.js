/* Observations module JS - vanilla. */
const API = "/observations/api";

async function loadObservations() {
  const status = document.getElementById("f-status").value;
  const severity = document.getElementById("f-severity").value;
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (severity) qs.set("severity", severity);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#obs-table tbody");
  tbody.innerHTML = "";
  for (const o of data.observations) {
    const tr = document.createElement("tr");
    const sevClass = o.severity === "critical" || o.severity === "high" ? "red" : "";
    let btns = "";
    if (o.status === "open") {
      btns += `<button class="btn btn-sm" onclick="obsAct(${o.id},'acknowledge')">Acknowledge</button> `;
      btns += `<button class="btn btn-sm btn-primary" onclick="obsAct(${o.id},'raise-capa')">Raise CAPA</button> `;
      btns += `<button class="btn btn-sm" onclick="obsAct(${o.id},'close')">Close</button>`;
    } else if (o.status === "acknowledged" || o.status === "corrective_action") {
      btns += `<button class="btn btn-sm" onclick="obsAct(${o.id},'close')">Close</button>`;
    }
    tr.innerHTML = `
      <td>${o.obs_ref}</td>
      <td>${o.obs_type}</td>
      <td>${o.title}</td>
      <td class="${sevClass}"><strong>${o.severity}</strong></td>
      <td>${o.reporter_email || "-"}</td>
      <td><strong>${o.status}</strong></td>
      <td>${btns || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

async function obsAct(id, action) {
  const fd = new FormData();
  if (action === "close") {
    const res = prompt("Resolution (optional):");
    if (res === null) return;
    fd.append("resolution", res);
  }
  const resp = await fetch(`${API}/${id}/${action}`, { method: "POST", body: fd });
  const data = await resp.json();
  if (!data.ok) alert(data.message || "Action failed");
  else if (data.capa_ref) alert(`CAPA created: ${data.capa_ref}`);
  loadObservations();
}

document.getElementById("obs-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    e.target.reset();
    loadObservations();
  } else {
    alert(data.message || "Failed to report");
  }
});

["f-status", "f-severity"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadObservations);
});

// Photo capture + vision classification
const photoForm = document.getElementById("photo-form");
const photoStatus = document.getElementById("photo-status");
const photoPreviewCard = document.getElementById("photo-preview-card");
const photoPreviewImg = document.getElementById("photo-preview-img");
const photoSuggestionBody = document.getElementById("photo-suggestion-body");

document.querySelector("[name='file']").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  photoPreviewImg.src = URL.createObjectURL(file);
  photoPreviewCard.style.display = "block";
  photoStatus.textContent = "Ready to classify";
});

photoForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(photoForm);
  const btn = document.getElementById("photo-submit");
  btn.disabled = true;
  photoStatus.textContent = "Analysing photo...";
  const resp = await fetch(`${API}/from-photo`, { method: "POST", body });
  const data = await resp.json();
  btn.disabled = false;
  if (!data.ok) {
    photoStatus.textContent = "Classification failed";
    alert(data.message || "Failed");
    return;
  }
  const s = data.vision;
  photoSuggestionBody.innerHTML = `
    <strong>${data.observation.obs_ref} - ${s.title}</strong><br>
    Type: ${s.obs_type} | Severity: ${s.severity}<br>
    ${s.description}<br>
    <em>Suggested controls:</em> ${(s.controls || []).join("; ") || "-"}`;
  photoStatus.textContent = "Observation created";
  photoForm.reset();
  loadObservations();
});

loadObservations();
