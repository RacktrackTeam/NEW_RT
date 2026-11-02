/**
 * Rack identity: which of the customer's racks is this scan?
 *
 * A scan is known only by a hash of its photo (the RK- id), and every new
 * photo of the same rack is a new hash. This module says which rack the
 * customer already has that scan is, shows the evidence, and refuses to choose
 * when it is not clear. It is the rack half of the ladder in
 * docs/netbox/match-and-reconcile.html, built for any datacentre: nothing here
 * knows a customer, a naming scheme or a site.
 *
 * It builds on lib/netbox/rack_match, the resolver the rest of the system
 * already uses (a row typed for the scan, the name a person gave the scan, the
 * rack looked up in NetBox, and the key NetBox ids are built on). What is added
 * here is what that resolver does not have: the label read from the photo, the
 * devices read from the photo, a stricter only-rack rule, the evidence, and a
 * person's confirmation.
 *
 * The ladder, in order. It stops at the first rung that leaves exactly one rack:
 *
 *   1. record        the scan already carries a rack: a person confirmed it
 *                    (rack_identity row), or its own racks_known row was typed
 *   2. label         a rack label read from the photo, or the name a person gave
 *                    the scan, equals exactly one known rack name or facility
 *                    id in this space, or across the Site when the scan has
 *                    no space (a near miss of one character only suggests)
 *   3. label-netbox  the same label against the NetBox racks of this Site
 *   4. devices       most device names on the scan sit under one rack in NetBox
 *                    (at least 3 names read, 60 percent agreement, and no second
 *                    rack within 10 points)
 *   5. only-rack     the space is said to hold one rack, one rack is set up in
 *                    it, and no other scan in it is unaccounted for
 *
 * Two of them state a rack: decision 'matched', with the rackKey the rest of
 * the system builds NetBox ids on. They are 'record', and a 'label' that equals
 * one rack in the space the scan was tied to. The other three are inference, so
 * they only ever suggest: decision 'suggested', one candidate with its score
 * and reasons, rack null and rackKey null, until a person confirms. A room set
 * up with one rack and holding twelve would otherwise merge all twelve into it.
 *
 * A rung that finds more than one rack does not decide; its racks stay on the
 * list. A later rung that brings different evidence (devices, the space) may
 * then speak only by agreeing with one rack on that list. If it names a rack
 * that is not on the list, the two disagree and nobody is chosen.
 *
 * Otherwise: 'ambiguous' when two or more racks remain, 'new' when the space
 * still has racks nobody has scanned and nothing matched (the name proposed is
 * the label that was read, never one made up here), 'unknown' when there is
 * nothing to go on.
 *
 * NetBox is only ever asked with a site filter. When which NetBox site is this
 * Site cannot be told, what NetBox answers is listed as candidates and never
 * suggested, because "Rack 1" at another site is a different rack.
 *
 * The rule it holds to: never pick between ties, and never invent a value
 * nobody read. identify() reads only. confirm() is the one write, and it is a
 * person's decision: it ties the scan to a racks_known row and records that in
 * rack_identity with source 'confirmed'.
 *
 * Schema (added on the first confirm, additive and idempotent):
 *   rack_identity(tenant_id, rack_id, known_rack_id, netbox_rack_id, source,
 *                 confirmed_by, confirmed_at), one row per (tenant, scan).
 *   No foreign keys on purpose: removing an organisation deletes racks_known
 *   and tenants, and a reference from here would block it. A row whose rack is
 *   gone can never resolve, because racks_known ids are never reused.
 *
 * Same database and env override as lib/estate.js, for the same reason: tests
 * seed a throwaway file and point this module at it.
 */

const path = require('path');
const fs = require('fs');
const vm = require('vm');
const Database = require('better-sqlite3');
const { logger } = require('./observability');
const estate = require('./estate');
const profile = require('./estate_profile');
const tenantLib = require('./tenant');
const { isValidRackId } = require('./rack_access');
const rackMatch = require('./netbox/rack_match');

const dbPath = process.env.RACKTRACK_AUTH_DB
  || path.join(__dirname, '..', 'data', 'auth.db');
const db = new Database(dbPath);
db.pragma('journal_mode = WAL');

const OUTPUTS_DIR = path.join(__dirname, '..', '..', 'outputs');

// The thresholds of the devices rung.
const MIN_DEVICES = 3;
const MIN_SHARE = 0.6;
const TIE_MARGIN = 0.1;
// How much a label match is worth by how it matched, before the reading's own
// confidence is applied.
const TIER_WEIGHT = { exact: 1, separators: 0.95, pattern: 0.85, near: 0.7 };
// A reading has to be this long before one wrong character is a small enough
// part of it to look past. "R1" and "R7" are two racks; "SP-HYB-RM01-R01-R1"
// with one letter misread is one rack and a smudge.
const NEAR_MIN_LENGTH = 8;
// A rack segment shared by the device labels carries no OCR confidence of its
// own; it ranks below anything read off the rack itself.
const INFERRED_CONFIDENCE = 0.6;
const MAX_LABELS = 20;
const MAX_DEVICE_LOOKUPS = 40;
const PATTERN_BUDGET_MS = 150;
// How long one NetBox question may take here, and a whole paged list. The
// client's own timeout is per request and longer; a results page should not
// wait on a NetBox that is not answering.
const NETBOX_CALL_MS = 6000;
const NETBOX_LIST_MS = 20000;
const NAME_MAX = 120;

/** A validation or state error the router turns into an HTTP status. */
class IdentityError extends Error {
  constructor(status, message, extra = null) {
    super(message);
    this.status = status;
    this.extra = extra;
  }
}

// -- Reading the scan --------------------------------------------------
// The seam the tests stub: the physical layer report as the pipeline wrote it.
// This module never builds the report; the route does that through the one
// function the physical-layer route uses.
const io = {
  readPhysicalLayer(rackId) {
    if (!isValidRackId(rackId)) return null; // the id reaches path.join
    try {
      return JSON.parse(fs.readFileSync(path.join(OUTPUTS_DIR, String(rackId), 'physical_layer.json'), 'utf8'));
    } catch { return null; }
  },
};

async function loadPhysicalLayer(rackId, given) {
  if (given && typeof given === 'object') return given;
  try {
    if (typeof given === 'function') return (await given(rackId)) || null;
    return (await io.readPhysicalLayer(rackId)) || null;
  } catch { return null; }
}

// -- Putting text in one shape -----------------------------------------
const SEP_RE = /[\s\-_./\\:]+/g;

// The same table as pipeline/physical_layer.py _CONFUSABLE: what OCR turns a
// digit into. It is applied only where the pattern says a digit belongs.
const CONFUSABLE = { O: '0', I: '1', L: '1', S: '5', B: '8', Z: '2', Q: '0', D: '0' };

/**
 * The same rule as pipeline/physical_layer.py _repair: O and I read as 0 and 1
 * when they sit next to a digit. The pipeline has already applied it to what it
 * read; it is applied to both sides here so a record and a reading compare in
 * one shape. It runs before the separators go, as the pipeline runs it on the
 * token as printed: RK-O7 is left alone, because nothing but a pattern says the
 * O is a digit.
 */
function repairNextToDigits(s) {
  return String(s).toUpperCase()
    .replace(/([A-Z])O(?=\d)/g, (m, a) => `${a}0`)
    .replace(/(\d)O/g, (m, d) => `${d}0`)
    .replace(/([A-Z])I(?=\d)/g, (m, a) => `${a}1`)
    .replace(/(\d)I/g, (m, d) => `${d}1`);
}

/** Case and outer space only: "rk 07 " -> "RK 07". */
function tidy(s) {
  return repairNextToDigits(String(s ?? '').trim().replace(/\s+/g, ' '));
}

/** Case and separators: "RK 07", "rk-07" and "RK07" are one key. */
function keyOf(s) {
  return tidy(s).replace(SEP_RE, '');
}

// -- The organisation's rack pattern -----------------------------------
function rackPatternFor(tenantId) {
  try {
    const p = profile.tenantProfile(tenantId)?.conventions?.rack_pattern;
    return typeof p === 'string' && p.trim() ? p.trim() : null;
  } catch { return null; }
}

/** Run a customer's regular expression over a short list, under a time budget. */
function matchingUnderBudget(re, list) {
  try {
    return vm.runInNewContext('list.filter((s) => re.test(s))', { re, list }, { timeout: PATTERN_BUDGET_MS });
  } catch { return []; }
}

/**
 * Fit a reading to a literal template (# a digit, A a letter, anything else
 * itself). Separators are ignored on both sides and written back the way the
 * template has them, so "RK 07" comes out as "RK-07". A letter sitting where
 * the template says digit is repaired through the confusable table; nothing
 * else is ever changed. Null when the reading is not shaped like the template.
 */
function fitLiteral(label, template, re) {
  const chars = String(label).toUpperCase().replace(SEP_RE, '').split('');
  const slots = String(template).split('');
  const isSep = (ch) => !/[A-Za-z0-9#]/.test(ch);
  if (slots.filter((ch) => !isSep(ch)).length !== chars.length) return null;
  let out = '';
  let repaired = false;
  let i = 0;
  for (const slot of slots) {
    if (isSep(slot)) { out += slot; continue; }
    const ch = chars[i++];
    if (slot === '#' || /[0-9]/.test(slot)) {
      let digit = ch;
      if (!/[0-9]/.test(ch)) {
        digit = CONFUSABLE[ch];
        if (!digit) return null;
        repaired = true;
      }
      if (slot !== '#' && digit !== slot) return null;
      out += digit;
    } else if (slot === 'A') {
      // OCR turns digits into letters, never the reverse, so a digit where a
      // letter belongs is a different label, not a misreading.
      if (!/[A-Z]/.test(ch)) return null;
      out += ch;
    } else {
      if (ch !== slot.toUpperCase()) return null;
      out += slot;
    }
  }
  return re.test(out) ? { text: out, repaired } : null;
}

/**
 * Fit a reading to a regular expression. The reading is tried as read and with
 * its separators rewritten; only if none of those match are the confusable
 * letters tried as digits, and a repair is taken only when exactly one repaired
 * reading matches. Two that match is a guess, so neither is used.
 */
function fitRegex(label, re) {
  const base = String(label).toUpperCase();
  const forms = (s) => {
    const parts = s.split(SEP_RE).filter(Boolean);
    return [...new Set([s, parts.join('-'), parts.join(''), parts.join('_'), parts.join(' ')])];
  };
  // As typed first (a record may be in lower case), then as the pipeline writes it.
  const asRead = matchingUnderBudget(re, [...new Set([...forms(String(label)), ...forms(base)])]);
  if (asRead.length) return { text: asRead[0], repaired: false };

  const at = [];
  base.split('').forEach((ch, i) => { if (CONFUSABLE[ch]) at.push(i); });
  if (!at.length || at.length > 6) return null;
  const variants = [];
  for (let mask = 1; mask < (1 << at.length); mask++) {
    const chars = base.split('');
    at.forEach((pos, bit) => { if (mask & (1 << bit)) chars[pos] = CONFUSABLE[chars[pos]]; });
    variants.push(...forms(chars.join('')));
  }
  const hits = matchingUnderBudget(re, [...new Set(variants)]);
  const keys = new Set(hits.map((h) => h.replace(SEP_RE, '')));
  return keys.size === 1 ? { text: hits[0], repaired: true } : null;
}

/** A reading in the organisation's own convention, or null when it does not fit it. */
function fitToPattern(label, pattern) {
  if (!pattern || !label) return null;
  let c;
  try { c = profile.compilePattern(pattern); } catch { return null; }
  if (!c || !c.ok) return null;
  const text = String(label).trim();
  return c.mode === 'literal' ? fitLiteral(text, pattern, c.re) : fitRegex(text, c.re);
}

// With no pattern set, what a rack identifier looks like on a sign: a rack word
// and a number. It only decides which reading may be proposed as a new rack's
// name or may contradict the only-rack rule; matching never depends on it.
const RACK_SHAPED_RE = /^(?:RACK|RK|CABINET|CAB|R)[-_ ]?\d[A-Z0-9\-_]*$/i;

// -- Evidence from the photo -------------------------------------------
function labelsFrom(pl, pattern) {
  const seen = new Set();
  const out = [];
  for (const c of (pl?.rack?.candidates || [])) {
    const text = typeof c?.text === 'string' ? c.text.trim() : '';
    if (!text) continue;
    const where = String(c.source || 'photo');
    const mark = `${where}|${text}`;
    if (seen.has(mark)) continue;
    seen.add(mark);
    const fit = fitToPattern(text, pattern);
    const conf = Number(c.conf);
    out.push({
      text,
      normalized: fit ? fit.text : tidy(text),
      where,
      confidence: c.conf == null || !Number.isFinite(conf) ? null : conf,
      repaired: !!(fit && fit.repaired),
      fitsPattern: !!fit,
    });
    if (out.length >= MAX_LABELS) break;
  }
  return out;
}

/** The rack number a name ends in: SP-HYD-RM01-R01-R3 -> 3, Rack 12 -> 12, else null. */
function rackNumberOf(name) {
  const m = String(name || '').match(/(?:\brack|r)[\s_-]?0*(\d+)\s*$/i);
  return m ? Number(m[1]) : null;
}

/**
 * The one rack number a label names, when it names exactly one: SP-R3-U13-SW02
 * -> 3. A label carrying two numbers (SP-HYD-RM01-R01-R2 has R01 and R2) is a
 * rack label, not a device label, and is left to the rack rules above.
 */
function rackNumberInLabel(text) {
  const found = [];
  const re = /(?:^|[^A-Z0-9])R0*(\d{1,3})(?=$|[^0-9])/gi;
  let m;
  while ((m = re.exec(String(text || ''))) !== null) found.push(Number(m[1]));
  return found.length === 1 ? found[0] : null;
}

/**
 * Rung 2b's evidence: the labels read (device labels, and any rail reading
 * that matched no rack) that agree on one rack number, and the one known rack
 * with that number. { rack, count, reason } or { rack: null }.
 */
/** SP-R3-U13-SW02 names a unit; a label that does is a device's, not a rack's. */
function namesAUnit(text) {
  return /(?:^|[^A-Z0-9])U0*\d{1,2}(?=$|[^0-9])/i.test(String(text || ''));
}

function labelsThatNumberARack(labels, deviceNames, knownRacks, railReadings = []) {
  const texts = [
    ...labels.map((l) => ({ text: l.text, conf: l.confidence == null ? 1 : Number(l.confidence) })),
    ...deviceNames.map((d) => ({ text: d.device, conf: 1, device: true })),
    ...railReadings,
  ];
  const seen = new Set();
  const numbers = [];
  for (const t of texts) {
    const key = String(t.text).toUpperCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const n = rackNumberInLabel(t.text);
    if (n !== null) numbers.push({ text: t.text, n, conf: t.conf, device: !!t.device });
  }
  if (!numbers.length) return { rack: null };
  const distinct = new Set(numbers.map((x) => x.n));
  if (distinct.size !== 1) return { rack: null };
  const n = numbers[0].n;
  const racks = knownRacks.filter((r) => rackNumberOf(r.facilityId) === n || rackNumberOf(r.name) === n);
  if (racks.length !== 1) return { rack: null };
  const named = numbers.map((x) => `"${x.text}"`).join(', ');
  // One label may state the rack on its own only when it is a device's label:
  // it names the unit the device sits in (U13). A rack-shaped reading with one
  // R-token left in it (SP-HYB-RM01-R01-RX) is the near tier's business.
  const device = numbers.filter((x) => x.device || namesAUnit(x.text));
  return {
    rack: racks[0], count: numbers.length,
    bestConf: device.length ? Math.max(...device.map((x) => x.conf)) : 0,
    reason: `${numbers.length === 1 ? 'the label' : 'the labels'} ${named} name${numbers.length === 1 ? 's' : ''} rack R${n}, and ${racks[0].facilityId || racks[0].name} is the one rack set up here with that number`,
  };
}

function deviceNamesFrom(pl) {
  const seen = new Set();
  const out = [];
  for (const u of (pl?.units || [])) {
    for (const d of (u?.devices || [])) {
      const name = typeof d?.label?.text === 'string' ? d.label.text.trim() : '';
      if (!name || seen.has(name.toUpperCase())) continue;
      seen.add(name.toUpperCase());
      out.push({ device: name, unit: u.unit ?? null });
    }
  }
  return out;
}

function weightOf(label) {
  if (label.where === 'record') return 1;
  return label.confidence == null ? INFERRED_CONFIDENCE : Math.max(0, Math.min(1, label.confidence));
}

function isRackShaped(label, pattern) {
  return pattern ? label.fitsPattern : RACK_SHAPED_RE.test(label.normalized);
}

const round2 = (n) => Math.round(n * 100) / 100;

// -- Candidates --------------------------------------------------------
const fromKnown = (r) => ({ source: 'known', id: r.id, name: r.name ?? null, facilityId: r.facility_id ?? null });
const fromNetBox = (r) => ({ source: 'netbox', id: r.id, name: r.name ?? null, facilityId: r.facility_id || null });

/**
 * Are these two the same rack? Within one source, the same row. Across the
 * customer's record and NetBox: the same facility id (the stronger key), else
 * the same name, compared as written apart from case. Separators are not set
 * aside here: R2-01 and R20-1 are two racks.
 */
function sameRack(a, b) {
  if (!a || !b) return false;
  if (a.source === b.source) return Number(a.id) === Number(b.id);
  if (a.netboxId != null && b.source === 'netbox' && Number(a.netboxId) === Number(b.id)) return true;
  if (b.netboxId != null && a.source === 'netbox' && Number(b.netboxId) === Number(a.id)) return true;
  if (a.facilityId && b.facilityId) return tidy(a.facilityId) === tidy(b.facilityId);
  return !!a.name && !!b.name && tidy(a.name) === tidy(b.name);
}

/**
 * The racks set up here that a NetBox rack is. As written first; only when
 * none is, with separators set aside too ("RK 07" in one system, "RK-07" in the
 * other). The caller takes a twin only when there is exactly one.
 */
function twinsOf(knownRacks, nbRack) {
  const exact = knownRacks.filter((k) => sameRack(k, nbRack));
  if (exact.length) return exact;
  return knownRacks.filter((k) => (k.facilityId && nbRack.facilityId
    ? keyOf(k.facilityId) === keyOf(nbRack.facilityId)
    : !!k.name && !!nbRack.name && keyOf(k.name) === keyOf(nbRack.name)));
}

/** Add to a list, folding into an entry that is the same rack: best score, every reason. */
function addCandidate(list, cand) {
  const hit = list.find((c) => sameRack(c, cand));
  if (!hit) { list.push({ ...cand, reasons: [...cand.reasons] }); return; }
  hit.score = Math.max(hit.score, cand.score);
  for (const r of cand.reasons) if (!hit.reasons.includes(r)) hit.reasons.push(r);
  if (hit.netboxId == null && cand.netboxId != null) hit.netboxId = cand.netboxId;
  if (hit.source === 'known' && cand.source === 'netbox') hit.netboxId = cand.id;
  if (hit.source === 'netbox' && cand.source === 'known') {
    // The customer's own record is the one to name; NetBox rides along.
    Object.assign(hit, { source: 'known', netboxId: hit.id, id: cand.id, name: cand.name, facilityId: cand.facilityId });
  }
}

/**
 * Match the labels read against a list of racks. Per label only the strictest
 * way it matches anything counts, so "R1-01" read cleanly does not also drag in
 * R10-1. Across labels every rack that matched is kept: two labels naming two
 * racks is a disagreement, not a choice.
 */
/**
 * One character apart, and no more: "SP-HYB-AM01-R01-R1" against
 * "SP-HYB-RM01-R01-R1". A reader that gets seventeen characters of a rack id
 * right and one wrong has still told us which rack this is - but it has not
 * PROVED it, so a match found this way only ever suggests.
 */
function nearlyEqual(a, b) {
  if (a === b) return false;                       // that is an exact match, not a near one
  if (a.length < NEAR_MIN_LENGTH || b.length < NEAR_MIN_LENGTH) return false;
  if (Math.abs(a.length - b.length) > 1) return false;
  // One substitution, or one character inserted or dropped.
  if (a.length === b.length) {
    let wrong = 0;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i] && (wrong += 1) > 1) return false;
    return wrong === 1;
  }
  const [shortText, longText] = a.length < b.length ? [a, b] : [b, a];
  let i = 0;
  let j = 0;
  let skipped = false;
  while (i < shortText.length && j < longText.length) {
    if (shortText[i] === longText[j]) { i += 1; j += 1; continue; }
    if (skipped) return false;
    skipped = true;
    j += 1;
  }
  return true;
}

function matchLabels(labels, racks, describe) {
  const found = [];
  const index = racks.map((r) => ({
    r,
    values: [['name', r.name], ['facility id', r.facilityId]].filter(([, v]) => v),
  }));
  for (const label of labels) {
    const tiers = [
      ['exact', (v) => tidy(v) === tidy(label.text), 'equals'],
      ['separators', (v) => keyOf(v) === keyOf(label.text), 'equals, once case and separators are set aside,'],
    ];
    // A reading the pattern repaired is tried last, and only as repaired. One the
    // pattern merely re-punctuated is already covered by the tier above.
    if (label.repaired) {
      tiers.push(['pattern', (v) => keyOf(v) === keyOf(label.normalized),
        `was repaired to "${label.normalized}" because the rack pattern says digits there, and that equals`]);
    }
    // Last, and only ever as a suggestion: a reading that differs from one
    // rack by a single character. Reading a rack's own label off the
    // photograph is what stops every scan asking a person which rack this is,
    // and a reader that hands back seventeen characters of eighteen has
    // answered. It is still a reading, so it asks rather than states, and it
    // is refused outright when two racks are equally close.
    tiers.push(['near', (v) => nearlyEqual(keyOf(v), keyOf(label.text)),
      'is one character different from']);
    for (const [tier, test, words] of tiers) {
      const hits = [];
      for (const { r, values } of index) {
        const on = values.find(([, v]) => test(v));
        if (on) hits.push({ r, field: on[0] });
      }
      if (!hits.length) continue;
      // Two racks a character away from the same reading is not a near miss,
      // it is a coin toss, and this never tosses one.
      if (tier === 'near' && hits.length > 1) continue;
      for (const { r, field } of hits) {
        const conf = label.confidence == null ? '' : `, confidence ${round2(label.confidence)}`;
        const cand = {
          ...r,
          score: round2(TIER_WEIGHT[tier] * weightOf(label)),
          reasons: [`the label "${label.text}" (${label.where}${conf}) ${words} the ${field} of ${describe}`],
        };
        // Only where it is true: a candidate carrying `near: undefined` is not
        // the same object as one without the key, and callers compare these.
        if (tier === 'near') cand.near = true;
        addCandidate(found, cand);
      }
      break;
    }
  }
  return found;
}

// -- The estate around the scan ----------------------------------------
function spaceIdsBeneath(tenantId, spaceId) {
  const ids = new Set([Number(spaceId)]);
  const spaces = estate.listSpaces(tenantId);
  let grew = true;
  while (grew) {
    grew = false;
    for (const s of spaces) {
      if (s.parent_id != null && ids.has(Number(s.parent_id)) && !ids.has(Number(s.id))) {
        ids.add(Number(s.id));
        grew = true;
      }
    }
  }
  return { ids, spaces: spaces.filter((s) => ids.has(Number(s.id))) };
}

function bindingsFor(tenantId) {
  try {
    const rows = db.prepare('SELECT rack_id, known_rack_id FROM rack_identity WHERE tenant_id = ?').all(Number(tenantId));
    return new Map(rows.map((r) => [String(r.rack_id), Number(r.known_rack_id)]));
  } catch { return new Map(); } // no table yet: nobody has confirmed anything
}

/**
 * The rack a person confirmed this scan to be, or null. Reads only, and answers
 * null rather than throwing when nothing was ever confirmed.
 */
function confirmedRack(tenantId, rackId) {
  if (tenantId == null || !rackId) return null;
  let bound;
  try {
    bound = db.prepare('SELECT * FROM rack_identity WHERE tenant_id = ? AND rack_id = ?')
      .get(Number(tenantId), String(rackId));
  } catch { return null; }
  if (!bound) return null;
  let rack = null;
  try {
    rack = db.prepare('SELECT * FROM racks_known WHERE id = ? AND tenant_id = ?')
      .get(Number(bound.known_rack_id), Number(tenantId)) || null;
  } catch { rack = null; }
  // The rack was removed since: the confirmation no longer points at anything.
  if (!rack) return null;
  return {
    rack,
    netboxRackId: bound.netbox_rack_id ?? null,
    source: bound.source,
    confirmedBy: bound.confirmed_by ?? null,
    confirmedAt: bound.confirmed_at,
  };
}

/**
 * What surrounds the scan: the named racks it could be, the scans nobody has
 * tied to a rack yet, and how many racks the space is said to hold.
 */
function surroundings(tenantId, rackId, spaceId) {
  const all = estate.listRacks(tenantId) || [];
  const others = all.filter((r) => String(r.rack_id) !== String(rackId));
  const named = (r) => !!(r.name || r.facility_id);
  if (spaceId == null) {
    return { space: null, known: others.filter(named), direct: [], loose: [], expected: null, identified: 0 };
  }
  const { ids, spaces } = spaceIdsBeneath(tenantId, spaceId);
  const space = spaces.find((s) => Number(s.id) === Number(spaceId)) || null;
  const inScope = others.filter((r) => r.space_id != null && ids.has(Number(r.space_id)));
  const direct = inScope.filter((r) => Number(r.space_id) === Number(spaceId));

  // What the admin said the space holds: its own count, else what the spaces
  // beneath it add up to, else not stated.
  let expected = space && space.rack_count != null ? Number(space.rack_count) : null;
  if (expected == null) {
    const below = spaces.filter((s) => Number(s.id) !== Number(spaceId) && s.rack_count != null);
    if (below.length) expected = below.reduce((n, s) => n + Number(s.rack_count), 0);
  }

  // Racks here that a scan is already tied to: confirmed onto, or a scan's own
  // row that carries a name. A scan nobody has identified is not counted; it
  // may be a second photo of a rack already counted.
  const bindings = bindingsFor(tenantId);
  const inScopeIds = new Set(inScope.map((r) => Number(r.id)));
  const identified = new Set();
  for (const [scan, knownId] of bindings) {
    if (String(scan) !== String(rackId) && inScopeIds.has(knownId)) identified.add(knownId);
  }
  for (const r of inScope) {
    let scanned = false;
    try { scanned = !!(named(r) && r.rack_id && tenantLib.tenantOwnsRack(tenantId, r.rack_id)); } catch { scanned = false; }
    if (scanned) identified.add(Number(r.id));
  }

  return {
    space,
    known: inScope.filter(named),
    direct,
    loose: direct.filter((r) => !named(r)),
    bindings,
    expected,
    identified: identified.size,
  };
}

// -- NetBox ------------------------------------------------------------
/**
 * The same client, with a deadline on every question and a memory: once NetBox
 * has failed to answer at all (a timeout, no route), the rest of this request
 * fails at once instead of waiting again. An HTTP error is an answer, and does
 * not trip it. Reads only: there is no post or patch on what this returns.
 */
function guarded(client) {
  if (!client) return null;
  let down = false;
  const ask = async (fn, budget) => {
    if (down) throw new Error('NetBox is not answering');
    let timer;
    try {
      return await Promise.race([
        fn(),
        new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error('NetBox took too long')), budget); }),
      ]);
    } catch (err) {
      if (!err || !err.status) down = true;
      throw err;
    } finally {
      clearTimeout(timer);
    }
  };
  const out = { get: (apiPath, params) => ask(() => client.get(apiPath, params), NETBOX_CALL_MS) };
  if (typeof client.paginate === 'function') {
    out.paginate = (apiPath, params) => ask(() => client.paginate(apiPath, params), NETBOX_LIST_MS);
  }
  return out;
}

async function listAll(client, apiPath, params) {
  if (typeof client.paginate === 'function') return (await client.paginate(apiPath, params)) || [];
  const r = await client.get(apiPath, { ...params, limit: 1000 });
  return (r && r.results) || [];
}

/**
 * The NetBox site that is this Site. A lookup with no site filter reaches
 * across the whole instance, and "Rack 1" at another site is a different rack
 * (docs/design/part-b-dedup-design-review.md, finding 3). The site is the one
 * named like the tenant, or with its slug, or the only site there is.
 */
async function netboxSite(client, tenant) {
  const one = async (params) => {
    const r = await client.get('/api/dcim/sites/', { ...params, limit: 2 });
    const rows = (r && r.results) || [];
    return rows.length === 1 ? rows[0] : null;
  };
  let site = null;
  if (tenant?.name) site = await one({ name__ie: tenant.name });
  if (!site && tenant?.slug) site = await one({ slug: tenant.slug });
  if (!site) site = await one({});
  return site;
}

/**
 * NetBox as this scan sees it, loaded once and only when a rung needs it.
 *   { reachable, site, racks }
 * With the site known, racks are that site's racks and a NetBox rung may
 * suggest. With the site not known, NetBox is still asked, without a site
 * filter, but whatever it answers is a candidate only and never a suggestion.
 */
function makeNetBox(client, tenant, notes) {
  let loaded = null;
  return {
    async load() {
      if (loaded) return loaded;
      loaded = { reachable: false, site: null, racks: [] };
      if (!client) { notes.push('NetBox is not connected, so the two NetBox rungs were skipped'); return loaded; }
      try {
        const site = await netboxSite(client, tenant);
        loaded.reachable = true;
        if (!site) {
          notes.push('NetBox has no single site that is this Site, so anything found there is a candidate only');
          return loaded;
        }
        loaded.site = { id: site.id, name: site.name };
        loaded.racks = (await listAll(client, '/api/dcim/racks/', { site_id: site.id })).map(fromNetBox);
      } catch (err) {
        loaded = { reachable: false, site: null, racks: [] };
        notes.push('NetBox could not be reached, so the two NetBox rungs were skipped');
        logger.warn({ event: 'rack_identity.netbox_failed', err: err.message }, 'NetBox lookup failed during rack identity');
      }
      return loaded;
    },
  };
}

const SITE_UNKNOWN = 'which NetBox site is this Site is not known, so this is a candidate only';

/** Rung 3 with no site to filter by: the labels asked of NetBox by name and facility id. Candidates only. */
async function looseLabelHits(client, labels, notes) {
  const found = [];
  try {
    for (const label of labels.slice(0, 8)) {
      for (const form of new Set([label.text, label.normalized])) {
        for (const params of [{ name__ie: form }, { facility_id: form }]) {
          const r = await client.get('/api/dcim/racks/', { ...params, limit: 5 });
          for (const hit of ((r && r.results) || [])) {
            const at = hit.site?.name ? ` at ${hit.site.name}` : '';
            addCandidate(found, {
              ...fromNetBox(hit),
              score: round2(TIER_WEIGHT.exact * weightOf(label) * 0.5),
              reasons: [`the label "${label.text}" (${label.where}) equals the ${params.facility_id ? 'facility id' : 'name'} of a rack${at} in NetBox`, SITE_UNKNOWN],
            });
          }
        }
      }
    }
  } catch (err) {
    notes.push('NetBox could not be asked about the label');
    logger.warn({ event: 'rack_identity.netbox_label_failed', err: err.message }, 'NetBox label lookup failed');
  }
  return found;
}

/** Rung 4: where NetBox says the devices read on this scan are racked. */
async function devicesRung(client, nb, deviceNames, notes) {
  const hints = deviceNames.map((d) => ({ ...d, netboxRack: null }));
  const out = { hints, decided: null, candidates: [] };
  if (!client || !nb.reachable || !deviceNames.length) return out;

  const byRack = new Map();
  try {
    for (const hint of hints.slice(0, MAX_DEVICE_LOOKUPS)) {
      const r = await client.get('/api/dcim/devices/',
        { name__ie: hint.device, ...(nb.site ? { site_id: nb.site.id } : {}), limit: 5 });
      const racks = new Map();
      for (const dev of ((r && r.results) || [])) {
        if (dev?.rack?.id != null) racks.set(Number(dev.rack.id), dev.rack);
      }
      if (racks.size === 1) {
        const only = [...racks.values()][0];
        hint.netboxRack = { id: only.id, name: only.name ?? null };
      }
      for (const [id, rack] of racks) {
        if (!byRack.has(id)) byRack.set(id, { rack, devices: [] });
        byRack.get(id).devices.push(hint.device);
      }
    }
  } catch (err) {
    notes.push('NetBox could not be asked about the devices, so the devices rung was skipped');
    logger.warn({ event: 'rack_identity.netbox_devices_failed', err: err.message }, 'NetBox device lookup failed');
    return out;
  }

  const total = deviceNames.length;
  const ranked = [...byRack.values()]
    .map(({ rack, devices }) => ({ rack, devices, share: devices.length / total }))
    .sort((a, b) => b.share - a.share);
  if (!ranked.length) return out;

  const enough = total >= MIN_DEVICES;
  const top = ranked[0];
  const second = ranked[1] || null;
  const tied = !!second && (top.share - second.share) < TIE_MARGIN;
  const decides = !!nb.site && enough && top.share >= MIN_SHARE && !tied;

  for (const row of ranked) {
    const full = nb.racks.find((r) => Number(r.id) === Number(row.rack.id))
      || fromNetBox({ id: row.rack.id, name: row.rack.name, facility_id: null });
    const reasons = [
      `${row.devices.length} of the ${total} device names read on this scan are under this rack in NetBox (${row.devices.join(', ')})`,
    ];
    if (!nb.site) reasons.push(SITE_UNKNOWN);
    else if (!enough) reasons.push(`only ${total} device names were read; at least ${MIN_DEVICES} are needed for the devices to suggest a rack`);
    else if (tied && (row === top || row === second)) reasons.push('another rack holds nearly as many of them, within 10 points, so the devices do not say which');
    else if (row.share < MIN_SHARE) reasons.push('that is under the 60 percent the devices need to suggest a rack');
    out.candidates.push({ ...full, score: round2(row.share), reasons });
  }
  if (decides) out.decided = out.candidates[0];
  return out;
}

// -- identify ----------------------------------------------------------
/** The name a person gave this scan in the NetBox flow (lib/netbox/rack_names), if any. */
function scanNameOf(rackId) {
  // Required here, not at the top: rack_names settles its data directory when
  // it loads, and app.js sets that directory before any request arrives.
  try { return require('./netbox/rack_names').get(rackId) || null; } catch { return null; }
}

/**
 * Rung 0, applied last: where the photo was taken.
 *
 * GPS indoors cannot tell one rack from the next, so it never picks a rack.
 * It can tell one datacentre from another, and every rung above looks for the
 * rack inside the Site the scan is filed under. So when the photo was taken at
 * a different Site of the organisation, or nowhere near this one, a match that
 * rests on reading a label is turned back into a suggestion, because "Rack 1"
 * is also a rack in the other city. A person's own confirmation is not
 * overruled: they were standing there. The verdict is always shown, whichever
 * way it goes, including when there is nothing to go on.
 */
const location = require('./location');

function captureLocationOf(rackId) {
  try {
    const meta = JSON.parse(fs.readFileSync(path.join(OUTPUTS_DIR, String(rackId), 'scan_meta.json'), 'utf8'));
    return meta && meta.captureLocation ? meta.captureLocation : null;
  } catch { return null; }
}

function sitesAround(tenantId) {
  try {
    const own = db.prepare('SELECT id, name, lat, lng, organization_id FROM tenants WHERE id = ?').get(tenantId);
    if (!own) return { site: null, others: [] };
    const others = own.organization_id == null ? []
      : db.prepare('SELECT id, name, lat, lng FROM tenants WHERE organization_id = ? AND id != ?')
        .all(own.organization_id, own.id);
    return { site: own, others };
  } catch { return { site: null, others: [] }; }
}

function applyLocation(out, { tenantId, capture }) {
  if (tenantId == null) return out;
  const { site, others } = sitesAround(tenantId);
  const where = location.judge(capture, site, others);
  out.evidence.location = where;
  out.evidence.notes.push(where.note);
  const doubtful = where.verdict === 'elsewhere' || where.verdict === 'away';
  if (doubtful && out.decision === 'matched' && out.rule !== 'record') {
    out.decision = 'suggested';
    out.confidence = 'possible';
    out.rack = null;
    out.rackKey = null;
    out.evidence.notes.push('So this is only a suggestion until a person confirms it.');
  }
  return out;
}

async function identify(rackId, opts = {}) {
  const out = await identifyFromEvidence(rackId, opts);
  const capture = opts.captureLocation !== undefined ? opts.captureLocation : captureLocationOf(rackId);
  return applyLocation(out, { tenantId: opts.tenantId, capture });
}


/**
 * Which of the customer's racks is this scan? Reads only.
 *
 *   identify(rackId, { tenantId, spaceId?, netboxClient?, physicalLayer? })
 *
 * spaceId defaults to the space the scan was bound to when it was captured
 * (its racks_known row); given explicitly it must belong to the tenant.
 * physicalLayer may be the report itself or a function that returns it; left
 * out, the cached outputs/<rackId>/physical_layer.json is read.
 *
 * Only two rules state a rack and hand out the key NetBox ids are built on
 * (rack_match.rackKeyFor): 'record', and a 'label' that equals one rack in the
 * space the scan was tied to. Everything else (the label against the whole
 * Site or against NetBox, the devices, the only rack in the space) is a
 * suggestion with a score: decision 'suggested', rack null, rackKey null, and
 * only a person's confirm turns it into a rack. A room set up with one rack and
 * holding twelve would otherwise merge all twelve into it
 * (docs/design/part-b-dedup-design-review.md, finding 2).
 */
async function identifyFromEvidence(rackId, { tenantId, spaceId, netboxClient: rawClient = null, physicalLayer } = {}) {
  const netboxClient = guarded(rawClient);
  const notes = [];
  const out = {
    rackId: rackId ?? null,
    spaceId: null,
    decision: 'unknown',
    confidence: 'unidentified',
    rack: null,
    rackKey: null,
    candidates: [],
    evidence: { labels: [], pattern: { rackPattern: null, matches: false }, deviceHints: [], notes },
    rule: null,
    proposal: null,
  };
  if (!rackId || tenantId == null) {
    notes.push('this scan belongs to no Site, so there is nothing to match it against');
    return out;
  }
  const tenant = estate.getTenant(tenantId);
  if (!tenant) throw new IdentityError(404, 'Site not found');

  let own = null;
  try { own = estate.getRackByRackId(tenantId, rackId); } catch { own = null; }
  if (spaceId != null) {
    const s = estate.getSpace(spaceId);
    if (!s || Number(s.tenant_id) !== Number(tenantId)) throw new IdentityError(404, 'Space not found');
    out.spaceId = Number(s.id);
  } else {
    out.spaceId = own?.space_id ?? null;
  }

  // The rack that was chosen or suggested leads the list; anything else seen stays on it.
  const lead = (cand) => { out.candidates = [cand, ...out.candidates.filter((c) => !sameRack(c, cand))]; };
  const matched = (rule, cand, rackKey) => {
    out.decision = 'matched';
    out.confidence = rule === 'record' ? 'confirmed' : 'probable';
    out.rule = rule;
    out.rack = { source: cand.source, id: cand.id, name: cand.name, facilityId: cand.facilityId };
    if (cand.netboxId != null) out.rack.netboxId = cand.netboxId;
    out.rackKey = rackKey ?? null;
    lead(cand);
    return out;
  };
  // A suggestion is a question for a person: no rack is stated and no key is given.
  const suggest = (rule, cand) => {
    out.decision = 'suggested';
    out.confidence = 'possible';
    out.rule = rule;
    lead(cand);
    return out;
  };

  // Rung 1: the record. A person's confirmation first - or the rack's own
  // label, read off the photograph and written down (source 'label').
  const confirmed = confirmedRack(tenantId, rackId);
  if (confirmed) {
    const cand = {
      ...fromKnown(confirmed.rack), score: 1,
      reasons: [confirmed.source === 'label'
        ? `the rack's own label was read off the photograph and this scan was assigned to it on ${confirmed.confirmedAt}`
        : `a person confirmed this scan is this rack on ${confirmed.confirmedAt}`],
    };
    if (confirmed.netboxRackId != null) cand.netboxId = confirmed.netboxRackId;
    return matched('record', cand, rackMatch.rackKeyFor(tenantId, confirmed.rack.id));
  }

  // What the resolver the rest of the system uses makes of this scan: a row
  // typed for it, or the name a person gave it matching a rack in its space,
  // then that rack looked up in NetBox. Its key is the key; none is minted here
  // for a rack it already answers for.
  const scanName = scanNameOf(rackId);
  let resolved = null;
  try {
    resolved = await rackMatch.resolveRack(netboxClient, { tenantId, rackId, scanName, fallbackName: rackId });
  } catch { resolved = null; }
  const resolverSays = (knownId) => (resolved && resolved.knownRackId != null
    && Number(resolved.knownRackId) === Number(knownId) ? resolved : null);

  // Rung 1, second half: a row typed for this scan is already the rack.
  if (own && (own.name || own.facility_id)) {
    const cand = { ...fromKnown(own), score: 1, reasons: ["this scan's own record carries the rack's name"] };
    const r = resolverSays(own.id);
    if (r?.netboxId != null) cand.netboxId = r.netboxId;
    return matched('record', cand, r ? r.rackKey : null);
  }

  // Everything below needs the photo's evidence and the estate around the scan.
  const pattern = rackPatternFor(tenantId);
  const pl = await loadPhysicalLayer(rackId, physicalLayer);
  if (!pl) notes.push('no physical layer report for this scan, so no label was read');
  // The name a person gave the scan is a reading too, and the freshest one.
  const given = scanName ? { rack: { candidates: [{ text: scanName, conf: 1, source: 'record' }, ...(pl?.rack?.candidates || [])] } } : pl;
  const labels = labelsFrom(given, pattern);
  const deviceNames = deviceNamesFrom(pl);
  // Every reading off the rails, rack-shaped or not: a device sticker sits on
  // the rail too, and SP-R3-U13-SW02 read there names its rack.
  const railReadings = ((pl && pl.labels && Array.isArray(pl.labels.rail)) ? pl.labels.rail : [])
    .filter((r) => r && typeof r.text === 'string' && r.text.trim())
    .map((r) => ({ text: r.text.trim(), conf: Number(r.conf) || 0 }));
  out.evidence.labels = labels.map(({ text, normalized, where, confidence, repaired }) =>
    ({ text, normalized, where, confidence, repaired }));
  out.evidence.pattern = { rackPattern: pattern, matches: labels.some((l) => l.fitsPattern) };
  out.evidence.deviceHints = deviceNames.map((d) => ({ ...d, netboxRack: null }));

  const around = surroundings(tenantId, rackId, out.spaceId);
  const knownRacks = around.known.map(fromKnown);
  const where = around.space ? `a rack in ${around.space.name}` : 'a rack set up for this Site';
  if (out.spaceId == null) notes.push('this scan is not tied to a space, so every rack set up for this Site was considered; a label equal to exactly one of them states the rack, anything else is a suggestion');

  // A later rung speaks only when its one rack agrees with the list so far.
  const pool = out.candidates;
  const narrow = (found) => {
    if (found.length !== 1) return null;
    if (!pool.length) return found[0];
    return pool.filter((c) => sameRack(c, found[0])).length === 1 ? found[0] : null;
  };
  const keep = (found) => { for (const c of found) addCandidate(pool, c); };
  const chosen = (cand) => pool.find((c) => sameRack(c, cand)) || cand;

  // Rung 2: a label against the racks set up here. One rack whose name or
  // facility id equals the label is the rack, and its key is the key - in the
  // space the scan was tied to, or across the Site when the scan has no space.
  // The Site used to make it only a suggestion; the owner asked on 25 Sep 2026
  // that a rack whose label was read be assigned without a tap, and changed by
  // hand only when it is wrong. Every rack of a Site has one name, so one
  // exact equal across the Site is as sure as one in a room. A near miss (one
  // character off) still asks.
  const byLabel = matchLabels(labels, knownRacks, where);
  if (byLabel.length === 1) {
    const cand = byLabel[0];
    if (cand.near) {
      // Read, not proved: the reading is one character from this rack's id, so
      // the rack is named and a person says yes.
      cand.reasons.push('one character was read differently, so this is a suggestion until a person confirms it');
      return suggest('label', cand);
    }
    if (out.spaceId == null) cand.reasons.push('the scan is not tied to a space; the label equals one rack across the whole Site');
    const r = resolverSays(cand.id);
    if (r?.netboxId != null) cand.netboxId = r.netboxId;
    return matched('label', cand, (r && r.rackKey) || rackMatch.rackKeyFor(tenantId, cand.id));
  }
  keep(byLabel);

  // Rung 2b: labels that name the rack by its number. A device label such as
  // SP-R3-U13-SW02 carries the number of the rack it stands in; two or more
  // labels that agree on one number, with exactly one rack set up for the
  // Site whose name ends in that number, state the rack (the owner, 25 Sep
  // 2026: R3's photograph shows the device labels and no rack label). One
  // label alone suggests; labels that disagree say nothing.
  if (!byLabel.length) {
    const byNumber = labelsThatNumberARack(labels, deviceNames, knownRacks, railReadings);
    if (byNumber.rack) {
      // Two labels that agree, or one read cleanly (nine in ten), state the rack.
      const sure = byNumber.count >= 2 || byNumber.bestConf >= 0.9;
      const cand = { ...byNumber.rack, score: sure ? 0.9 : 0.6, reasons: [byNumber.reason] };
      if (sure) {
        const r = resolverSays(cand.id);
        if (r?.netboxId != null) cand.netboxId = r.netboxId;
        return matched('label', cand, (r && r.rackKey) || rackMatch.rackKeyFor(tenantId, cand.id));
      }
      cand.reasons.push('one label is not enough on its own, so this is a suggestion until a person confirms it');
      return suggest('label', cand);
    }
  }

  // Rung 3: the same labels against NetBox. They are the same evidence, so they
  // are not asked to break a tie they already failed to break.
  const netbox = makeNetBox(netboxClient, tenant, notes);
  const askNetBox = (labels.length > 0 && !byLabel.length) || deviceNames.length > 0;
  const nb = askNetBox ? await netbox.load() : { reachable: false, site: null, racks: [] };
  const fold = (found) => found.map((c) => {
    // A NetBox rack that is one rack already set up here is that rack.
    const twins = twinsOf(knownRacks, c);
    return twins.length === 1 ? { ...twins[0], netboxId: c.id, score: c.score, reasons: c.reasons } : c;
  });
  if (!byLabel.length && labels.length && nb.reachable) {
    if (nb.site) {
      const byNetBox = fold(matchLabels(labels, nb.racks, `a rack at ${nb.site.name} in NetBox`));
      if (byNetBox.length === 1) return suggest('label-netbox', byNetBox[0]);
      keep(byNetBox);
    } else {
      keep(fold(await looseLabelHits(netboxClient, labels, notes)));
    }
  }

  // Rung 4: the devices. Different evidence, so it may speak where labels tied.
  const devices = await devicesRung(netboxClient, nb, deviceNames, notes);
  out.evidence.deviceHints = devices.hints;
  const byDevices = fold(devices.candidates);
  if (devices.decided) {
    const pick = narrow([byDevices[0]]);
    if (pick) {
      keep(byDevices);
      // The share is the score of this rung, whatever a label scored above it.
      return suggest('devices', { ...chosen(pick), score: pick.score });
    }
  }
  keep(byDevices);

  // Rung 5: the only rack set up in the space (rack_match.pickCandidate's space
  // rule), and only when the space is also said to hold one rack, no other scan
  // in it is unaccounted for, and no rack label read says otherwise.
  if (around.space) {
    const picked = rackMatch.pickCandidate(around.direct.filter((r) => r.name || r.facility_id), null);
    if (picked.rack && picked.source === 'space') {
      const only = fromKnown(picked.rack);
      const cand = { ...only, score: 0.5, reasons: [`${picked.why}: ${around.space.name}`] };
      const strays = around.loose.filter((r) => around.bindings.get(String(r.rack_id)) !== Number(only.id));
      const against = labels.filter((l) => isRackShaped(l, pattern)
        && ![only.name, only.facilityId].filter(Boolean).some((v) => keyOf(v) === keyOf(l.normalized)));
      let blocked = null;
      if (Number(around.space.rack_count) !== 1) {
        blocked = around.space.rack_count == null
          ? 'nobody has said how many racks this space holds, so the space alone says nothing'
          : `this space is set up to hold ${around.space.rack_count} racks, so the space alone says nothing`;
      } else if (strays.length) {
        blocked = `${strays.length} other scan(s) in this space are not tied to a rack yet, so the space alone says nothing`;
      } else if (against.length) {
        blocked = `the label read says "${against[0].normalized}", which is not this rack, so the space alone says nothing`;
      }
      if (blocked) {
        // Worth listing only where it could have spoken: a space said to hold one rack.
        if (Number(around.space.rack_count) === 1) { cand.reasons.push(blocked); keep([cand]); }
      } else {
        const pick = narrow([cand]);
        keep([cand]);
        if (pick) return suggest('only-rack', chosen(cand));
      }
    }
  }

  out.candidates.sort((a, b) => b.score - a.score);
  if (out.candidates.length >= 2) { out.decision = 'ambiguous'; return out; }

  // Nothing matched. If the space still has racks nobody has scanned, this may
  // be one of them. The name offered is the one label that reads as a rack
  // identifier; with none, or with two that differ, no name is offered.
  const room = around.expected != null && around.expected - around.identified > 0;
  if (room) {
    out.decision = 'new';
    const shaped = labels.filter((l) => isRackShaped(l, pattern));
    if (new Set(shaped.map((l) => keyOf(l.normalized))).size === 1) {
      const best = shaped.slice().sort((a, b) => weightOf(b) - weightOf(a))[0];
      out.proposal = { name: best.normalized, where: best.where, confidence: best.confidence };
    }
    return out;
  }
  if (!out.candidates.length && !labels.length && !deviceNames.length) {
    notes.push('nothing was read that says which rack this is');
  }
  return out;
}

// -- confirm -----------------------------------------------------------
let _ready = false;
function _prep() {
  if (_ready) return;
  db.exec(`
    CREATE TABLE IF NOT EXISTS rack_identity (
      tenant_id      INTEGER NOT NULL,
      rack_id        TEXT    NOT NULL,
      known_rack_id  INTEGER NOT NULL,
      netbox_rack_id INTEGER,
      source         TEXT    NOT NULL DEFAULT 'confirmed',
      confirmed_by   INTEGER,
      confirmed_at   TEXT    NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (tenant_id, rack_id)
    );
    CREATE INDEX IF NOT EXISTS idx_rack_identity_known ON rack_identity(known_rack_id);
  `);
  _ready = true;
  logger.info({ event: 'rack_identity.schema_ready' }, 'rack identity schema ready');
}

function knownRow(tenantId, id) {
  try {
    return db.prepare('SELECT * FROM racks_known WHERE id = ? AND tenant_id = ?').get(Number(id), Number(tenantId)) || null;
  } catch { return null; }
}

/**
 * A person says which rack this scan is. Exactly one of:
 *   knownRackId   a rack already set up for this Site
 *   netboxRackId  a rack at this Site in NetBox; it is tied to the one rack set
 *                 up here that it matches, or the scan's own row takes its name
 *   name          a rack that has no record yet; the scan's own row takes the name
 * Returns { rackId, knownRackId, netboxRackId, name, facilityId, rackKey, created, source }.
 * rackKey is rack_match.rackKeyFor(tenantId, knownRackId): the key NetBox ids
 * are built on for that rack.
 */
async function confirm(rackId, { tenantId, userId = null, knownRackId, netboxRackId, name, netboxClient: rawClient = null, source = 'confirmed' } = {}) {
  // 'confirmed' is a person's yes; 'label' is the rack's own label read off the
  // photograph, recorded so every screen agrees and a person can still change it.
  if (!['confirmed', 'label'].includes(source)) throw new IdentityError(400, 'source must be confirmed or label');
  const netboxClient = guarded(rawClient);
  if (!rackId || tenantId == null) throw new IdentityError(400, 'rackId and tenantId are required');
  const tenant = estate.getTenant(tenantId);
  if (!tenant) throw new IdentityError(404, 'Site not found');
  const given = [knownRackId, netboxRackId, name].filter((v) => v !== undefined && v !== null && v !== '');
  if (given.length !== 1) throw new IdentityError(400, 'Give exactly one of knownRackId, netboxRackId or name');

  const own = estate.getRackByRackId(tenantId, rackId);
  const spaceId = own?.space_id ?? null;
  const around = surroundings(tenantId, rackId, spaceId);
  let target = null;
  let created = false;
  let nbId = null;

  // Name the scan's own row. The one place a racks_known row is made or named here.
  const nameOwnRow = (fields) => {
    const r = estate.upsertRack(tenantId, {
      rack_id: rackId, ...(spaceId != null ? { space_id: spaceId } : {}), ...fields, source: 'learned',
    }, userId);
    created = r.created;
    return r.rack;
  };
  const ownNameClash = (wanted) => own && own.name && keyOf(own.name) !== keyOf(wanted);

  if (knownRackId !== undefined && knownRackId !== null && knownRackId !== '') {
    if (!/^\d+$/.test(String(knownRackId))) throw new IdentityError(400, 'knownRackId must be a whole number');
    target = knownRow(tenantId, knownRackId);
    // A rack of another Site is not there, the same as one that does not exist.
    if (!target) throw new IdentityError(404, 'Rack not found');
    if (!target.name && !target.facility_id) {
      throw new IdentityError(400, 'That record has no name yet; confirm with a name instead');
    }
  } else if (netboxRackId !== undefined && netboxRackId !== null && netboxRackId !== '') {
    if (!/^\d+$/.test(String(netboxRackId))) throw new IdentityError(400, 'netboxRackId must be a whole number');
    if (!netboxClient) throw new IdentityError(409, 'NetBox is not connected');
    let nbRack = null;
    let site = null;
    try {
      site = await netboxSite(netboxClient, tenant);
      nbRack = await netboxClient.get(`/api/dcim/racks/${Number(netboxRackId)}/`);
    } catch (err) {
      if (err && err.status === 404) throw new IdentityError(404, 'Rack not found in NetBox');
      throw new IdentityError(502, 'NetBox could not be reached');
    }
    if (!nbRack || nbRack.id == null) throw new IdentityError(404, 'Rack not found in NetBox');
    // With the NetBox site of this Site known, a rack at another site is not
    // there. With it not known, NetBox only ever offered candidates, and
    // choosing among candidates is exactly what a person's confirm is for.
    if (site && Number(nbRack.site?.id) !== Number(site.id)) throw new IdentityError(404, 'Rack not found in NetBox');
    nbId = Number(nbRack.id);
    const twins = twinsOf(around.known.map(fromKnown), fromNetBox(nbRack)).map((t) => around.known.find((k) => k.id === t.id));
    if (twins.length > 1) {
      throw new IdentityError(409, 'More than one rack set up here matches that NetBox rack; confirm one of them by knownRackId',
        { knownRackIds: twins.map((t) => t.id) });
    }
    if (twins.length === 1) target = twins[0];
    else {
      if (!nbRack.name) throw new IdentityError(400, 'That NetBox rack has no name');
      if (ownNameClash(nbRack.name)) throw new IdentityError(409, `This scan's record is already named ${own.name}; an admin changes names in setup`);
      target = nameOwnRow({ name: String(nbRack.name).slice(0, NAME_MAX), ...(nbRack.facility_id ? { facility_id: String(nbRack.facility_id) } : {}) });
    }
  } else {
    if (typeof name !== 'string') throw new IdentityError(400, 'name must be text');
    const wanted = name.trim().replace(/\s+/g, ' ');
    if (!wanted) throw new IdentityError(400, 'name is required');
    if (wanted.length > NAME_MAX) throw new IdentityError(400, `name is too long (max ${NAME_MAX})`);
    const clash = around.known.find((k) => [k.name, k.facility_id].filter(Boolean).some((v) => keyOf(v) === keyOf(wanted)));
    if (clash) {
      throw new IdentityError(409, `${clash.name || clash.facility_id} is already set up here; confirm it by knownRackId`,
        { knownRackId: clash.id });
    }
    if (ownNameClash(wanted)) throw new IdentityError(409, `This scan's record is already named ${own.name}; an admin changes names in setup`);
    target = nameOwnRow({ name: wanted });
  }

  _prep();
  db.prepare(`
    INSERT INTO rack_identity (tenant_id, rack_id, known_rack_id, netbox_rack_id, source, confirmed_by)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(tenant_id, rack_id) DO UPDATE SET
      known_rack_id = excluded.known_rack_id,
      netbox_rack_id = excluded.netbox_rack_id,
      source = excluded.source,
      confirmed_by = excluded.confirmed_by,
      confirmed_at = datetime('now')
  `).run(Number(tenantId), String(rackId), Number(target.id), nbId, source, userId);
  logger.info({ event: 'rack_identity.confirmed', tenantId, rackId, knownRackId: target.id, netboxRackId: nbId, userId, created },
    `scan ${rackId} confirmed as rack ${target.id}`);
  return {
    rackId,
    knownRackId: target.id,
    netboxRackId: nbId,
    name: target.name ?? null,
    facilityId: target.facility_id ?? null,
    rackKey: rackMatch.rackKeyFor(tenantId, target.id),
    created,
    source,
  };
}

// -- Whose rack is it --------------------------------------------------
/**
 * The Site a scan is looked at under, for a signed-in user. A member or a site
 * manager only ever gets their own Site. An organisation admin or the owner
 * gets the one Site they can see that holds the scan; when two do, the one
 * where the scan is tied to a space, else they have to say which.
 */
/**
 * The rack somebody confirmed this scan is of, without being told the site.
 *
 * A confirmation is one row per site and scan, and a scan id is minted for one
 * rack, so the row finds itself. This exists for the readers that hold a scan
 * and nothing else - the report a photograph builds knows the rack it is of and
 * nothing about the estate around it, and it was heading its page "Unidentified
 * rack" while a person had confirmed the rack two days earlier (the owner,
 * 23 September 2026). Where the caller knows the site, confirmedRack() above is
 * the one to use.
 */
function confirmedRackAnywhere(rackId) {
  if (!rackId) return null;
  let bound;
  try {
    bound = db.prepare('SELECT * FROM rack_identity WHERE rack_id = ? ORDER BY confirmed_at DESC LIMIT 1')
      .get(String(rackId));
  } catch { return null; }
  if (!bound) return null;
  return confirmedRack(bound.tenant_id, rackId);
}

/**
 * Every scan somebody has confirmed is this rack.
 *
 * The other direction of confirmedRack(): given the customer's rack, which
 * photographs have been tied to it. What that buys is the network side of a
 * mapping - the switches read at those scans are the switches of this rack.
 */
function scansForKnownRack(knownRackId) {
  if (knownRackId == null) return [];
  try {
    return db.prepare(
      'SELECT tenant_id, rack_id, confirmed_at FROM rack_identity WHERE known_rack_id = ? ORDER BY confirmed_at DESC',
    ).all(Number(knownRackId));
  } catch { return []; }
}

function tenantForRack(user, rackId, asked = null) {
  if (!user || !rackId) return null;
  const own = Number(user.tenant_id ?? user.tenantId ?? 0);
  const want = asked == null || asked === '' ? null : Number(asked);
  if (own && (want == null || want === own) && tenantLib.tenantOwnsRack(own, rackId)) return own;
  const visible = tenantLib.visibleTenantIds(user); // null: the platform owner
  let holders = [];
  try {
    holders = db.prepare('SELECT tenant_id FROM rack_owners WHERE rack_id = ?').all(String(rackId)).map((r) => Number(r.tenant_id));
  } catch { holders = []; }
  let pool = holders.filter((t) => visible === null || visible.map(Number).includes(t));
  if (want != null) pool = pool.filter((t) => t === want);
  if (pool.length > 1) {
    const tied = pool.filter((t) => { try { return !!estate.getRackByRackId(t, rackId); } catch { return false; } });
    if (tied.length === 1) pool = tied;
  }
  if (pool.length > 1) throw new IdentityError(409, 'This scan belongs to more than one Site; say which with tenantId', { tenantIds: pool });
  return pool[0] ?? null;
}

module.exports = {
  CONFUSABLE,
  /* Rung 2b, so a page that shows the ladder can run the same rule rather
     than a second spelling of it: the rack number a device's own label names,
     and the number a rack's name ends in. */
  rackNumberInLabel, rackNumberOf, namesAUnit,
  IdentityError,
  identify,
  applyLocation,
  confirm,
  confirmedRack,
  confirmedRackAnywhere,
  scansForKnownRack,
  tenantForRack,
  io,
  // Exported for the tests and for anyone who needs text in the same shape.
  keyOf,
  fitToPattern,
  MIN_DEVICES,
  MIN_SHARE,
  TIE_MARGIN,
};
