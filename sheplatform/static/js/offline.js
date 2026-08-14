/* EcoAegis offline queue + PWA helpers (B1). */
(function () {
  'use strict';

  const DB_NAME = 'ecoAegisOffline';
  const DB_VERSION = 1;
  const STORE = 'queue';
  const SYNC_URL = '/api/offline-sync';

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve(req.result);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const s = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
          s.createIndex('type', 'type', { unique: false });
          s.createIndex('idempotencyKey', 'idempotencyKey', { unique: false });
        }
      };
    });
  }

  async function enqueue(item) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const store = tx.objectStore(STORE);
      item.createdAt = new Date().toISOString();
      const req = store.add(item);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function drainQueue() {
    const db = await openDb();
    const items = await new Promise((resolve, reject) => {
      const out = [];
      const tx = db.transaction(STORE, 'readonly');
      const store = tx.objectStore(STORE);
      const req = store.openCursor();
      req.onsuccess = (e) => {
        const cur = e.target.result;
        if (cur) {
          out.push({ ...cur.value, _queueId: cur.primaryKey });
          cur.continue();
        } else {
          resolve(out);
        }
      };
      req.onerror = () => reject(req.error);
    });

    if (!items.length) return { processed: 0 };

    const payload = items.map(i => ({ type: i.type, data: i.data, idempotencyKey: i.idempotencyKey }));
    const headers = { 'Content-Type': 'application/json' };
    const csrf = getCsrfToken();
    if (csrf) headers['X-CSRF-Token'] = csrf;
    const resp = await fetch(SYNC_URL, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
    const result = await resp.json().catch(() => ({}));
    if (!result.ok) throw new Error(result.message || 'sync failed');

    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const store = tx.objectStore(STORE);
      items.forEach(i => store.delete(i._queueId));
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });

    return result;
  }

  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|; )she_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }

  function isOnline() {
    return navigator.onLine;
  }

  function updateIndicator() {
    const el = document.getElementById('offline-indicator');
    if (!el) return;
    if (navigator.onLine) {
      el.textContent = 'Online';
      el.classList.remove('offline');
      el.style.display = 'none';
    } else {
      el.textContent = 'Offline - reports will queue';
      el.classList.add('offline');
      el.style.display = 'block';
    }
  }

  async function flushIfOnline() {
    if (!navigator.onLine) return;
    try {
      await drainQueue();
    } catch (err) {
      console.warn('offline flush failed', err);
    }
  }

  // Public API
  window.OfflineQueue = {
    enqueue,
    drainQueue,
    flushIfOnline,
    uuid,
    isOnline,
    updateIndicator,
  };

  if (typeof window !== 'undefined') {
    window.addEventListener('online', () => {
      updateIndicator();
      flushIfOnline();
    });
    window.addEventListener('offline', updateIndicator);
    document.addEventListener('DOMContentLoaded', () => {
      updateIndicator();
      flushIfOnline();
    });
  }
})();
