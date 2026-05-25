/* Bonos Bomberos CDELU — Service Worker v3
 *
 * Estrategia de caché:
 *   - /static/*  (CSS/JS/íconos propios) → cache-first
 *   - CDN (bootstrap, bootstrap-icons)   → stale-while-revalidate
 *   - Navegaciones HTML                  → network-first + fallback offline
 *   - POST / PUT / DELETE de formularios → interceptados: si hay red, pasan normal;
 *                                          si no hay red, se encolan en IndexedDB
 *                                          y se reintentan con Background Sync.
 *
 * Para forzar actualización en clientes: subir CACHE_VERSION.
 */

const CACHE_VERSION = "v3";
const STATIC_CACHE  = `bonos-static-${CACHE_VERSION}`;
const RUNTIME_CACHE = `bonos-runtime-${CACHE_VERSION}`;
const SYNC_TAG      = "bonos-offline-queue";

// Páginas clave pre-cacheadas en background cuando el usuario está online.
const KEY_PAGES = [
  "/compradores/",
  "/reportes/",
  "/cobranza/",
  "/vendedores/",
];

// Endpoints de escritura que admiten cola offline.
const QUEUEABLE_PREFIXES = [
  "/cobranza/liquidacion/",
  "/vendedores/",
];

// Precarga mínima garantizada (sin auth necesaria).
const PRECACHE_URLS = [
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
  "/manifest.webmanifest",
];

// ─── IndexedDB helpers ────────────────────────────────────────────────────────

const DB_NAME    = "bonos-offline";
const DB_VERSION = 1;
const STORE_NAME = "queue";

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "id", autoIncrement: true });
        store.createIndex("timestamp", "timestamp");
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror   = (e) => reject(e.target.error);
  });
}

async function enqueueRequest(request) {
  const body    = await request.clone().text();
  const headers = {};
  request.headers.forEach((v, k) => { headers[k] = v; });
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    store.add({
      url: request.url, method: request.method,
      headers, body, timestamp: Date.now(), retries: 0,
    });
    tx.oncomplete = () => resolve();
    tx.onerror    = (e) => reject(e.target.error);
  });
}

async function getAllQueued() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req   = store.getAll();
    req.onsuccess = (e) => resolve(e.target.result || []);
    req.onerror   = (e) => reject(e.target.error);
  });
}

async function deleteQueued(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    store.delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror    = (e) => reject(e.target.error);
  });
}

async function countQueued() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx    = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req   = store.count();
    req.onsuccess = (e) => resolve(e.target.result || 0);
    req.onerror   = () => resolve(0);
  });
}

// ─── Notificar a todos los clientes abiertos ──────────────────────────────────

async function notifyClients(msg) {
  const clients = await self.clients.matchAll({ includeUncontrolled: true });
  clients.forEach((c) => c.postMessage(msg));
}

// ─── Install ──────────────────────────────────────────────────────────────────

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

// ─── Activate ─────────────────────────────────────────────────────────────────

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== STATIC_CACHE && k !== RUNTIME_CACHE)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

// ─── Message handler ──────────────────────────────────────────────────────────

self.addEventListener("message", (event) => {
  const { data } = event;
  if (!data) return;

  if (data.type === "PREFETCH_PAGES") {
    event.waitUntil(prefetchKeyPages());
  }

  if (data.type === "GET_PENDING_COUNT") {
    event.waitUntil(
      countQueued().then((count) => {
        if (event.source) {
          event.source.postMessage({ type: "PENDING_COUNT", count });
        }
      })
    );
  }

  if (data.type === "SYNC_NOW") {
    event.waitUntil(replayQueue());
  }
});

// ─── Background Sync ──────────────────────────────────────────────────────────

self.addEventListener("sync", (event) => {
  if (event.tag === SYNC_TAG) {
    event.waitUntil(replayQueue());
  }
});

// ─── Fetch handler ────────────────────────────────────────────────────────────

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // No tocar auth ni backup.
  if (
    url.pathname.startsWith("/auth/") ||
    url.pathname.startsWith("/backup/") ||
    url.pathname === "/service-worker.js"
  ) return;

  // POST/PUT/DELETE: solo interceptar endpoints encolables.
  if (request.method !== "GET") {
    const isQueueable = QUEUEABLE_PREFIXES.some((p) => url.pathname.startsWith(p));
    if (isQueueable) {
      event.respondWith(handleWriteRequest(request));
    }
    return;
  }

  // Assets propios /static/* → cache-first.
  if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // CDN → stale-while-revalidate.
  if (url.hostname === "cdn.jsdelivr.net" || url.hostname === "cdnjs.cloudflare.com") {
    event.respondWith(staleWhileRevalidate(request, RUNTIME_CACHE));
    return;
  }

  // Navegaciones HTML → network-first con fallback.
  const isNav =
    request.mode === "navigate" ||
    (request.method === "GET" && (request.headers.get("accept") || "").includes("text/html"));
  if (isNav) {
    event.respondWith(networkFirst(request, RUNTIME_CACHE));
    return;
  }

  // Resto → network con fallback a caché.
  event.respondWith(
    fetch(request).catch(() => caches.match(request))
  );
});

// ─── Estrategias de caché ─────────────────────────────────────────────────────

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
  const cache  = await caches.open(cacheName);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((resp) => { if (resp && resp.ok) cache.put(request, resp.clone()); return resp; })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await fetch(request);
    if (fresh.ok && fresh.type === "basic") {
      cache.put(request, fresh.clone());
    }
    return fresh;
  } catch (e) {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response(
      `<!doctype html><html lang="es"><meta charset="utf-8">
       <title>Sin conexion - Bonos CDELU</title>
       <meta name="viewport" content="width=device-width,initial-scale=1">
       <style>
         body{font-family:system-ui;background:#1a2a4a;color:#fff;
              display:flex;align-items:center;justify-content:center;
              min-height:100vh;margin:0;text-align:center;padding:2rem;}
         h1{margin:0 0 .5rem;}p{opacity:.8;line-height:1.6;}a{color:#ffc107;font-weight:600;}
       </style>
       <body>
         <div>
           <div style="font-size:3rem;margin-bottom:1rem;">&#128225;</div>
           <h1>Sin conexion</h1>
           <p>No hay conexion con el servidor.<br>Revisa tu internet e intenta de nuevo.</p>
           <p style="margin-top:1.5rem;"><a href="javascript:location.reload()">Reintentar</a></p>
         </div>
       </body></html>`,
      { headers: { "Content-Type": "text/html; charset=utf-8" }, status: 503 }
    );
  }
}

// ─── Manejar escrituras (POST a endpoints encolables) ─────────────────────────

async function handleWriteRequest(request) {
  const cloned = request.clone();
  try {
    const resp = await fetch(request);
    const count = await countQueued();
    notifyClients({ type: "PENDING_COUNT", count });
    return resp;
  } catch (err) {
    try {
      await enqueueRequest(cloned);
      const count = await countQueued();
      notifyClients({ type: "PENDING_COUNT", count });
      if (self.registration.sync) {
        await self.registration.sync.register(SYNC_TAG);
      }
    } catch (e) {
      console.warn("[SW] No se pudo encolar:", e);
    }
    return new Response(
      JSON.stringify({ ok: false, queued: true, message: "Sin conexion - cambios guardados localmente." }),
      { headers: { "Content-Type": "application/json" }, status: 202 }
    );
  }
}

// ─── Replay de cola offline ───────────────────────────────────────────────────

async function replayQueue() {
  const items = await getAllQueued();
  if (!items.length) return;

  let replayed = 0;
  let failed   = 0;

  for (const item of items) {
    try {
      const resp = await fetch(item.url, {
        method:  item.method,
        headers: item.headers,
        body:    item.body || undefined,
      });
      if (resp.ok || resp.redirected || (resp.status >= 200 && resp.status < 400)) {
        await deleteQueued(item.id);
        replayed++;
      } else {
        failed++;
      }
    } catch (e) {
      failed++;
    }
  }

  const remaining = await countQueued();
  notifyClients({ type: "SYNC_COMPLETE", replayed, failed, remaining });
}

// ─── Pre-carga de páginas clave ───────────────────────────────────────────────

async function prefetchKeyPages() {
  const cache = await caches.open(RUNTIME_CACHE);
  for (const page of KEY_PAGES) {
    try {
      const resp = await fetch(page, { credentials: "include" });
      if (resp.ok && resp.type === "basic") {
        await cache.put(page, resp);
      }
    } catch (e) {
      // Ignorar silenciosamente (sin red o 401).
    }
  }
  notifyClients({ type: "PREFETCH_DONE" });
}
