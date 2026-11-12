/**
 * One scan against the site's patching matrix (the Excel sheet).
 *
 *   resolve(view, matrixDoc)  gives each device of the scan the name the sheet
 *                             uses for it, and says how it was matched
 *   check(view, matrixDoc)    the GET /scans/:scanId/sheet-check answer
 *
 * How a device in the photo is matched to a device in the sheet:
 *   1. by the label read on it: the text on the device (SW-CORE-01) is a
 *      device name in the sheet (matchedBy "label");
 *   2. else by where it stands: the sheet puts a device in this rack (the
 *      rack label a person confirmed) at a unit this device covers
 *      (matchedBy "unit").
 * A device matched by where it stands is in the right unit by definition, so
 * "wrong_unit" can only be found for a device whose label was read. Both are
 * said on each row (matchedBy), so a report never claims more than it knows.
 *
 * The sheet rows are the stored snake_case rows of lib/matrix.js
 * (a_device, a_port, a_rack, a_unit, z_...).
 */
'use strict';

const { norm, portNumber } = require('./scan_view');
const labelMatch = require('./label_match');
const sheetModel = require('./sheet_model');
const { portFamily } = require('./workbook');

// A patch-panel position code such as 14A (lib/checks.js positionCode).
const POSITION_CODE = /^\s*\d{1,3}\s*[A-Za-z]\s*$/;

/**
 * The port a printed position code names on a panel. The site's codes run
 * across the rack in blocks of 24 (PP1 1-24, PP2 25-48, PP3 49-72 ...): the
 * number is the column of the panel, and the letter the row, A the upper
 * and B the lower. The photo numbers a two-row panel column by column, upper
 * then lower, so 42A on a 48-port panel is column 18, port 35, and 42B port
 * 36; on a one-row panel the column is the port. Null when it cannot be
 * placed (no code, or past the panel's ports).
 */
const CODE_BLOCK = 24;
/**
 * A dust cap is for a port that takes an optic or a fibre. A port read as
 * copper (RJ45, console, management, USB) and not as an SFP-type cage is not
 * one. A port whose type was not read (a patch panel's ports, mostly) keeps
 * its cap check only when a close-up of it was taken: from across the room
 * an open copper jack and an open fibre port look alike.
 */
const COPPER = /RJ45|RJ-45|CONSOLE|AUX|MANAGEMENT|USB/i;
function opticalPort(p) {
  if (p?.sfpType) return true;
  if (p?.type) return !COPPER.test(String(p.type));
  return p?.stateSource === 'close-up';
}

function codeColumnRow(code) {
  const m = String(code ?? '').trim().match(/^(\d{1,3})\s*([A-Za-z])$/);
  if (!m) return null;
  return { column: ((Number(m[1]) - 1) % CODE_BLOCK) + 1, row: m[2].toUpperCase() === 'B' ? 'B' : 'A' };
}
function codeToPort(code, portCount) {
  const at = codeColumnRow(code);
  if (!at) return null;
  const rows = Number(portCount) > CODE_BLOCK ? 2 : 1;
  const port = rows === 2 ? (at.column - 1) * 2 + (at.row === 'B' ? 2 : 1) : at.column;
  return Number(portCount) > 0 && port > Number(portCount) ? null : port;
}

/**
 * Where each port of a panel sits on the photo: its column, counted from the
 * leftmost port read at the panel's own pitch (so a port the photo missed
 * leaves a gap instead of shifting the rest), and its row, A the upper and
 * B the lower. Only for devices whose ports carry boxes; one answer per
 * device, kept on it.
 */
const GRID = Symbol('panel grid');
function panelGrid(device) {
  if (device[GRID] !== undefined) return device[GRID];
  const boxed = (device.ports || []).filter((p) => Array.isArray(p.box) && p.box.length === 4);
  let grid = null;
  if (boxed.length >= 2) {
    const cx = (p) => (p.box[0] + p.box[2]) / 2;
    const cy = (p) => (p.box[1] + p.box[3]) / 2;
    const w = boxed.map((p) => p.box[2] - p.box[0]).sort((a, b) => a - b);
    const width = w[Math.floor(w.length / 2)] || 1;
    // Columns: ports whose centres sit within half a port width of each other.
    const xs = boxed.map(cx).sort((a, b) => a - b);
    const cols = [];
    for (const x of xs) {
      const last = cols[cols.length - 1];
      if (last && x - last.x <= width * 0.5) { last.sum += x; last.n += 1; last.x = last.sum / last.n; } else cols.push({ x, sum: x, n: 1 });
    }
    const gaps = cols.slice(1).map((c, i) => c.x - cols[i].x).sort((a, b) => a - b);
    // The pitch: when most of the row was read, the span of the row over its
    // 24 columns (a doubled reading of one port cannot shrink it); else the
    // usual gap between neighbours.
    const span = cols[cols.length - 1].x - cols[0].x;
    const pitch = cols.length >= 20 && span > 0 ? span / (CODE_BLOCK - 1) : (gaps.length ? gaps[Math.floor(gaps.length / 2)] : width);
    const ys = boxed.map(cy).sort((a, b) => a - b);
    const h = boxed.map((p) => p.box[3] - p.box[1]).sort((a, b) => a - b)[Math.floor(boxed.length / 2)] || 1;
    const twoRows = ys[ys.length - 1] - ys[0] > h * 0.8;
    const midY = (ys[0] + ys[ys.length - 1]) / 2;
    grid = new Map();
    const rowYs = { A: [], B: [] };
    for (const p of boxed) {
      const column = 1 + Math.round((cx(p) - cols[0].x) / (pitch || 1));
      const row = twoRows && cy(p) > midY ? 'B' : 'A';
      grid.set(p.portNo, { column, row });
      rowYs[row].push(cy(p));
    }
    const mid = (a) => { const s = a.slice().sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null; };
    grid.twoRows = twoRows;
    grid.x0 = cols[0].x;
    grid.pitch = pitch;
    grid.rowY = { A: mid(rowYs.A), B: mid(rowYs.B) };
    grid.portSize = { w: width, h };
  }
  Object.defineProperty(device, GRID, { value: grid, enumerable: false, configurable: true });
  return grid;
}

/**
 * The port of a device that a printed code names: by where the ports sit on
 * the photo when they carry boxes, else by counting (codeToPort). Null when
 * that column and row were not read.
 */
function portForCode(code, device) {
  const at = codeColumnRow(code);
  if (!at || !device) return null;
  const grid = panelGrid(device);
  if (!grid) return codeToPort(code, (device.ports || []).length);
  const row = grid.twoRows ? at.row : 'A';
  for (const [portNo, g] of grid) if (g.column === at.column && g.row === row) return portNo;
  return null;
}

/**
 * Where a code's port sits on the photo when the port itself was not read:
 * the column's place along the row, in the row the letter names. A box in
 * photo coordinates the size of the panel's ports, or null when the column
 * is past the panel or the row has no read port to set its height.
 */
function positionForCode(code, device) {
  const at = codeColumnRow(code);
  if (!at || !device) return null;
  const grid = panelGrid(device);
  if (!grid || at.column < 1 || at.column > CODE_BLOCK) return null;
  const row = grid.twoRows ? at.row : 'A';
  const y = grid.rowY[row] ?? grid.rowY.A ?? grid.rowY.B;
  if (y == null) return null;
  const x = grid.x0 + (at.column - 1) * grid.pitch;
  const { w, h } = grid.portSize;
  return [Math.round(x - w / 2), Math.round(y - h / 2), Math.round(x + w / 2), Math.round(y + h / 2)];
}

const unitOf = (u) => {
  const m = String(u ?? '').match(/\d+/);
  return m ? Number(m[0]) : null;
};

/**
 * Every device the sheet names, per device name:
 * normName -> { name, aliases, height, ends: [{ e, rack, unit, port, cable, inventory?, panel? }] }
 * From the manager's DEVICE_INVENTORY first (its unit is the device's own;
 * an end with no cable), then every end of every cable, then every patch
 * panel a cable's path passes (A_Patch_Panel_Name at A_Panel_RU). aliases
 * are the other names it goes by (Device_ID, the label printed on it);
 * height is its RU_Height, when the workbook gives one.
 */
function sheetDevices(doc) {
  const out = new Map();
  const entry = (name) => {
    const k = norm(name);
    if (!out.has(k)) out.set(k, { name, aliases: [], height: null, passive: false, ends: [] });
    return out.get(k);
  };
  const alias = (v, a) => { if (a && norm(a) !== norm(v.name) && !v.aliases.some((x) => norm(x) === norm(a))) v.aliases.push(String(a).trim()); };
  for (const d of sheetModel.inventoryDevices(doc)) {
    const v = entry(d.name);
    for (const a of d.aliases) alias(v, a);
    if (d.height) v.height = d.height;
    if (d.passive) v.passive = true;
    v.ends.push({ e: null, rack: d.rack, unit: d.unit, port: null, cable: null, inventory: true });
  }
  for (const c of doc?.cables || []) {
    for (const e of ['a', 'z']) {
      const end = c[e] || null;
      const name = String(c[`${e}_device`] || '').trim();
      if (name) {
        const v = entry(name);
        v.ends.push({ e, rack: String(c[`${e}_rack`] || '').trim(), unit: unitOf(c[`${e}_unit`]), port: c[`${e}_port`], cable: c });
        if (end) {
          alias(v, end.device_id);
          if (!v.height && unitOf(end.ru_height) > 0) v.height = unitOf(end.ru_height);
        }
      }
      if (end && end.panel_name) {
        const v = entry(end.panel_name);
        alias(v, end.panel_id);
        v.ends.push({ e, rack: String(c[`${e}_rack`] || '').trim(), unit: unitOf(end.panel_ru), port: end.panel_position, cable: c, panel: true });
      }
    }
  }
  return out;
}

/** A device's name and its other names, as matcher entries. */
const namesOf = (v) => [[v.name, v], ...(v.aliases || []).map((a) => [a, v])];

const sameRack = (a, b) => !!a && !!b && norm(a) === norm(b);

/**
 * Give each visible device of the view its sheet name. Mutates the devices:
 * sheetName, matchedBy ("label" | "sticker" | "flags" | "order" | "unit" |
 * null), labelMatch (how the label
 * read matched the sheet's name: "exact" | "normalised" | "one_edit", see
 * lib/label_match.js; null when not matched by label), unitExpected.
 */
function resolve(view, doc) {
  const byName = sheetDevices(doc);
  const names = labelMatch.createMatcher([...byName.values()].flatMap(namesOf));
  const rack = view.rackLabel;
  const taken = new Set();
  restorePhoto(view);
  for (const d of view.visible) { d.sheetName = null; d.matchedBy = null; d.labelMatch = null; d.unitExpected = null; d.heightExpected = null; }
  // 1. By the label read on the device (the camera's usual misreadings
  // allowed for). A name printed on the device's own sticker (scan_view
  // stickerName) is matched the same way, and also by its tail: the sticker
  // prints sis-rt02, the Excel nvs-sgkc-sis-rt02 (matchedBy "sticker").
  for (const d of view.visible) {
    if (!d.labelText) continue;
    const by = d.labelSource === 'sticker' ? 'sticker' : 'label';
    const hit = names.match(d.labelText) || (by === 'sticker' && !names.why(d.labelText) ? stickerTail(d.labelText, byName, taken) : null);
    if (hit) {
      d.sheetName = hit.value.name; d.matchedBy = by; d.labelMatch = hit.how; taken.add(norm(hit.value.name));
    }
  }
  // 1a. One name belongs to one device: when the same name was read on two,
  // the one the photo places nearer the Excel's unit keeps it; the other is
  // matched again below, by its unit.
  const unitIn = (name) => {
    const dev = byName.get(norm(name));
    const e = dev && dev.ends.find((x) => x.unit != null && (!rack || sameRack(x.rack, rack)));
    return e ? e.unit : null;
  };
  const byLabelName = new Map();
  for (const d of view.visible) {
    if (!byOwnName(d)) continue;
    const k = norm(d.sheetName);
    if (!byLabelName.has(k)) byLabelName.set(k, []);
    byLabelName.get(k).push(d);
  }
  for (const [, ds] of byLabelName) {
    if (ds.length < 2) continue;
    const u = unitIn(ds[0].sheetName);
    const gap = (d) => (u == null || !d.units.length ? 99 : Math.min(...d.units.map((x) => Math.abs(x - u))));
    ds.sort((a, b) => gap(a) - gap(b));
    for (const d of ds.slice(1)) { d.sheetName = null; d.matchedBy = null; d.labelMatch = null; }
  }
  // 1b. A label that was refused as a near-tie ("SP-RI-U15-SWO4" is one
  // character from both ...-U18-SW04 and ...-U15-SW02) is tried again against
  // the names no other device has claimed by its own label.
  const free = [...byName.entries()].filter(([k]) => !taken.has(k));
  if (free.length && free.length < byName.size) {
    const rest = labelMatch.createMatcher(free.flatMap(([, v]) => namesOf(v)));
    for (const d of view.visible) {
      if (d.sheetName || !d.labelText || !names.why(d.labelText)) continue;
      const hit = rest.match(d.labelText);
      if (hit && !taken.has(norm(hit.value.name))) {
        d.sheetName = hit.value.name; d.matchedBy = d.labelSource === 'sticker' ? 'sticker' : 'label'; d.labelMatch = hit.how; taken.add(norm(hit.value.name));
      }
    }
  }
  // 1b2. The flags on the cables hanging on a device name it: a flag line
  // ("nvs-sgkc-sis-sw05b Gi1-0-26 B12") placed on the device under it carries
  // a device name of the Excel. The name most of a device's flags carry is
  // the device, when it is an Excel device no other device has taken; a name
  // whose rack is this rack (when the rack is known) counts double.
  nameByFlags(view, byName, taken, rack);
  // 1c. The whole rack at once, top to bottom (alignByOrder below): the photo
  // numbers units badly but keeps the devices in their order. When it names
  // devices, rung 2 is not needed.
  const aligned = rack ? alignByOrder(view, byName, rack) : false;
  // 2. By the unit it stands in, in a rack the sheet names: the unit itself
  // first, then one unit either way (the photo places devices only roughly).
  if (rack && !aligned) {
    for (const slack of [0, 1]) {
      for (const d of view.visible) {
        if (d.sheetName || !d.units.length) continue;
        for (const [k, dev] of byName) {
          if (taken.has(k)) continue;
          const here = dev.ends.find((x) => sameRack(x.rack, rack) && x.unit != null && d.units.some((u) => Math.abs(u - x.unit) <= slack));
          if (here) { d.sheetName = dev.name; d.matchedBy = 'unit'; taken.add(k); break; }
        }
      }
    }
  }
  for (const d of view.visible) {
    if (!d.sheetName) continue;
    const dev = byName.get(norm(d.sheetName));
    const ends = dev ? dev.ends.filter((x) => x.unit != null) : [];
    const inRack = ends.filter((x) => rack && sameRack(x.rack, rack));
    const pick = (inRack.length ? inRack : ends)[0];
    d.unitExpected = pick ? pick.unit : null;
    d.heightExpected = dev && dev.height ? dev.height : null;
  }
  placeByExcel(view);
  return view;
}

/** How a device got its Excel name, in words for a screen. */
function nameHowWords(d) {
  const fixed = d.labelMatch && d.labelMatch !== 'exact';
  if (d.matchedBy === 'sticker') return d.labelMatch === 'tail' ? 'the name on its own sticker, matched on the part printed' : fixed ? 'the name on its own sticker, matched after correcting the reading' : 'the name on its own sticker';
  if (d.matchedBy === 'label') return fixed ? 'the label read on it, matched after correcting the reading' : 'the label read on it';
  if (d.matchedBy === 'flags') return 'the cable tags hanging on it';
  if (d.matchedBy === 'order') return 'its place in the rack, top to bottom';
  return 'the unit it stands in';
}

/** Named by what is printed on it: a label read on it, or its own sticker. */
const byOwnName = (d) => d.matchedBy === 'label' || d.matchedBy === 'sticker';

/**
 * A sticker's name as the tail of exactly one Excel device no other device
 * has taken, cut at a separator: the sticker prints sis-rt02 (the site's
 * short form) and the Excel holds nvs-sgkc-sis-rt02. Each part is compared
 * with the camera's usual confusions allowed for (label_match canon), and the
 * whole tail may be one character off when it is six or more long. Needs
 * two parts or more and five letters and digits or more: rt02 alone is too
 * short to say. Returns { value, how: 'tail' } like a matcher hit, or null.
 */
function stickerTail(text, byName, taken) {
  const parts = String(text || '').toLowerCase().split(/[-_ .]+/).filter(Boolean);
  const total = labelMatch.canon(parts.join(''));
  if (parts.length < 2 || total.length < 5) return null;
  const hits = [];
  for (const [k, dev] of byName) {
    if (taken.has(k)) continue;
    const segs = String(dev.name).toLowerCase().split(/[-_ .]+/).filter(Boolean);
    if (segs.length <= parts.length) continue;
    const tail = segs.slice(segs.length - parts.length);
    const same = tail.every((x, i) => labelMatch.canon(x) === labelMatch.canon(parts[i]));
    const near = !same && total.length >= 6 && labelMatch.oneEdit(labelMatch.canon(tail.join('')), total);
    if (same || near) hits.push({ dev, exact: same });
  }
  const exact = hits.filter((h) => h.exact);
  const pick = exact.length === 1 ? exact[0] : !exact.length && hits.length === 1 ? hits[0] : null;
  return pick ? { value: pick.dev, how: 'tail' } : null;
}

/** The visible device with ports whose box holds the point, else the nearest above or below within a box height. */
function deviceUnderBox(view, box) {
  if (!Array.isArray(box) || box.length < 4) return null;
  const cx = (box[0] + box[2]) / 2;
  const cy = (box[1] + box[3]) / 2;
  const withPorts = view.visible.filter((x) => Array.isArray(x.box) && (x.hasPorts || (x.ports || []).length || x.flagCarrier));
  const inside = withPorts.find((x) => cx >= x.box[0] && cx <= x.box[2] && cy >= x.box[1] && cy <= x.box[3]);
  if (inside) return inside;
  let best = null;
  let bestGap = Infinity;
  for (const x of withPorts) {
    if (cx < x.box[0] || cx > x.box[2]) continue;
    const h = Math.max(1, x.box[3] - x.box[1]);
    const gap = cy < x.box[1] ? x.box[1] - cy : cy - x.box[3];
    if (gap <= h && gap < bestGap) { best = x; bestGap = gap; }
  }
  return best;
}

/** A flag's device name against an Excel name: the same once normalised, or the shorter the tail of the longer (sw05b is nvs-sgkc-sis-sw05b). */
function flagNameFits(a, b) {
  const x = labelMatch.canon(a);
  const y = labelMatch.canon(b);
  if (!x || !y) return false;
  if (x === y) return true;
  const [short, long] = x.length <= y.length ? [x, y] : [y, x];
  return short.length >= 4 && long.endsWith(short);
}

/**
 * The names a device's own stickers print inside its box: "Name: sis-rt02",
 * "nvs-sgkc-wan-rt05 2001706" (a serial may follow). A line with a port is
 * a cable tag, not a sticker.
 */
const STICKER_NAME = /^(?:name[:.]?\s*)?([a-z]{2,}(?:-[a-z0-9]+)*-[a-z]*\d+[a-z]?)(?:\s+\d{5,})?$/i;
function stickerNamesIn(view, d) {
  const out = [];
  if (!Array.isArray(d.box)) return out;
  for (const l of view.flagLines || []) {
    if (l?.end || !Array.isArray(l?.box)) continue;
    const cx = (l.box[0] + l.box[2]) / 2;
    const cy = (l.box[1] + l.box[3]) / 2;
    if (cx < d.box[0] || cx > d.box[2] || cy < d.box[1] || cy > d.box[3]) continue;
    const m = String(l.text || '').trim().match(STICKER_NAME);
    if (m) out.push(m[1]);
  }
  return out;
}

function nameByFlags(view, byName, taken, rack) {
  const lines = (view.flagLines || []).filter((l) => l?.end?.device && Array.isArray(l.box));
  if (!lines.length) return;
  const votes = new Map(); // deviceId -> Map(normName -> weight)
  for (const l of lines) {
    const d = deviceUnderBox(view, l.box);
    if (!d || d.sheetName) continue;
    for (const [k, dev] of byName) {
      if (taken.has(k) || ![dev.name, ...(dev.aliases || [])].some((n) => flagNameFits(l.end.device, n))) continue;
      const here = rack ? dev.ends.some((x) => sameRack(x.rack, rack)) : false;
      // A device the Excel places only in another rack is a far end, never this one.
      if (rack && !here && dev.ends.some((x) => x.rack)) continue;
      const inRack = l.end.position && rack ? sameRack(l.end.position, rack) : false;
      const w = 1 + (here ? 1 : 0) + (inRack ? 1 : 0);
      if (!votes.has(d.deviceId)) votes.set(d.deviceId, new Map());
      const m = votes.get(d.deviceId);
      m.set(k, (m.get(k) || 0) + w);
    }
  }
  // Devices with the clearest vote first, so a name goes to the device that carries it most.
  const order = [...votes.entries()].map(([id, m]) => {
    const sorted = [...m.entries()].sort((a, b) => b[1] - a[1]);
    return { id, best: sorted[0], lead: sorted.length > 1 ? sorted[0][1] - sorted[1][1] : sorted[0][1] };
  }).sort((a, b) => b.lead - a.lead);
  for (const { id, best, lead } of order) {
    const [k, weight] = best;
    if (taken.has(k) || weight < 2 || lead < 1) continue;
    const d = view.visible.find((x) => x.deviceId === id);
    if (!d || d.sheetName) continue;
    // The device's own sticker wins over a cable tag hanging over it: a
    // router whose sticker reads wan-rt05 is not named mt02 by the tag.
    const own = stickerNamesIn(view, d);
    const known = [byName.get(k).name, ...(byName.get(k).aliases || [])];
    if (own.length && !own.some((n) => known.some((x) => flagNameFits(n, x)))) continue;
    d.sheetName = byName.get(k).name; d.matchedBy = 'flags'; d.labelMatch = null; taken.add(k);
  }
}

/*
 * The photo places devices only roughly: on the office rack every device
 * below the firewall came out one unit low, a device was seen twice and the
 * firewall was read as a router. The Excel is the record, so a device the
 * Excel names (by the label read on it, or by its unit) is placed where the Excel says, when
 * the photo is within one unit of it (two or more units off stays as read
 * and is flagged "wrong unit"). The Excel's unit is the device's lowest unit.
 * Its kind follows the name when the name says it (PP, SW, FW, RTR). A
 * device with no name that then shares a unit with a placed one is the same
 * device seen twice, and is left out. What the photo said is kept aside, so
 * resolving again starts from the photo.
 */
const PHOTO = Symbol('photo placement');
const KIND_IN_NAME = [
  [/(^|[-_ ])(PP|PATCH)\d*($|[-_ ])/i, 'Patch Panel'],
  [/(^|[-_ ])SW\d*($|[-_ ])/i, 'Switch'],
  [/(^|[-_ ])FW\d*($|[-_ ])/i, 'Firewall'],
  [/(^|[-_ ])(RTR|ROUTER)\d*($|[-_ ])/i, 'Router'],
  [/(^|[-_ ])PDU\d*($|[-_ ])/i, 'PDU'],
];

const kindOfName = (name) => { const k = KIND_IN_NAME.find(([re]) => re.test(name)); return k ? k[1] : null; };
const EDGE = new Set(['Router', 'Firewall', 'Gateway']);

/** How well a device on the photo fits a device of the Excel, higher is better. */
function fitScore(d, x) {
  let s = 0;
  if (d.labelSays) s += norm(d.labelSays) === norm(x.name) ? 10 : -3;
  if (x.kind) {
    if (x.kind === d.type) s += 3;
    else if (EDGE.has(x.kind) && EDGE.has(d.type)) s += 2;
    else s -= 4;
  }
  if (x.ports && d.ports.length) s += d.ports.length >= x.ports ? 1 : -1;
  if (d.units.length) s -= 0.2 * Math.min(...d.units.map((u) => Math.abs(u - x.unit)));
  return s;
}

/*
 * The photo numbers units badly (on the office rack the firewall came out at
 * U22 against U19, everything under it off by a different amount), but it
 * keeps the devices in their order from the top. So the devices of the photo,
 * top to bottom, are lined up with the devices the Excel puts in this rack,
 * top to bottom: the best pairing that keeps both orders, allowing for a
 * device the photo missed or one it saw that the Excel does not have. Each
 * pair scores on the label read (strongest), the kind (from the name: PP, SW,
 * FW, RTR), the ports and, lightly, the unit. A device whose own label names
 * another Excel device keeps that name (it stands where the other should).
 * Needs three or more devices on each side; returns whether it named any.
 */
function alignByOrder(view, byName, rack) {
  const excel = [];
  for (const [, dev] of byName) {
    const here = dev.ends.filter((x) => sameRack(x.rack, rack) && x.unit != null);
    if (!here.length) continue;
    excel.push({ name: dev.name, unit: here[0].unit, kind: kindOfName(dev.name), ports: new Set(here.filter((x) => x.port != null && x.port !== '').map((x) => String(x.port))).size });
  }
  excel.sort((a, b) => b.unit - a.unit);
  const yOf = (d) => (Array.isArray(d.box) ? d.box[1] : d.units.length ? -Math.max(...d.units) * 1000 : Infinity);
  const det = view.visible.filter((d) => d.units.length || Array.isArray(d.box)).sort((a, b) => yOf(a) - yOf(b));
  if (excel.length < 3 || det.length < 3) return false;
  for (const d of det) { d.labelSays = byOwnName(d) ? d.sheetName : null; d.labelBy = byOwnName(d) ? d.matchedBy : null; }
  const n = det.length; const m = excel.length; const SKIP_PHOTO = -1.5; const SKIP_EXCEL = -1;
  const best = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  const how = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(''));
  for (let i = 1; i <= n; i += 1) { best[i][0] = best[i - 1][0] + SKIP_PHOTO; how[i][0] = 'p'; }
  for (let j = 1; j <= m; j += 1) { best[0][j] = best[0][j - 1] + SKIP_EXCEL; how[0][j] = 'e'; }
  for (let i = 1; i <= n; i += 1) {
    for (let j = 1; j <= m; j += 1) {
      const pair = best[i - 1][j - 1] + fitScore(det[i - 1], excel[j - 1]);
      const skipP = best[i - 1][j] + SKIP_PHOTO;
      const skipE = best[i][j - 1] + SKIP_EXCEL;
      if (pair >= skipP && pair >= skipE) { best[i][j] = pair; how[i][j] = 'm'; }
      else if (skipP >= skipE) { best[i][j] = skipP; how[i][j] = 'p'; }
      else { best[i][j] = skipE; how[i][j] = 'e'; }
    }
  }
  const pairs = [];
  for (let i = n, j = m; i > 0 || j > 0;) {
    const h = how[i][j];
    if (h === 'm') { pairs.push([det[i - 1], excel[j - 1]]); i -= 1; j -= 1; }
    else if (h === 'p') i -= 1;
    else j -= 1;
  }
  // A pair only counts when it fits at all (a switch is never a patch panel).
  const good = pairs.filter(([d, x]) => fitScore(d, x) > -2);
  if (!good.length) return false;
  for (const d of det) { d.sheetName = null; d.matchedBy = null; }
  for (const [d, x] of good) {
    if (d.labelSays && norm(d.labelSays) !== norm(x.name)) {
      // Its own label names another device: that is what it is, standing
      // where the Excel puts the other one.
      d.sheetName = d.labelSays; d.matchedBy = d.labelBy; d.placeAt = x.unit;
    } else {
      d.sheetName = x.name; d.matchedBy = d.labelSays ? d.labelBy : 'order'; d.placeAt = x.unit;
    }
  }
  // A device left out of the pairing keeps the name its own label gives, where
  // the photo put it (and is then a wrong unit when that is not the Excel's).
  const named = new Set(det.filter((d) => d.sheetName).map((d) => norm(d.sheetName)));
  for (const d of det) {
    if (!d.sheetName && d.labelSays && !named.has(norm(d.labelSays))) {
      d.sheetName = d.labelSays; d.matchedBy = d.labelBy; named.add(norm(d.labelSays));
    }
    delete d.labelSays;
    delete d.labelBy;
  }
  return true;
}

function restorePhoto(view) {
  if (view[PHOTO]) { view.visible.splice(0, view.visible.length, ...view[PHOTO].visible); }
  for (const d of view.visible) {
    if (d[PHOTO]) Object.assign(d, d[PHOTO]);
    delete d.placeAt;
  }
}

function placeByExcel(view) {
  if (!view[PHOTO]) Object.defineProperty(view, PHOTO, { value: { visible: [...view.visible] }, enumerable: false, configurable: true });
  const placed = [];
  for (const d of view.visible) {
    // Lined up top to bottom (alignByOrder): the Excel's unit for that place.
    // Otherwise only when the photo is within one unit of the Excel's.
    const at = d.placeAt != null ? d.placeAt : d.unitExpected;
    if (!d.matchedBy || at == null || (!d.units.length && d.placeAt == null)) continue;
    if (d.placeAt == null) {
      const low = Math.min(...d.units);
      if (Math.abs(low - at) > 1 && Math.abs(Math.max(...d.units) - at) > 1) continue;
    }
    const kind = KIND_IN_NAME.find(([re]) => re.test(d.sheetName));
    if (!d[PHOTO]) Object.defineProperty(d, PHOTO, { value: { units: d.units, unitTop: d.unitTop, unitBottom: d.unitBottom, type: d.type }, enumerable: false, configurable: true });
    // The workbook's RU_Height when it gives one, else the height the photo read.
    const h = Math.max(1, d.heightExpected || d.units.length);
    d.units = Array.from({ length: h }, (_, i) => at + i);
    d.unitBottom = at;
    d.unitTop = at + h - 1;
    if (kind) d.type = kind[1];
    placed.push(d);
  }
  if (!placed.length) return;
  // A device is never taller than the gap to the next placed device above it.
  const order = placed.slice().sort((a, b) => a.unitBottom - b.unitBottom);
  for (let i = 0; i < order.length - 1; i += 1) {
    const d = order[i];
    const room = order[i + 1].unitBottom - d.unitBottom;
    if (room >= 1 && d.units.length > room) {
      d.units = d.units.slice(0, room);
      d.unitTop = d.unitBottom + room - 1;
    }
  }
  // A device read shorter than most of its kind in this rack (one 1U patch
  // panel among 2U ones) takes their height when the room above allows it.
  const heights = new Map();
  for (const d of placed) {
    if (!heights.has(d.type)) heights.set(d.type, []);
    heights.get(d.type).push(d.units.length);
  }
  for (let i = 0; i < order.length; i += 1) {
    const d = order[i];
    if (d.heightExpected) continue;
    const hs = heights.get(d.type) || [];
    const usual = hs.slice().sort((a, b) => hs.filter((x) => x === b).length - hs.filter((x) => x === a).length)[0];
    const room = i < order.length - 1 ? order[i + 1].unitBottom - d.unitBottom : usual;
    if (hs.length >= 3 && usual > d.units.length && hs.filter((x) => x === usual).length > hs.length / 2 && room >= usual) {
      d.units = Array.from({ length: usual }, (_, k) => d.unitBottom + k);
      d.unitTop = d.unitBottom + usual - 1;
    }
  }
  const used = new Set(placed.flatMap((d) => d.units));
  for (let i = view.visible.length - 1; i >= 0; i -= 1) {
    const d = view.visible[i];
    if (!d.sheetName && d.units.some((u) => used.has(u))) view.visible.splice(i, 1);
  }
}

/** The sheet's cable on one port of one named device in this rack, or null. */
function cableOn(doc, rack, name, portNo) {
  for (const c of doc?.cables || []) {
    for (const e of ['a', 'z']) {
      if (norm(c[`${e}_device`]) !== norm(name)) continue;
      const r = c[`${e}_rack`];
      if (r && rack && !sameRack(r, rack)) continue;
      if (portNumber(c[`${e}_port`]) === portNo) return { cable: c, e };
    }
  }
  return null;
}

/** The far end of cable c from its end e: { device, port, rack, unit }. */
function otherEndOf(c, e) {
  const o = e === 'a' ? 'z' : 'a';
  const port = c[`${o}_port`];
  return {
    device: String(c[`${o}_device`] || '').trim() || null,
    port: port != null && port !== '' ? String(port) : null,
    rack: String(c[`${o}_rack`] || '').trim() || null,
    unit: unitOf(c[`${o}_unit`]),
  };
}

/** GET /scans/:scanId/sheet-check */
function check(view, doc) {
  const out = {
    rackLabel: view.rackLabel || null,
    matrixImportId: doc?.import?.id ?? null,
    units: [],
    missing: [],
    unexpected: [],
    // The two dust-cap marks a person puts on a port, kept apart: an empty
    // port with no cap, and an optic fitted (staged) with no cap. uncapped
    // also carries what the photo found (markedBy "photo", with the reason):
    // an empty port the dust-cap check read as open, on a port no person has
    // said anything about. A person's mark, or a mark a person took off,
    // always wins over the photo.
    uncapped: [],
    opticNoCap: [],
    // Ports the photo could not settle (capState "uncertain"), same shape.
    capUnsure: [],
    // Ports whose type the workbook names (Port_Type) and the photo reads as
    // another family: an SFP cage where the workbook says QSFP28.
    wrongType: [],
    notSeen: [],
  };
  if (doc) resolve(view, doc);
  // What the workbook expects on each port of each named device (lib/sheet_model.js).
  const devicesOfSheet = doc ? sheetDevices(doc) : null;
  const expectCache = new Map();
  const expectFor = (d) => {
    if (!doc || !d.sheetName) return null;
    if (!expectCache.has(d.deviceId)) {
      const dev = devicesOfSheet.get(norm(d.sheetName));
      expectCache.set(d.deviceId, sheetModel.expectedPorts(doc, d.sheetName, view.rackLabel, dev ? dev.aliases : [], (code) => portForCode(code, d)));
    }
    return expectCache.get(d.deviceId);
  };
  const nameOf = (d) => d.sheetName || d.labelText || [d.make, d.model].filter(Boolean).join(' ') || `${d.type}${d.unitTop ? ` U${d.unitTop}` : ''}`;
  for (const d of view.visible) {
    for (const p of d.ports) {
      const row = { deviceId: d.deviceId, deviceName: nameOf(d), portNo: p.portNo };
      const want = expectFor(d)?.get(p.portNo) || null;
      // The port type, when the workbook names one and the photo read the
      // port's cage (the SFP model), or a person set it, or the port type
      // model was clear about it.
      const typeRead = p.fixedType || p.sfpType || (typeof p.confidence === 'number' && p.confidence >= 0.7 ? p.type : null);
      const wantFamily = portFamily(want?.portType);
      const gotFamily = typeRead ? sheetModel.photoFamily({ sfpType: p.sfpType, fixedType: p.fixedType, type: typeRead }) : null;
      if (wantFamily && gotFamily && wantFamily !== gotFamily) {
        out.wrongType.push({ ...row, expected: want.portType, found: p.fixedType || p.sfpType || p.type, markedBy: p.fixedType ? 'person' : 'photo' });
      }
      if (p.uncapped) out.uncapped.push({ ...row, markedBy: p.uncapped.markedBy, at: p.uncapped.at });
      if (p.opticNoCap) out.opticNoCap.push({ ...row, markedBy: p.opticNoCap.markedBy, at: p.opticNoCap.at });
      // The photo speaks only where no person has: no mark on the port, and
      // no mark taken off it (capByPerson "capped").
      if (p.capByPerson) continue;
      // A cap off is told only where the port itself was read reliably (a
      // close-up, or a clear read on the rack photo), as a missing cable is.
      const reliable = p.stateSource === 'close-up' || p.stateSource === 'person'
        || (typeof p.confidence === 'number' && p.confidence >= 0.7);
      // Dust caps protect optics: the photo flags a missing cap only on a
      // fibre or SFP-type port. An open copper (RJ45) port is not a fault.
      // The workbook's Expected_Dust_Cap decides when it says: No (an optic
      // or cable is expected there) is never a fault, Yes always counts.
      if (want?.dustCap === 'No') continue;
      if (want?.dustCap !== 'Yes' && !opticalPort(p)) continue;
      if (p.state === 'empty' && p.capState === 'empty' && reliable) {
        out.uncapped.push({ ...row, markedBy: 'photo', at: null, reason: p.capReason || null });
      } else if (p.capState === 'uncertain' && p.state !== 'connected') {
        out.capUnsure.push({ ...row, markedBy: 'photo', at: null, reason: p.capReason || null });
      }
    }
  }
  if (!doc) {
    out.units = view.visible.map((d) => ({ deviceId: d.deviceId, name: nameOf(d), unitFound: d.unitTop, unitExpected: null, status: 'not_in_sheet', matchedBy: null }));
    return out;
  }
  const rack = view.rackLabel;
  // Devices the Excel puts in this rack that the photo did not find.
  const matched = new Set(view.visible.filter((d) => d.sheetName).map((d) => norm(d.sheetName)));
  out.notSeen = [];
  if (rack) {
    for (const [k, dev] of sheetDevices(doc)) {
      const here = dev.ends.find((x) => sameRack(x.rack, rack));
      // A cable manager or blank panel (lib/sheet_model.js PASSIVE) is read as
      // an unidentified unit by the photo's model, so it is never "not seen".
      if (here && here.unit != null && !matched.has(k) && !dev.passive) out.notSeen.push({ name: dev.name, unit: here.unit });
    }
    out.notSeen.sort((a, b) => (b.unit ?? 0) - (a.unit ?? 0));
  }
  for (const d of view.visible) {
    let status = 'not_in_sheet';
    // One unit either way is within what a rack photo can place; only a
    // device two or more units off is called in the wrong unit.
    const near = (u) => d.units.some((x) => Math.abs(x - u) <= 1);
    if (d.sheetName) status = d.unitExpected == null || near(d.unitExpected) ? 'ok' : 'wrong_unit';
    out.units.push({ deviceId: d.deviceId, name: nameOf(d), unitFound: d.unitTop, unitExpected: d.unitExpected, status, matchedBy: d.matchedBy,
      labelMatch: d.labelMatch || null, labelRead: byOwnName(d) ? d.labelText : null });
    if (!d.sheetName) continue;
    // What the sheet says about this device's ports, against what the photo shows.
    const sheetPorts = new Map();
    // A patch panel's printed position codes ("53A") are not port numbers
    // (panel 3's first port carries 53A): they are never read as port 53, and
    // a device the Excel names by codes cannot have an unexpected cable told.
    let byCodes = false;
    for (const c of doc.cables || []) {
      for (const e of ['a', 'z']) {
        if (norm(c[`${e}_device`]) !== norm(d.sheetName)) continue;
        const r = c[`${e}_rack`];
        if (r && rack && !sameRack(r, rack)) continue;
        if (POSITION_CODE.test(String(c[`${e}_port`] ?? ''))) {
          // A code names a column and row; the port it is placed on can be
          // told missing, but an extra cable is never accused on a coded device.
          byCodes = true;
          const at = portForCode(c[`${e}_port`], d);
          if (at != null && !sheetPorts.has(at)) sheetPorts.set(at, { c, e });
          continue;
        }
        const n = portNumber(c[`${e}_port`]);
        if (n != null && !sheetPorts.has(n)) sheetPorts.set(n, { c, e });
      }
    }
    // Missing cable: the sheet lists a cable on this port (at its A-end or
    // its Z-end) and the photo reads the port as empty.
    // Only a port state that can be relied on raises a flag: seen in a
    // close-up, set by a person, or read clearly on the rack photo (0.7 or more, the detector rarely scores above 0.8). A far
    // whole-rack photo guesses port states too roughly to accuse a cable.
    const sure = (p) => p.stateSource === 'close-up' || p.stateSource === 'person'
      || (typeof p.confidence === 'number' && p.confidence >= 0.7);
    for (const [n, { c, e }] of sheetPorts) {
      const p = d.ports.find((x) => x.portNo === n);
      if (p && p.state === 'empty' && sure(p)) {
        out.missing.push({ deviceId: d.deviceId, deviceName: d.sheetName, portNo: n, cableLabel: c.cable_label || null,
          cableType: c.cable_type || null, end: e, otherEnd: otherEndOf(c, e) });
      }
    }
    // The workbook's own expectations: a port PORT_INVENTORY expects occupied,
    // and a patch panel position a cable's path passes, with no cable row
    // ending on it. Empty on the photo is missing; Expected_Occupied No and
    // connected is an extra cable, even on a panel named by codes.
    const want = expectFor(d) || new Map();
    for (const [n, x] of want) {
      if (sheetPorts.has(n) || !(x.occupied === 'Yes' || x.panel)) continue;
      const p = d.ports.find((y) => y.portNo === n);
      if (p && p.state === 'empty' && sure(p)) {
        out.missing.push({ deviceId: d.deviceId, deviceName: d.sheetName, portNo: n, cableLabel: x.cable ? x.cable.cable_label || null : null,
          cableType: x.cable ? x.cable.cable_type || null : null, end: x.end || null, otherEnd: x.cable && x.end ? otherEndOf(x.cable, x.end) : null,
          expectedBy: x.panel ? 'panel' : 'port' });
      }
    }
    for (const p of d.ports) {
      const x = want.get(p.portNo);
      const expected = sheetPorts.has(p.portNo) || (x && (x.occupied === 'Yes' || x.panel));
      const saysEmpty = x && x.occupied === 'No';
      if ((saysEmpty || !byCodes) && p.state === 'connected' && !expected && sure(p)) {
        out.unexpected.push({ deviceId: d.deviceId, deviceName: d.sheetName, portNo: p.portNo, ...(saysEmpty ? { expectedBy: 'port' } : {}) });
      }
    }
  }
  return out;
}

module.exports = { resolve, check, nameHowWords, stickerTail, cableOn, sheetDevices, sameRack, otherEndOf, codeToPort, codeColumnRow, panelGrid, portForCode, positionForCode };
