/* The rack drawn as a topology diagram, in the look of a network-planning tool. A plain-JavaScript port of RT-EOS's
   client/src/components/TopologyDiagram.jsx (one rack, no cables: a sweep reads ports, not what they are cabled to):
   a clean rounded frame with a quiet title row and a thin unit ruler on the left; devices are white tiles at their units
   with a coloured mark on the left edge for their kind; empty units are faint dashed slots. Pinch or ctrl-scroll to zoom,
   drag to pan once zoomed, double tap to fit; the + / - / fit buttons do the same. Everything drawn comes from the data. */
(function () {
  'use strict';
  var KIND = { switch: ['#2f6bd8', 'SW'], router: ['#7a4fd0', 'RT'], firewall: ['#c43d3d', 'FW'], patch_panel: ['#cc8a0e', 'PP'], server: ['#7b848b', 'SV'],
    pdu: ['#e07a1f', 'PDU'], ups: ['#e07a1f', 'UPS'], gateway: ['#0f8a83', 'GT'], storage: ['#55606a', 'ST'], unknown: ['#cdd2d6', ''] };
  function kindKey(t) {
    var k = String(t == null ? '' : t).trim().toLowerCase().replace(/[\s-]+/g, '_');
    if (KIND[k]) return k;
    if (k.indexOf('patch') === 0) return 'patch_panel'; if (k.indexOf('storage') >= 0) return 'storage'; if (k.indexOf('gateway') >= 0) return 'gateway';
    if (k.indexOf('firewall') >= 0) return 'firewall'; if (k.indexOf('switch') >= 0) return 'switch'; if (k.indexOf('router') >= 0) return 'router'; if (k.indexOf('server') >= 0) return 'server';
    return 'unknown';
  }
  var CH = 0.6, ZMAX = 5;
  var clamp = function (v, lo, hi) { return Math.max(lo, Math.min(hi, v)); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  function wrapText(s, n) {
    var max = Math.max(3, Math.floor(n)), words = String(s == null ? '' : s).match(/[^\s\-_/,.]*[\s\-_/,.]?/g).filter(Boolean), lines = [], line = '';
    words.forEach(function (w) {
      while (w.length > max) { if (line) { lines.push(line.replace(/\s+$/, '')); line = ''; } lines.push(w.slice(0, max)); w = w.slice(max); }
      if ((line + w).replace(/\s+$/, '').length > max && line) { lines.push(line.replace(/\s+$/, '')); line = ''; }
      line += w;
    });
    if (line.trim()) lines.push(line.replace(/\s+$/, ''));
    return lines.length ? lines : [''];
  }
  function tileText(name, w, h, big) {
    var sizes = big ? [12.5, 11.5, 10.5] : [11.5, 10.5, 10], i, fs, lines;
    for (i = 0; i < sizes.length; i++) { fs = sizes[i]; lines = wrapText(name, w / (fs * CH)); if (lines.length * (fs + 2) <= h - 2) return { fs: fs, lines: lines }; }
    fs = sizes[sizes.length - 1]; var n = Math.max(3, Math.floor(w / (fs * CH))), s = String(name == null ? '' : name), tail = s.split(/[\s\-_/]+/).filter(Boolean).pop() || '';
    return { fs: fs, lines: [s.length <= n ? s : tail.length <= n ? tail : tail.slice(0, n - 1) + '…'] };
  }

  /* Where everything goes, from the data and the width alone. rack: { name, units, rows: [{unit, status}], devices: [{id, name, sub, kind, top, bottom}] } */
  function layoutOf(rack, W) {
    var small = W < 600, RULER = small ? 17 : 22, TITLE = small ? 28 : 32, PADB = 8, RIGHT = small ? 6 : 8;
    var rackW = clamp(W - 16, 150, 420), uh = clamp(rackW * 0.12, 20, 30), n = Math.max(1, rack.units || 1), top = 6;
    var floor = top + TITLE + n * uh + PADB, h = TITLE + n * uh + PADB, x = Math.max(0, (W - rackW) / 2);
    var uy = function (u) { return floor - PADB - u * uh; }, tx = x + RULER, tw = rackW - RULER - RIGHT;
    var blocks = [], used = {};
    rack.devices.forEach(function (d) {
      if (d.top == null) return; var t = Math.max(d.top, d.bottom == null ? d.top : d.bottom), b = Math.min(d.top, d.bottom == null ? d.top : d.bottom);
      for (var u = b; u <= t; u++) used[u] = 1;
      blocks.push({ d: d, x: tx, y: uy(t) + 1, w: tw, h: (t - b + 1) * uh - 2 });
    });
    var status = {}; (rack.rows || []).forEach(function (r) { status[r.unit] = r.status || 'free'; });
    var slots = [], run = null;
    for (var u = n; u >= 1; u--) {
      if (used[u]) { run = null; continue; }
      var st = status[u] || 'free';
      if (run && run.status === st) run.bottom = u; else { run = { top: u, bottom: u, status: st }; slots.push(run); }
    }
    slots = slots.map(function (s) { return { key: s.top + '-' + s.bottom, status: s.status, x: tx, y: uy(s.top) + 1, w: tw, h: (s.top - s.bottom + 1) * uh - 2, top: s.top, bottom: s.bottom }; });
    var step = uh >= 15 ? (n > 30 && small ? 2 : 1) : 5, ruler = [];
    for (u = 1; u <= n; u++) if (u === 1 || u % step === 0) ruler.push({ u: u, y: uy(u) + uh / 2 });
    return { small: small, W: Math.max(W, rackW + 16), H: floor + 8, x: x, y: floor - h, w: rackW, h: h, titleH: TITLE, blocks: blocks, slots: slots, ruler: ruler, rulerX: x + RULER - 5 };
  }

  var WORD = { free: 'free', unoccupied: 'free', occupied: 'full', uncertain: 'not sure' };
  function draw(L, rack, selected) {
    var small = L.small, out = [];
    out.push('<rect class="tframe" data-bg="" x="' + L.x + '" y="' + L.y + '" width="' + L.w + '" height="' + L.h + '" rx="12"/>');
    var fs = small ? 11.5 : 12.5, count = (rack.units || 1) + 'U', room = L.w - 24 - count.length * 10.5 * CH - 6, name = rack.name || 'Rack';
    var nfs = name.length * fs * CH <= room ? fs : Math.max(9.5, room / (name.length * CH)), ty = L.y + L.titleH / 2 + 1;
    out.push('<text class="trackname" x="' + (L.x + 12) + '" y="' + ty + '" font-size="' + nfs + '" dominant-baseline="central">' + esc(name) + '</text>');
    if (name.length * nfs * CH <= room) out.push('<text class="trackunits" x="' + (L.x + L.w - 12) + '" y="' + ty + '" text-anchor="end" dominant-baseline="central">' + count + '</text>');
    out.push('<line class="ttitlerule" x1="' + (L.x + 1) + '" x2="' + (L.x + L.w - 1) + '" y1="' + (L.y + L.titleH - 2) + '" y2="' + (L.y + L.titleH - 2) + '"/>');
    L.ruler.forEach(function (m) { out.push('<text class="truler" font-size="' + (small ? 8 : 9.5) + '" x="' + L.rulerX + '" y="' + m.y + '" text-anchor="end" dominant-baseline="central">' + m.u + '</text>'); });
    L.slots.forEach(function (s) {
      out.push('<g class="tslotg" data-status="' + esc(s.status) + '"><rect class="tslot" x="' + s.x + '" y="' + s.y + '" width="' + s.w + '" height="' + s.h + '" rx="6"/>'
        + (s.h >= 14 ? '<text class="tslotword" x="' + (s.x + s.w / 2) + '" y="' + (s.y + s.h / 2) + '" text-anchor="middle" dominant-baseline="central">' + esc(WORD[s.status] || s.status) + '</text>' : '') + '</g>');
    });
    L.blocks.forEach(function (b) {
      var d = b.d, kk = KIND[kindKey(d.kind)] || KIND.unknown, colour = kk[0], letters = kk[1];
      var showLetters = letters && b.w >= 120 && b.h >= 14, lettersW = showLetters ? letters.length * 8.5 * CH + 10 : 0, nx = b.x + 10, room = b.w - 14 - lettersW;
      var showSub = d.sub && b.h >= (small ? 34 : 38) && room >= 50, t = tileText(d.name, room, showSub ? b.h - 14 : b.h, !small), lh = t.fs + 2;
      var blockH = t.lines.length * lh + (showSub ? 13 : 0), ty2 = b.y + b.h / 2 - blockH / 2 + lh / 2, on = selected != null && String(selected) === String(d.id);
      out.push('<g class="tdev tap' + (on ? ' ton' : '') + '" role="button" tabindex="0" data-dev="' + esc(d.id) + '" aria-label="' + esc(d.aria || d.name) + '" aria-pressed="' + on + '">'
        + '<rect class="tblock" x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="' + Math.min(8, b.h / 2.6) + '"/>'
        + '<rect x="' + (b.x + 3) + '" y="' + (b.y + 3) + '" width="3" height="' + Math.max(2, b.h - 6) + '" rx="1.5" fill="' + colour + '"/>'
        + '<text class="tname" x="' + nx + '" y="' + ty2 + '" font-size="' + t.fs + '" dominant-baseline="central">' + t.lines.map(function (s, i) { return '<tspan x="' + nx + '" dy="' + (i ? lh : 0) + '">' + esc(s) + '</tspan>'; }).join('') + '</text>'
        + (showSub ? '<text class="tsub" x="' + nx + '" y="' + (ty2 + (t.lines.length - 1) * lh + 13) + '" dominant-baseline="central">' + esc(d.sub) + '</text>' : '')
        + (showLetters ? '<text class="tkind" x="' + (b.x + b.w - 8) + '" y="' + (b.y + Math.min(b.h / 2, 11)) + '" text-anchor="end" dominant-baseline="central" fill="' + colour + '">' + letters + '</text>' : '')
        + '</g>');
    });
    return out.join('');
  }

  /* The view over the drawing: zoom and the top-left corner of the viewBox. Pinch, drag, ctrl-scroll and double tap move it. */
  function attachView(svg, L) {
    var v = { z: 1, x: 0, y: 0 }, pts = {}, start = null, moved = false, lastTap = 0;
    function apply() { v.z = clamp(v.z, 1, ZMAX); v.x = clamp(v.x, 0, L.W - L.W / v.z); v.y = clamp(v.y, 0, L.H - L.H / v.z); svg.setAttribute('viewBox', v.x + ' ' + v.y + ' ' + (L.W / v.z) + ' ' + (L.H / v.z)); svg.style.touchAction = v.z > 1 ? 'none' : 'pan-y'; svg.dataset.zoom = v.z.toFixed(2); svg.dispatchEvent(new CustomEvent('zoomchange', { detail: v.z })); }
    function toPoint(cx, cy) { var r = svg.getBoundingClientRect(); return { x: v.x + ((cx - r.left) / r.width) * (L.W / v.z), y: v.y + ((cy - r.top) / r.height) * (L.H / v.z) }; }
    function zoomAt(z, px, py) { var nz = clamp(z, 1, ZMAX); v = { z: nz, x: px - ((px - v.x) * v.z) / nz, y: py - ((py - v.y) * v.z) / nz }; apply(); }
    function list() { return Object.keys(pts).map(function (k) { return pts[k]; }); }
    function begin(ev) { var l = list(); if (l.length === 1) start = { kind: 'pan', cx: l[0].x, cy: l[0].y, v: { z: v.z, x: v.x, y: v.y } };
      else if (l.length >= 2) { start = { kind: 'pinch', d: Math.hypot(l[0].x - l[1].x, l[0].y - l[1].y) || 1, v: { z: v.z, x: v.x, y: v.y }, anchor: toPoint((l[0].x + l[1].x) / 2, (l[0].y + l[1].y) / 2) }; moved = true; try { svg.setPointerCapture(ev.pointerId); } catch (e) {} } }
    svg.addEventListener('pointerdown', function (ev) { if (!Object.keys(pts).length) moved = false; pts[ev.pointerId] = { x: ev.clientX, y: ev.clientY }; begin(ev); });
    svg.addEventListener('pointermove', function (ev) {
      if (!pts[ev.pointerId] || !start) return; pts[ev.pointerId] = { x: ev.clientX, y: ev.clientY }; var r = svg.getBoundingClientRect();
      if (start.kind === 'pan') { var dx = ev.clientX - start.cx, dy = ev.clientY - start.cy; if (!moved && Math.hypot(dx, dy) < 6) return; if (start.v.z <= 1) return; if (!moved) { try { svg.setPointerCapture(ev.pointerId); } catch (e) {} } moved = true; var k = L.W / start.v.z / r.width; v = { z: start.v.z, x: start.v.x - dx * k, y: start.v.y - dy * k }; apply(); }
      else { var l = list(); if (l.length < 2) return; var z = clamp((start.v.z * Math.hypot(l[0].x - l[1].x, l[0].y - l[1].y)) / start.d, 1, ZMAX), mx = (l[0].x + l[1].x) / 2 - r.left, my = (l[0].y + l[1].y) / 2 - r.top; v = { z: z, x: start.anchor.x - (mx / r.width) * (L.W / z), y: start.anchor.y - (my / r.height) * (L.H / z) }; apply(); }
    });
    function up(ev) { if (!pts[ev.pointerId]) return; delete pts[ev.pointerId]; if (Object.keys(pts).length) { begin(ev); return; } start = null;
      if (!moved && ev.pointerType !== 'mouse') { var now = Date.now(); if (now - lastTap < 320) { v = { z: 1, x: 0, y: 0 }; apply(); lastTap = 0; } else lastTap = now; } }
    svg.addEventListener('pointerup', up); svg.addEventListener('pointercancel', function (ev) { delete pts[ev.pointerId]; if (!Object.keys(pts).length) start = null; });
    svg.addEventListener('click', function (ev) { if (moved) { ev.stopPropagation(); ev.preventDefault(); moved = false; } }, true);
    svg.addEventListener('dblclick', function () { v = { z: 1, x: 0, y: 0 }; apply(); });
    svg.addEventListener('wheel', function (ev) {
      if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); var p = toPoint(ev.clientX, ev.clientY); zoomAt(v.z * Math.exp(-ev.deltaY * 0.01), p.x, p.y); }
      else if (v.z > 1) { ev.preventDefault(); var r = svg.getBoundingClientRect(), k = L.W / v.z / r.width; v = { z: v.z, x: v.x + ev.deltaX * k, y: v.y + ev.deltaY * k }; apply(); }
    }, { passive: false });
    var by = function (f) { zoomAt(v.z * f, v.x + L.W / v.z / 2, v.y + L.H / v.z / 2); };
    apply();
    return { zoomIn: function () { by(1.5); }, zoomOut: function () { by(1 / 1.5); }, fit: function () { v = { z: 1, x: 0, y: 0 }; apply(); }, zoom: function () { return v.z; } };
  }

  /* render(el, rack, { selected, onDevice }): draws the rack into el and keeps it fitted to el's width. */
  function render(el, rack, opts) {
    opts = opts || {};
    var W = el.clientWidth || 360, L = layoutOf(rack, W), shownW = Math.min(W, L.W), shownH = (L.H * shownW) / L.W;
    el.innerHTML = '<svg class="tsvg" width="' + shownW + '" height="' + shownH + '" role="group" aria-label="' + esc(rack.name || 'Rack') + '" style="touch-action:pan-y">' + draw(L, rack, opts.selected) + '</svg>'
      + '<div class="tzoom"><button type="button" class="tzbtn" data-z="in" aria-label="Zoom in">+</button><button type="button" class="tzbtn" data-z="out" aria-label="Zoom out" disabled>&minus;</button><button type="button" class="tzbtn" data-z="fit" aria-label="Fit" disabled>&#8862;</button></div>';
    var svg = el.querySelector('svg'), view = attachView(svg, L), btns = el.querySelectorAll('.tzbtn');
    svg.addEventListener('zoomchange', function (e) { btns[0].disabled = e.detail >= ZMAX; btns[1].disabled = e.detail <= 1; btns[2].disabled = e.detail <= 1; });
    el.querySelector('.tzoom').addEventListener('click', function (e) { var b = e.target.closest('.tzbtn'); if (!b) return; ({ in: view.zoomIn, out: view.zoomOut, fit: view.fit })[b.dataset.z](); });
    if (opts.onDevice) {
      svg.addEventListener('click', function (e) { var g = e.target.closest('[data-dev]'); if (g) opts.onDevice(g.dataset.dev); });
      svg.addEventListener('keydown', function (e) { var g = e.target.closest('[data-dev]'); if (g && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); opts.onDevice(g.dataset.dev); } });
    }
    if (!el._ro && typeof ResizeObserver !== 'undefined') { el._ro = new ResizeObserver(function () { var w = el.clientWidth; if (w && Math.abs(w - el._w) > 2) { el._w = w; render(el, el._rack, el._opts); } }); el._ro.observe(el); }
    el._w = W; el._rack = rack; el._opts = opts;
  }
  window.RTTopology = { render: render, KIND: KIND, kindKey: kindKey };
})();
