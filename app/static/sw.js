// Service worker mínimo: deixa o app instalável e abrir rápido.
// Estratégia "rede primeiro": busca sempre a versão atual e só usa o cache se estiver sem internet.
// (Antes era "cache primeiro", e quem já tinha instalado o app ficava preso num CSS antigo.)
const CACHE = 'trilha-de-fe-v4';
const SHELL = ['/static/css/style.css', '/static/app.js', '/static/logo.svg', '/static/manifest.json', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

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
  if (event.request.method !== 'GET' || !SHELL.includes(url.pathname)) return;
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const copia = resp.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copia));
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
