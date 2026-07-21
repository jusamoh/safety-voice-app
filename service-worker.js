const CACHE_VERSION = 'global-interpreter-v1';
const APP_SHELL = [
  '/',
  '/style.css',
  '/i18n.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png'
];
const CACHEABLE_PATHS = new Set(APP_SHELL);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_VERSION).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_VERSION).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) return;
  const containsAccessToken = url.searchParams.has('room_token') || url.searchParams.has('token');

  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok && !containsAccessToken && !url.search && CACHEABLE_PATHS.has(url.pathname)) {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        if (request.mode === 'navigate') return caches.match('/');
        throw new Error('Offline resource unavailable');
      })
  );
});
