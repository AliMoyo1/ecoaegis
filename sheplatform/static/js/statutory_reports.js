/* Statutory reporting UI (B4) */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

let currentReport = null;
let templates = [];

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: {'Accept': 'application/json', ...(opts.headers || {})},
    ...opts
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return {ok: res.ok, status: res.status, json};
}

async function loadTemplates() {
  const r = await api('/statutory-reports/api/templates');
  if (!r.ok) return;
  templates = r.json.templates || [];
  const select = $('#sr-template');
  select.innerHTML = '';
  templates.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t.template_key;
    opt.textContent = `${t.authority.toUpperCase()} - ${t.title}`;
    select.appendChild(opt);
  });
}

async function loadReports() {
  const r = await api('/statutory-reports/api/reports');
  if (!r.ok) return;
  const tbody = $('#sr-list');
  tbody.innerHTML = '';
  (r.json.reports || []).forEach(rep => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${rep.report_ref}</td><td>${rep.title}</td>` +
      `<td>${(rep.period_start || '').slice(0,10)} - ${(rep.period_end || '').slice(0,10)}</td>` +
      `<td>${rep.status}</td>` +
      `<td><button class="btn small" data-id="${rep.id}">Open</button></td>`;
    tbody.appendChild(tr);
  });
  $$('#sr-list button').forEach(btn => {
    btn.addEventListener('click', () => openReport(btn.dataset.id));
  });
}

async function openReport(id) {
  const r = await api(`/statutory-reports/api/reports/${id}`);
  if (!r.ok) return;
  currentReport = r.json.report;
  $('#sr-editor').style.display = 'block';
  $('#sr-editor-ref').textContent = currentReport.report_ref;
  const fieldsDiv = $('#sr-fields');
  fieldsDiv.innerHTML = '';
  const tpl = templates.find(t => t.template_key === currentReport.template_key) || {};
  const data = currentReport.data || {};
  (tpl.fields || []).forEach(field => {
    const label = document.importNode($('#sr-field-template').content, true);
    $('.sr-field-name', label).textContent = field.label;
    const input = $('.sr-field-input', label);
    input.dataset.name = field.name;
    input.value = data[field.name] ?? '';
    input.disabled = currentReport.status !== 'draft';
    if (field.type === 'date') input.type = 'date';
    if (field.type === 'number') input.type = 'number';
    if (field.type === 'textarea') {
      const ta = document.createElement('textarea');
      ta.className = 'sr-field-input';
      ta.dataset.name = field.name;
      ta.value = data[field.name] ?? '';
      ta.disabled = currentReport.status !== 'draft';
      input.replaceWith(ta);
    }
    fieldsDiv.appendChild(label);
  });
  $('#sr-export-json').href = `/statutory-reports/api/reports/${id}/export.json`;
  $('#sr-export-txt').href = `/statutory-reports/api/reports/${id}/export.txt`;
}

function getFieldUpdates() {
  const updates = {};
  $$('#sr-fields .sr-field-input').forEach(el => {
    updates[el.dataset.name] = el.type === 'number' ? parseFloat(el.value) : el.value;
  });
  return updates;
}

async function saveReport() {
  if (!currentReport) return;
  const fd = new FormData();
  fd.append('updates_json', JSON.stringify(getFieldUpdates()));
  const r = await api(`/statutory-reports/api/reports/${currentReport.id}/update`, {
    method: 'POST', body: fd
  });
  if (r.ok) loadReports();
}

async function lockReport() {
  if (!currentReport) return;
  const r = await api(`/statutory-reports/api/reports/${currentReport.id}/lock`, {method: 'POST'});
  if (r.ok) openReport(currentReport.id);
}

async function submitReport() {
  if (!currentReport) return;
  const r = await api(`/statutory-reports/api/reports/${currentReport.id}/submit`, {method: 'POST'});
  if (r.ok) openReport(currentReport.id);
}

document.addEventListener('DOMContentLoaded', () => {
  loadTemplates();
  loadReports();

  $('#sr-form').addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const r = await api('/statutory-reports/api/reports', {method: 'POST', body: fd});
    if (r.ok) {
      e.target.reset();
      loadReports();
      openReport(r.json.report_id);
    }
  });

  $('#sr-save').addEventListener('click', saveReport);
  $('#sr-lock').addEventListener('click', lockReport);
  $('#sr-submit').addEventListener('click', submitReport);
});
