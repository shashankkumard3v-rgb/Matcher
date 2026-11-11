// Service worker: lets phones install Matcher as an app and open it fast.
// - /api/... is never cached (your data always comes fresh from the server).
// - /assets/... files have a unique name per build and never change: served from the cache
//   instantly after the first visit.
// - Everything else (the page itself): network first, so updates show up right away;
//   the cached copy is only used when the server can't be reached.
const SHELL = "matcher-shell-v3";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/", "/manifest.webmanifest", "/icon-192.png"])));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (url.pathname.startsWith("/assets/")) {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); }
        return res;
      }))
    );
    return;
  }
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); }
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});
