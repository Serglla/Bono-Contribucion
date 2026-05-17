/* Bonos Bomberos CDELU — Service Worker
 *
 * Estrategia:
 *   - Recursos /static/* (CSS/JS/íconos)        → cache-first (rápidos y casi nunca cambian)
 *   - CDNs (bootstrap, bootstrap-icons)         → stale-while-revalidate (sirve cache, refresca atrás)
 *   - Navegaciones (HTML, login, dashboards...) → network-first (siempre datos frescos; fallback offline)
 *   - POST / PUT / DELETE                       → NUNCA se cachean (pasan derecho a red)
 *
 * Para forzar a los clientes a tomar una versión nueva del SW, subir CACHE_VERSION.
 */

const CACHE_VERSION = "v2";
const STATIC_CACHE  = `bonos-static-${CACHE_VERSION}`;
const RUNTIME_CACHE = `bonos-runtime-${CACHE_VERSION}`;

// Precarga mínima: solo cosas que sí o sí queremos disponibles offline.
const PRECACHE_URLS = [
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
  "/manifest.webmanifest",
];

// Install: precachear los recursos críticos.
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

// Activate: limpiar caches viejos de versiones anteriores.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== RUNTIME_CACHE)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// Helpers ─────────────────────────────────────────────────────────────────────

function isCdnAsset(url) {
  return (
    url.hostname === "cdn.jsdelivr.net" ||
    url.hostname === "cdnjs.cloudflare.com"
  );
}

function isStaticAsset(url) {
  // Solo nuestros propios /static/* — NO cachear /auth/, /reportes/, etc.
  return url.origin === self.location.origin && url.pathname.startsWith("/static/");
}

function isNavigationRequest(request) {
  return (
    request.mode === "navigate" ||
    (request.method === "GET" &&
      request.headers.get("accept") &&
      request.headers.get("accept").includes("text/html"))
  );
}

// Fetch handler ───────────────────────────────────────────────────────────────

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 1) No tocar nada que no sea GET (POST/PUT/DELETE pasan derecho).
  if (request.method !== "GET") return;

  // 2) No cachear endpoints sensibles ni con query (?) — esos siempre van a red.
  //    Ejemplos: /auth/login, /backup/*, búsquedas con filtros, etc.
  if (
    url.pathname.startsWith("/auth/") ||
    url.pathname.startsWith("/backup/") ||
    url.pathname === "/service-worker.js"
  ) {
    return; // dejamos pasar a la red sin intervenir
  }

  // 3) Assets estáticos propios → cache-first.
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // 4) CDN (Bootstrap, iconos) → stale-while-revalidate.
  if (isCdnAsset(url)) {
    event.respondWith(staleWhileRevalidate(request, RUNTIME_CACHE));
    return;
  }

  // 5) Navegaciones HTML → network-first con fallback al último HTML cacheado.
  if (isNavigationRequest(request)) {
    event.respondWith(networkFirst(request, RUNTIME_CACHE));
    return;
  }

  // 6) Resto (APIs internas, fetch dinámico): network-first sin cachear agresivo.
  event.respondWith(
    fetch(request).catch(() => caches.match(request))
  );
});

// Strategies ──────────────────────────────────────────────────────────────────

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const fresh = await fetch(request);
    if (fresh.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, fresh.clone());
    }
    return fresh;
  } catch (e) {
    return cached || Response.error();
  }
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((resp) => {
      if (resp && resp.ok) cache.put(request, resp.clone());
      return resp;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await fetch(request);
    // Solo cacheamos respuestas OK de páginas (200) — evitamos guardar redirects de login.
    if (fresh.ok && fresh.type === "basic") {
      cache.put(request, fresh.clone());
    }
    return fresh;
  } catch (e) {
    const cached = await cache.match(request);
    if (cached) return cached;
    // Último recurso: pantalla mínima offline.
    return new Response(
      `<!doctype html><html lang="es"><meta charset="utf-8">
       <title>Sin conexión</title>
       <body style="font-family:system-ui;background:#1a2a4a;color:#fff;
              display:flex;align-items:center;justify-content:center;
              min-height:100vh;margin:0;text-align:center;padding:2rem;">
         <div>
           <h1 style="margin:0 0 .5rem;">Sin conexión</h1>
           <p style="opacity:.8;">No se pudo contactar al servidor.<br>
              Revisá tu internet e intentá de nuevo.</p>
         </div>
       </body></html>`,
      { headers: { "Content-Type": "text/html; charset=utf-8" }, status: 503 }
    );
  }
}
