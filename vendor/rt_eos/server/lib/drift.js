/**
 * Drift check for one port: what the app read on the port against the row
 * the Excel dataset has for it, field by field.
 *
 *   compare(actual, reference) -> the drift result
 *   referenceOf(cable, end)    -> the reference for one end of an Excel row
 *   referenceFor(doc, where)   -> the reference for { device, port, rack }, or null
 *   actualOfPort(port, extra)  -> the actual reading of one detail port
 *
 * It takes structured data only, never typed text: the device and port the
 * person chose, and what the photo and its close-ups read there. The scan
 * detail (lib/scan_detail.js) runs it on every port, so a chosen port arrives
 * with its result; POST /api/matrix/drift-check runs it on data sent by hand,
 * for testing without a photo.
 *
 *   actual    { device, port, units, unitExpected, status: connected | empty | unknown, statusSure,
 *               connector, cableColor, cableType, label, otherEnd: { device, port } }
 *   reference { device, port, cableLabel, cableType, connector, cableColor,
 *               other: { device, port, rack } } or null when the Excel has
 *               no cable on the port
 *
 * Each field is match, mismatch, not_read (the app did not read it) or
 * not_in_excel (the Excel has no value for it, so it cannot drift). The
 * result is drift when any field is a mismatch, no_drift when none is and
 * the port's status was read, and not_checked when the status could not be
 * told and nothing else differs.
 */
'use strict';

/** Lower case, letters and digits only, and no leading zeros in a number (R01 is R1). */
function key(s) {
  return String(s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '').replace(/(^|[a-z])0+(?=\d)/g, '$1');
}

const blank = (v) => v == null || String(v).trim() === '';
const lastNumber = (s) => { const m = String(s ?? '').match(/(\d+)(?!.*\d)/); return m ? Number(m[1]) : null; };
// A patch panel's printed position code (14A) is a name, not port 14.
const POSITION_CODE = /^\s*\d{1,3}\s*[A-Za-z]\s*$/;

/** The same port: the same text, else the same last number when neither is a panel code. */
function samePort(a, b) {
  if (blank(a) || blank(b)) return false;
  if (key(a) === key(b)) return true;
  if (POSITION_CODE.test(String(a)) || POSITION_CODE.test(String(b))) return false;
  const x = lastNumber(a);
  return x != null && x === lastNumber(b);
}

// The camera reads a light grey patch cable as white, so the two are one colour here.
const COLOUR_SAME = { gray: 'grey', white: 'grey', violet: 'purple', aqua: 'cyan' };
const colourKey = (c) => { const k = key(c); return COLOUR_SAME[k] || k; };
// RJ-45, RJ 45 and rj45 are one connector.
const connectorKey = (c) => key(c);

/** copper, fibre or dac from a connector or a cable type (Cat6 UTP, LC-LC OM4, RJ-45), else null. */
function family(s) {
  const t = String(s ?? '').toLowerCase();
  if (!t.trim()) return null;
  if (/\bdac\b|twinax|direct\s*attach/.test(t)) return 'dac';
  if (/fib(er|re)|\b(lc|sc|st|mpo|mtp|fc)\b|\bom[1-5]\b|\bos[12]\b|\bsmf\b|\bmmf\b|single\s*mode|multi\s*mode|optic/.test(t)) return 'fibre';
  if (/rj\s*-?\s*(45|11)|\bcat\s*-?\s*\d|utp|stp|ftp|copper|ethernet/.test(t)) return 'copper';
  return null;
}
const FAMILY_WORD = { copper: 'Copper', fibre: 'Fibre', dac: 'DAC' };

const STATUS_WORD = { connected: 'Connected', empty: 'Empty', unknown: 'Could not tell' };

const endText = (e) => (e && !blank(e.device) ? `${e.device}${!blank(e.port) ? ` port ${e.port}` : ''}` : null);

/** The reference for end e ('a' or 'z') of an Excel row (snake_case, lib/matrix.js). */
function referenceOf(c, e) {
  const o = e === 'a' ? 'z' : 'a';
  const val = (k) => (blank(c[k]) ? null : String(c[k]).trim());
  return {
    device: val(`${e}_device`),
    port: val(`${e}_port`),
    rack: val(`${e}_rack`),
    cableLabel: val('cable_label'),
    cableType: val('cable_type'),
    // The Excel's own connector column, else the connector its cable type
    // names: a Cat5/6/7 patch is RJ45.
    connector: val('connector') || (/\bcat\s*-?\s*\d/i.test(String(c.cable_type || '')) ? 'RJ45' : null),
    cableColor: val('cable_color'),
    rowNo: c.row_no ?? null,
    other: { device: val(`${o}_device`), port: val(`${o}_port`), rack: val(`${o}_rack`) },
  };
}

/**
 * The Excel's cable on { device, port } (and rack, when both name one), as a
 * reference, or null. found says whether the Excel names the device at all,
 * so "no cable on this port" and "device not in the Excel" are told apart.
 */
function referenceFor(doc, { device, port, rack } = {}) {
  let found = false;
  for (const c of doc?.cables || []) {
    for (const e of ['a', 'z']) {
      if (key(c[`${e}_device`]) !== key(device)) continue;
      const r = c[`${e}_rack`];
      if (!blank(r) && !blank(rack) && key(r) !== key(rack)) continue;
      found = true;
      if (samePort(c[`${e}_port`], port)) return { found, reference: referenceOf(c, e) };
    }
  }
  return { found, reference: null };
}

/**
 * The actual reading of one port of the scan detail (lib/scan_detail.js),
 * with the device's name in the Excel. The other end is the one the cable's
 * own tag names, when it was read.
 */
function actualOfPort(p, { device, units = null, unitExpected = null }) {
  return {
    device,
    port: p.portNo,
    units: Array.isArray(units) ? units : null,
    unitExpected: unitExpected ?? null,
    status: p.state === 'connected' || p.state === 'empty' ? p.state : 'unknown',
    // The same rule as the flags (lib/sheet_check.js): a cable is accused
    // missing or extra only on a port read reliably.
    // A cable colour from the whole rack photo is a hint (a neighbouring
    // cable, a shadow read as black); only a close-up or a person settles it.
    colourSure: ['close-up', 'person'].includes(p.fields?.cableColor?.source) || p.stateSource === 'close-up' || p.stateSource === 'person',
    statusSure: p.stateSource === 'close-up' || p.stateSource === 'person'
      || (typeof p.confidence === 'number' && p.confidence >= 0.7),
    connector: p.cableConnector || null,
    cableColor: p.cableColor || null,
    cableType: null,
    label: p.label?.text || null,
    labelMatched: p.status === 'matched' ? true : p.status === 'wrong_port' ? false : null,
    otherEnd: p.tag?.other ? { device: p.tag.other.device, port: p.tag.other.port } : null,
  };
}

function compare(actual, reference) {
  const a = actual || {};
  const ref = reference || null;
  const status = a.status === 'connected' || a.status === 'empty' ? a.status : 'unknown';
  const cabled = status === 'connected';
  const fields = [];
  const add = (field, label, expected, got, result) => fields.push({ field, label, expected: expected ?? null, actual: got ?? null, result });

  add('device', 'Device', ref ? ref.device : a.device, a.device, 'match');
  add('port', 'Port', ref ? ref.port : (a.port != null ? String(a.port) : null), a.port != null ? String(a.port) : null, 'match');

  // The unit: where the Excel puts the device against where the photo finds
  // it. One unit either way is within what a rack photo can place (the same
  // rule as the unit check, lib/sheet_check.js).
  if (a.unitExpected != null && a.units?.length) {
    const near = a.units.some((u) => Math.abs(u - a.unitExpected) <= 1);
    const seen = a.units.length > 1 ? `U${Math.min(...a.units)}-${Math.max(...a.units)}` : `U${a.units[0]}`;
    add('unit', 'Unit', `U${a.unitExpected}`, seen, near ? 'match' : 'mismatch');
  }

  const expStatus = ref ? 'Connected' : 'Empty';
  const same = (status === 'connected') === !!ref;
  if (status === 'unknown') add('status', 'Connection status', expStatus, STATUS_WORD[status], 'not_read');
  // A difference the photo read only roughly is not drift: it says so, and
  // a close-up settles it.
  else if (!same && a.statusSure === false) add('status', 'Connection status', expStatus, `${STATUS_WORD[status]} (photo not sure)`, 'not_read');
  else add('status', 'Connection status', expStatus, STATUS_WORD[status], same ? 'match' : 'mismatch');

  // What can only be read on a cable: nothing to compare on an empty port.
  const onCable = (field, label, expected, got, same) => {
    if (!ref) return;
    if (blank(expected)) { add(field, label, null, got, 'not_in_excel'); return; }
    if (!cabled || blank(got)) { add(field, label, expected, null, 'not_read'); return; }
    add(field, label, expected, got, same(expected, got) ? 'match' : 'mismatch');
  };

  onCable('cableLabel', 'Cable label', ref?.cableLabel, a.label,
    (x, y) => (typeof a.labelMatched === 'boolean' ? a.labelMatched : key(x) === key(y)));
  onCable('otherEnd', 'Other end', endText(ref?.other), endText(a.otherEnd),
    () => key(ref.other.device) === key(a.otherEnd.device) && (blank(ref.other.port) || blank(a.otherEnd.port) || samePort(ref.other.port, a.otherEnd.port)));
  onCable('connector', 'Connector type', ref?.connector, a.connector, (x, y) => connectorKey(x) === connectorKey(y));
  onCable('cableColor', 'Cable colour', ref?.cableColor, a.cableColor, (x, y) => colourKey(x) === colourKey(y));
  const colour = fields.find((f) => f.field === 'cableColor');
  if (colour && colour.result === 'mismatch' && a.colourSure === false) {
    colour.result = 'not_read';
    colour.actual = `${colour.actual} (photo not sure)`;
  }
  // The cable type is compared by its kind (copper, fibre, DAC): the Excel
  // writes "Cat6 UTP" or "LC-LC OM4", the photo reads the connector.
  if (ref && !blank(ref.cableType)) {
    const got = a.cableType || a.connector;
    const fx = family(ref.cableType);
    const fy = family(got);
    if (!cabled || blank(got) || !fy) add('cableType', 'Cable type', ref.cableType, got || null, 'not_read');
    else if (!fx) add('cableType', 'Cable type', ref.cableType, FAMILY_WORD[fy], 'not_read');
    else add('cableType', 'Cable type', ref.cableType, `${FAMILY_WORD[fy]} (${got})`, fx === fy ? 'match' : 'mismatch');
  } else if (ref) {
    add('cableType', 'Cable type', null, a.cableType || a.connector || null, 'not_in_excel');
  }

  const mismatches = fields.filter((f) => f.result === 'mismatch');
  // A difference the photo is not sure of is neither drift nor a match.
  const unsure = fields.filter((f) => f.result === 'not_read' && /\(photo not sure\)$/.test(String(f.actual ?? '')));
  const statusRead = fields.find((f) => f.field === 'status').result !== 'not_read';
  const result = mismatches.length ? 'drift' : statusRead && !unsure.length ? 'no_drift' : 'not_checked';
  const reason = result !== 'not_checked' ? null
    : !statusRead ? 'Port not clear on the photo'
      : unsure.map((f) => `${f.label.toLowerCase()} not clear on the photo (Excel ${f.expected}, read ${String(f.actual).replace(/ \(photo not sure\)$/, '')})`).join('; ').replace(/^./, (c) => c.toUpperCase());
  return {
    result,
    reason,
    device: a.device ?? null,
    port: a.port != null ? String(a.port) : null,
    excelRow: ref?.rowNo ?? null,
    fields,
    mismatches: mismatches.map((f) => f.field),
    summary: summaryOf(result, a, mismatches, reason),
  };
}

/**
 * The whole check from structured data alone: find the Excel row for the
 * actual device and port, then compare. A device the Excel does not name
 * cannot drift, so it is not checked.
 */
function checkAgainst(doc, actual) {
  const a = actual || {};
  if (!doc) return notChecked(a, 'There is no Excel dataset for this site yet.');
  if (blank(a.device) || blank(a.port)) return notChecked(a, 'Give the device and the port.');
  const { found, reference } = referenceFor(doc, { device: a.device, port: a.port, rack: a.rack });
  if (!found) return notChecked(a, `${a.device} is not in the Excel.`);
  return compare(a, reference);
}

function notChecked(a, why) {
  return {
    result: 'not_checked',
    device: a.device ?? null,
    port: a.port != null ? String(a.port) : null,
    excelRow: null,
    fields: [],
    mismatches: [],
    reason: why,
    summary: ['NOT CHECKED', `Device: ${a.device ?? '-'}`, `Port: ${a.port ?? '-'}`, why].join('\n'),
  };
}

/** The result in a few plain lines, as the teammate's spec words it. */
function summaryOf(result, a, mismatches, reason = null) {
  const head = [
    result === 'drift' ? 'DRIFT DETECTED' : result === 'no_drift' ? 'NO DRIFT' : 'NOT CHECKED',
    `Device: ${a.device ?? '-'}`,
    `Port: ${a.port ?? '-'}`,
  ];
  if (result === 'no_drift') return [...head, 'Actual configuration matches the expected configuration.'].join('\n');
  if (result === 'not_checked') return [...head, `${reason || 'The photo could not tell for sure whether a cable is plugged in'}. Take a close-up of the port.`].join('\n');
  return [...head, 'Mismatch:', ...mismatches.flatMap((f) => [f.label, `  Expected: ${f.expected ?? '-'}`, `  Actual: ${f.actual ?? '-'}`])].join('\n');
}

module.exports = { compare, checkAgainst, referenceOf, referenceFor, actualOfPort, family, samePort, key };
