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

// ══════════════════ Web Push (OS 알림) ══════════════════
// Flask 서버가 pywebpush 로 푸시 전송 → 여기서 OS 알림 표시.
// 앱이 닫혀있어도(백그라운드 SW) OS 가 깨워서 알림 띄움 (팀즈처럼).
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {
    try { data = { title: "ENJET", body: e.data ? e.data.text() : "" }; } catch (__) {}
  }
  const title = data.title || "ENJET 알림";
  const opts = {
    body: data.body || "",
    icon: data.icon || "pwa/icon-192.png",
    badge: data.badge || "pwa/icon-192.png",
    tag: data.tag || ("enjet-" + Date.now()),   // 같은 tag 면 덮어씀(중복 방지)
    renotify: !!data.renotify,
    requireInteraction: !!data.requireInteraction,  // true 면 사용자가 닫을때까지 유지
    data: { url: data.url || "/", ...(data.data || {}) },
  };
  if (data.image) opts.image = data.image;
  e.waitUntil(self.registration.showNotification(title, opts));
});

// 알림 클릭 → 해당 화면으로 (이미 열린 창 있으면 포커스, 없으면 새로 열기)
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) {
      // 이미 열린 PWA 창이 있으면 그쪽으로 포커스 + 메시지
      if ("focus" in c) {
        c.postMessage({ type: "notification-click", url: target });
        return c.focus();
      }
    }
    if (self.clients.openWindow) return self.clients.openWindow(target);
  })());
});
