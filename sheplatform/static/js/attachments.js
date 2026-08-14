// Generic attachments helper (guide 3.1)
// Usage: renderAttachments('incident', incidentId, document.getElementById('attachments'))

function createAttachmentEl(att) {
  const div = document.createElement('div');
  div.className = 'attachment-item';
  div.style.marginBottom = '0.5rem';

  if (att.mime_type && att.mime_type.startsWith('image/')) {
    const img = document.createElement('img');
    img.src = `/attachments/api/serve/${att.id}`;
    img.alt = att.original_name;
    img.style.maxWidth = '120px';
    img.style.maxHeight = '120px';
    img.style.borderRadius = '4px';
    div.appendChild(img);
  }

  const link = document.createElement('a');
  link.href = `/attachments/api/serve/${att.id}`;
  link.target = '_blank';
  link.textContent = ` ${att.original_name} (${formatBytes(att.size_bytes)})`;
  div.appendChild(link);

  if (att.ai_labels) {
    const labels = document.createElement('div');
    labels.className = 'ai-labels text-muted small';
    labels.textContent = JSON.stringify(att.ai_labels).slice(0, 200);
    div.appendChild(labels);
  }

  return div;
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

async function renderAttachments(entityType, entityId, mountEl) {
  if (!mountEl) return;
  mountEl.innerHTML = '<p>Loading attachments...</p>';
  try {
    const resp = await fetch(`/attachments/api/${entityType}/${entityId}`);
    const data = await resp.json();
    mountEl.innerHTML = '';
    if (!data.attachments || data.attachments.length === 0) {
      mountEl.innerHTML = '<p class="text-muted">No attachments yet.</p>';
      return;
    }
    data.attachments.forEach(att => mountEl.appendChild(createAttachmentEl(att)));
  } catch (err) {
    mountEl.innerHTML = '<p class="text-danger">Failed to load attachments.</p>';
  }
}

function wireAttachmentUpload(entityType, entityId, inputEl, mountEl, options = {}) {
  if (!inputEl) return;
  inputEl.addEventListener('change', async () => {
    const file = inputEl.files[0];
    if (!file) return;
    const kind = options.kind || 'file';
    const formData = new FormData();
    formData.append('file', file);
    formData.append('kind', kind);

    try {
      const resp = await fetch(`/attachments/api/${entityType}/${entityId}`, {
        method: 'POST',
        body: formData,
      });
      const data = await resp.json();
      if (data.ok) {
        inputEl.value = '';
        if (mountEl) renderAttachments(entityType, entityId, mountEl);
        if (options.onUpload) options.onUpload(data.attachment);
      } else {
        alert(data.message || 'Upload failed');
      }
    } catch (err) {
      alert('Upload failed: ' + err.message);
    }
  });
}
