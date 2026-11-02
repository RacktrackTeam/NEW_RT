import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import styles from './TopologyDiagram.module.css';

/*
 * The one data-drawn topology of the app, in the look of a network-planning
 * tool: one rack, or two racks side by side, each a clean rounded frame with
 * a quiet title row (name, unit count) and a thin unit ruler on the left, the
 * racks standing on the same baseline. Devices are white tiles at their
 * units, a coloured mark on the left edge for their kind; empty units are
 * faint dashed slots. Every cable end is a small dot on the edge of its tile
 * (filled when the end was read, hollow when it is only expected), and every
 * cable is a smooth curve from dot to dot:
 *
 *   - between the two racks it crosses the gap in a gentle S;
 *   - inside one rack it loops out on the outer side, the longer loops
 *     further out, so they nest and do not sit on one another;
 *   - to a device that is not drawn it runs out to a small pill tag with
 *     that end's name, on the outer side.
 *
 * Pinch or scroll with ctrl (trackpad pinch) to zoom, drag to pan once zoomed,
 * double tap to fit again; the small + / - / fit buttons do the same. Taps
 * work at any zoom: the view is a viewBox over a fixed layout.
 *
 * Every tile and line comes from the data the caller passes; nothing is
 * drawn that was not read or is not in the Excel dataset. This file and
 * Icon.jsx are the only places an svg may be written (the noSvg test holds
 * that line), and only the cables are drawn as paths.
 *
 * Tap a device or a cable: it and what it touches stay, the rest fades.
 * With onRack, a tap on a rack's frame or title opens that rack (the pair).
 *
 * racks:  [{ key, name, units, devices: [{ id, name, text, sub, kind, top, bottom, unit, tone, unknown, aria }] }]
 * cables: [{ id, label, tone: 'ok' | 'bad' | 'ink' | 'grey', dashed, count,
 *            ends: [end, end] }], an end being { rack, device, order, hollow }
 *            on a drawn device, or { off: { key, text, device } } off the drawing.
 */

export const TONE = { ok: '#1d7f47', bad: '#b4232a', ink: '#2b3440', grey: '#9aa1a6' };

/** A device kind's mark colour and its short letters. */
export const KIND = {
  switch: ['#2f6bd8', 'SW'],
  router: ['#7a4fd0', 'RT'],
  firewall: ['#c43d3d', 'FW'],
  patch_panel: ['#cc8a0e', 'PP'],
  server: ['#7b848b', 'SV'],
  pdu: ['#e07a1f', 'PDU'],
  ups: ['#e07a1f', 'UPS'],
  psu: ['#e07a1f', 'PSU'],
  gateway: ['#0f8a83', 'GT'],
  load_balancer: ['#5a5fd6', 'LB'],
  storage: ['#55606a', 'ST'],
  modem: ['#0f8a83', 'MD'],
  controller: ['#5a5fd6', 'CT'],
  recorder: ['#7b848b', 'REC'],
  amplifier: ['#7b848b', 'AMP'],
  kvm: ['#7b848b', 'KVM'],
  cable_manager: ['#a3a9ae', 'CM'],
  shelf: ['#a3a9ae', 'SH'],
  closed_unit: ['#a3a9ae', 'BP'],
  unknown: ['#cdd2d6', ''],
};

/** "Patch Panel", "patch-panel", "patch" -> "patch_panel"; anything else not known -> 'unknown'. */
export function kindKey(type) {
  const k = String(type ?? '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  if (KIND[k]) return k;
  if (k.startsWith('patch')) return 'patch_panel';
  if (k === 'blank_panel' || k === 'blank') return 'closed_unit';
  if (k.startsWith('cable')) return 'cable_manager';
  if (k.includes('gateway')) return 'gateway';
  if (k.includes('balancer')) return 'load_balancer';
  if (k.includes('firewall')) return 'firewall';
  if (k.includes('switch')) return 'switch';
  if (k.includes('router')) return 'router';
  if (k.includes('server')) return 'server';
  return 'unknown';
}

const CH = 0.6; // a character's width, as a share of the font size

/** Text in lines of at most n characters, broken after a separator where there is one. */
export function wrapText(s, n) {
  const max = Math.max(3, Math.floor(n));
  const words = String(s ?? '').match(/[^\s\-_/,.]*[\s\-_/,.]?/g).filter(Boolean);
  const lines = [];
  let line = '';
  for (let w of words) {
    while (w.length > max) {
      if (line) { lines.push(line.trimEnd()); line = ''; }
      lines.push(w.slice(0, max));
      w = w.slice(max);
    }
    if ((line + w).trimEnd().length > max && line) { lines.push(line.trimEnd()); line = ''; }
    line += w;
  }
  if (line.trim()) lines.push(line.trimEnd());
  return lines.length ? lines : [''];
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** The width of the box the diagram sits in, read again when it changes. */
function useWidth(fallback) {
  const ref = useRef(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const read = () => { if (el.clientWidth) setW(el.clientWidth); };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** A tile's name in the lines and size that fit it; a name too long for a short tile keeps its last part. */
function tileText(name, w, h, big) {
  const sizes = big ? [12.5, 11.5, 10.5] : [11.5, 10.5, 10];
  for (const fs of sizes) {
    const lines = wrapText(name, w / (fs * CH));
    if (lines.length * (fs + 2) <= h - 2) return { fs, lines };
  }
  const fs = sizes[sizes.length - 1];
  const n = Math.max(3, Math.floor(w / (fs * CH)));
  const tail = String(name ?? '').split(/[\s\-_/]+/).filter(Boolean).pop() || '';
  const one = String(name ?? '').length <= n ? String(name) : tail.length <= n ? tail : `${tail.slice(0, n - 1)}…`;
  return { fs, lines: [one] };
}

/** Where everything goes, from the data and the width alone. */
function layoutOf(racks, cables, W) {
  const pair = racks.length === 2;
  const small = W < 600;
  const rackIndex = new Map(racks.map((r, i) => [r.key, i]));
  const drawn = new Map(racks.map((r) => [r.key, new Set(r.devices.filter((d) => d.top != null).map((d) => d.id))]));
  const onRack = (e) => (e && !e.off && drawn.get(e.rack)?.has(e.device) ? e.rack : null);

  // Which kinds of line each rack sends out of its outer side, so the room is made only where needed.
  const loopsAt = racks.map(() => false);
  const stubsAt = racks.map(() => false);
  for (const c of cables) {
    const [r1, r2] = c.ends.map(onRack);
    if (r1 && r2 && r1 === r2 && c.ends[0].device !== c.ends[1].device) loopsAt[rackIndex.get(r1)] = true;
    else if ((r1 && !r2) || (!r1 && r2)) stubsAt[rackIndex.get(r1 || r2)] = true;
  }

  const RULER = small ? 17 : 22;
  const TITLE = small ? 28 : 32;
  const PADB = 8;
  const RIGHT = small ? 6 : 8;
  let rackW; let gap = 0; const outer = [];
  if (pair) {
    const tagW = clamp(W * 0.24, 84, 170);
    const loopW = clamp(W * 0.06, 20, 64);
    for (let i = 0; i < 2; i += 1) outer.push((stubsAt[i] ? tagW : 0) + (loopsAt[i] ? loopW : 0) + 6);
    gap = clamp(W * 0.13, 44, 200);
    rackW = clamp((W - outer[0] - outer[1] - gap) / 2, 84, 300);
  } else {
    const tagW = stubsAt[0] ? clamp(W * 0.24, 80, 200) : 8;
    const loopW = loopsAt[0] ? clamp(W * 0.27, 72, 240) : 8;
    outer.push(tagW, loopW);
    rackW = clamp(W - tagW - loopW, 150, 380);
  }
  const uh = pair ? clamp(rackW * 0.19, 17, 30) : clamp(rackW * 0.12, 20, 30);
  const units = Math.max(1, ...racks.map((r) => r.units || 1));
  const top = 6;
  const floor = top + TITLE + units * uh + PADB;
  const total = pair ? outer[0] + 2 * rackW + gap + outer[1] : outer[0] + rackW + outer[1];
  const x0 = Math.max(0, (W - total) / 2);
  const rx = pair ? [x0 + outer[0], x0 + outer[0] + rackW + gap] : [x0 + outer[0]];

  // The racks, their tiles and the empty slots between them.
  const rackBoxes = racks.map((r, i) => {
    const x = rx[i];
    const n = r.units || 1;
    const h = TITLE + n * uh + PADB;
    const uy = (u) => floor - PADB - u * uh; // the top of unit u
    const tx = x + RULER;
    const tw = rackW - RULER - RIGHT;
    const blocks = new Map();
    const used = new Set();
    for (const d of r.devices) {
      if (d.top == null) continue;
      const t = Math.max(d.top, d.bottom ?? d.top);
      const b = Math.min(d.top, d.bottom ?? d.top);
      for (let u = b; u <= t; u += 1) used.add(u);
      blocks.set(d.id, { d, x: tx, y: uy(t) + 1, w: tw, h: (t - b + 1) * uh - 2, rack: r.key });
    }
    const empty = [];
    let run = null;
    for (let u = n; u >= 1; u -= 1) {
      if (!used.has(u)) {
        if (!run) { run = { top: u, bottom: u }; empty.push(run); } else run.bottom = u;
      } else run = null;
    }
    const slots = empty.map((s) => ({ key: `${s.top}-${s.bottom}`, x: tx, y: uy(s.top) + 1, w: tw, h: (s.top - s.bottom + 1) * uh - 2 }));
    const step = uh >= 15 ? (n > 30 && small ? 2 : 1) : 5;
    const ruler = [];
    for (let u = 1; u <= n; u += 1) if (u === 1 || u % step === 0) ruler.push({ u, y: uy(u) + uh / 2 });
    const side = pair ? (i === 0 ? -1 : 1) : 1;
    return {
      r, x, y: floor - h, w: rackW, h, titleH: TITLE, blocks, slots, ruler, rulerX: x + RULER - 5,
      loop: side, // the side its loops go out on
      out: pair ? side : -1, // the side its far-end tags sit on
    };
  });
  const rackOf = new Map(rackBoxes.map((b) => [b.r.key, b]));
  const blockOf = (end) => rackOf.get(end.rack)?.blocks.get(end.device) || null;

  // Each cable's kind and the side of the tile each drawn end leaves from.
  const plans = [];
  for (const c of cables) {
    const [e1, e2] = c.ends;
    const b1 = e1 && !e1.off ? blockOf(e1) : null;
    const b2 = e2 && !e2.off ? blockOf(e2) : null;
    if (!b1 && !b2) continue;
    if (b1 && b2) {
      if (b1 === b2) continue;
      if (b1.rack === b2.rack) {
        const rb = rackOf.get(b1.rack);
        plans.push({ c, kind: 'loop', ends: [{ b: b1, side: rb.loop, e: e1 }, { b: b2, side: rb.loop, e: e2 }], rb });
      } else {
        const [l, r] = rackOf.get(b1.rack).x < rackOf.get(b2.rack).x ? [[b1, e1], [b2, e2]] : [[b2, e2], [b1, e1]];
        plans.push({ c, kind: 'between', ends: [{ b: l[0], side: 1, e: l[1] }, { b: r[0], side: -1, e: r[1] }] });
      }
    } else {
      const [b, e, off] = b1 ? [b1, e1, e2?.off] : [b2, e2, e1?.off];
      const rb = rackOf.get(b.rack);
      plans.push({ c, kind: 'stub', ends: [{ b, side: rb.out, e }], off: off || { key: `none:${c.id}`, text: 'Not known' }, rb });
    }
  }

  // The loops of each rack in lanes: a loop goes one lane further out than
  // the loops it overlaps that are shorter, so they nest.
  const lanes = new Map();
  const laneGap = new Map();
  for (const rb of rackBoxes) {
    const mine = plans.filter((p) => p.kind === 'loop' && p.rb === rb).map((p) => {
      const ys = p.ends.map((q) => q.b.y + q.b.h / 2);
      return { p, lo: Math.min(...ys), hi: Math.max(...ys) };
    }).sort((a, b) => (a.hi - a.lo) - (b.hi - b.lo));
    let most = 0;
    mine.forEach((m, i) => {
      let lane = 1;
      for (let j = 0; j < i; j += 1) {
        const o = mine[j];
        if (o.lo < m.hi && o.hi > m.lo) lane = Math.max(lane, lanes.get(o.p) + 1);
      }
      lanes.set(m.p, lane);
      most = Math.max(most, lane);
    });
    const room = pair ? clamp(W * 0.06, 20, 64) : (outer[1] - 12);
    laneGap.set(rb, most ? Math.min(small ? 9 : 16, room / most) : 0);
    rb.loopRoom = most ? laneGap.get(rb) * most : 0;
  }

  // The tags of the ends off the drawing, one per far end and side, each
  // beside the devices it is cabled to, pushed apart where they would meet.
  const tagFs = small ? 9 : 10.5;
  // On a pair the loops of a rack share its outer side with the tags: the tags then sit beyond them.
  const shared = (rb) => (rb.loop === rb.out && rb.loopRoom ? rb.loopRoom + 16 : 14);
  const tags = new Map();
  for (const p of plans) {
    if (p.kind !== 'stub') continue;
    const k = `${p.rb.r.key}|${p.off.key}`;
    if (!tags.has(k)) {
      const room = (pair ? outer[p.rb === rackBoxes[0] ? 0 : 1] : outer[0]) - shared(p.rb) - 4;
      const lines = wrapText(p.off.text, (room - 12) / (tagFs * CH));
      const w = Math.min(room, Math.max(...lines.map((s) => s.length)) * tagFs * CH + 12);
      tags.set(k, { key: k, off: p.off, side: p.rb.out, rb: p.rb, lines, w, h: lines.length * (tagFs + 3) + 7, want: [], cables: [] });
    }
    const t = tags.get(k);
    t.want.push(p.ends[0].b.y + p.ends[0].b.h / 2);
    t.cables.push(p.c.id);
    p.tag = t;
  }
  for (const rb of rackBoxes) {
    const list = [...tags.values()].filter((t) => t.rb === rb);
    for (const t of list) t.y = t.want.reduce((s, v) => s + v, 0) / t.want.length;
    list.sort((a, b) => a.y - b.y);
    let bottom = -Infinity;
    for (const t of list) {
      t.y = Math.max(t.y, bottom + t.h / 2 + 5);
      bottom = t.y + t.h / 2;
    }
    const over = bottom - floor;
    if (over > 0) for (const t of list) t.y -= over;
    let topEdge = top;
    for (const t of list) { if (t.y - t.h / 2 < topEdge) t.y = topEdge + t.h / 2; topEdge = t.y + t.h / 2 + 5; }
    const edge = rb.out < 0 ? rb.x : rb.x + rb.w;
    for (const t of list) {
      t.ix = edge + rb.out * shared(rb); // the edge the line meets
      t.x = rb.out < 0 ? t.ix - t.w : t.ix;
    }
  }

  // Where each end meets its tile: the ends on one side of a tile spread
  // along it so the lines leave without crossing there (loops nest: the
  // farther a loop goes, the closer to the outside its end sits).
  const groups = new Map();
  for (const p of plans) {
    p.ends.forEach((end, i) => {
      const k = `${end.b.rack}|${end.b.d.id}|${end.side}`;
      if (!groups.has(k)) groups.set(k, []);
      const here = end.b.y + end.b.h / 2;
      let toward;
      if (p.kind === 'stub') toward = p.tag.y;
      else { const o = p.ends[1 - i].b; toward = o.y + o.h / 2; }
      // Loops: the ones going up first, each run with the farthest outside.
      const key = p.kind === 'between' ? toward : (toward < here ? 0 : 1e6) - toward;
      groups.get(k).push({ end, key, order: end.e?.order ?? 0 });
    });
  }
  for (const g of groups.values()) {
    g.sort((a, b) => a.key - b.key || a.order - b.order);
    g.forEach(({ end }, i) => {
      const { b, side } = end;
      const inset = Math.min(5, b.h / 4);
      const span = b.h - 2 * inset;
      end.y = b.y + inset + (span * (i + 1)) / (g.length + 1);
      end.x = side > 0 ? b.x + b.w : b.x;
      end.r = clamp(span / (g.length + 1) / 2.2, 1.7, small ? 2.8 : 3.2);
    });
  }

  // The paths.
  const lines = plans.map((p) => {
    const [a, b] = p.ends;
    if (p.kind === 'between') {
      const dx = (b.x - a.x) * 0.5;
      return { p, d: `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`, dots: [a, b], lx: (a.x + b.x) / 2, ly: (a.y + b.y) / 2 };
    }
    if (p.kind === 'loop') {
      const rb = p.rb;
      const s = rb.loop;
      const edge = s > 0 ? rb.x + rb.w : rb.x;
      const apex = edge + s * (laneGap.get(rb) * lanes.get(p) + 4);
      const te = a.x;
      const X = te + (apex - te) / 0.75;
      return { p, d: `M ${a.x} ${a.y} C ${X} ${a.y}, ${X} ${b.y}, ${b.x} ${b.y}`, dots: [a, b], lx: apex, ly: (a.y + b.y) / 2 };
    }
    const t = p.tag;
    const m = (a.x + t.ix) / 2;
    return { p, d: `M ${a.x} ${a.y} C ${m} ${a.y}, ${m} ${t.y}, ${t.ix} ${t.y}`, dots: [a], lx: m, ly: (a.y + t.y) / 2 };
  });

  const Wd = Math.max(W, total);
  return { pair, small, W: Wd, H: floor + 8, floor, uh, tagFs, rackBoxes, lines, tags: [...tags.values()] };
}

/** The devices and cables to keep in full when something is chosen; null keeps all. */
function focusOf(selected, cables) {
  if (!selected) return null;
  const devices = new Set();
  const lit = new Set();
  const devKey = (e) => (e.off ? (e.off.device != null ? `off:${e.off.device}` : null) : `${e.rack}|${e.device}`);
  if (selected.kind === 'cable') {
    const c = cables.find((x) => x.id === selected.id);
    if (!c) return null;
    lit.add(c.id);
    c.ends.forEach((e) => { const k = e && devKey(e); if (k) devices.add(k); });
    return { devices, cables: lit };
  }
  if (selected.kind === 'device') {
    devices.add(selected.key);
    for (const c of cables) {
      const keys = c.ends.map((e) => e && devKey(e));
      if (!keys.includes(selected.key)) continue;
      lit.add(c.id);
      keys.forEach((k) => { if (k) devices.add(k); });
    }
    return { devices, cables: lit };
  }
  return null;
}

const onKey = (fn) => (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fn(); } };

const ZMAX = 5;

/**
 * The view over the drawing: { z, x, y }, the zoom and the top-left corner of
 * the viewBox. Pinch, drag, ctrl-scroll and double tap move it; taps pass.
 */
function useView(svgRef, L) {
  const [v, setV] = useState({ z: 1, x: 0, y: 0 });
  const ref = useRef({ v, L });
  ref.current.v = v;
  ref.current.L = L;
  const fix = useCallback((n) => {
    const { L: l } = ref.current;
    const z = clamp(n.z, 1, ZMAX);
    return { z, x: clamp(n.x, 0, l.W - l.W / z), y: clamp(n.y, 0, l.H - l.H / z) };
  }, []);
  const set = useCallback((n) => setV(fix(n)), [fix]);
  // Zoom to z keeping the point (px, py) of the drawing where it is on the screen.
  const zoomAt = useCallback((z, px, py) => {
    const { v: o } = ref.current;
    const nz = clamp(z, 1, ZMAX);
    set({ z: nz, x: px - ((px - o.x) * o.z) / nz, y: py - ((py - o.y) * o.z) / nz });
  }, [set]);
  const toPoint = useCallback((cx, cy) => {
    const { v: o, L: l } = ref.current;
    const r = svgRef.current.getBoundingClientRect();
    return { x: o.x + ((cx - r.left) / r.width) * (l.W / o.z), y: o.y + ((cy - r.top) / r.height) * (l.H / o.z) };
  }, [svgRef]);

  const g = useRef({ pts: new Map(), start: null, moved: false, lastTap: 0 });
  const begin = (ev) => {
    const s = g.current;
    const list = [...s.pts.values()];
    if (list.length === 1) s.start = { kind: 'pan', cx: list[0].x, cy: list[0].y, v: ref.current.v };
    else if (list.length >= 2) {
      const [p, q] = list;
      s.start = { kind: 'pinch', d: Math.hypot(p.x - q.x, p.y - q.y) || 1, v: ref.current.v, anchor: toPoint((p.x + q.x) / 2, (p.y + q.y) / 2) };
      s.moved = true;
      try { ev.currentTarget.setPointerCapture(ev.pointerId); } catch { /* not captured */ }
    }
  };
  const handlers = {
    onPointerDown: (ev) => {
      const s = g.current;
      if (!s.pts.size) s.moved = false;
      s.pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      begin(ev);
    },
    onPointerMove: (ev) => {
      const s = g.current;
      if (!s.pts.has(ev.pointerId) || !s.start) return;
      s.pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      const r = svgRef.current.getBoundingClientRect();
      const { L: l } = ref.current;
      if (s.start.kind === 'pan') {
        const dx = ev.clientX - s.start.cx;
        const dy = ev.clientY - s.start.cy;
        if (!s.moved && Math.hypot(dx, dy) < 6) return;
        if (s.start.v.z <= 1) return;
        if (!s.moved) { try { ev.currentTarget.setPointerCapture(ev.pointerId); } catch { /* not captured */ } }
        s.moved = true;
        const k = l.W / s.start.v.z / r.width;
        set({ z: s.start.v.z, x: s.start.v.x - dx * k, y: s.start.v.y - dy * k });
      } else {
        const [p, q] = [...s.pts.values()];
        if (!q) return;
        const z = clamp((s.start.v.z * Math.hypot(p.x - q.x, p.y - q.y)) / s.start.d, 1, ZMAX);
        const mx = (p.x + q.x) / 2 - r.left;
        const my = (p.y + q.y) / 2 - r.top;
        set({ z, x: s.start.anchor.x - (mx / r.width) * (l.W / z), y: s.start.anchor.y - (my / r.height) * (l.H / z) });
      }
    },
    onPointerUp: (ev) => {
      const s = g.current;
      if (!s.pts.has(ev.pointerId)) return;
      s.pts.delete(ev.pointerId);
      if (s.pts.size) { begin(ev); return; }
      s.start = null;
      if (!s.moved && ev.pointerType !== 'mouse') {
        const now = Date.now();
        if (now - s.lastTap < 320) { setV({ z: 1, x: 0, y: 0 }); s.lastTap = 0; } else s.lastTap = now;
      }
    },
    onPointerCancel: (ev) => {
      const s = g.current;
      s.pts.delete(ev.pointerId);
      if (!s.pts.size) s.start = null;
    },
    onClickCapture: (ev) => {
      if (g.current.moved) { ev.stopPropagation(); ev.preventDefault(); g.current.moved = false; }
    },
    onDoubleClick: () => setV({ z: 1, x: 0, y: 0 }),
  };

  // Ctrl-scroll (a trackpad pinch) zooms at the pointer; once zoomed, scroll pans.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const onWheel = (ev) => {
      const { v: o, L: l } = ref.current;
      if (ev.ctrlKey || ev.metaKey) {
        ev.preventDefault();
        const p = toPoint(ev.clientX, ev.clientY);
        zoomAt(o.z * Math.exp(-ev.deltaY * 0.01), p.x, p.y);
      } else if (o.z > 1) {
        ev.preventDefault();
        const r = el.getBoundingClientRect();
        const k = l.W / o.z / r.width;
        set({ z: o.z, x: o.x + ev.deltaX * k, y: o.y + ev.deltaY * k });
      }
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [svgRef, toPoint, zoomAt, set]);

  const by = (f) => { const { v: o, L: l } = ref.current; zoomAt(o.z * f, o.x + l.W / o.z / 2, o.y + l.H / o.z / 2); };
  return { v: fix(v), handlers, zoomIn: () => by(1.5), zoomOut: () => by(1 / 1.5), fit: () => setV({ z: 1, x: 0, y: 0 }) };
}

/**
 * The diagram. `selected` is { kind: 'device', key } (key = `${rack}|${id}`,
 * or `off:${id}` for a far end that is a device) or { kind: 'cable', id };
 * `focus` ({ devices, cables } sets) overrides what stays in full (a trace).
 */
export function TopologyDiagram({ racks, cables, selected = null, focus = null, onDevice, onCable, onClear, onRack, label, testId }) {
  const [ref, W] = useWidth(360);
  const svgRef = useRef(null);
  const L = useMemo(() => layoutOf(racks, cables, W), [racks, cables, W]);
  const { v, handlers, zoomIn, zoomOut, fit } = useView(svgRef, L);
  const keep = focus || focusOf(selected, cables);
  const devOn = (k) => !keep || keep.devices.has(k);
  const cabOn = (id) => !keep || keep.cables.has(id);
  const devSel = (k) => selected?.kind === 'device' && selected.key === k;
  const anySel = selected?.kind === 'cable';
  const tagFs = L.tagFs;
  const shownW = Math.min(W, L.W);
  const shownH = (L.H * shownW) / L.W;

  return (
    <div ref={ref} className={styles.wrap} data-testid={testId}>
      <svg
        ref={svgRef}
        className={styles.svg}
        width={shownW}
        height={shownH}
        viewBox={`${v.x} ${v.y} ${L.W / v.z} ${L.H / v.z}`}
        role="group"
        aria-label={label}
        style={{ touchAction: v.z > 1 ? 'none' : 'pan-y' }}
        data-zoom={v.z.toFixed(2)}
        onClick={(ev) => { if (ev.target === ev.currentTarget || ev.target.getAttribute?.('data-bg') != null) onClear?.(); }}
        {...handlers}
      >
        {L.rackBoxes.map((rb) => (
          <g key={rb.r.key} aria-label={`Rack ${rb.r.name}`} role="group">
            <rect
              data-bg=""
              className={cx(styles.frame, onRack && styles.tap)}
              x={rb.x} y={rb.y} width={rb.w} height={rb.h} rx={12}
              onClick={onRack ? (ev) => { ev.stopPropagation(); onRack(rb.r.key); } : undefined}
            />
            <RackTitle rb={rb} small={L.small} onRack={onRack} />
            {rb.ruler.map((m) => (
              <text key={m.u} className={styles.ruler} fontSize={L.small ? 8 : 9.5} x={rb.rulerX} y={m.y} textAnchor="end" dominantBaseline="central">{m.u}</text>
            ))}
            {rb.slots.map((s) => <rect key={s.key} className={styles.slot} x={s.x} y={s.y} width={s.w} height={s.h} rx={6} />)}
            {[...rb.blocks.values()].map((b) => {
              const d = b.d;
              const k = `${rb.r.key}|${d.id}`;
              const [colour, letters] = KIND[d.unknown ? 'unknown' : kindKey(d.kind)] || KIND.unknown;
              const showLetters = letters && b.w >= 120 && b.h >= 14;
              const lettersW = showLetters ? letters.length * 8.5 * CH + 10 : 0;
              const nx = b.x + 10;
              const room = b.w - 14 - lettersW;
              const name = d.unknown ? 'Unknown' : (d.text ?? d.name);
              const showSub = !d.unknown && d.sub && b.h >= (L.small ? 34 : 38) && room >= 50;
              const t = tileText(name, room, showSub ? b.h - 14 : b.h, !L.small);
              const lh = t.fs + 2;
              const blockH = t.lines.length * lh + (showSub ? 13 : 0);
              const ty = b.y + b.h / 2 - blockH / 2 + lh / 2;
              const body = (
                <>
                  <rect className={styles.block} data-tone={d.tone || undefined} data-unknown={d.unknown ? '' : undefined} x={b.x} y={b.y} width={b.w} height={b.h} rx={Math.min(8, b.h / 2.6)} />
                  <rect x={b.x + 3} y={b.y + 3} width={3} height={Math.max(2, b.h - 6)} rx={1.5} fill={colour} />
                  <text className={d.unknown ? styles.blockGrey : styles.blockName} x={nx} y={ty} fontSize={t.fs} dominantBaseline="central">
                    {t.lines.map((s, i) => <tspan key={i} x={nx} dy={i ? lh : 0}>{s}</tspan>)}
                  </text>
                  {showSub ? <text className={styles.sub} x={nx} y={ty + (t.lines.length - 1) * lh + 13} dominantBaseline="central">{d.sub}</text> : null}
                  {showLetters ? <text className={styles.kind} x={b.x + b.w - 8} y={b.y + Math.min(b.h / 2, 11)} textAnchor="end" dominantBaseline="central" fill={colour}>{letters}</text> : null}
                </>
              );
              if (d.unknown || !onDevice) {
                return <g key={k} aria-label={d.aria || d.name} className={cx(styles.dev, !devOn(k) && styles.faded)}>{body}</g>;
              }
              return (
                <g
                  key={k}
                  role="button"
                  tabIndex={0}
                  aria-label={d.aria || d.name}
                  aria-pressed={devSel(k)}
                  className={cx(styles.dev, styles.tap, devSel(k) && styles.devOn, !devOn(k) && styles.faded)}
                  onClick={() => onDevice({ kind: 'device', key: k, rack: rb.r.key, id: d.id })}
                  onKeyDown={onKey(() => onDevice({ kind: 'device', key: k, rack: rb.r.key, id: d.id }))}
                >
                  {body}
                </g>
              );
            })}
          </g>
        ))}
        {L.lines.map(({ p, d, dots, lx, ly }) => {
          const c = p.c;
          const colour = TONE[c.tone] || TONE.ink;
          const on = cabOn(c.id);
          const sel = selected?.kind === 'cable' && selected.id === c.id;
          const count = c.count > 1 ? String(c.count) : null;
          const cw = count ? count.length * 9 * CH + 9 : 0;
          return (
            <g
              key={c.id}
              role="button"
              tabIndex={0}
              aria-label={c.label}
              aria-pressed={sel}
              data-tone={c.tone}
              className={cx(styles.cable, !on && (anySel ? styles.fadedCable : styles.fadedSoft))}
              onClick={() => onCable?.({ kind: 'cable', id: c.id })}
              onKeyDown={onKey(() => onCable?.({ kind: 'cable', id: c.id }))}
            >
              <path d={d} className={styles.hit} />
              <path d={d} className={styles.line} stroke={colour} strokeWidth={sel ? 3 : 2} strokeDasharray={c.dashed ? '4 4' : undefined} />
              {dots.map((q, i) => (
                <circle
                  key={i}
                  cx={q.x} cy={q.y} r={q.r}
                  fill={q.e?.hollow ? '#ffffff' : colour}
                  stroke={colour}
                  strokeWidth={q.e?.hollow ? 1.3 : 0}
                />
              ))}
              {count ? (
                <>
                  <rect className={styles.countBox} x={lx - cw / 2} y={ly - 7} width={cw} height={14} rx={7} stroke={colour} />
                  <text className={styles.count} x={lx} y={ly} textAnchor="middle" dominantBaseline="central" fill={colour}>{count}</text>
                </>
              ) : null}
            </g>
          );
        })}
        {L.tags.map((t) => {
          const k = t.off.device != null ? `off:${t.off.device}` : null;
          const on = k ? devOn(k) : t.cables.some(cabOn);
          const lh = tagFs + 3;
          const y0 = t.y - ((t.lines.length - 1) * lh) / 2;
          const body = (
            <>
              <rect className={styles.pill} x={t.x} y={t.y - t.h / 2} width={t.w} height={t.h} rx={Math.min(t.h / 2, 9)} />
              <text className={styles.tag} x={t.x + 6} y={y0} dominantBaseline="central" fontSize={tagFs}>
                {t.lines.map((s, i) => <tspan key={i} x={t.x + 6} dy={i ? lh : 0}>{s}</tspan>)}
              </text>
            </>
          );
          if (k && onDevice) {
            return (
              <g
                key={t.key}
                role="button"
                tabIndex={0}
                aria-label={t.off.aria || t.off.text}
                aria-pressed={devSel(k)}
                className={cx(styles.tap, devSel(k) && styles.devOn, !on && styles.faded)}
                onClick={() => onDevice({ kind: 'device', key: k, id: t.off.device, off: true })}
                onKeyDown={onKey(() => onDevice({ kind: 'device', key: k, id: t.off.device, off: true }))}
              >
                {body}
              </g>
            );
          }
          return <g key={t.key} className={cx(!on && styles.faded)} aria-hidden="true">{body}</g>;
        })}
      </svg>
      <div className={styles.zoom}>
        <button type="button" className={styles.zbtn} aria-label="Zoom in" onClick={zoomIn} disabled={v.z >= ZMAX}><Icon name="zoom_in" /></button>
        <button type="button" className={styles.zbtn} aria-label="Zoom out" onClick={zoomOut} disabled={v.z <= 1}><Icon name="zoom_out" /></button>
        <button type="button" className={styles.zbtn} aria-label="Fit" onClick={fit} disabled={v.z <= 1}><Icon name="fit_screen" /></button>
      </div>
    </div>
  );
}

/** A rack's title row: its name and unit count; with onRack, the way to open it alone. */
function RackTitle({ rb, small, onRack }) {
  const fs = small ? 11.5 : 12.5;
  const count = `${rb.r.units || 1}U`;
  const room = rb.w - 24 - count.length * 10.5 * CH - 6;
  const fits = rb.r.name.length * fs * CH <= room;
  const nfs = fits ? fs : Math.max(9.5, room / (rb.r.name.length * CH));
  const showCount = rb.r.name.length * nfs * CH <= room;
  const y = rb.y + rb.titleH / 2 + 1;
  const body = (
    <>
      <text className={styles.rackName} x={rb.x + 12} y={y} fontSize={nfs} dominantBaseline="central">{rb.r.name}</text>
      {showCount ? <text className={styles.rackUnits} x={rb.x + rb.w - 12} y={y} textAnchor="end" dominantBaseline="central">{count}</text> : null}
      <line className={styles.titleRule} x1={rb.x + 1} x2={rb.x + rb.w - 1} y1={rb.y + rb.titleH - 2} y2={rb.y + rb.titleH - 2} />
    </>
  );
  if (!onRack) return body;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Open ${rb.r.name}`}
      className={styles.tap}
      onClick={(ev) => { ev.stopPropagation(); onRack(rb.r.key); }}
      onKeyDown={onKey(() => onRack(rb.r.key))}
    >
      <rect x={rb.x} y={rb.y} width={rb.w} height={rb.titleH} fill="transparent" />
      {body}
    </g>
  );
}

const cx = (...a) => a.filter(Boolean).join(' ');

/** One quiet line under a diagram: what each kind of line means. Only the kinds present. */
export function TopologyLegend({ items }) {
  if (!items?.length) return null;
  return (
    <p className={styles.legend} aria-label="What the lines mean">
      {items.map(([tone, word, dashed]) => (
        <span key={word}><i className={cx(styles.lkey, dashed && styles.lkeyDash)} style={{ '--k': TONE[tone] }} />{word}</span>
      ))}
    </p>
  );
}
