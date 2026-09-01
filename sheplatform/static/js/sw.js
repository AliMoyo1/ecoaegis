/* EcoAegis service worker - caches public shell assets without persisting tenant pages. */
const CACHE_NAME = 'ecoAegis-shell-v4';
const SHELL_ASSETS = [
  '/static/css/app.css',
  '/static/js/theme-boot.js',
  '/static/js/shell.js',
  '/static/js/dashboard.js',
  '/static/js/permits.js',
  '/static/ui_foundation/foundation.css',
  '/static/ui_foundation/foundation.js',
  '/static/ui_foundation/assets/econet-wireless-logo.png',
  '/static/js/offline.js',
  '/static/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== 'GET') {
    return;
  }

  // Authenticated pages can contain tenant data. Keep them network-only and use
  // a generic offline response so one user's page is never replayed to another.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => new Response(
        'EcoAegis is offline. Reconnect to access your organization data.',
        {
          status: 503,
          headers: { 'Content-Type': 'text/plain; charset=utf-8' },
        },
      )),
    );
    return;
  }

  if (url.origin !== self.location.origin || !url.pathname.startsWith('/static/')) {
    return;
  }

  // Public same-origin static assets are safe to serve cache-first.
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((resp) => {
        if (request.method === 'GET' && resp.status === 200) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return resp;
      });
    })
  );
});
