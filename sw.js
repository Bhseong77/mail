// ENJET PWA 서비스워커
// 전략: 네트워크 우선(Network-first) — 온라인이면 항상 최신을 가져오고,
//       오프라인일 때만 마지막 캐시로 폴백. (자주 배포 + 🔄캐시삭제 흐름을 깨지 않음)
const CACHE = "enjet-pwa-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;                  // POST 등(Graph/MSAL)은 그대로 통과
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (url.origin !== self.location.origin) return;   // 동일 출처만 처리(CDN·Graph 제외)

  e.respondWith(
    fetch(req)
      .then((res) => {
        try {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        } catch (_) {}
        return res;
      })
      .catch(() =>
        caches.match(req).then((m) => m || (req.mode === "navigate" ? caches.match("./") : undefined))
      )
  );
});
