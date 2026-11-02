/**
 * Name the rack in a scan without asking, when the scan and the site's Excel
 * dataset say which rack it is. The owner, 29 Sep 2026: "Don't ask to name
 * this rack. First read the label of the rack on the rail; if you can't find
 * it, get the labels of the devices and match which rack it is; if no label is
 * found, according to the units and its devices map the rack name from the
 * Excel; if all steps fail, ask the user."
 *
 * Runs after every analysis (a single scan, and each photo of Map), before the
 * result opens. The rungs, in order; the first that names one rack wins:
 *
 *   1. rail_label     the rack's own label, read off its rail or frame
 *                     (pipeline/rail_labels.py via side_labels, in the physical
 *                     layer report's rack.candidates). A reading equal to a
 *                     rack set up for the site or a rack in the dataset (case
 *                     and separators aside) names that rack. One character off
 *                     a single such rack, on a reading of eight or more, names
 *                     it too. A reading off the rail that matches nothing is
 *                     used as read only when the reader was sure of it
 *                     (RAIL_ALONE_CONF).
 *   2. device_labels  every text read on the devices (device labels, the chips
 *                     on the rails, the front-of-rack text, close-up labels) is
 *                     looked up in the dataset: a device name gives the rack it
 *                     stands in (aDevice -> aRack, zDevice -> zRack), a cable
 *                     label gives the racks at its two ends. The rack most of
 *                     them agree on is the rack, when at least MIN_LABEL_VOTES
 *                     texts support it and they are more than half of all the
 *                     texts that matched anything.
 *   3. layout         nothing matched by name: the scan's layout (which unit
 *                     holds which kind of device, how many ports) is compared
 *                     with each rack in the dataset. See scoreRack() for the
 *                     points; the best rack is taken only when it scores at
 *                     least LAYOUT_MIN_SCORE and beats the next by
 *                     LAYOUT_MIN_LEAD. A tie is never broken.
 *   4. person         all three failed (or there is no dataset): the result
 *                     asks "Which rack is this?", and only then.
 *
 * decide() is pure: evidence in, answer out. nameAfterScan() gathers the
 * evidence from the scan's files, the estate and the dataset, runs decide(),
 * and when a rack was named records it the way a person's answer is recorded
 * (lib/rack_identity.confirm: the rack_identity row, the scan's racks_known
 * row, and the rack folder renamed through lib/store.js), plus how it was
 * found in the scan's meta (rackNaming).
 */
'use strict';

const RAIL_MATCH_MIN_CONF = 0.3;
const RAIL_ALONE_CONF = 0.8;
const NEAR_MIN_LENGTH = 8;
const MIN_LABEL_VOTES = 2;
const LAYOUT_MIN_SCORE = 0.5;
const LAYOUT_MIN_LEAD = 0.15;
const LAYOUT_MIN_DEVICES = 2;

const round2 = (n) => Math.round(n * 100) / 100;
const labelMatch = require('./label_match');

/**
 * Text in one shape for comparing: upper case, no separators, and O or I next
 * to a digit read as 0 or 1 (the same repair lib/rack_identity applies).
 */
function keyOf(s) {
  return String(s ?? '').toUpperCase().trim()
    .replace(/([A-Z])O(?=\d)/g, (m, a) => `${a}0`)
    .replace(/(\d)O/g, (m, d) => `${d}0`)
    .replace(/([A-Z])I(?=\d)/g, (m, a) => `${a}1`)
    .replace(/(\d)I/g, (m, d) => `${d}1`)
    .replace(/[^A-Z0-9]/g, '');
}

/** One character substituted, added or dropped, on keys long enough to allow it. */
function nearlyEqual(a, b) {
  if (a === b || a.length < NEAR_MIN_LENGTH || b.length < NEAR_MIN_LENGTH) return false;
  if (Math.abs(a.length - b.length) > 1) return false;
  if (a.length === b.length) {
    let wrong = 0;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i] && (wrong += 1) > 1) return false;
    return wrong === 1;
  }
  const [s, l] = a.length < b.length ? [a, b] : [b, a];
  let i = 0;
  let j = 0;
  let skipped = false;
  while (i < s.length && j < l.length) {
    if (s[i] === l[j]) { i += 1; j += 1; continue; }
    if (skipped) return false;
    skipped = true;
    j += 1;
  }
  return true;
}

const unitOf = (u) => {
  const m = String(u ?? '').match(/\d+/);
  if (!m) return null;
  const n = Number(m[0]);
  return n >= 1 && n <= 58 ? n : null;
};

/** The kind of device a dataset name says it is: SW-CORE-01 -> Switch. Null when it does not say. */
function kindOfName(name) {
  const tokens = String(name || '').toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean);
  const has = (re) => tokens.some((t) => re.test(t));
  if (has(/^(SW|SWITCH|LSW|ASW|CSW|DSW|TOR)\d*[A-Z]?$/)) return 'Switch';
  if (has(/^(PP|PATCH|PANEL|FPP|CPP|ODF)\d*[A-Z]?$/)) return 'Patch Panel';
  if (has(/^(FW|FIREWALL|FWL)\d*[A-Z]?$/)) return 'Firewall';
  if (has(/^(RTR|ROUTER|RT|GW|GATEWAY)\d*[A-Z]?$/)) return 'Router';
  if (has(/^(SVR|SRV|SERVER|ESX|HOST|NAS)\d*[A-Z]?$/)) return 'Server';
  if (has(/^PDU\d*[A-Z]?$/)) return 'PDU';
  if (has(/^UPS\d*[A-Z]?$/)) return 'UPS';
  if (has(/^LB\d*[A-Z]?$/)) return 'Load Balancer';
  return null;
}
// Router and gateway are one family on a photo; so are the two names.
const sameKind = (a, b) => {
  const fam = (k) => (k === 'Gateway' ? 'Router' : k);
  return fam(a) === fam(b);
};

/** The racks of the dataset, each with the devices it holds: Map(key -> { name, devices: Map(key -> dev) }). */
function datasetRacks(cables) {
  const racks = new Map();
  for (const c of cables || []) {
    for (const e of ['a', 'z']) {
      const rack = String(c[`${e}_rack`] || '').trim();
      if (!rack) continue;
      const rk = keyOf(rack);
      if (!rk) continue;
      if (!racks.has(rk)) racks.set(rk, { name: rack, devices: new Map() });
      const device = String(c[`${e}_device`] || '').trim();
      if (!device) continue;
      const devs = racks.get(rk).devices;
      const dk = keyOf(device);
      if (!devs.has(dk)) devs.set(dk, { name: device, units: new Set(), ports: new Set(), kind: kindOfName(device) });
      const d = devs.get(dk);
      const u = unitOf(c[`${e}_unit`]);
      if (u != null) d.units.add(u);
      const p = String(c[`${e}_port`] || '').trim();
      if (p) d.ports.add(keyOf(p));
    }
  }
  return racks;
}

// -- Rung 1 ---------------------------------------------------------------
function byRailLabel(rail, names) {
  const readings = (rail || [])
    .filter((r) => r && typeof r.text === 'string' && keyOf(r.text).length >= 2)
    .map((r) => ({ ...r, key: keyOf(r.text), conf: Number.isFinite(Number(r.conf)) ? Number(r.conf) : 0 }))
    .sort((a, b) => b.conf - a.conf);
  if (!readings.length) return { outcome: 'no rack label was read on the rail' };
  const pool = [...names.values()];
  const matches = [];
  for (const r of readings) {
    if (r.conf < RAIL_MATCH_MIN_CONF) continue;
    const exact = pool.filter((n) => n.key === r.key);
    if (exact.length) {
      // keyOf() already mends an O or I beside a digit; that is still a corrected reading.
      const asRead = labelMatch.plain(r.text) === labelMatch.plain(exact[0].name);
      matches.push({ r, n: exact[0], tier: asRead ? 'exact' : 'normalised' });
      continue;
    }
    // The camera's usual misreadings (O for 0, I or L for 1 ...), lib/label_match.js.
    const fixed = pool.filter((n) => labelMatch.canon(n.key) === labelMatch.canon(r.key));
    if (fixed.length === 1) { matches.push({ r, n: fixed[0], tier: 'normalised' }); continue; }
    const near = pool.filter((n) => nearlyEqual(n.key, r.key));
    if (near.length === 1) matches.push({ r, n: near[0], tier: 'near' });
  }
  const racksNamed = new Set(matches.map((m) => m.n.key));
  if (racksNamed.size > 1) {
    return { outcome: `the labels on the rail name ${racksNamed.size} different racks (${[...racksNamed].map((k) => names.get(k).name).join(', ')}), so none is taken` };
  }
  if (matches.length) {
    const best = matches.find((m) => m.tier === 'exact') || matches[0];
    const conf = best.tier === 'exact' ? Math.max(0.6, best.r.conf) : best.tier === 'normalised' ? best.r.conf * 0.9 : best.r.conf * 0.85;
    return {
      named: true, name: best.n.name, knownRackId: best.n.knownRackId ?? null,
      confidence: round2(Math.min(1, conf)),
      reasons: [best.tier === 'exact'
        ? `the label "${best.r.text}" read on the rail (${best.r.source || 'rail'}, confidence ${round2(best.r.conf)}) is ${best.n.name}, ${best.n.where}`
        : best.tier === 'normalised'
          ? `the label "${best.r.text}" read on the rail is ${best.n.name}, ${best.n.where}, matched after correcting the reading`
          : `the label "${best.r.text}" read on the rail is one character from ${best.n.name}, ${best.n.where}, and from no other rack`],
    };
  }
  // A reading off the rail itself that matches nothing known: used as read,
  // only when the reader was sure of it and it looks like a name (has a digit).
  const alone = readings.find((r) => r.source === 'rack rail' && r.conf >= RAIL_ALONE_CONF && /\d/.test(r.key) && r.key.length >= 2);
  if (alone) {
    return {
      named: true, name: String(alone.text).trim().toUpperCase(), knownRackId: null,
      confidence: round2(alone.conf * 0.8),
      reasons: [`the label "${alone.text}" was read on the rack's rail with confidence ${round2(alone.conf)}; no rack of that name is set up yet, so it is used as read`],
    };
  }
  return { outcome: `a label was read on the rail ("${readings[0].text}") but it matches no rack and was not read clearly enough to use on its own` };
}

// -- Rung 2 ---------------------------------------------------------------
function byDeviceLabels(texts, cables) {
  if (!cables || !cables.length) return { outcome: 'there is no Excel dataset for this site' };
  const index = new Map(); // key -> { racks: Set(rackKey), what }
  const add = (key, rack, what) => {
    if (!key || key.length < 3 || !rack) return;
    if (!index.has(key)) index.set(key, { racks: new Map(), what });
    index.get(key).racks.set(keyOf(rack), rack);
  };
  for (const c of cables) {
    add(keyOf(c.a_device), String(c.a_rack || '').trim(), 'device');
    add(keyOf(c.z_device), String(c.z_rack || '').trim(), 'device');
    // A rack's own name, as a cable flag prints the position (B12).
    add(keyOf(c.a_rack), String(c.a_rack || '').trim(), 'rack');
    add(keyOf(c.z_rack), String(c.z_rack || '').trim(), 'rack');
    const lk = keyOf(c.cable_label);
    if (lk && lk.length >= 3) {
      add(lk, String(c.a_rack || '').trim(), 'cable');
      add(lk, String(c.z_rack || '').trim(), 'cable');
    }
  }
  // Each text read is looked up with the camera's usual misreadings allowed
  // for (lib/label_match.js): RO1-PP-A01 finds R01-PP-A01. A text read once
  // counts once, except a cable flag: every flag on the rack is its own
  // evidence, and the near ends outnumber the far ends.
  const matcher = labelMatch.createMatcher(index.entries());
  const seen = new Set();
  const hits = [];
  for (const t of texts || []) {
    const k = keyOf(t && t.text);
    if (!k || k.length < 3) continue;
    if (t.where !== 'cable flag') {
      if (seen.has(k)) continue;
      seen.add(k);
    }
    const m = matcher.match(k);
    const hit = m ? m.value : null;
    if (hit && hit.racks.size) hits.push({ text: String(t.text).trim(), how: m.how, where: t.where, ...hit });
  }
  if (!hits.length) return { outcome: `${seen.size} label${seen.size === 1 ? '' : 's'} read on the devices, none of them in the Excel dataset` };
  const support = new Map();
  for (const h of hits) {
    for (const [rk, name] of h.racks) {
      if (!support.has(rk)) support.set(rk, { name, texts: [], corrected: 0, flags: 0 });
      support.get(rk).texts.push(h.text);
      if (h.how !== 'exact') support.get(rk).corrected += 1;
      if (h.where === 'cable flag') support.get(rk).flags += 1;
    }
  }
  const ranked = [...support.values()].sort((a, b) => b.texts.length - a.texts.length);
  const [best, next] = ranked;
  const n = best.texts.length;
  if (next && next.texts.length === n) {
    return { outcome: `the labels read agree equally on ${best.name} and ${next.name}, so neither is taken` };
  }
  if (n < MIN_LABEL_VOTES) return { outcome: `only one label read ("${best.texts[0]}") is in the Excel dataset; two are needed` };
  // Cable flags print the far end of every cable too, so on a rack read from
  // its flags the other racks always share the votes: a clear plurality (at
  // least twice the runner-up) backed by flags is enough.
  const flagLed = best.flags > 0 && (!next || n >= 2 * next.texts.length);
  if (n * 2 <= hits.length && !flagLed) return { outcome: `${n} of ${hits.length} labels found in the dataset point to ${best.name}, which is not more than half` };
  const share = n / hits.length;
  return {
    named: true, name: best.name, knownRackId: null,
    confidence: round2(share * Math.min(1, 0.6 + n / 10)),
    reasons: [`${n} of the ${hits.length} labels read that are in the Excel dataset place this rack as ${best.name}: ${best.texts.slice(0, 6).map((t) => `"${t}"`).join(', ')}${best.texts.length > 6 ? ` and ${best.texts.length - 6} more` : ''}${best.corrected ? ` (${best.corrected} matched after correcting the reading)` : ''}`],
    corrected: best.corrected,
  };
}

// -- Rung 3 ---------------------------------------------------------------
/**
 * How well one dataset rack fits the scan, from 0 to 1. For each device the
 * dataset places at a unit in that rack (its lowest unit):
 *
 *   +1    the scan has a device covering that unit
 *   +0.5  instead, the scan has one a unit above or below (numbering slips)
 *   -1    the photo shows that unit and nothing is in it
 *    0    the photo does not reach that unit
 *   +1    the device found is the kind the dataset name says (SW -> Switch),
 *   -0.5  a different kind (nothing either way when the name does not say)
 *   +0.5  the device found has at least as many ports as the dataset uses on
 *         it, -0.5 fewer (nothing when either count is unknown)
 *
 * The total is divided by 2.5 points a device (the most one can earn), so a
 * rack whose every device is where the scan has the right kind of device with
 * enough ports scores 1.
 */
function scoreRack(rack, layout) {
  const placed = [...rack.devices.values()].filter((d) => d.units.size);
  if (placed.length < LAYOUT_MIN_DEVICES) return null;
  const covered = layout.flatMap((d) => d.units);
  const lo = covered.length ? Math.min(...covered) : null;
  const hi = covered.length ? Math.max(...covered) : null;
  let points = 0;
  const notes = [];
  for (const d of placed) {
    const u = Math.min(...d.units);
    let s = layout.find((x) => x.units.includes(u));
    let got = 0;
    if (s) got += 1;
    else {
      s = layout.find((x) => x.units.includes(u - 1) || x.units.includes(u + 1));
      if (s) got += 0.5;
      else if (lo != null && u >= lo && u <= hi) got -= 1;
    }
    if (s) {
      if (d.kind && s.kind) got += sameKind(d.kind, s.kind) ? 1 : -0.5;
      if (d.ports.size && s.ports > 0) got += d.ports.size <= s.ports ? 0.5 : -0.5;
    }
    if (s && got >= 2) notes.push(`${d.name} at U${u}`);
    points += got;
  }
  return { score: Math.max(0, round2(points / (2.5 * placed.length))), devices: placed.length, notes };
}

function byLayout(layout, cables) {
  if (!cables || !cables.length) return { outcome: 'there is no Excel dataset for this site' };
  const scanDevices = (layout || []).filter((d) => d && Array.isArray(d.units) && d.units.length);
  if (scanDevices.length < LAYOUT_MIN_DEVICES) return { outcome: 'too few devices were placed on units to compare the layout' };
  const scored = [];
  for (const rack of datasetRacks(cables).values()) {
    const s = scoreRack(rack, scanDevices);
    if (s) scored.push({ name: rack.name, ...s });
  }
  if (!scored.length) return { outcome: 'no rack in the Excel dataset gives units for two or more devices' };
  scored.sort((a, b) => b.score - a.score);
  const [best, next] = scored;
  const lead = next ? round2(best.score - next.score) : best.score;
  const board = scored.slice(0, 3).map((s) => `${s.name} ${s.score}`).join(', ');
  if (best.score < LAYOUT_MIN_SCORE) return { outcome: `no rack in the dataset fits the layout well enough (best: ${board})`, scores: scored.slice(0, 5) };
  if (next && lead < LAYOUT_MIN_LEAD) return { outcome: `the layout fits ${best.name} and ${next.name} about as well (${board}), so neither is taken`, scores: scored.slice(0, 5) };
  return {
    named: true, name: best.name, knownRackId: null,
    confidence: round2(Math.min(0.9, best.score * 0.9)),
    reasons: [`the layout of this scan fits ${best.name} in the Excel dataset best (score ${best.score} of 1${next ? `, next ${next.name} ${next.score}` : ''})${best.notes.length ? `: ${best.notes.slice(0, 4).join(', ')}` : ''}`],
    scores: scored.slice(0, 5),
  };
}

/**
 * decide({ rail, deviceTexts, layout, cables, knownRacks })
 *   rail         [{ text, conf, source }]   readings off the rack itself
 *   deviceTexts  [{ text }]                 every text read on the devices
 *   layout       [{ units: [n], kind, ports }]  the scan's devices
 *   cables       the dataset's rows (snake_case, lib/matrix.js), or null
 *   knownRacks   [{ id, name, facilityId }] racks set up for the site
 *   rackAliases  [{ name, aliases }] the workbook's racks (RACK_INVENTORY):
 *                its Rack_ID and Expected_Rack_Label name the rack too
 * -> { named, name, knownRackId, method, confidence, reasons, tried, ask, why }
 */
function decide({ rail = [], deviceTexts = [], layout = [], cables = null, knownRacks = [], rackAliases = [] } = {}) {
  const names = new Map();
  for (const r of knownRacks || []) {
    for (const v of [r.name, r.facilityId]) {
      const k = keyOf(v);
      if (k && !names.has(k)) names.set(k, { key: k, name: String(r.name || r.facilityId), knownRackId: r.id ?? null, where: 'a rack set up for this site' });
    }
  }
  for (const rack of datasetRacks(cables).values()) {
    const k = keyOf(rack.name);
    if (k && !names.has(k)) names.set(k, { key: k, name: rack.name, knownRackId: null, where: 'a rack in the Excel dataset' });
  }
  for (const r of rackAliases || []) {
    for (const v of [r.name, ...(r.aliases || [])]) {
      const k = keyOf(v);
      if (k && !names.has(k)) names.set(k, { key: k, name: r.name, knownRackId: null, where: v === r.name ? 'a rack in the Excel dataset' : `the label of rack ${r.name} in the Excel dataset` });
    }
  }
  const tried = [];
  const rungs = [
    ['rail_label', () => byRailLabel(rail, names)],
    ['device_labels', () => byDeviceLabels(deviceTexts, cables)],
    ['layout', () => byLayout(layout, cables)],
  ];
  for (const [method, run] of rungs) {
    const r = run();
    if (r.named) {
      // A dataset name may also be a rack already set up: bind to that row.
      const same = names.get(keyOf(r.name));
      const knownRackId = r.knownRackId ?? (same ? same.knownRackId : null);
      return { named: true, name: r.name, knownRackId, method, confidence: r.confidence, reasons: r.reasons, tried, ask: false, why: null, ...(r.scores ? { scores: r.scores } : {}) };
    }
    tried.push({ method, outcome: r.outcome });
  }
  return {
    named: false, name: null, knownRackId: null, method: null, confidence: null, reasons: [],
    tried, ask: true, why: [...new Set(tried.map((t) => t.outcome))].join('; '),
  };
}

// -- Gathering the evidence of one scan -----------------------------------
/** The physical layer report's readings off the rack itself. */
function railReadingsOf(pl) {
  return (pl?.rack?.candidates || [])
    .filter((c) => c && typeof c.text === 'string' && ['rack rail', 'rail chip', 'front label'].includes(c.source))
    .map((c) => ({ text: c.text, conf: c.conf == null ? 0 : Number(c.conf), source: c.source }))
    // A rack-shaped word in the front text is only a rack label when it
    // matches a rack by name, so it goes in with a low confidence that can
    // match but never stand alone.
    .map((c) => (c.source === 'front label' ? { ...c, conf: Math.min(c.conf, 0.5) } : c));
}

/** Every text read on the devices of a scan, from the report, the side strips, the front and the close-ups. */
function deviceTextsOf({ pl, side, front, zones }) {
  const out = [];
  const push = (text, where) => { if (typeof text === 'string' && text.trim()) out.push({ text: text.trim(), where }); };
  const units = pl?.units;
  const groups = Array.isArray(units) ? units.map((u) => u?.devices || []) : units && typeof units === 'object' ? Object.values(units) : [];
  for (const devs of groups) {
    for (const d of devs || []) {
      push(d?.label?.text, 'device label');
      push(d?.label?.raw, 'device label');
    }
  }
  for (const l of side?.labels || []) if (l && l.side !== 'rail') push(l.text, `${l.side || 'side'} strip`);
  for (const l of front?.labels || []) {
    push(l?.text, 'front');
    for (const tok of String(l?.text || '').split(/\s+/)) if (tok !== l.text) push(tok, 'front');
  }
  // The flags on the cables (pipeline/cable_flags.py): each line names a
  // device, a port and a rack position, so the device name and the position
  // both say which rack this is.
  for (const l of front?.flagLines || []) {
    const e = l?.end;
    if (!e) continue;
    push(e.device, 'cable flag');
    push(e.position, 'cable flag');
  }
  for (const z of zones || []) for (const l of z?.labels || []) push(l?.text, 'close-up');
  return out;
}

/** The scan's devices as the layout rung compares them. */
function layoutOf(view) {
  return (view?.visible || []).map((d) => ({ units: d.units || [], kind: d.type || null, ports: Number(d.portCount) || 0 }));
}

const METHOD_WORD = {
  rail_label: 'read from the rail label',
  device_labels: 'matched from the device labels and the Excel dataset',
  layout: 'matched from the Excel dataset by its layout',
  person: 'named by a person',
};
// How each way of finding it is kept in rack_identity.source.
const SOURCE_OF = { rail_label: 'label', device_labels: 'device_labels', layout: 'layout', person: 'confirmed' };
const METHOD_OF = { label: 'rail_label', device_labels: 'device_labels', layout: 'layout', confirmed: 'person' };

/**
 * Run the ladder for one scan and record what it found.
 *   nameAfterScan(rackId, { tenantId, userId, physicalLayer })
 * physicalLayer: the report, or a function (rackId) -> report.
 * -> the scan's rackNaming: { name, method, confidence, reasons, tried, ask, why, at }
 */
async function nameAfterScan(rackId, { tenantId = null, userId = null, physicalLayer = null } = {}) {
  const store = require('./store');
  const estate = require('./estate');
  const rackIdentity = require('./rack_identity');
  const matrix = require('./matrix');
  const scanView = require('./scan_view');
  const path = require('path');
  const at = new Date().toISOString();
  const meta = (() => { try { return store.readMeta(rackId) || {}; } catch { return {}; } })();
  const save = (naming) => {
    try { const m = store.readMeta(rackId) || {}; m.rackNaming = naming; store.writeMeta(rackId, m); } catch { /* bookkeeping */ }
    return naming;
  };

  if (tenantId == null) {
    return save({ name: null, method: null, confidence: null, reasons: [], tried: [], ask: true, why: 'this scan belongs to no site', at });
  }
  // Already settled: a person said, or an earlier run of this ladder did.
  const bound = rackIdentity.confirmedRack(tenantId, rackId);
  if (bound) {
    const name = bound.rack.name || bound.rack.facility_id;
    const prior = meta.rackNaming && meta.rackNaming.name && keyOf(meta.rackNaming.name) === keyOf(name) ? meta.rackNaming : null;
    return prior || save({ name, method: METHOD_OF[bound.source] || 'person', confidence: bound.source === 'confirmed' ? 1 : null, reasons: [], tried: [], ask: false, why: null, at });
  }
  let own = null;
  try { own = estate.getRackByRackId(tenantId, rackId); } catch { own = null; }
  if (own && (own.name || own.facility_id)) {
    return save({ name: own.name || own.facility_id, method: 'person', confidence: 1, reasons: ["this scan's own record carries the rack's name"], tried: [], ask: false, why: null, at });
  }

  let pl = null;
  try {
    pl = typeof physicalLayer === 'function' ? await physicalLayer(rackId) : physicalLayer;
    if (!pl) pl = rackIdentity.io.readPhysicalLayer(rackId);
  } catch { pl = null; }
  const engine = (name) => { try { return store.readJson(path.join(store.file(rackId, 'engine'), name), null); } catch { return null; } };
  let view = null;
  try { view = scanView.load(rackId); } catch { view = null; }
  const info = (() => { try { return store.scanInfo(rackId); } catch { return null; } })();
  let doc = null;
  try { doc = info ? matrix.active(info.orgId, info.siteId ?? tenantId) : null; } catch { doc = null; }
  let known = [];
  try {
    known = (estate.listRacks(tenantId) || [])
      .filter((r) => String(r.rack_id) !== String(rackId) && (r.name || r.facility_id))
      .map((r) => ({ id: r.id, name: r.name || null, facilityId: r.facility_id || null }));
  } catch { known = []; }

  const sheetModel = require('./sheet_model');
  const answer = decide({
    rail: railReadingsOf(pl),
    deviceTexts: deviceTextsOf({ pl, side: engine('side_labels.json'), front: engine('labels-front.json'), zones: view?.zones }),
    layout: layoutOf(view),
    // The cables and, from the manager's workbook, where every device and
    // rack stands (DEVICE_INVENTORY, RACK_INVENTORY).
    cables: doc ? doc.cables.concat(sheetModel.placementRows(doc)) : null,
    knownRacks: known,
    rackAliases: doc ? sheetModel.rackAliases(doc) : [],
  });
  if (!answer.named) {
    return save({ name: null, method: null, confidence: null, reasons: [], tried: answer.tried, ask: true, why: answer.why, at });
  }

  // Record it as a person's answer is recorded: by the rack set up for the
  // site when there is one of that name, else by naming this scan's own row.
  const source = SOURCE_OF[answer.method];
  try {
    if (answer.knownRackId != null) {
      await rackIdentity.confirm(rackId, { tenantId, userId, knownRackId: answer.knownRackId, source });
    } else {
      try {
        await rackIdentity.confirm(rackId, { tenantId, userId, name: answer.name, source });
      } catch (err) {
        if (err && err.status === 409 && err.extra && err.extra.knownRackId != null) {
          await rackIdentity.confirm(rackId, { tenantId, userId, knownRackId: err.extra.knownRackId, source });
        } else throw err;
      }
    }
  } catch (err) {
    return save({ name: null, method: null, confidence: null, reasons: [], tried: [...answer.tried, { method: answer.method, outcome: `${answer.name} was found but could not be recorded: ${err.message}` }], ask: true, why: err.message, at });
  }
  return save({ name: answer.name, method: answer.method, confidence: answer.confidence, reasons: answer.reasons, tried: answer.tried, ask: false, why: null, at });
}

module.exports = {
  decide, nameAfterScan, byRailLabel, byDeviceLabels, byLayout, scoreRack, datasetRacks, kindOfName, keyOf,
  railReadingsOf, deviceTextsOf, layoutOf, METHOD_WORD, SOURCE_OF, METHOD_OF,
  LAYOUT_MIN_SCORE, LAYOUT_MIN_LEAD, MIN_LABEL_VOTES,
};
