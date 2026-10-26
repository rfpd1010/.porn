const VERSION = 'v2';
const PERMITIDOS = ['github.com', 'githubusercontent.com'];

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

const permitido = host => PERMITIDOS.some(d => host === d || host.endsWith('.' + d));

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  if (!permitido(u.hostname)) return;

  // peticiones "frescas" (con ?t=) van siempre a la red
  if (u.searchParams.has('t')) return;

  e.respondWith((async () => {
    const hit = await caches.match(req, { ignoreSearch: true });
    if (hit) return hit;
    try {
      const res = await fetch(req);
      const ok = res.status === 200 || res.type === 'opaque';
      if (ok) {
        const nombre = (res.type === 'opaque' || /\/releases\//.test(u.pathname))
          ? 'media-' + VERSION : 'app-' + VERSION;
        const c = await caches.open(nombre);
        c.put(req, res.clone());
      }
      return res;
    } catch (err) {
      const otra = await caches.match(req, { ignoreSearch: true });
      if (otra) return otra;
      throw err;
    }
  })());
});
