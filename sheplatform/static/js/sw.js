/* EcoAegis service worker - caches shell assets and lets offline queue handle API fallback. */
const CACHE_NAME = 'ecoAegis-shell-v1';
const SHELL_ASSETS = [
  '/',
  '/dashboard',
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/js/offline.js',
  '/static/manifest.json',
  '/incidents',
  '/observations',
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

  // API writes: let offline.js queue handle retries; do not cache.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/incidents/api/') || url.pathname.startsWith('/observations/api/')) {
    return; // browser default fetch
  }

  // Static / page assets: cache-first with network fallback.
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((resp) => {
        if (request.method === 'GET' && resp.status === 200) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return resp;
      }).catch(() => {
        if (request.mode === 'navigate') {
          return caches.match('/dashboard') || caches.match('/');
        }
      });
    })
  );
});
