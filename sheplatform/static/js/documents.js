/* Document control JS - vanilla. */
const API = "/documents/api";

async function loadDocuments() {
  const docType = document.getElementById("f-type").value;
  const status = document.getElementById("f-status").value;
  const qs = new URLSearchParams();
  if (docType) qs.set("doc_type", docType);
  if (status) qs.set("status", status);
  const resp = await fetch(`${API}/list?${qs}`);
  const data = await resp.json();
  const tbody = document.querySelector("#doc-table tbody");
  tbody.innerHTML = "";
  for (const d of data.documents) {
    const tr = document.createElement("tr");
    let btns = "";
    if (d.status === "draft") {
      btns += `<button class="btn btn-sm" onclick="docAct(${d.id},'submit')">Submit review</button>`;
    }
    if (d.status === "in_review") {
      btns += `<button class="btn btn-sm btn-primary" onclick="docAct(${d.id},'approve')">Approve</button>`;
    }
    if (d.status === "approved") {
      btns += `<button class="btn btn-sm" onclick="docAct(${d.id},'acknowledge')">Acknowledge</button>`;
      btns += ` <button class="btn btn-sm" onclick="showAcks(${d.id})">Who hasn't read</button>`;
    }
    tr.innerHTML = `
      <td>${d.doc_ref}</td>
      <td>${d.title}</td>
      <td>${d.doc_type}</td>
      <td>${d.version}</td>
      <td><strong>${d.status}</strong></td>
      <td>${d.approver_email || "-"}</td>
      <td style="font-family:var(--mono);font-size:12px">${d.review_due_date ? d.review_due_date.slice(0, 10) : "-"}</td>
      <td>${d.ack_count}</td>
      <td>${btns || "-"}</td>`;
    tbody.appendChild(tr);
  }
}

async function docAct(id, action) {
  const resp = await fetch(`${API}/${id}/${action}`, { method: "POST" });
  const data = await resp.json();
  if (!data.ok) alert(data.message || "Action failed");
  else alert("Done");
  loadDocuments();
}

async function showAcks(id) {
  const resp = await fetch(`${API}/${id}/unacknowledged`);
  const data = await resp.json();
  const names = (data.users || []).map((u) => `${u.first_name} ${u.last_name} (${u.email})`).join("\n");
  alert(names ? `Not yet acknowledged:\n${names}` : "Everyone has acknowledged");
}

document.getElementById("doc-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const resp = await fetch(`${API}/create`, { method: "POST", body });
  const data = await resp.json();
  if (data.ok) {
    e.target.reset();
    loadDocuments();
  } else {
    alert(data.message || "Failed to register");
  }
});

document.getElementById("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = new FormData(e.target);
  const answerBox = document.getElementById("ask-answer");
  const answerText = document.getElementById("ask-answer-text");
  const sourcesBox = document.getElementById("ask-sources");
  answerBox.style.display = "block";
  answerText.textContent = "Thinking...";
  sourcesBox.textContent = "";
  const resp = await fetch(`${API}/ask`, { method: "POST", body });
  const data = await resp.json();
  if (!data.ok) {
    answerText.textContent = data.message || "Could not answer that question";
    return;
  }
  answerText.textContent = data.answer;
  sourcesBox.textContent = data.sources.length
    ? "Sources: " + data.sources.map((s) => `${s.doc_ref} (${s.title})`).join(", ")
    : "";
});

["f-type", "f-status"].forEach((id) => {
  document.getElementById(id).addEventListener("change", loadDocuments);
});
loadDocuments();
