/* Permit workspace rendering from tenant-scoped API records. */
(function () {
  "use strict";
  const API = "/permits/api";
  const statusFilter = document.getElementById("permit-status-filter");
  const feedback = document.getElementById("permit-feedback");
  let permits = [];

  const roleLabels = {
    line_manager: "Supervisor",
    she_officer: "SHE officer",
    she_manager: "SHE manager",
    she_hod: "Site manager",
  };
  const workflowRoles = ["line_manager", "she_officer", "she_manager", "she_hod"];

  function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = String(value);
  }

  function textElement(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value == null || value === "" ? "-" : String(value);
    return element;
  }

  function formatStatus(status) {
    return String(status || "unknown").replace(/_/g, " ");
  }

  function formatType(type) {
    return String(type || "general").replace(/_/g, " ");
  }

  function formatDate(value) {
    if (!value) return "Not set";
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf())
      ? String(value).slice(0, 10)
      : new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", year: "numeric" }).format(parsed);
  }

  function statusClass(status) {
    if (status === "active") return "status-active";
    if (status === "pending_approval") return "status-pending";
    if (["closed", "revoked", "cancelled", "expired"].includes(status)) return "status-terminal";
    return "status-other";
  }

  function statusPill(status) {
    const pill = textElement("span", "status-chip " + statusClass(status), formatStatus(status));
    return pill;
  }

  function showFeedback(message, tone) {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.dataset.tone = tone || "info";
    feedback.hidden = false;
    window.clearTimeout(showFeedback.timer);
    showFeedback.timer = window.setTimeout(() => { feedback.hidden = true; }, 5000);
  }

  function countsFor(records) {
    const counts = { total: records.length, active: 0, pending: 0, terminal: 0, other: 0 };
    records.forEach(record => {
      if (record.status === "active") counts.active += 1;
      else if (record.status === "pending_approval") counts.pending += 1;
      else if (["closed", "revoked", "cancelled", "expired"].includes(record.status)) counts.terminal += 1;
      else counts.other += 1;
    });
    return counts;
  }

  function renderSummary() {
    const counts = countsFor(permits);
    setText("permit-awaiting-count", counts.pending);
    setText("permit-total-count", counts.total);
    setText("permit-active-count", counts.active);
    setText("permit-pending-count", counts.pending);
    setText("permit-terminal-count", counts.terminal);
    setText("permit-donut-total", counts.total);
    setText("permit-legend-active", counts.active);
    setText("permit-legend-pending", counts.pending);
    setText("permit-legend-terminal", counts.terminal);
    setText("permit-legend-other", counts.other);

    const total = counts.total || 1;
    const activeEnd = counts.active / total * 360;
    const pendingEnd = activeEnd + counts.pending / total * 360;
    const terminalEnd = pendingEnd + counts.terminal / total * 360;
    const donut = document.getElementById("permit-status-donut");
    if (donut) {
      donut.style.background = counts.total
        ? "conic-gradient(#3bc58c 0deg " + activeEnd + "deg, #e8aa3b " + activeEnd + "deg " + pendingEnd +
          "deg, #77808e " + pendingEnd + "deg " + terminalEnd + "deg, #3568d4 " + terminalEnd + "deg 360deg)"
        : "conic-gradient(rgba(155,161,170,.2) 0deg 360deg)";
    }
  }

  function buildWorkflow(record) {
    const workflow = document.createElement("div");
    workflow.className = "permit-workflow";
    const pendingOrder = Number(record.pending_step?.step_order || 0);
    workflowRoles.forEach((role, index) => {
      const order = index + 1;
      const stage = document.createElement("div");
      stage.className = "permit-workflow-stage";
      let state = "waiting";
      if (record.status === "active" || record.status === "closed") state = "done";
      else if (record.status === "revoked") state = "blocked";
      else if (pendingOrder && order < pendingOrder) state = "done";
      else if (pendingOrder === order) state = "current";
      stage.dataset.state = state;
      stage.append(
        textElement("i", "", ""),
        textElement("strong", "", roleLabels[role]),
        textElement("span", "", state === "done" ? "Approved" : state === "current" ? "Action required" : state === "blocked" ? "Stopped" : "Waiting"),
      );
      workflow.appendChild(stage);
    });
    return workflow;
  }

  async function approvePermit(record, decision) {
    let comments = "";
    if (decision === "rejected") {
      comments = window.prompt("Enter the rejection reason:") || "";
      if (!comments) return;
    }
    const form = new FormData();
    form.append("step_id", record.pending_step.id);
    form.append("decision", decision);
    form.append("comments", comments);
    const response = await fetch(API + "/" + record.id + "/approve", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      showFeedback(data.message || "The approval could not be recorded.", "error");
      return;
    }
    showFeedback("Permit " + record.permit_ref + " workflow updated.", "success");
    await loadPermits();
  }

  function buildPermitCard(record, priority) {
    const card = document.createElement("article");
    card.className = "permit-card premium-hover" + (priority ? " permit-card-priority" : "");
    const meta = document.createElement("div");
    meta.className = "permit-card-meta";
    meta.append(textElement("span", "", record.permit_ref + " | " + formatType(record.permit_type)), statusPill(record.status));
    card.append(meta, textElement("h3", "", record.title), textElement("p", "", record.description || record.site_location || "No description recorded."));
    if (priority) card.appendChild(buildWorkflow(record));
    const footer = document.createElement("footer");
    footer.appendChild(textElement("span", "", record.site_name ? record.site_code + " | " + record.site_name : record.site_location || "Site not linked"));
    if (record.status === "pending_approval" && record.pending_step) {
      const actions = document.createElement("span");
      actions.className = "permit-card-actions";
      const approve = textElement("button", "panel-link", "Approve");
      approve.type = "button";
      approve.addEventListener("click", () => approvePermit(record, "approved"));
      const reject = textElement("button", "panel-link panel-link-danger", "Reject");
      reject.type = "button";
      reject.addEventListener("click", () => approvePermit(record, "rejected"));
      actions.append(approve, reject);
      footer.appendChild(actions);
    } else {
      footer.appendChild(textElement("time", "", "Valid until " + formatDate(record.valid_until)));
    }
    card.appendChild(footer);
    return card;
  }

  function renderCards() {
    const priorityMount = document.getElementById("permit-priority");
    const grid = document.getElementById("permit-card-grid");
    if (!priorityMount || !grid) return;
    priorityMount.replaceChildren();
    grid.replaceChildren();
    if (!permits.length) {
      const empty = document.createElement("div");
      empty.className = "command-empty";
      empty.append(textElement("span", "", "✓"), textElement("p", "", "No permit records are available yet."));
      priorityMount.appendChild(empty.cloneNode(true));
      grid.appendChild(empty);
      return;
    }
    const priority = permits.find(item => item.status === "pending_approval") || permits.find(item => item.status === "active") || permits[0];
    priorityMount.appendChild(buildPermitCard(priority, true));
    permits.filter(item => item.id !== priority.id).slice(0, 4).forEach(item => grid.appendChild(buildPermitCard(item, false)));
    if (permits.length === 1) {
      const note = textElement("div", "command-empty permit-queue-note", "This is the only permit currently in the register.");
      grid.appendChild(note);
    }
  }

  function appendCell(row, value, className) {
    const cell = textElement("td", className || "", value);
    row.appendChild(cell);
    return cell;
  }

  function renderTable() {
    const body = document.querySelector("#permit-table tbody");
    const empty = document.getElementById("permit-table-empty");
    if (!body) return;
    const selected = statusFilter?.value || "";
    const records = selected ? permits.filter(item => item.status === selected) : permits;
    body.replaceChildren();
    records.forEach(record => {
      const row = document.createElement("tr");
      appendCell(row, record.permit_ref, "permit-ref-cell");
      const permit = appendCell(row, "", "permit-title-cell");
      permit.append(textElement("strong", "", record.title), textElement("small", "", formatType(record.permit_type)));
      appendCell(row, record.site_name ? record.site_code + " | " + record.site_name : record.site_location || "Unlinked");
      appendCell(row, record.vendor_id || "-");
      const status = appendCell(row, "");
      status.appendChild(statusPill(record.status));
      appendCell(row, formatDate(record.valid_until));
      const workflow = appendCell(row, "");
      if (record.pending_step) {
        workflow.appendChild(textElement("span", "workflow-owner", "Awaiting " + (roleLabels[record.pending_step.role_required] || formatStatus(record.pending_step.role_required))));
      } else {
        workflow.appendChild(textElement("span", "workflow-owner", record.status === "active" ? "Approval complete" : formatStatus(record.status)));
      }
      body.appendChild(row);
    });
    if (empty) empty.hidden = records.length > 0;
  }

  async function loadPermits() {
    const response = await fetch(API + "/list");
    if (!response.ok) {
      showFeedback("Permit records could not be loaded.", "error");
      return;
    }
    const data = await response.json();
    permits = Array.isArray(data.permits) ? data.permits : [];
    renderSummary();
    renderCards();
    renderTable();
  }

  statusFilter?.addEventListener("change", renderTable);
  document.querySelectorAll("[data-select-tab]").forEach(button => {
    button.addEventListener("click", () => document.getElementById(button.dataset.selectTab)?.click());
  });

  const permitForm = document.getElementById("permit-form");
  permitForm?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('[type="submit"]');
    const data = new FormData(form);
    ["valid_from", "valid_until"].forEach(key => {
      const value = data.get(key);
      if (value) data.set(key, new Date(value).toISOString());
    });
    submit.disabled = true;
    submit.textContent = "Submitting...";
    try {
      const response = await fetch(API + "/create", { method: "POST", body: data });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        showFeedback((result.code ? result.code + ": " : "") + (result.message || "The permit could not be created."), "error");
        return;
      }
      form.reset();
      form.closest("[data-input-tray]")?.querySelector("[data-input-tray-close]")?.click();
      showFeedback("Permit " + result.permit.permit_ref + " entered the approval workflow.", "success");
      await loadPermits();
    } finally {
      submit.disabled = false;
      submit.textContent = "Submit for approval";
    }
  });

  loadPermits().catch(() => showFeedback("Permit records could not be loaded.", "error"));
})();
