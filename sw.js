/* ENJET T 프로젝트 서비스워커
   목적: PWA 설치 가능 조건 충족(fetch 핸들러).
   전략: 네트워크 우선(network-first). 대시보드는 항상 최신이어야 하므로
        캐시는 오프라인 폴백 용도로만 최소 사용. 오래된 화면이 뜨는 걸 방지.   */
const CACHE = "enjet-t-v1";

self.addEventListener("install", (e) => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  // GET 만 처리, API/그래프/외부는 그대로 통과
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // 같은 출처(앱 셸)만 캐시 폴백 대상
  const sameOrigin = url.origin === self.location.origin;

  e.respondWith(
    fetch(req)
      .then((res) => {
        if (sameOrigin && res && res.status === 200) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req))   // 오프라인이면 마지막 캐시
  );
});
