// Mundo Mágico - service worker (cache offline)
const CACHE = 'mundo-magico-v2';
const ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './icon-512-maskable.png'
];
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  // network-first para o HTML/JS do app (pega versão nova quando online); cache-first para o resto
  const isAppShell = e.request.mode === 'navigate' || /\/(index\.html)?$/.test(new URL(e.request.url).pathname);
  if (isAppShell) {
    e.respondWith(
      fetch(e.request).then((resp) => {
        try { const copy = resp.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); } catch (_) {}
        return resp;
      }).catch(() => caches.match(e.request).then((r) => r || caches.match('./index.html')))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request).then((resp) => {
      try { const copy = resp.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); } catch (_) {}
      return resp;
    }).catch(() => null))
  );
});
