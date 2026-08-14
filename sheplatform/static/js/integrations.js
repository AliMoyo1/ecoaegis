document.addEventListener("DOMContentLoaded", () => {
  async function api(url, opts = {}) {
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || `Error ${res.status}`);
      throw new Error(data.detail || `Error ${res.status}`);
    }
    return res.status === 204 ? null : res.json();
  }

  async function loadEndpoints() {
    const data = await api("/integrations/api/endpoints");
    const tbody = document.querySelector("#endpoints-table tbody");
    tbody.innerHTML = "";
    (data.endpoints || []).forEach((ep) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${ep.endpoint_key}</td><td>${ep.name}</td><td>${ep.system_type}</td><td>${ep.direction}</td><td>${ep.base_url || ""}</td><td>${ep.auth_type}</td><td>${ep.active}</td>`;
      tbody.appendChild(tr);
    });
  }

  async function loadChannels() {
    const data = await api("/integrations/api/channels");
    const tbody = document.querySelector("#channels-table tbody");
    tbody.innerHTML = "";
    (data.channels || []).forEach((ch) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${ch.channel_key}</td><td>${ch.name}</td><td>${ch.authority}</td><td>${ch.channel_type}</td><td>${ch.active}</td>`;
      tbody.appendChild(tr);
    });
  }

  async function loadSubmissions() {
    const data = await api("/integrations/api/submissions");
    const tbody = document.querySelector("#submissions-table tbody");
    tbody.innerHTML = "";
    (data.submissions || []).forEach((s) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${s.id}</td><td>${s.report_id}</td><td>${s.channel_key}</td><td>${s.status}</td><td>${s.tracking_ref || ""}</td><td>${s.updated_at || ""}</td>`;
      tbody.appendChild(tr);
    });
  }

  document.querySelector("#endpoint-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      endpoint_key: form.endpoint_key.value,
      name: form.name.value,
      system_type: form.system_type.value,
      direction: form.direction.value,
      base_url: form.base_url.value,
      auth_type: form.auth_type.value,
    };
    await api("/integrations/api/endpoints", { method: "POST", body: JSON.stringify(body) });
    form.reset();
    loadEndpoints();
  });

  document.querySelector("#seed-channels").addEventListener("click", async () => {
    await api("/integrations/api/channels/seed", { method: "POST" });
    loadChannels();
  });

  document.querySelector("#process-queue").addEventListener("click", async () => {
    await api("/integrations/api/queue/process", { method: "POST" });
    loadSubmissions();
  });

  loadEndpoints();
  loadChannels();
  loadSubmissions();
});
