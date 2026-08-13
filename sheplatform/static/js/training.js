/* Training module JS - vanilla. */
const API = "/training/api";

async function loadNeeds() {
  const resp = await fetch(`${API}/needs`);
  const data = await resp.json();
  const tbody = document.querySelector("#need-table tbody");
  tbody.innerHTML = "";
  for (const n of data.needs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${n.need_ref}</td>
      <td>${n.title}</td>
      <td>${n.source_trigger}</td>
      <td>${n.delivery_method}</td>
      <td>${n.status}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("need-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/needs`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadNeeds();
  } else {
    alert(data.message || "Failed to add training need");
  }
});

loadNeeds();
