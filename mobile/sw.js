/* RackTrack service worker: the app shell is kept so the app opens at once; the API, the camera guide and the reports are always live. */
var SHELL = 'racktrack-shell-v2';
var FILES = ['/app/', '/app/index.html', '/app/app.css', '/app/app.js', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png', '/app/fonts/geist-latin.woff2', '/app/fonts/geist-mono-latin.woff2'];
self.addEventListener('install', function (e) { e.waitUntil(caches.open(SHELL).then(function (c) { return c.addAll(FILES); }).then(function () { return self.skipWaiting(); })); });
self.addEventListener('activate', function (e) { e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k !== SHELL; }).map(function (k) { return caches.delete(k); })); }).then(function () { return self.clients.claim(); })); });
self.addEventListener('fetch', function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  var shell = FILES.indexOf(url.pathname) >= 0;
  if (!shell) return;                                             // everything else: straight to the server, never cached here
  e.respondWith(fetch(e.request).then(function (r) { var c = r.clone(); caches.open(SHELL).then(function (cache) { cache.put(e.request, c); }); return r; })
    .catch(function () { return caches.match(e.request); }));
});
