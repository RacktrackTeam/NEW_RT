/**
 * One scan as the physical-layer work reads it: the rack's label, its devices
 * (1-based deviceId, the number every screen and route uses), their units,
 * their ports with the state the photo shows, the labels read, the zone
 * close-ups taken and the ports marked uncapped.
 *
 * Read only from the scan's own files (lib/store.js builds every path):
 *   engine/device_unit_map.json   devices, units, ports (the engine's reading)
 *   engine/labels-front.json      text read on the whole rack photo, and the cable flag lines
 *   engine/ocr_devices.json       make and model read per device
 *   result.json (api.devices)     make and model after people's corrections
 *   zones/<zone id>.json          the close-ups (routes/scan_work.js writes them)
 *   uncapped.json                 { "<deviceId>:<portNo>": { markedBy, at, kind } }
 *                                 kind "uncapped" (empty port, no dust cap; the
 *                                 default for marks made before kinds),
 *                                 "optic_no_cap" (an optic fitted, no dust cap)
 *                                 or "capped" (a person took a mark off: the
 *                                 cap is on, and what the photo says about
 *                                 this port's cap is not raised again)
 *   corrections.jsonl             port_state rows a person confirmed or changed
 *
 * Where two sources say different things about one port, the closer look
 * wins: a person's correction, then a zone close-up, then the rack photo.
 *
 * The photo's own dust-cap read (pipeline/cap_check.py: cap_state and
 * cap_reason on each engine port, and on each close-up port when the zone
 * JSON carries them) is passed through as capState and capReason. It is a
 * finding, never a mark: lib/sheet_check.js turns it into one only where no
 * person has said anything about that port.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const store = require('./store');
const { rackNameFor } = require('./rack_name');
const fieldSources = require('./field_sources');
const { isAssetTag } = require('./device_lookup');
const { modelFromText, modelInText, matchesShape } = require('./model_read');

// Rows the engine adds for gaps in the rack; never a device.
const HIDDEN = new Set(['Empty', 'Closed Unit', 'Unidentified']);
// The kinds of device a technician patches.
const PORT_TYPES = new Set(['Switch', 'Patch Panel', 'Router', 'Gateway', 'Firewall', 'PDU']);

const unitNum = (u) => {
  const m = String(u ?? '').match(/\d+/);
  if (!m) return null;
  const n = Number(m[0]);
  return n >= 1 && n <= 58 ? n : null;
};

/** "d3", "3" or 3 -> 3; anything else -> null. */
/**
 * A cable's colour as the photo read it, only when the cable classifier was
 * sure of it. From a whole-rack photo a cable's crop holds its neighbours too,
 * so one cable of another colour among many is often read in theirs (QA,
 * 5 October 2026); below this score the colour is left out, not guessed.
 */
const COLOUR_MIN = 0.75;
function colourOf(p) {
  if (!p?.cable_color) return null;
  const c = typeof p.cable_confidence === 'number' ? p.cable_confidence : null;
  return c == null || c >= COLOUR_MIN ? p.cable_color : null;
}

function deviceIndexOf(raw) {
  const m = String(raw ?? '').trim().match(/^d?(\d{1,3})$/i);
  if (!m) return null;
  const n = Number(m[1]);
  return n >= 1 ? n : null;
}

/** The port number in "Et18", "Gi1/0/18", "Port 18" or "18"; null when none. */
function portNumber(port) {
  const m = String(port ?? '').match(/(\d+)(?!.*\d)/);
  return m ? Number(m[1]) : null;
}

/** Upper case, letters and digits only: "fc-001 " and "FC001" compare equal. */
function norm(s) {
  return String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function readEngine(scanId, name) {
  try { return store.readJson(path.join(store.file(scanId, 'engine'), name), null); } catch { return null; }
}

function rackLabelOf(scanId) {
  const info = store.scanInfo(scanId);
  if (!info) return null;
  try {
    const rj = store.readJson(path.join(path.dirname(path.dirname(info.dir)), 'rack.json'), null);
    if (rj && rj.label) return String(rj.label);
  } catch { /* fall through */ }
  try { return rackNameFor(scanId, { tenantId: info.siteId ?? null }); } catch { return null; }
}

// A label that names a device: letters and digits with a separator, like
// SW-CORE-01 or R01-PP-A01. Brand badges and chatter are not names.
function looksLikeName(text) {
  const t = String(text || '').trim();
  return t.length >= 4 && /[A-Za-z]/.test(t) && /\d/.test(t) && /[-_/.]/.test(t);
}

/**
 * The device's own name on its sticker: "Name: sis-rt02", "nvs-sgkc-wan-rt05
 * 2001706", "ISXSGSG01". Read on the rack photo inside the device's box
 * (labels-front.json flagLines with no cable end), or in the text of the
 * device's own read (ocr_devices raw_text). A line with a port is a cable
 * tag, not a sticker. The clearest, then the longest, wins.
 */
const STICKER_LINE = /^(?:name[:.]?\s*)?([a-z]{2,}(?:-[a-z0-9]+)*-[a-z]+\d+[a-z]?)(?:\s+\d{5,})?$/i;
// The last part is letters then digits (rt02, sw05b): DGS-1210-10 is a model, not a name.
const HOSTNAME = /^[a-z]{2,}(?:-[a-z0-9]+)*-[a-z]+\d+[a-z]?$/i;
const NAMED = /\bname[:.]?\s+([a-z][a-z0-9-]{3,})/i;
const TAG_PORT = /^(gi|te|eth|et|fa|xe|hu|fo|tw|mgmt|po|lom)[a-z]*[0-9/().-]*[a-z0-9/().-]*$/i;
function stickerName(box, flagLines, rawText) {
  const found = [];
  if (Array.isArray(box) && box.length >= 4) {
    for (const l of flagLines || []) {
      if (l?.end || !Array.isArray(l?.box) || !l.text) continue;
      const cx = (l.box[0] + l.box[2]) / 2;
      const cy = (l.box[1] + l.box[3]) / 2;
      if (cx < box[0] || cx > box[2] || cy < box[1] || cy > box[3]) continue;
      const m = String(l.text).trim().match(STICKER_LINE);
      if (m) found.push({ name: m[1], conf: typeof l.conf === 'number' ? l.conf : 0.5 });
    }
  }
  const text = String(rawText || '');
  const named = text.match(NAMED);
  if (named && /\d/.test(named[1])) found.push({ name: named[1], conf: 0.5 });
  // A name followed by a port (Gi1-11, Te0, Eth1-17) is a cable tag hanging
  // in front of the device, read into its text: it names a cable end, often
  // a device in another rack, never this device.
  const toks = text.split(/\s+/);
  toks.forEach((tok, i) => {
    if (!HOSTNAME.test(tok) || /^(cat|cisco|class)/i.test(tok)) return;
    if (TAG_PORT.test(toks[i + 1] || '')) return;
    found.push({ name: tok, conf: 0.5 });
  });
  if (!found.length) return null;
  found.sort((a, b) => (b.conf - a.conf) || (b.name.length - a.name.length));
  return found[0];
}

/** The kind a device's name says: sw05b is a switch, rt02 a router, pp1 a patch panel. */
const KIND_TOKEN = [
  [/^(sw|switch)\d*[a-z]?$/i, 'Switch'], [/^(rt|rtr|router)\d*[a-z]?$/i, 'Router'], [/^(fw|firewall)\d*[a-z]?$/i, 'Firewall'],
  [/^(pp|patchpanel)\d*[a-z]?$/i, 'Patch Panel'], [/^pdu\d*[a-z]?$/i, 'PDU'], [/^(srv|svr|server)\d*[a-z]?$/i, 'Server'],
];
function kindOfName(name) {
  for (const tok of String(name || '').split(/[-_ .]+/)) {
    for (const [re, kind] of KIND_TOKEN) if (re.test(tok)) return kind;
  }
  return null;
}
/** The kinds the photo tells apart badly: a router read as a server, a switch as a router. */
const CONFUSABLE = new Set(['Server', 'Router', 'Switch', 'Firewall', 'Unidentified', 'Unknown']);

/**
 * A model read off the photo that is not the device's model: its asset tag,
 * its own name or the tail of it (SIS-RT02 of nvs-sgkc-sis-rt02), a location
 * sticker (A08-36), a serial number after S/N, or a name that does not
 * appear in the text read at all (a guess from the model list).
 */
function saneModel(model, { labelText = null, rawText = null } = {}) {
  if (!model) return false;
  const m = String(model).trim();
  if (isAssetTag(m)) return false;
  if (labelText && norm(m) === norm(labelText)) return false;
  const c = (s) => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (labelText && c(m).length >= 4 && c(labelText).endsWith(c(m))) return false;
  if (/^[A-Z]{1,2}\d{1,3}-\d{1,3}$/i.test(m)) return false;
  // A device name read off a tag or a sticker (nvs-sokc-sis-sw05b), not a model.
  if (/^[a-z]{2,}(?:-[a-z0-9]+){1,}-[a-z]+\d+[a-z]?$/i.test(m)) return false;
  if (rawText) {
    const raw = c(rawText);
    if (raw.includes(`SN${c(m)}`)) return false;
    // The model must be its own word(s) in the text (one misread letter
    // allowed), not a catalogue guess and not part of a name,
    // a cable tag, a serial, a location, a port label (RS-232) or a
    // regulatory line (ISO11801): lib/model_read.js, the reader's own rules.
    if (!modelInText(m, rawText)) return false;
  }
  return true;
}

/**
 * The model to show for a device read off the photo: the stored model when
 * it is sane, else the clean model token in the device's own text (an older
 * scan whose reader took "Name: sis-rt02" over "Cisco ASR1001-X" in the same
 * text), else null - shown as "model not read".
 */
function readModelOf(stored, { labelText = null, rawText = null, make = null } = {}) {
  // A word of no known model shape stands only on a device whose make was
  // read: a lone "B8888" with no maker is a tag, not a model.
  const shaped = stored && (make || matchesShape(String(stored).trim()));
  if (shaped && saneModel(stored, { labelText, rawText })) return { model: stored, fromText: false };
  const found = rawText ? modelFromText(rawText, make) : null;
  if (found && saneModel(found.model, { labelText, rawText: null })) return { model: found.model, fromText: true };
  return { model: null, fromText: false };
}

/** The best name-shaped label read inside a device's box on the rack photo. */
function labelInside(box, labels) {
  if (!Array.isArray(box) || box.length < 4) return null;
  let best = null;
  for (const l of labels || []) {
    const b = l.bbox || {};
    const cx = (b.x ?? 0) + (b.w ?? 0) / 2;
    const cy = (b.y ?? 0) + (b.h ?? 0) / 2;
    if (cx < box[0] - 6 || cx > box[2] + 6 || cy < box[1] - 6 || cy > box[3] + 6) continue;
    if (!looksLikeName(l.text)) continue;
    if (!best || (l.conf || 0) > (best.conf || 0)) best = l;
  }
  return best ? { text: String(best.text).trim(), confidence: best.conf ?? null } : null;
}

function readZones(scanId) {
  let dir;
  try { dir = store.file(scanId, 'zones'); } catch { return []; }
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => n.endsWith('.json')); } catch { return []; }
  const out = [];
  for (const n of names) {
    const z = store.readJson(path.join(dir, n), null);
    if (z && z.zoneId) out.push(z);
  }
  return out.sort((a, b) => String(a.at).localeCompare(String(b.at)));
}

/** The two dust-cap marks a person can put on a port. */
const CAP_FLAGS = ['uncapped', 'optic_no_cap'];

/** The kind of a stored cap record: "uncapped" | "optic_no_cap" | "capped", or null for none. */
function capKind(cap) {
  if (!cap) return null;
  return cap.kind === 'optic_no_cap' || cap.kind === 'capped' ? cap.kind : 'uncapped';
}

/**
 * A port's cap mark as its two fields: uncapped and opticNoCap, each
 * { markedBy, at } or null. A "capped" record is a person's answer that the
 * cap is on, so it is neither mark.
 */
function capFields(cap) {
  const mark = cap ? { markedBy: cap.markedBy ?? null, at: cap.at ?? null } : null;
  const kind = capKind(cap);
  return { uncapped: kind === 'uncapped' ? mark : null, opticNoCap: kind === 'optic_no_cap' ? mark : null };
}

/** The photo's dust-cap read on one raw port (engine or close-up), or nulls. */
function capRead(p) {
  const state = p && (p.cap_state ?? p.capState);
  if (!state) return { capState: null, capReason: null };
  return { capState: String(state), capReason: p.cap_reason ?? p.capReason ?? null };
}

function readUncapped(scanId) {
  try {
    const doc = store.readJson(store.file(scanId, 'uncapped'), {});
    return doc && typeof doc === 'object' ? doc : {};
  } catch { return {}; }
}

/** device_unit corrections: deviceId -> the top unit a person gave (the latest wins). */
function unitCorrections(scanId) {
  const out = {};
  try {
    for (const c of store.readCorrections(scanId)) {
      if (c.kind !== 'device_unit' || !c.target) continue;
      const n = Number(c.after?.unitTop);
      if (Number.isInteger(n) && n >= 1 && n <= 58) out[Number(c.target.device_index)] = n;
    }
  } catch { /* none */ }
  return out;
}

/** port_type corrections: "deviceId:portNo" -> the type a person gave (the latest wins). */
function portTypeCorrections(scanId) {
  const out = {};
  try {
    for (const c of store.readCorrections(scanId)) {
      if (c.kind !== 'port_type' || !c.target || c.target.port == null) continue;
      const v = c.after?.port_type;
      if (v) out[`${c.target.device_index}:${c.target.port}`] = String(v);
    }
  } catch { /* none */ }
  return out;
}

/** port_state corrections: "deviceId:portNo" -> "connected" | "empty" (the latest wins). */
function portCorrections(scanId) {
  const out = {};
  for (const c of store.readCorrections(scanId)) {
    if (c.kind !== 'port_state' || !c.target) continue;
    const key = `${c.target.device_index}:${c.target.port}`;
    const v = c.after?.state ?? c.before?.state;
    if (v === 'connected' || v === 'empty') out[key] = v;
  }
  return out;
}

function portsOf(raw) {
  const out = [];
  const main = Array.isArray(raw.ports) ? raw.ports : [];
  main.forEach((p, j) => {
    if (!p || typeof p !== 'object') return;
    out.push({ portNo: Number(p.index) || j + 1, kind: 'main', raw: p });
  });
  let next = out.reduce((m, p) => Math.max(m, p.portNo), 0);
  for (const key of ['sfp_ports', 'console_ports', 'other_ports']) {
    const list = Array.isArray(raw[key]) ? raw[key] : [];
    if (key !== 'sfp_ports') continue; // console and USB ports carry no patching
    list.forEach((p) => {
      if (!p || typeof p !== 'object') return;
      next += 1;
      out.push({ portNo: next, kind: 'sfp', raw: p });
    });
  }
  return out;
}

/**
 * Everything the physical-layer routes need about one scan.
 * Returns null for a scan the store does not hold.
 */
function load(scanId) {
  const info = store.scanInfo(scanId);
  if (!info) return null;
  const map = readEngine(scanId, 'device_unit_map.json') || {};
  const front = readEngine(scanId, 'labels-front.json') || {};
  const ocrDevices = readEngine(scanId, 'ocr_devices.json') || {};
  let apiDevices = [];
  try { apiDevices = store.readResult(scanId)?.api?.devices || []; } catch { apiDevices = []; }
  const meta = (() => { try { return store.readMeta(scanId) || {}; } catch { return {}; } })();
  const zones = readZones(scanId);
  const uncapped = readUncapped(scanId);
  const fixes = portCorrections(scanId);
  const typeFixes = portTypeCorrections(scanId);
  const unitFixes = unitCorrections(scanId);
  const overrides = readEngine(scanId, 'device_overrides.json') || {};

  const rawDevices = Array.isArray(map.devices) ? map.devices : [];
  // When the rails were read and the photo's rows sit a whole number of units
  // off them, the rails' numbers are shown (lib/rack_height.js unitOffset).
  const offset = Number.isInteger(meta.rackHeight?.unitOffset) ? meta.rackHeight.unitOffset : 0;
  const devices = rawDevices.map((raw, i) => {
    const deviceId = i + 1;
    const api = apiDevices[i] || {};
    const ocr = (ocrDevices.devices || [])[i] || {};
    let units = (raw.units || []).map(unitNum).filter((n) => n != null).map((n) => n + offset).filter((n) => n >= 1 && n <= 58);
    // A person said where it stands: the same height, from that top unit down.
    const unitFixed = unitFixes[deviceId] != null;
    if (unitFixed) {
      const h = Math.max(1, units.length);
      units = Array.from({ length: h }, (_, k) => unitFixes[deviceId] - k).filter((n) => n >= 1);
    }
    // The label read in the box, unless the device's own sticker names it better.
    const readLabel = labelInside(raw.box, front.labels);
    const sticker = stickerName(raw.box, front.flagLines, ocr.raw_text);
    const label = sticker && (!readLabel || sticker.conf >= (readLabel.confidence ?? 0)) ? { text: sticker.name, confidence: sticker.conf } : readLabel;
    const zoneState = {};
    // The close-up's dust-cap read per port, when the zone JSON carries it;
    // a close-up is the closer look, so it wins over the rack photo's read.
    const zoneCap = {};
    for (const z of zones) {
      if (Number(z.deviceId) !== deviceId) continue;
      for (const p of z.ports || []) {
        zoneState[p.portNo] = p.state;
        const read = capRead(p);
        if (read.capState) zoneCap[p.portNo] = read;
      }
      for (const l of z.labels || []) {
        if (l.portNo != null && l.text && !zoneState[l.portNo]) zoneState[l.portNo] = 'connected';
      }
    }
    const ports = portsOf(raw).map(({ portNo, kind, raw: p }) => {
      const photo = p.status === 'connected' ? 'connected' : p.status === 'empty' ? 'empty' : 'unknown';
      const fixed = fixes[`${deviceId}:${portNo}`];
      const state = fixed || zoneState[portNo] || photo;
      const cap = uncapped[`${deviceId}:${portNo}`];
      const fixedType = typeFixes[`${deviceId}:${portNo}`] || null;
      return {
        portNo,
        kind,
        type: fixedType || p.port_type || p.class_name || null,
        // The cage type the SFP type model read (SFP, QSFP, OSFP), when it ran.
        sfpType: p.sfp_type || null,
        fixedType,
        raw: p,
        state,
        stateSource: fixed ? 'person' : zoneState[portNo] ? 'close-up' : 'photo',
        confidence: typeof p.confidence === 'number' ? p.confidence : null,
        box: Array.isArray(p.box) && Array.isArray(raw.box)
          ? [raw.box[0] + p.box[0], raw.box[1] + p.box[1], raw.box[0] + p.box[2], raw.box[1] + p.box[3]]
          : null,
        cable: state === 'connected' && (p.cable_connector || colourOf(p))
          ? [p.cable_connector, colourOf(p)].filter(Boolean).join(', ') : null,
        cableConnector: state === 'connected' ? (p.cable_connector || null) : null,
        cableColor: state === 'connected' ? colourOf(p) : null,
        // What the photo says about the dust cap (connected, empty, capped,
        // uncertain, too_small) and why, close-up first.
        ...(zoneCap[portNo] || capRead(p)),
        ...capFields(cap),
        // What a person said about the cap, if anything: uncapped,
        // optic_no_cap or capped (a mark taken off). A photo finding never
        // overrides it.
        capByPerson: capKind(cap),
      };
    });
    // Ports a close-up saw beyond what the rack photo counted.
    for (const [no, st] of Object.entries(zoneState)) {
      const n = Number(no);
      if (!ports.some((p) => p.portNo === n)) {
        const fixed = fixes[`${deviceId}:${n}`];
        const cap = uncapped[`${deviceId}:${n}`];
        ports.push({ portNo: n, kind: 'main', type: null, state: fixed || st, stateSource: fixed ? 'person' : 'close-up',
          confidence: null, box: null, cable: null, cableConnector: null, cableColor: null,
          ...(zoneCap[n] || { capState: null, capReason: null }), ...capFields(cap), capByPerson: capKind(cap) });
      }
    }
    ports.sort((a, b) => a.portNo - b.portNo);
    for (const p of ports) {
      p.fields = fieldSources.portFields({ p, raw: p.raw || {}, fixedType: p.fixedType || null });
      delete p.raw;
      delete p.fixedType;
    }
    let type = raw.class_name || 'Unknown';
    // The device's own name says what it is: the photo reads a router as a
    // server too often, the name on its sticker does not.
    const kind = kindOfName(label?.text);
    if (kind && CONFUSABLE.has(type)) type = kind;
    // A device the photo could not identify that carries cable flags naming
    // interface ports (Gi1-0-26, Eth1-17, Te0) is switching gear: the flags
    // hang from its ports. It is shown, and the flags name it from the Excel.
    let flagCarrier = false;
    if (type === 'Unidentified' && Array.isArray(raw.box)) {
      const [bx1, by1, bx2, by2] = raw.box;
      const mine = (front.flagLines || []).filter((l) => l?.end?.device && Array.isArray(l.box)
        && (l.box[0] + l.box[2]) / 2 >= bx1 && (l.box[0] + l.box[2]) / 2 <= bx2
        && (l.box[1] + l.box[3]) / 2 >= by1 && (l.box[1] + l.box[3]) / 2 <= by2);
      if (mine.some((l) => /^(gi|te|eth|et|fa|xe|hu|fo|mgmt|po)\w*[\d/-]/i.test(String(l.end.port || '')))) {
        type = 'Switch';
        flagCarrier = true;
      }
    }
    // The key a person's make and model are kept under (engine/device_overrides.json):
    // the device's lowest unit as the engine numbers it, "U04".
    const rawLow = (raw.units || []).map(unitNum).filter((n) => n != null);
    const position = String(api.position || ocr.position || (rawLow.length ? `U${String(Math.min(...rawLow)).padStart(2, '0')}` : '')).split(/[\s-]/)[0];
    const override = position && overrides[position] ? overrides[position] : null;
    // A person's make and model win over what was read.
    const make = override?.make || (api.make ?? ocr.make ?? null);
    // A model read off the photo is kept only when it is a model (saneModel):
    // not the device's asset tag, its name, a location sticker or a serial.
    const readModel = api.model ?? ocr.model ?? null;
    const read = readModelOf(readModel, { labelText: label?.text, rawText: ocr.raw_text, make });
    const model = override?.model || read.model;
    return {
      deviceId,
      index: deviceId,
      type,
      hidden: HIDDEN.has(type),
      flagCarrier,
      box: Array.isArray(raw.box) ? raw.box : null,
      units,
      unitTop: units.length ? Math.max(...units) : null,
      unitBottom: units.length ? Math.min(...units) : null,
      heightU: units.length ? units.length : null,
      confidence: typeof raw.confidence === 'number' ? raw.confidence : null,
      source: raw.class_name_source === 'user_corrected' ? 'corrected' : 'camera',
      make,
      model,
      positionKey: position || null,
      labelText: label?.text ?? null,
      labelConfidence: label?.confidence ?? null,
      // Where the name came from: the device's own sticker (its hostname
      // printed on it) or a name-shaped label read inside its box.
      labelSource: label ? (label === readLabel ? 'photo' : 'sticker') : null,
      // Where each detail came from and how sure (lib/field_sources.js).
      fields: (() => {
        // A model found in the text now (not the one stored) has no match score of its own.
        const ocrRow = ocr && Object.keys(ocr).length ? (read.fromText ? { ...ocr, model: null } : ocr) : null;
        const f = fieldSources.deviceFields({ raw, ocr: ocrRow, override, make, model,
          labelText: label?.text ?? null, labelConfidence: label?.confidence ?? null, units });
        if (unitFixed) f.unit = fieldSources.field('person', null, 'set by a person');
        return f;
      })(),
      portCount: typeof raw.port_count === 'number' && raw.port_count > 0 ? raw.port_count : ports.filter((p) => p.kind === 'main').length,
      hasPorts: PORT_TYPES.has(type) || ports.length > 0,
      ports,
    };
  });

  return {
    scanId,
    info,
    siteId: info.siteId,
    orgId: info.orgId,
    rackLabel: rackLabelOf(scanId),
    // How tall the rack is and how that was decided (lib/rack_height.js, set
    // after the analysis); null for a scan read before heights were decided.
    unitsTotal: Number.isInteger(meta.rackHeight?.unitsTotal) ? meta.rackHeight.unitsTotal : null,
    height: meta.rackHeight || null,
    // How the rack was named after the analysis (lib/rack_naming.js), or null.
    naming: meta.rackNaming || null,
    takenAt: meta.timestamp || info.createdAt,
    by: meta.username || null,
    devices,
    visible: devices.filter((d) => !d.hidden),
    zones,
    uncapped,
    frontLabels: Array.isArray(front.labels) ? front.labels : [],
    // The cable flag lines read on the rack photo (pipeline/cable_flags.py): each
    // with its box and, when it has that shape, its end (device, port, position).
    flagLines: Array.isArray(front.flagLines) ? front.flagLines : [],
    imageSize: front.image_size || null,
  };
}

/**
 * Put one of the two dust-cap marks on a port, or take it off (flag null):
 *   uncapped       the port is empty and its dust cap is off
 *   optic_no_cap   an optic is fitted (staged, no cable) and has no dust cap
 * A port carries one mark at most. Kept in the scan's uncapped.json, keyed
 * "<deviceId>:<portNo>". Taking a mark off is kept too, as kind "capped":
 * the person looked and the cap is on, so the photo's own finding for that
 * port (lib/sheet_check.js) is not raised again. Returns the mark, or null
 * when the port now carries none.
 */
function setCapFlag(scanId, deviceId, portNo, flag, user) {
  if (flag != null && !CAP_FLAGS.includes(flag)) throw new Error(`unknown cap flag ${flag}`);
  const file = store.file(scanId, 'uncapped');
  const doc = readUncapped(scanId);
  const key = `${Number(deviceId)}:${Number(portNo)}`;
  doc[key] = { markedBy: user?.username ?? null, at: new Date().toISOString(), kind: flag || 'capped' };
  store.writeJsonAtomic(file, doc);
  return flag ? doc[key] : null;
}

/**
 * Mark (or unmark) one port uncapped: the dust cap is off and no cable is in
 * it. Unmarking leaves an "optic fitted, no cap" mark alone.
 */
function setUncapped(scanId, deviceId, portNo, value, user) {
  if (value) return setCapFlag(scanId, deviceId, portNo, 'uncapped', user);
  const cur = readUncapped(scanId)[`${Number(deviceId)}:${Number(portNo)}`];
  if (cur && cur.kind === 'optic_no_cap') return null;
  return setCapFlag(scanId, deviceId, portNo, null, user);
}

/** What to call a device on a screen or a report when the sheet does not name it. */
function displayName(d) {
  if (!d) return 'Device';
  if (d.sheetName) return d.sheetName;
  if (d.labelText) return d.labelText;
  if (d.model) return [d.make, d.model].filter(Boolean).join(' ');
  const unit = d.unitTop ? ` U${d.unitTop}` : '';
  return `${d.make ? `${d.make} ` : ''}${d.type || 'Device'}${unit}`;
}

/** The same without the unit, for screens that print the unit beside it. */
function shortName(d) {
  if (!d) return 'Device';
  if (d.sheetName) return d.sheetName;
  if (d.labelText) return d.labelText;
  if (d.model) return [d.make, d.model].filter(Boolean).join(' ');
  return `${d.make ? `${d.make} ` : ''}${d.type || 'Device'}`;
}

module.exports = {
  load, displayName, shortName, setUncapped, setCapFlag, capFields, capKind, capRead, CAP_FLAGS, deviceIndexOf, portNumber, norm, readZones, readUncapped,
  looksLikeName, HIDDEN, PORT_TYPES, stickerName, kindOfName, saneModel, readModelOf,
};
