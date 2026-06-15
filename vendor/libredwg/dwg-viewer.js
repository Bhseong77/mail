/* =====================================================================
 * dwg-viewer.js  —  브라우저 DWG 미리보기 (libredwg-web, 서버 불필요)
 * ---------------------------------------------------------------------
 * 검증: 23,848개 웨이퍼 파일 ~1.65s, 사내망 OK (jsdelivr 아님, 자체호스팅)
 *
 * 사용 전제:
 *   /mail/vendor/libredwg/libredwg-web.js
 *   /mail/vendor/libredwg/libredwg-web.wasm   ← 같은 폴더 (셀프테스트와 동일)
 *
 * 공개 API (window.DwgViewer):
 *   await DwgViewer.ready()                      → 라이브러리 1회 로드(캐시)
 *   await DwgViewer.toSvg(arrayBuffer)           → { svg, bbox, ms, types }
 *   await DwgViewer.previewInto(container, buf)  → 컨테이너에 렌더+팬/줌, {ms,types}
 *   DwgViewer.SUPPORTED                          → 렌더되는 엔티티 타입 Set
 * ===================================================================== */
(function () {
  'use strict';

  // 모듈/wasm 위치. 페이지 기준 상대경로. 필요시 배포 전 한 줄만 수정.
  var MODULE_URL = './vendor/libredwg/libredwg-web.js';

  // svgConverter가 실제로 그리는 타입 (소스 lib/svg/svgConverter.js 기준 실측)
  var SUPPORTED = new Set([
    'ARC', 'CIRCLE', 'DIMENSION', 'ELLIPSE', 'INSERT', 'LINE',
    'LWPOLYLINE', 'MTEXT', 'SPLINE', 'RAY', 'TABLE', 'TEXT', 'XLINE'
  ]);
  // 조용히 누락되는 타입 (참고용): HATCH, SOLID, LEADER, MLINE, IMAGE,
  // ATTDEF, POINT, OLE2FRAME, POLYLINE …  → 채움/일부 라벨 안 보일 수 있음

  var _libPromise = null;   // LibreDwg 인스턴스 (1회 생성 후 재사용)
  var _Dwg = null;          // { LibreDwg, Dwg_File_Type }

  // 라이브러리 1회 로드 + create(). 브라우저에선 create() 인자 불필요
  // (import.meta.url 이 vendor/libredwg/ 라서 wasm 같은 폴더에서 자동 로드)
  function ready() {
    if (_libPromise) return _libPromise;
    _libPromise = (async function () {
      var mod = await import(MODULE_URL);
      _Dwg = { LibreDwg: mod.LibreDwg, Dwg_File_Type: mod.Dwg_File_Type };
      return await mod.LibreDwg.create();
    })().catch(function (e) {
      _libPromise = null; // 실패 시 재시도 가능하게
      throw e;
    });
    return _libPromise;
  }

  // ArrayBuffer/Uint8Array → { svg, bbox, ms, types }
  async function toSvg(buf) {
    var lib = await ready();
    var bytes = (buf instanceof Uint8Array) ? buf : new Uint8Array(buf);
    var t0 = performance.now();
    var dwg = lib.dwg_read_data(bytes, _Dwg.Dwg_File_Type.DWG);
    var types = {};
    try {
      var db = lib.convert(dwg);
      // 엔티티 타입 히스토그램 (진단/배지용)
      (db.entities || []).forEach(function (e) {
        var ty = (e && e.type) || '?';
        types[ty] = (types[ty] || 0) + 1;
      });
      var out = lib.dwg_to_svg(db); // { bbox, element(=SVG string) }
      var ms = Math.round(performance.now() - t0);
      return { svg: out.element, bbox: out.bbox, ms: ms, types: types };
    } finally {
      try { lib.dwg_free(dwg); } catch (_) {}
    }
  }

  // 컨테이너에 SVG 주입 + 팬/줌/맞춤. 기존 미리보기 영역 그대로 재사용.
  async function previewInto(container, buf) {
    var res = await toSvg(buf);
    container.innerHTML = '';

    var wrap = document.createElement('div');
    wrap.style.cssText =
      'position:relative;width:100%;height:100%;overflow:hidden;' +
      'background:#0e0f12;touch-action:none;cursor:grab;';
    wrap.innerHTML = res.svg;
    var svg = wrap.querySelector('svg');
    if (svg) {
      svg.style.cssText =
        'position:absolute;left:0;top:0;transform-origin:0 0;' +
        'width:100%;height:100%;';
      // 도면 선 보이도록 기본 stroke (libredwg svg는 currentColor 사용)
      svg.style.color = '#d6dae0';
    }
    container.appendChild(wrap);

    // ----- 팬/줌 -----
    var scale = 1, tx = 0, ty = 0, dragging = false, sx = 0, sy = 0;
    function apply() {
      if (svg) svg.style.transform =
        'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    }
    wrap.addEventListener('wheel', function (e) {
      e.preventDefault();
      var r = wrap.getBoundingClientRect();
      var px = e.clientX - r.left, py = e.clientY - r.top;
      var f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      var ns = Math.min(60, Math.max(0.02, scale * f));
      // 커서 기준 줌
      tx = px - (px - tx) * (ns / scale);
      ty = py - (py - ty) * (ns / scale);
      scale = ns; apply();
    }, { passive: false });
    wrap.addEventListener('mousedown', function (e) {
      dragging = true; sx = e.clientX - tx; sy = e.clientY - ty;
      wrap.style.cursor = 'grabbing';
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      tx = e.clientX - sx; ty = e.clientY - sy; apply();
    });
    window.addEventListener('mouseup', function () {
      dragging = false; wrap.style.cursor = 'grab';
    });
    apply();

    return { ms: res.ms, types: res.types, bbox: res.bbox };
  }

  window.DwgViewer = {
    ready: ready,
    toSvg: toSvg,
    previewInto: previewInto,
    SUPPORTED: SUPPORTED,
    set moduleUrl(u) { MODULE_URL = u; }
  };
})();
