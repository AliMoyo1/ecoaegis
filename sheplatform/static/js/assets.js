/* Analytics-first Asset Assurance workspace. */
(function () {
  "use strict";

  const API = "/assets/api";
  const state = { assets: [], maintenance: [] };
  const canManage = Boolean(document.getElementById("asset-form"));

  function textElement(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value == null ? "" : String(value);
    return element;
  }

  function appendCell(row, value, className) {
    const cell = textElement("td", className || "", value);
    row.appendChild(cell);
    return cell;
  }

  function replaceChildren(container, ...children) {
    if (container) container.replaceChildren(...children);
  }

  function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = String(value);
  }

  function titleCase(value) {
    return String(value || "Unknown")
      .replace(/_/g, " ")
      .replace(/\b\w/g, letter => letter.toUpperCase());
  }

  function numberValue(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function formatHours(value) {
    return numberValue(value).toLocaleString(undefined, { maximumFractionDigits: 1 });
  }

  function formatDate(value) {
    if (!value) return "Not recorded";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleDateString();
  }

  function showFeedback(message, tone) {
    const feedback = document.getElementById("asset-feedback");
    if (!feedback) return;
    feedback.textContent = message;
    feedback.dataset.tone = tone || "success";
    feedback.hidden = false;
    window.setTimeout(() => { feedback.hidden = true; }, 4200);
  }

  async function requestJSON(url, options) {
    const response = await fetch(url, options);
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "The asset workspace could not complete this request.");
    }
    return data;
  }

  function emptyTableRow(columns, message) {
    const row = document.createElement("tr");
    const cell = appendCell(row, message, "table-empty");
    cell.colSpan = columns;
    return row;
  }

  function statusChip(status) {
    const normalized = String(status || "unknown").toLowerCase();
    const chip = textElement("span", "status-chip", titleCase(normalized));
    chip.classList.add(normalized === "active" ? "status-active" : normalized === "maintenance" ? "status-pending" : "status-other");
    return chip;
  }

  function updateSummary() {
    const dueAssetIds = new Set(state.maintenance.map(task => Number(task.asset_id)));
    const total = state.assets.length;
    const due = dueAssetIds.size;
    const unscheduled = state.assets.filter(asset => !dueAssetIds.has(Number(asset.id)) && !numberValue(asset.service_interval_hours)).length;
    const ready = Math.max(0, total - due - unscheduled);
    const active = state.assets.filter(asset => String(asset.status || "active").toLowerCase() === "active").length;
    const reporting = state.assets.filter(asset => numberValue(asset.total_run_hours) > 0).length;
    const readiness = total ? Math.round((ready / total) * 100) : 0;

    setText("asset-total-count", total);
    setText("asset-active-count", active);
    setText("asset-maintenance-count", state.maintenance.length);
    setText("asset-reporting-count", reporting);
    setText("asset-health-score", readiness + "%");
    setText("asset-ready-count", ready);
    setText("asset-due-count", due);
    setText("asset-unscheduled-count", unscheduled);

    const donut = document.getElementById("asset-health-donut");
    if (donut) {
      if (!total) {
        donut.style.background = "conic-gradient(rgba(115, 123, 135, .28) 0 100%)";
      } else {
        const readyEnd = (ready / total) * 100;
        const dueEnd = readyEnd + (due / total) * 100;
        donut.style.background = `conic-gradient(#3bc58c 0 ${readyEnd}%, #e8aa3b ${readyEnd}% ${dueEnd}%, #65728a ${dueEnd}% 100%)`;
      }
    }
  }

  function renderAssetMix() {
    const container = document.getElementById("asset-mix-bars");
    if (!container) return;
    const counts = new Map();
    state.assets.forEach(asset => {
      const key = String(asset.asset_type || "other");
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    if (!counts.size) {
      const empty = textElement("div", "command-empty", "");
      empty.append(textElement("span", "", "◇"), textElement("p", "", "Asset mix will appear when records are available."));
      replaceChildren(container, empty);
      return;
    }

    const largest = Math.max(...counts.values());
    const rows = Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1])
      .map(([type, count]) => {
        const row = document.createElement("div");
        row.className = "asset-mix-row";
        const label = textElement("span", "", titleCase(type));
        const track = document.createElement("span");
        track.className = "asset-mix-track";
        const fill = document.createElement("i");
        fill.style.width = `${Math.max(8, (count / largest) * 100)}%`;
        track.appendChild(fill);
        row.append(label, track, textElement("strong", "", count));
        return row;
      });
    replaceChildren(container, ...rows);
  }

  function renderMaintenancePreview() {
    const container = document.getElementById("asset-maintenance-preview");
    if (!container) return;
    if (!state.maintenance.length) {
      const empty = textElement("div", "command-empty", "");
      empty.append(textElement("span", "", "✓"), textElement("p", "", "No open maintenance actions."));
      replaceChildren(container, empty);
      return;
    }

    const cards = state.maintenance.slice(0, 4).map(task => {
      const card = document.createElement("article");
      card.className = "asset-maintenance-item premium-hover";
      const meta = document.createElement("div");
      meta.className = "asset-maintenance-meta";
      meta.append(textElement("span", "", task.asset_ref || "Asset"), textElement("time", "", formatDate(task.created_at)));
      card.append(meta, textElement("strong", "", task.asset_name || "Unnamed asset"), textElement("p", "", task.reason || "Maintenance action requires review."));
      return card;
    });
    replaceChildren(container, ...cards);
  }

  function servicePositionCell(row, asset) {
    const cell = appendCell(row, "", "asset-service-cell");
    const total = numberValue(asset.total_run_hours);
    const baseline = numberValue(asset.hours_at_last_service);
    const interval = numberValue(asset.service_interval_hours);
    const since = Math.max(0, total - baseline);
    if (!interval) {
      cell.appendChild(textElement("span", "asset-service-unscheduled", "No interval set"));
      return;
    }
    const value = textElement("span", "", `${formatHours(since)} / ${formatHours(interval)} h`);
    const track = document.createElement("span");
    track.className = "asset-service-track";
    const fill = document.createElement("i");
    fill.style.width = `${Math.min(100, (since / interval) * 100)}%`;
    if (since >= interval) fill.className = "is-due";
    track.appendChild(fill);
    cell.append(value, track);
  }

  function renderAssets() {
    const tableBody = document.querySelector("#asset-table tbody");
    if (!tableBody) return;
    const query = document.getElementById("asset-search")?.value.trim().toLowerCase() || "";
    const status = document.getElementById("asset-status-filter")?.value || "";
    const filtered = state.assets.filter(asset => {
      const haystack = `${asset.asset_ref || ""} ${asset.name || ""} ${asset.asset_type || ""}`.toLowerCase();
      return (!query || haystack.includes(query)) && (!status || String(asset.status || "active").toLowerCase() === status);
    });

    setText("asset-result-count", `${filtered.length} ${filtered.length === 1 ? "asset" : "assets"}`);
    if (!filtered.length) {
      replaceChildren(tableBody, emptyTableRow(6, query || status ? "No assets match the current view." : "No assets have been registered."));
      return;
    }

    const rows = filtered.map(asset => {
      const row = document.createElement("tr");
      appendCell(row, asset.asset_ref || "Not set", "asset-ref-cell");
      const identity = appendCell(row, "", "asset-identity-cell");
      identity.append(textElement("strong", "", asset.name || "Unnamed asset"), textElement("small", "", asset.install_date ? `Installed ${formatDate(asset.install_date)}` : "Install date not recorded"));
      appendCell(row, titleCase(asset.asset_type));
      appendCell(row, formatHours(asset.total_run_hours), "data-value");
      servicePositionCell(row, asset);
      const statusCell = appendCell(row, "");
      statusCell.appendChild(statusChip(asset.status || "active"));
      return row;
    });
    replaceChildren(tableBody, ...rows);
  }

  async function completeMaintenance(taskId, button) {
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Completing...";
    try {
      await requestJSON(`${API}/maintenance/${taskId}/complete`, { method: "POST" });
      showFeedback("Maintenance action completed and the service baseline was reset.", "success");
      await loadWorkspace();
    } catch (error) {
      showFeedback(error.message, "error");
      button.disabled = false;
      button.textContent = previous;
    }
  }

  function renderMaintenance() {
    const tableBody = document.querySelector("#maintenance-table tbody");
    if (!tableBody) return;
    setText("maintenance-result-count", `${state.maintenance.length} open ${state.maintenance.length === 1 ? "action" : "actions"}`);
    if (!state.maintenance.length) {
      replaceChildren(tableBody, emptyTableRow(4, "No open maintenance actions."));
      return;
    }

    const rows = state.maintenance.map(task => {
      const row = document.createElement("tr");
      const asset = appendCell(row, "", "asset-identity-cell");
      asset.append(textElement("strong", "", task.asset_name || "Unnamed asset"), textElement("small", "", task.asset_ref || "Reference not set"));
      appendCell(row, task.reason || "Maintenance action requires review.");
      appendCell(row, formatDate(task.created_at), "data-value");
      const action = appendCell(row, "");
      if (canManage) {
        const button = textElement("button", "btn btn-sm", "Mark complete");
        button.type = "button";
        button.addEventListener("click", () => completeMaintenance(task.id, button));
        action.appendChild(button);
      } else {
        action.appendChild(textElement("span", "data-value", "View only"));
      }
      return row;
    });
    replaceChildren(tableBody, ...rows);
  }

  function renderWorkspace() {
    updateSummary();
    renderAssetMix();
    renderMaintenancePreview();
    renderAssets();
    renderMaintenance();
  }

  async function loadWorkspace() {
    try {
      const [assetData, maintenanceData] = await Promise.all([
        requestJSON(`${API}/list`),
        requestJSON(`${API}/maintenance?status=open`),
      ]);
      state.assets = Array.isArray(assetData.assets) ? assetData.assets : [];
      state.maintenance = Array.isArray(maintenanceData.tasks) ? maintenanceData.tasks : [];
      renderWorkspace();
    } catch (error) {
      showFeedback(error.message, "error");
    }
  }

  document.getElementById("asset-search")?.addEventListener("input", renderAssets);
  document.getElementById("asset-status-filter")?.addEventListener("change", renderAssets);
  document.querySelectorAll("[data-select-tab]").forEach(button => {
    button.addEventListener("click", () => document.getElementById(button.dataset.selectTab)?.click());
  });

  document.getElementById("asset-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await requestJSON(`${API}/create`, { method: "POST", body: new FormData(form) });
      form.reset();
      form.closest("[data-input-tray]")?.querySelector("[data-input-tray-close]")?.click();
      showFeedback("Asset registered successfully.", "success");
      await loadWorkspace();
    } catch (error) {
      showFeedback(error.message, "error");
    } finally {
      submit.disabled = false;
    }
  });

  document.getElementById("api-key-form")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    const box = document.getElementById("api-key-result");
    submit.disabled = true;
    try {
      const data = await requestJSON(`${API}/api-keys`, { method: "POST", body: new FormData(form) });
      box.hidden = false;
      box.textContent = `${data.api_key} | Send this value in the X-Asset-API-Key header.`;
      form.reset();
      showFeedback("Telemetry key created. Copy it before closing the tray.", "success");
    } catch (error) {
      box.hidden = false;
      box.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  loadWorkspace();
})();
