/**
 * The A-end / Z-end label check: every fibre label read in two racks, looked
 * up in the site's patching matrix, and each end placed against the device
 * and port the sheet says.
 *
 * Labels found in a rack:
 *   * the zone close-ups of its scan (each label already carries its port
 *     number, pipeline/zone_read.py),
 *   * the labels the full rack photo read (engine/labels-front.json) that sit
 *     inside a device with ports; each is given the nearest port of that
 *     device, when one is close enough, and
 *   * the cable flag lines the rack photo read (labels-front.json flagLines,
 *     pipeline/cable_flags.py): a line names its device, port and rack
 *     position, so it is placed on the device whose name it carries (the
 *     flag's "nvs-sgkc-sis-sw05b" and the faceplate's "SIS-SW05B" are one
 *     name) and on the port it names; a line whose device is cut off is
 *     placed on the device under the flag. A line naming a device that is
 *     not in this rack is the cable's far end and is not placed here.
 * A close-up is the closer look: when both read the same label on the same
 * device, the close-up's reading is the one used.
 *
 * A flag line is looked up in the sheet as the whole line, then as its
 * device and port, then as its port and position (candidatesOf), so the
 * sheet may hold whichever of those the site prints.
 *
 * Per cable end the status is:
 *   matched        the label was read on the device and port the sheet says
 *   wrong_port     the label was read, on another port or another device
 *   missing_cable  no label read, and the photo shows the sheet's port empty
 *   not_found      no label read at that end (or read with no port to place it)
 *   not_in_sheet   a label was read that no cable in the sheet carries
 *   on_tag         the cable's tag, read in one of the racks, prints this end
 *                  as the sheet lists it (device, port, and rack when
 *                  printed), but the device was not shown or named on that
 *                  photo, or this end's rack was not photographed
 * A cable end in a rack that is neither of the two checked is not part of
 * this check (status null). A cable's status is the worst of its two ends.
 *
 * Two things a rack photo half reads are still used, and said so:
 *   * a tag face folded over its port (device and rack printed, the port
 *     hidden): its port is taken from the Excel row the other face of the
 *     same tag points to (found.match / portFrom "other_face"), or from the
 *     only Excel cable on that device in that rack not yet accounted for
 *     ("only_cable"); see foldedFaces;
 *   * a label hanging between two ports (portUnder): placed on the nearer
 *     only when it is clearly nearer, else kept with both ports
 *     (found.between [x, y]); not found unless the Excel's port is neither
 *     of the two, which is a wrong port.
 *
 * A label read is looked up in the sheet with the camera's usual misreadings
 * allowed for (lib/label_match.js): each end found says how its label matched
 * (found.match: "exact", "normalised" or "one_edit") and what was read
 * (found.read), so a screen can say "matched after correcting the reading".
 *
 * Stored as <site>/checks/<id>.json (lib/store.js checksDir).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const store = require('./store');
const scanView = require('./scan_view');
const sheetCheck = require('./sheet_check');
const labelMatch = require('./label_match');

const { norm, portNumber } = scanView;
const ID_RE = /^[a-f0-9]{8,16}$/;
/** A rack-photo reading below this confidence is not taken for a cable label. */
const FAINT = 0.6;
/** A flag line whose device was cut off is kept only when read clearly. */
const NO_DEVICE_MIN_CONF = 0.7;

/**
 * A patch-panel position code such as 14A or 50B: the code printed on the
 * panel's strip over a port, not the port's number counted along the panel.
 * A label found on the right panel matches its position code; the number is
 * never compared (panel 3's first port carries 53A, not 1).
 */
function positionCode(port) {
  return /^\s*\d{1,3}\s*[A-Za-z]\s*$/.test(String(port ?? ''));
}
const RANK = { wrong_port: 4, missing_cable: 3, not_found: 2, on_tag: 1, matched: 1 };

class CheckError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// A label a person hung on a fibre: letters or digits, two or more, and not a
// bare faceplate number.
function cableShaped(text) {
  const t = String(text || '').trim();
  return t.length >= 3 && /\d/.test(t) && !/^\d{1,3}$/.test(t);
}

// What a printed tag can hold: letters, digits and the usual separators. A
// space is taken only inside a short position code ("64 A" is 64A); "43 8"
// or "2 %" is noise read off a faceplate or a strip, never a tag.
function tagShaped(text) {
  const t = String(text || '').trim();
  if (!/^[A-Za-z0-9 ._/:-]+$/.test(t)) return false;
  if (/\s/.test(t)) return /^\d{1,3}\s*[A-Za-z]$/.test(t);
  return true;
}

/**
 * The texts a flag line may stand under in the sheet, fullest first: the
 * line as printed, the device and port, the port and position, the port.
 */
function candidatesOf(l) {
  const e = l && l.end;
  const out = [String(l.text || '').trim()];
  if (e) {
    if (e.deviceRepaired && e.port) {
      if (e.position) out.push(`${e.deviceRepaired} ${e.port} ${e.position}`);
      out.push(`${e.deviceRepaired} ${e.port}`);
    }
    if (e.device && e.port) out.push(`${e.device} ${e.port}`);
    if (e.port && e.position) out.push(`${e.port} ${e.position}`);
    if (e.device && e.position) out.push(`${e.device} ${e.position}`);
  }
  return [...new Set(out.filter(Boolean))];
}

/** The sheet's cable for a label read: the first of its candidates that matches. */
function matchLabel(matcher, l) {
  for (const c of candidatesOf(l)) {
    const m = matcher.match(c);
    if (m) return m;
  }
  return null;
}

/**
 * A flag's device name against a device's name: the same once normalised
 * (lib/label_match.js canon), or the shorter the tail of the longer:
 * nvs-sgkc-sis-sw05b is SIS-SW05B is sw05b.
 */
function nameFits(a, b) {
  const x = labelMatch.canon(a);
  const y = labelMatch.canon(b);
  if (!x || !y) return false;
  if (x === y) return true;
  const [short, long] = x.length <= y.length ? [x, y] : [y, x];
  return short.length >= 4 && long.endsWith(short);
}

/**
 * The device names a flag may carry: every device the sheet names and every
 * device the scan shows. A read device token is repaired against them
 * (repairDevice): the same once normalised, one character off, or the token
 * as the end of exactly one name, four characters or more (61002 is not
 * b1002, 03b is too short to say, sgkc-sis-pg09i1 is nvs-sgkc-dc-sis-pg09i1).
 * A long token, twelve characters or more, may be two characters off
 * (mvs-s9kc-wan-n05 is nvs-sgkc-wan-n05) when exactly one name is that close.
 * A token that fits two names is left as read. Nothing is repaired without a sheet or a scan to say so.
 */
const TWO_EDITS_MIN_LENGTH = 12;
function deviceVocabulary(view, doc) {
  const names = new Set();
  for (const c of doc?.cables || []) {
    for (const e of ['a', 'z']) {
      const n = String(c[`${e}_device`] || '').trim();
      if (n) names.add(n);
    }
  }
  for (const d of view?.visible || []) {
    for (const n of [d.labelText, d.sheetName, d.name]) if (n) names.add(String(n).trim());
  }
  // One name per device: the faceplate's SIS-SW05B and the sheet's
  // nvs-sgkc-sis-sw05b are one device, and only the longer is kept, or a
  // token that ends both would be refused as fitting two.
  const all = [...names].sort((x, y) => labelMatch.canon(y).length - labelMatch.canon(x).length);
  const list = [];
  for (const n of all) {
    if (list.some((longer) => nameFits(n, longer))) continue;
    list.push(n);
  }
  return { names: list, matcher: labelMatch.createMatcher(list.map((n) => [n, n])) };
}

function repairDevice(token, vocab) {
  if (!token || !vocab || !vocab.names.length) return null;
  const m = vocab.matcher.match(token);
  if (m) return { name: m.text, how: m.how };
  // A fragment of four characters or more: three (03b) can confirm a
  // misread digit when the sheet holds a device that happens to end so.
  const c = labelMatch.canon(token);
  if (c.length < 4) return null;
  const hits = vocab.names.filter((n) => { const k = labelMatch.canon(n); return k !== c && k.length > c.length && k.endsWith(c); });
  if (hits.length === 1) return { name: hits[0], how: 'tail' };
  if (c.length >= TWO_EDITS_MIN_LENGTH) {
    const near = vocab.names.filter((n) => { const k = labelMatch.canon(n); return k.length >= TWO_EDITS_MIN_LENGTH && labelMatch.editsWithin(c, k, 2); });
    if (near.length === 1) return { name: near[0], how: 'two_edits' };
  }
  return null;
}

/**
 * A tag face that prints its device and rack but not its port: the tag is
 * folded over the port, or the port was cut off ("s-sw05b Gi B12",
 * "nvs-sgkc-sis-sw05b B12"). { device, position, port: null, portNo: null,
 * portHidden: true, printed } or null. The device must be a hyphenated name
 * of four letters and digits or more, the rack a letter and one or two
 * digits; anything between them can only be a port's letters with no number.
 */
const POSITION_TOKEN = /^[A-Z]\d{1,2}$/i;
const PORT_LETTERS_ONLY = /^(gi|gig|te|eth|et|fa|xe|hu|fo|tw|mgmt|po|e|g|t)$/i;
function portHiddenEnd(l) {
  const e = l?.end;
  if (e) {
    if (e.device && e.position && e.portNo == null) return { device: e.device, ...(e.deviceRepaired ? { deviceRepaired: e.deviceRepaired, deviceMatch: e.deviceMatch } : {}), position: e.position, port: null, portNo: null, portHidden: true, printed: e.port || null };
    return null;
  }
  const toks = String(l?.text || '').trim().split(/\s+/);
  if (toks.length < 2 || toks.length > 3) return null;
  const pos = toks[toks.length - 1];
  const dev = toks[0];
  if (!POSITION_TOKEN.test(pos) || !/[a-z]/i.test(dev) || !/-/.test(dev) || labelMatch.canon(dev).length < 4) return null;
  if (toks.length === 3 && !PORT_LETTERS_ONLY.test(toks[1])) return null;
  return { device: dev, position: pos.toUpperCase(), port: null, portNo: null, portHidden: true, printed: toks.length === 3 ? toks[1] : null };
}

/** Two ports the same: the same once normalised, or the same last number. */
function samePort(a, b) {
  if (a == null || b == null || a === '' || b === '') return false;
  if (labelMatch.canon(a) === labelMatch.canon(b)) return true;
  const x = portNumber(a);
  return x != null && x === portNumber(b);
}

/**
 * Tag faces whose port is hidden (portHiddenEnd), given their port from the
 * Excel when one row can only be theirs:
 *   other_face  the other face of the same tag (the same flag on the photo)
 *               reads the far end in full, and exactly one Excel row runs
 *               from this face's device in this face's rack to that far end
 *               and port: that row is the cable, so this end's port is the
 *               row's port ("port from the other face of its tag");
 *   only_cable  no such face, but exactly one Excel cable on this device in
 *               this rack is not yet accounted for (no tag face or label
 *               read names it): that cable is taken.
 * Each Excel row is given to one face at most. Returns
 * [{ line, end, cable, e, match, far }], end carrying the Excel's port and
 * portFrom (the match). Nothing without a sheet.
 */
function foldedFaces(view, doc) {
  const rows = doc?.cables || [];
  if (!rows.length) return [];
  const lines = [...(view.flagLines || []), ...(view.zones || []).flatMap((z) => (z.labels || []).map((l) => ({ ...l, flag: l.flag ?? null, conf: l.conf ?? l.confidence })))];
  const hidden = lines.map((l) => ({ l, h: portHiddenEnd(l) })).filter((x) => x.h);
  if (!hidden.length) return [];
  const vocab = deviceVocabulary(view, doc);
  const fullName = (end) => {
    const start = end.deviceRepaired || end.device;
    const r = repairDevice(start, vocab);
    return r ? r.name : start;
  };
  const rackFits = (c, e, pos) => !c[`${e}_rack`] || !pos || sheetCheck.sameRack(c[`${e}_rack`], pos);
  // Cables a full face (device and port) or a cable label read already stands for.
  const accounted = new Set();
  const cableMatcher = labelMatch.createMatcher(rows.flatMap((c) => [c.cable_label, c.label_a, c.label_z, c.label_alias].filter(Boolean).map((t) => [t, c])));
  for (const l of lines) {
    if (l?.end?.device && l.end.port) {
      const name = fullName(l.end);
      for (const c of rows) {
        for (const e of ['a', 'z']) {
          if (nameFits(c[`${e}_device`], name) && samePort(c[`${e}_port`], l.end.port) && rackFits(c, e, l.end.position)) accounted.add(c);
        }
      }
    }
    const m = l?.text ? cableMatcher.match(String(l.text)) : null;
    if (m && l.end?.port) accounted.add(m.value);
  }
  for (const t of view.frontLabels || []) {
    const m = t?.text ? cableMatcher.match(String(t.text)) : null;
    if (m) accounted.add(m.value);
  }
  const taken = new Set();
  const out = [];
  const portNoOf = (port, deviceName) => {
    if (positionCode(port)) {
      const d = deviceNamed(view, deviceName);
      return d ? sheetCheck.portForCode(port, d) : null;
    }
    return portNumber(port);
  };
  const give = (x, near, c, e, match, far) => {
    taken.add(c);
    const port = c[`${e}_port`] != null ? String(c[`${e}_port`]) : null;
    out.push({
      line: x.l,
      end: { ...x.h, device: x.h.device, ...(labelMatch.canon(near) !== labelMatch.canon(x.h.device) ? { deviceRepaired: near, deviceMatch: x.h.deviceMatch || repairDevice(x.h.device, vocab)?.how || 'tail' } : {}),
        port, portNo: port != null ? portNoOf(port, near) : null, portFrom: match },
      cable: c, e, match, far,
    });
  };
  // The other face first: it is the stronger evidence.
  const rest = [];
  for (const x of hidden) {
    const near = fullName(x.h);
    const sibs = x.l.flag != null ? lines.filter((y) => y !== x.l && y.flag === x.l.flag && y.end?.device && y.end.port) : [];
    const hits = [];
    for (const sib of sibs) {
      const farName = fullName(sib.end);
      if (nameFits(farName, near)) continue;
      for (const c of rows) {
        if (taken.has(c)) continue;
        for (const e of ['a', 'z']) {
          const o = e === 'a' ? 'z' : 'a';
          if (!nameFits(c[`${e}_device`], near) || !rackFits(c, e, x.h.position)) continue;
          if (!nameFits(c[`${o}_device`], farName) || !samePort(c[`${o}_port`], sib.end.port) || !rackFits(c, o, sib.end.position)) continue;
          if (!hits.some((h) => h.c === c)) hits.push({ c, e, sib });
        }
      }
    }
    if (hits.length === 1) {
      const { c, e, sib } = hits[0];
      give(x, near, c, e, 'other_face', { device: sib.end.device, port: sib.end.port, rack: sib.end.position || null, read: sib.text });
    } else if (!hits.length) {
      // The far device the other face names, even with its port cut: a
      // cable taken below must run to it.
      const farNames = x.l.flag != null ? lines.filter((y) => y !== x.l && y.flag === x.l.flag && y.end?.device).map((y) => fullName(y.end)).filter((n) => !nameFits(n, near)) : [];
      rest.push({ x, near, farNames });
    }
  }
  for (const { x, near, farNames } of rest) {
    const free = [];
    for (const c of rows) {
      if (taken.has(c) || accounted.has(c)) continue;
      for (const e of ['a', 'z']) {
        if (!nameFits(c[`${e}_device`], near) || !c[`${e}_rack`] || !sheetCheck.sameRack(c[`${e}_rack`], x.h.position)) continue;
        // The other face names another far device than this cable's: not this cable.
        const o = e === 'a' ? 'z' : 'a';
        if (farNames.length && !farNames.some((n) => nameFits(n, c[`${o}_device`]))) continue;
        free.push({ c, e });
      }
    }
    if (free.length === 1) give(x, near, free[0].c, free[0].e, 'only_cable', null);
  }
  return out;
}

/** The visible device a flag line names, or null. Ambiguity is refused. */
function deviceNamed(view, name) {
  if (!name) return null;
  const hits = view.visible.filter((d) => [d.labelText, d.sheetName, d.name].some((n) => n && nameFits(name, n)));
  return hits.length === 1 ? hits[0] : null;
}

/** The visible device with ports whose box holds the point, else the nearest within a box height. */
function deviceUnder(view, box) {
  if (!Array.isArray(box) || box.length < 4) return null;
  const cx = (box[0] + box[2]) / 2;
  const cy = (box[1] + box[3]) / 2;
  const inside = view.visible.find((x) => x.hasPorts && Array.isArray(x.box)
    && cx >= x.box[0] && cx <= x.box[2] && cy >= x.box[1] && cy <= x.box[3]);
  if (inside) return inside;
  let best = null;
  let bestGap = Infinity;
  for (const x of view.visible) {
    if (!x.hasPorts || !Array.isArray(x.box)) continue;
    if (cx < x.box[0] || cx > x.box[2]) continue;
    const h = Math.max(1, x.box[3] - x.box[1]);
    const gap = cy < x.box[1] ? x.box[1] - cy : cy - x.box[3];
    if (gap <= h && gap < bestGap) { best = x; bestGap = gap; }
  }
  return best;
}

/**
 * The port a label read at (cx, cy) on a device sits on: the nearest within
 * three port heights, when it is clearly the nearer of the two nearest (its
 * gap under BETWEEN_RATIO of the other's, measured along the row, or across
 * the rows for two ports one above the other: a label hanging straight under
 * a port is that port's). A label with no clear winner is left unplaced and
 * says which two ports it hangs between. { portNo, between: [x, y] | null }.
 */
const BETWEEN_RATIO = 0.6;
function portUnder(d, cx, cy) {
  const near = [];
  for (const p of d.ports || []) {
    if (!p.box) continue;
    const px = (p.box[0] + p.box[2]) / 2;
    const py = (p.box[1] + p.box[3]) / 2;
    const h = Math.max(1, p.box[3] - p.box[1]);
    const dist = Math.hypot(px - cx, py - cy);
    if (dist <= 3 * h) near.push({ p, px, py, h, dist });
  }
  near.sort((x, y) => x.dist - y.dist);
  if (!near.length) return { portNo: null, between: null };
  if (near.length === 1) return { portNo: near[0].p.portNo, between: null };
  const [a, b] = near;
  const sameRow = Math.abs(a.py - b.py) <= 0.6 * Math.max(a.h, b.h);
  const ga = sameRow ? Math.abs(cx - a.px) : Math.abs(cy - a.py);
  const gb = sameRow ? Math.abs(cx - b.px) : Math.abs(cy - b.py);
  if (gb > 0 && ga < BETWEEN_RATIO * gb) return { portNo: a.p.portNo, between: null };
  return { portNo: null, between: [a.p.portNo, b.p.portNo].sort((x, y) => x - y) };
}

/**
 * Every label read in one scan, with the device and port it sits on. With
 * the sheet (doc), a flag line's device token is repaired against the
 * device names it and the scan know before the line is placed.
 */
function labelsOf(view, doc = null) {
  const out = [];
  const seen = new Set();
  const devName = (d) => d.sheetName || scanView.displayName(d);
  const deviceNames = new Set(view.visible.map((d) => norm(d.labelText)).filter(Boolean));
  const vocab = deviceVocabulary(view, doc);
  // The reader may already have repaired the name against names read on the
  // same photo (deviceMatch "photo"); the sheet's names are tried from that.
  const withRepair = (end) => {
    if (!end || !end.device) return end;
    const start = end.deviceRepaired || end.device;
    const r = repairDevice(start, vocab);
    if (!r || labelMatch.canon(r.name) === labelMatch.canon(start)) return end;
    return { ...end, deviceRepaired: r.name, deviceMatch: r.how };
  };
  const nameOf = (end) => end.deviceRepaired || end.device;
  // Readings that are not cable labels: a serial number printed after "S/N"
  // on a sticker, a flag cut off mid-port ("Gi1-0-"), a line naming no
  // device that was not read clearly, and a faint reading on the rack photo.
  const serials = new Set();
  for (const l of view.flagLines || []) {
    const m = String(l?.text || '').match(/^S\/?N\b[:.\s]*([A-Z0-9]{6,})/i);
    if (m) serials.add(labelMatch.canon(m[1]));
  }
  const cutOff = (l) => /[-/]$/.test(String(l.raw || l.text || '').trim());
  // A line with its device cut off is a cable tag only when the position it
  // prints is a rack the sheet knows (or this rack): "CAT6 75 B88" and
  // "TS011 B01" print no such rack and are stickers, not tags.
  const sheetRacks = new Set((doc?.cables || []).flatMap((c) => [c.a_rack, c.z_rack]).map((r) => norm(r)).filter(Boolean));
  if (view.rackLabel) sheetRacks.add(norm(view.rackLabel));
  const tagShapedEnd = (end) => !doc || !sheetRacks.size || (end.position && sheetRacks.has(norm(end.position)));
  for (const z of view.zones) {
    const d = view.devices.find((x) => x.deviceId === Number(z.deviceId));
    if (!d) continue;
    for (const l of z.labels || []) {
      if (!l.text) continue;
      // A flag line the close-up read: it names its own device, port and
      // position, so a plain word test does not apply. It is placed on this
      // device only when it names it, or names no device at all; a line
      // naming another device is the cable's far end.
      if (l.end) {
        const end = withRepair(l.end);
        if (end.device && !deviceNamed(view, nameOf(end))) continue;
        if (end.device && deviceNamed(view, nameOf(end)).deviceId !== d.deviceId) continue;
        const k = `${norm(l.text)}@${d.deviceId}`;
        if (seen.has(k)) continue;
        seen.add(k);
        out.push({ text: String(l.text), key: norm(l.text), deviceId: d.deviceId, device: devName(d),
          portNo: l.portNo ?? end.portNo ?? null, confidence: l.confidence ?? null, source: 'close-up', end,
          ...(l.portNo == null && end.portNo == null && Array.isArray(l.between) && l.between.length === 2 ? { between: l.between } : {}) });
        continue;
      }
      // Faceplate text read in a close-up (D-Link, Gigabit Switch, the
      // device's own name) is not a cable label.
      if (!cableShaped(l.text) || !tagShaped(l.text) || deviceNames.has(norm(l.text))) continue;
      const k = `${norm(l.text)}@${d.deviceId}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ text: String(l.text), key: norm(l.text), deviceId: d.deviceId, device: devName(d), portNo: l.portNo ?? null, confidence: l.confidence ?? null, source: 'close-up',
        ...(l.portNo == null && Array.isArray(l.between) && l.between.length === 2 ? { between: l.between } : {}) });
    }
  }
  // The flag lines read on the rack photo, each with its device, port and position.
  for (const l of view.flagLines || []) {
    if (!l.end || !l.text || cutOff(l)) continue;
    if (!l.end.device && ((l.conf ?? 1) < NO_DEVICE_MIN_CONF || !tagShapedEnd(l.end))) continue;
    const e = withRepair(l.end);
    // A line printing another rack the sheet knows names a device of that
    // rack (the far end of the cable), whatever its name looks like here.
    if (e.device && e.position && view.rackLabel && sheetRacks.has(norm(e.position)) && norm(e.position) !== norm(view.rackLabel)) continue;
    let d = null;
    let placedBy = null;
    if (e.device) {
      d = deviceNamed(view, nameOf(e));
      placedBy = d ? 'name' : null;
    } else {
      d = deviceUnder(view, l.box);
      placedBy = d ? 'position' : null;
    }
    if (!d) continue;
    const k = `${norm(l.text)}@${d.deviceId}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ text: String(l.text), key: norm(l.text), deviceId: d.deviceId, device: devName(d),
      portNo: e.portNo ?? null, confidence: l.conf ?? null, source: 'flag', end: e, placedBy, flag: l.flag ?? null, box: l.box || null });
  }
  // Tag faces whose port is hidden, given their port from the Excel
  // (foldedFaces): placed on their device when it is on this photo and the
  // Excel names it so.
  for (const f of doc ? foldedFaces(view, doc) : []) {
    const d = deviceNamed(view, f.end.deviceRepaired || f.end.device);
    if (!d || !d.sheetName || !nameFits(d.sheetName, f.cable[`${f.e}_device`])) continue;
    const text = String(f.line.text || '');
    const k = `${norm(text)}@${d.deviceId}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ text, key: norm(f.cable.cable_label), deviceId: d.deviceId, device: devName(d), portNo: f.end.portNo ?? null,
      confidence: f.line.conf ?? f.line.confidence ?? null, source: 'flag', end: f.end, placedBy: 'name', flag: f.line.flag ?? null,
      box: f.line.box || null, match: f.match, portFrom: f.match, cable: f.cable, far: f.far });
  }
  const names = new Set(view.visible.map((d) => norm(d.labelText)).filter(Boolean));
  for (const l of view.frontLabels) {
    const text = String(l.text || '').trim();
    // Read from across the room: only a single word with letters and digits
    // (FC-001, not "ISO Cat 5E") is taken for a cable label.
    if (!cableShaped(text) || /\s/.test(text) || !/[A-Za-z]/.test(text) || names.has(norm(text)) || norm(text) === norm(view.rackLabel)) continue;
    if ((l.conf ?? 1) < FAINT || serials.has(labelMatch.canon(text))) continue;
    const b = l.bbox || {};
    const cx = (b.x ?? 0) + (b.w ?? 0) / 2;
    const cy = (b.y ?? 0) + (b.h ?? 0) / 2;
    const d = view.visible.find((x) => x.hasPorts && Array.isArray(x.box)
      && cx >= x.box[0] && cx <= x.box[2] && cy >= x.box[1] - 4 && cy <= x.box[3] + 4);
    if (!d) continue;
    const k = `${norm(text)}@${d.deviceId}`;
    if (seen.has(k)) continue;
    seen.add(k);
    const { portNo, between } = portUnder(d, cx, cy);
    const bb = l.bbox || {};
    out.push({ text, key: norm(text), deviceId: d.deviceId, device: devName(d), portNo, confidence: l.conf ?? null, source: 'rack photo', box: [bb.x ?? 0, bb.y ?? 0, (bb.x ?? 0) + (bb.w ?? 0), (bb.y ?? 0) + (bb.h ?? 0)],
      ...(between ? { between } : {}) });
  }
  return out;
}

/** Run the check. views: { a, z } from scan_view.load; doc: the active matrix import. */
function run({ a, z, doc, by }) {
  if (!doc) throw new CheckError(409, 'Import the patching matrix for this site first. The check looks every label up in it.');
  sheetCheck.resolve(a, doc);
  sheetCheck.resolve(z, doc);
  const views = { a, z };
  const found = { a: labelsOf(a, doc), z: labelsOf(z, doc) };
  const racks = { a: a.rackLabel, z: z.rackLabel };
  const named = { a: new Set(a.visible.map((d) => norm(d.sheetName)).filter(Boolean)), z: new Set(z.visible.map((d) => norm(d.sheetName)).filter(Boolean)) };
  const sheetLabels = new Map();
  const labelEntries = [];
  for (const c of doc.cables || []) {
    for (const t of [c.cable_label, c.label_a, c.label_z, c.label_alias]) {
      if (!t) continue;
      if (!sheetLabels.has(norm(t))) sheetLabels.set(norm(t), c);
      labelEntries.push([t, c]);
    }
  }
  // Each label read, placed on the sheet's label it stands for.
  const cableMatcher = labelMatch.createMatcher(labelEntries);
  for (const l of [...found.a, ...found.z]) {
    // A face given its port from the Excel already knows its cable.
    if (l.portFrom) continue;
    const m = matchLabel(cableMatcher, l);
    l.match = m ? m.how : null;
    if (m) l.key = m.key;
  }

  // The cable tags read in either rack, each naming a device, port and
  // position. A tag is evidence for an end the photo could not place: the
  // device not shown or not named on that photo, or the far end in a rack
  // that was not photographed. Each tag speaks for one cable end.
  const tags = [];
  for (const side of a.scanId === z.scanId ? ['a'] : ['a', 'z']) {
    const v = views[side];
    const vocab = deviceVocabulary(v, doc);
    for (const l of [...(v.flagLines || []), ...(v.zones || []).flatMap((zn) => zn.labels || [])]) {
      if (!l?.end?.device || !l.text) continue;
      const r = repairDevice(l.end.device, vocab);
      const end = r && labelMatch.canon(r.name) !== labelMatch.canon(l.end.device) ? { ...l.end, deviceRepaired: r.name, deviceMatch: r.how } : l.end;
      tags.push({ text: String(l.text), end, rack: racks[side] || null, box: Array.isArray(l.box) ? l.box : null, side });
    }
    // A face whose port is hidden, with its port from the Excel (foldedFaces).
    for (const f of foldedFaces(v, doc)) {
      tags.push({ text: String(f.line.text || ''), end: f.end, rack: racks[side] || null, box: Array.isArray(f.line.box) ? f.line.box : null, side, portFrom: f.match });
    }
  }
  const usedTags = new Set();
  const onTag = (expected) => {
    if (!expected.device || !expected.port) return null;
    const want = portNumber(expected.port);
    for (const t of tags) {
      if (usedTags.has(t)) continue;
      const e = t.end;
      if (!nameFits(e.deviceRepaired || e.device, expected.device)) continue;
      if (!(labelMatch.canon(e.port) === labelMatch.canon(expected.port) || (want != null && e.portNo === want))) continue;
      if (e.position && expected.rack && !sheetCheck.sameRack(e.position, expected.rack)) continue;
      usedTags.add(t);
      return { device: expected.device, port: e.portNo != null ? String(e.portNo) : null, source: 'tag', read: t.text, match: t.portFrom || e.deviceMatch || 'exact', inRack: t.rack, box: t.box, side: t.side, ...(t.portFrom ? { portFrom: t.portFrom } : {}) };
    }
    return null;
  };

  // Which of the two scans holds one end of a cable, or null for neither.
  const sideOf = (c, e) => {
    const r = c[`${e}_rack`];
    if (r) {
      if (sheetCheck.sameRack(r, racks.a)) return 'a';
      if (sheetCheck.sameRack(r, racks.z)) return 'z';
      return null;
    }
    const dev = norm(c[`${e}_device`]);
    if (named.a.has(dev)) return 'a';
    if (named.z.has(dev)) return 'z';
    return (racks.a || racks.z) ? null : e;
  };

  const foundKeys = new Set([...found.a, ...found.z].map((l) => l.key));
  const relevant = (doc.cables || []).filter((c) => c.cable_label && (
    sideOf(c, 'a') || sideOf(c, 'z') || [c.cable_label, c.label_a, c.label_z, c.label_alias].some((t) => t && foundKeys.has(norm(t)))));

  const cables = [];
  let labelsExpected = 0;
  let labelsRead = 0;
  for (const c of relevant) {
    const entry = { cableLabel: c.cable_label, rowNo: c.row_no ?? null };
    // The manager's workbook's own IDs and the label each end should carry.
    if (c.connection_id) entry.connectionId = c.connection_id;
    if (c.cable_id) entry.cableId = c.cable_id;
    if (c.label_a || c.label_z) entry.labels = { a: c.label_a || null, z: c.label_z || null };
    // A label read on a third device is evidence for one end, not both.
    const used = new Set();
    const statuses = [];
    for (const e of ['a', 'z']) {
      const expected = { device: c[`${e}_device`] || null, port: c[`${e}_port`] || null, rack: c[`${e}_rack`] || null };
      const side = sideOf(c, e);
      if (!side) {
        // This end's rack was not photographed: the tag read at the other
        // end may still print this end as the sheet lists it.
        const t = onTag(expected);
        entry[e] = { expected, found: t, status: t ? 'on_tag' : null };
        if (t) statuses.push('on_tag');
        continue;
      }
      labelsExpected += 1;
      // This end's printed label: its own (end labels) or the cable's.
      const want = new Set([norm(c.cable_label), norm(c[`label_${e}`]), norm(c.label_alias)].filter(Boolean));
      const list = found[side].filter((l) => want.has(l.key));
      // A label read on the device of the OTHER end is that end's evidence,
      // never this end's: a code printed at the patch panel read there says
      // nothing about the switch end (the false "wrong port" of 29 Sep 2026).
      const o = e === 'a' ? 'z' : 'a';
      const otherDevice = norm(c[`${o}_device`]);
      const hit = list.find((l) => norm(l.device) === norm(expected.device))
        || list.find((l) => !used.has(l) && (!otherDevice || norm(l.device) !== otherDevice))
        || null;
      if (hit) used.add(hit);
      let status;
      let foundAt = null;
      if (hit) {
        labelsRead += 1;
        foundAt = { device: hit.device, port: hit.portNo != null ? String(hit.portNo) : null, source: hit.source, read: hit.text, match: hit.match || 'exact', box: hit.box || null, side,
          ...(hit.portFrom ? { portFrom: hit.portFrom } : {}), ...(hit.between ? { between: hit.between } : {}) };
        const sameDevice = norm(hit.device) === norm(expected.device);
        const want = portNumber(expected.port);
        if (!sameDevice) status = 'wrong_port';
        else if (positionCode(expected.port)) status = 'matched';
        // Read between two ports: neither is said, unless the Excel's port
        // is neither of them (then it is not where the Excel says).
        else if (hit.portNo == null) status = hit.between && want != null && !hit.between.includes(want) ? 'wrong_port' : 'not_found';
        else status = want === hit.portNo ? 'matched' : 'wrong_port';
        if (status === 'wrong_port') {
          const at = hit.portNo != null ? ` port ${hit.portNo}` : hit.between ? ` between ports ${hit.between[0]} and ${hit.between[1]}` : '';
          entry[`${e}Explained`] = `Label ${hit.text} found on ${hit.device}${at}, the Excel says ${expected.device || 'no device'}${expected.port ? ` port ${expected.port}` : ''}`;
        } else if (status === 'not_found' && hit.between) {
          entry[`${e}Explained`] = `Label ${hit.text} found on ${hit.device} between ports ${hit.between[0]} and ${hit.between[1]}; a close-up of those ports says which`;
        }
      } else {
        const t = onTag(expected);
        if (t) {
          labelsRead += 1;
          foundAt = t;
          status = 'on_tag';
        } else {
          const d = views[side].visible.find((x) => norm(x.sheetName) === norm(expected.device));
          const p = d ? d.ports.find((x) => x.portNo === portNumber(expected.port)) : null;
          status = p && p.state === 'empty' ? 'missing_cable' : 'not_found';
        }
      }
      entry[e] = { expected, found: foundAt, status };
      statuses.push(status);
    }
    // Matched when every end whose label was read agrees with the sheet; an
    // end not read yet is shown on its own (not_found) and does not undo the
    // end that was read. Any wrong end makes the cable wrong. A cable seen
    // only on its tag is on_tag, unless the photo shows a port of it empty.
    entry.status = statuses.includes('wrong_port') ? 'wrong_port'
      : statuses.includes('matched') ? 'matched'
        : statuses.includes('missing_cable') ? 'missing_cable'
          : statuses.includes('on_tag') ? 'on_tag'
            : statuses.sort((x, y) => (RANK[y] || 0) - (RANK[x] || 0))[0] || 'not_found';
    entry.ends = ['a', 'z'].filter((e) => entry[e]?.status).map((e) => ({ end: e, status: entry[e].status }));
    cables.push(entry);
  }

  // Labels read that no cable in the sheet carries.
  for (const side of a.scanId === z.scanId ? ['a'] : ['a', 'z']) {
    const done = new Set();
    for (const l of found[side]) {
      if (sheetLabels.has(l.key) || done.has(l.key)) continue;
      // A tag face that already stood for a cable end is not a new cable.
      if ([...usedTags].some((t) => norm(t.text) === l.key)) continue;
      done.add(l.key);
      const other = side === 'a' ? 'z' : 'a';
      // A tag names its own end; the other face of the same tag (the same
      // flag on the photo) names the far end. Both are offered to add to the Excel.
      let add = null;
      if (l.end && (l.end.deviceRepaired || l.end.device) && l.end.port) {
        const near = { device: l.end.deviceRepaired || l.end.device, port: l.end.port, rack: l.end.position || views[side].rackLabel || null };
        let far = null;
        if (l.flag != null) {
          const sib = (views[side].flagLines || []).find((x) => x.flag === l.flag && x.text !== l.text && x.end?.device && x.end?.port);
          if (sib) far = { device: sib.end.device, port: sib.end.port, rack: sib.end.position || null, read: sib.text };
        }
        add = { cable_label: l.text, a_device: near.device, a_port: near.port, a_rack: near.rack || '', z_device: far?.device || '', z_port: far?.port || '', z_rack: far?.rack || '' };
      }
      cables.push({
        cableLabel: l.text,
        rowNo: null,
        add,
        [side]: { expected: null, found: { device: l.device, port: l.portNo != null ? String(l.portNo) : null, source: l.source, read: l.text, match: null, ...(l.between ? { between: l.between } : {}) }, status: 'not_in_sheet' },
        [other]: { expected: null, found: null, status: null },
        status: 'not_in_sheet',
      });
    }
  }

  const counts = { matched: 0, onTag: 0, wrongPort: 0, notInSheet: 0, notFound: 0, missingCable: 0 };
  const KEY = { matched: 'matched', on_tag: 'onTag', wrong_port: 'wrongPort', not_in_sheet: 'notInSheet', not_found: 'notFound', missing_cable: 'missingCable' };
  for (const c of cables) counts[KEY[c.status]] += 1;
  // Cable ends whose label matched only after the reading was corrected.
  counts.correctedReadings = cables.reduce((n, c) => n + ['a', 'z'].filter((e) => c[e]?.found?.match && c[e].found.match !== 'exact' && !c[e].found.portFrom).length, 0);
  // Cable ends whose port was hidden on the tag and taken from the Excel.
  counts.portFromTag = cables.reduce((n, c) => n + ['a', 'z'].filter((e) => c[e]?.found?.portFrom).length, 0);
  counts.uncapped = [a, z].reduce((n, v) => n + v.visible.reduce((m, d) => m + d.ports.filter((p) => p.uncapped).length, 0), 0);
  counts.opticNoCap = [a, z].reduce((n, v) => n + v.visible.reduce((m, d) => m + d.ports.filter((p) => p.opticNoCap).length, 0), 0);

  const units = [...sheetCheck.check(a, doc).units, ...sheetCheck.check(z, doc).units].filter((u) => u.status !== 'not_in_sheet');
  const order = { wrong_port: 0, missing_cable: 1, not_found: 2, not_in_sheet: 3, on_tag: 4, matched: 5 };
  cables.sort((x, y) => (order[x.status] - order[y.status]) || String(x.cableLabel).localeCompare(String(y.cableLabel), undefined, { numeric: true }));

  return {
    id: crypto.randomBytes(5).toString('hex'),
    at: new Date().toISOString(),
    by,
    siteId: a.siteId,
    aScanId: a.scanId,
    zScanId: z.scanId,
    aRack: a.rackLabel || null,
    zRack: z.rackLabel || null,
    matrixImportId: doc.import?.id ?? null,
    counts,
    accuracy: {
      labelsRead,
      labelsExpected,
      readRate: labelsExpected ? Math.round((labelsRead / labelsExpected) * 1000) / 1000 : null,
      unitPlacementOk: units.filter((u) => u.status === 'ok').length,
      unitPlacementTotal: units.length,
    },
    labelsFound: { a: found.a.length, z: found.z.length },
    cables,
  };
}

// ── Storage ─────────────────────────────────────────────────────────────
function save(orgId, siteId, check) {
  store.writeJsonAtomic(path.join(store.checksDir(orgId, siteId), `${check.id}.json`), check);
  return check;
}

function get(orgId, siteId, id) {
  if (!ID_RE.test(String(id || ''))) return null;
  return store.readJson(path.join(store.checksDir(orgId, siteId), `${id}.json`), null);
}

/** Newest first: [ { id, aRack, zRack, at, by, counts } ] */
function list(orgId, siteId) {
  const dir = store.checksDir(orgId, siteId);
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => /^[a-f0-9]{8,16}\.json$/.test(n)); } catch { return []; }
  return names
    .map((n) => store.readJson(path.join(dir, n), null))
    .filter(Boolean)
    .sort((x, y) => String(y.at).localeCompare(String(x.at)))
    .map((c) => ({ id: c.id, aRack: c.aRack, zRack: c.zRack, aScanId: c.aScanId, zScanId: c.zScanId, at: c.at, by: c.by, counts: c.counts }));
}

/** The Home card: { count, last: { id, aRack, zRack, matched, problems, at } | null } */
function homeCard(orgId, siteId) {
  const all = list(orgId, siteId);
  const last = all[0] || null;
  const problems = (c) => (c.wrongPort || 0) + (c.notInSheet || 0) + (c.notFound || 0) + (c.missingCable || 0);
  return {
    count: all.length,
    last: last ? { id: last.id, aRack: last.aRack, zRack: last.zRack, matched: last.counts?.matched ?? 0, problems: problems(last.counts || {}), at: last.at } : null,
  };
}

const csvCell = (v) => {
  const s = v == null ? '' : String(v);
  return /[",\n\r]/.test(s) || /^[=+\-@]/.test(s) ? `"${(/^[=+\-@]/.test(s) ? `'${s}` : s).replace(/"/g, '""')}"` : s;
};

/** One row per cable end. */
function toCsv(check) {
  const head = ['Cable label', 'Connection ID', 'Cable ID', 'Cable status', 'End', 'Rack', 'Sheet device', 'Sheet port', 'Label expected', 'Found device', 'Found port', 'Found by', 'End status', 'Label read', 'How the label matched'];
  const rows = [head];
  for (const c of check.cables || []) {
    for (const e of ['a', 'z']) {
      const end = c[e] || {};
      if (!end.expected && !end.found) continue;
      rows.push([
        c.cableLabel, c.connectionId || '', c.cableId || '', c.status, e === 'a' ? 'A-end' : 'Z-end',
        end.expected?.rack || (e === 'a' ? check.aRack : check.zRack) || '',
        end.expected?.device || '', end.expected?.port || '', c.labels?.[e] || '',
        end.found?.device || '', end.found?.port || '', end.found?.source || '',
        end.status || 'not checked',
        end.found?.read || '',
        end.found?.match ? (labelMatch.howWords(end.found.match) || 'exact') : '',
      ]);
    }
  }
  return rows.map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

module.exports = { positionCode, portUnder, portHiddenEnd, foldedFaces, samePort, run, save, get, list, homeCard, toCsv, labelsOf, candidatesOf, matchLabel, nameFits, deviceVocabulary, repairDevice, CheckError, csvCell };
