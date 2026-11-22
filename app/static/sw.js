// Service worker mínimo: cacheia o shell estático para abrir rápido e permitir instalação como PWA.
// Não cacheia páginas dinâmicas (login, devocional, quiz) para sempre mostrar o progresso atual.
const CACHE = 'devocional-shell-v1';
const SHELL = ['/static/css/style.css', '/static/manifest.json', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (SHELL.some((p) => url.pathname === p)) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
  }
});
