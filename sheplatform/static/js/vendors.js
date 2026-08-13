/* Vendors module JS - vanilla. */
const API = "/vendors/api";

async function loadVendors() {
  const resp = await fetch(`${API}/list`);
  const data = await resp.json();
  const tbody = document.querySelector("#vendor-table tbody");
  tbody.innerHTML = "";
  for (const v of data.vendors) {
    const tr = document.createElement("tr");
    const eligible = v.ptw_eligible ? "Yes" : `<strong class="red">NO</strong>`;
    tr.innerHTML = `
      <td>${v.vendor_ref}</td>
      <td>${v.company_name}</td>
      <td>${v.contact_person || "-"}</td>
      <td>${v.risk_profile}</td>
      <td>${eligible}</td>
      <td>${v.certification_status}</td>
      <td>${v.status}</td>`;
    tbody.appendChild(tr);
  }
}

document.getElementById("vendor-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const resp = await fetch(`${API}/create`, { method: "POST", body: new FormData(form) });
  const data = await resp.json();
  if (data.ok) {
    form.reset();
    loadVendors();
  } else {
    alert(data.message || "Failed to register vendor");
  }
});

loadVendors();
