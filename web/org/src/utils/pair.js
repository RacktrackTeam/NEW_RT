/* Two racks read together: the cables the Excel dataset runs between them,
   and everything flagged on either rack, as plain rows. */

const norm = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const portNo = (p) => { const m = String(p ?? '').match(/(\d+)(?!.*\d)/); return m ? Number(m[1]) : null; };
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

/** The device and port of one cable end in a scan's detail, or null. */
export function findEnd(detail, end) {
  if (!detail || !end?.device) return null;
  if (end.rack && detail.rackLabel && norm(end.rack) !== norm(detail.rackLabel)) return null;
  const device = detail.devices.find((d) => d.sheetName && norm(d.sheetName) === norm(end.device));
  if (!device) return null;
  const port = device.ports.find((p) => p.portNo === portNo(end.port)) || null;
  return { device, port };
}

/**
 * Every cable the sheet runs between the two racks, seen from the A-end,
 * with each end placed on the rack it is in (null when that end's device or
 * port is not in the photo).
 */
export function cablesBetween(a, z) {
  if (!a || !z) return [];
  const out = [];
  const seen = new Set();
  const add = (from, fromSide, otherDetail) => {
    for (const d of from.devices) {
      for (const p of d.ports) {
        if (!p.expected) continue;
        const key = p.expected.cableLabel;
        if (!key || seen.has(key)) continue;
        const far = findEnd(otherDetail, p.expected.other);
        const farNamed = far || (p.expected.other.rack && otherDetail.rackLabel && norm(p.expected.other.rack) === norm(otherDetail.rackLabel));
        if (!farNamed) continue;
        seen.add(key);
        const here = { device: d, port: p, expected: { device: d.sheetName, port: String(p.portNo), rack: from.rackLabel } };
        const there = { device: far?.device || null, port: far?.port || null, expected: p.expected.other };
        out.push({
          label: key,
          type: p.expected.cableType,
          a: fromSide === 'a' ? here : there,
          z: fromSide === 'a' ? there : here,
        });
      }
    }
  };
  add(a, 'a', z);
  add(z, 'z', a);
  return out.sort((x, y) => collator.compare(x.label, y.label));
}

/** The worst of several port statuses, for one line about a cable. */
export const worst = (...s) => ['wrong_port', 'missing_cable', 'not_found', 'not_in_sheet', 'uncapped', 'optic_no_cap', 'matched'].find((k) => s.includes(k)) || s.find(Boolean) || null;

/** Which of the two racks holds one end of a label check cable: 'a', 'z' or null. */
export function rackOfEnd(end, a, z) {
  const rack = end?.expected?.rack;
  if (rack) {
    if (a?.rackLabel && norm(rack) === norm(a.rackLabel)) return 'a';
    if (z?.rackLabel && norm(rack) === norm(z.rackLabel)) return 'z';
  }
  const name = norm(end?.found?.device || end?.expected?.device);
  if (!name) return null;
  const has = (d) => (d?.devices || []).some((x) => norm(x.sheetName) === name || norm(x.name) === name);
  if (has(a)) return 'a';
  if (has(z)) return 'z';
  return null;
}

export const FLAG_ORDER = ['missing_cable', 'wrong_port', 'wrong_unit', 'not_in_sheet', 'wrong_type', 'not_found', 'uncapped', 'optic_no_cap'];

export const FLAG_WORD = {
  missing_cable: 'Missing cable',
  wrong_port: 'Wrong port',
  wrong_unit: 'Wrong unit',
  not_in_sheet: 'Not in the Excel dataset',
  not_found: 'Not found',
  uncapped: 'No dust cap',
  optic_no_cap: 'Optic fitted, cap off',
  wrong_type: 'Wrong port type',
};

/* One plain sentence for a flag from the rack's own sheet check. */
function sheetDetail(f) {
  if (f.kind === 'wrong_unit') return `In U${f.unit ?? '?'}, the Excel dataset puts it in U${String(f.unitExpected ?? '?').replace(/^U/i, '')}.`;
  if (f.kind === 'missing_cable') {
    const o = f.otherEnd;
    const to = o?.device ? `, to ${[[o.device, o.port ? `port ${o.port}` : null].filter(Boolean).join(' '), o.rack].filter(Boolean).join(', ')}` : '';
    return `Empty. The Excel dataset has ${f.cableLabel ? `cable ${f.cableLabel}` : 'a cable'} here${to}.`;
  }
  if (f.kind === 'not_in_sheet') return f.expectedBy === 'port' ? 'A cable is plugged in here; the Excel dataset expects this port empty.' : 'A cable is plugged in here that the Excel dataset does not have.';
  if (f.kind === 'wrong_type') return `The photo reads ${f.found || 'another type'}; the Excel dataset says ${f.expected || 'another type'}.`;
  if (f.kind === 'uncapped') return `Empty port with the dust cap off${f.markedBy ? `, marked by ${f.markedBy}` : ''}.`;
  if (f.kind === 'optic_no_cap') return `Optic fitted without a dust cap${f.markedBy ? `, marked by ${f.markedBy}` : ''}.`;
  return '';
}

/* One plain sentence for one end of a label check cable. */
function checkDetail(label, end) {
  const at = (x) => [x?.device, x?.port != null && x.port !== '' ? `port ${x.port}` : null].filter(Boolean).join(' ');
  if (end.status === 'wrong_port') {
    return end.found && end.expected
      ? `Label ${label} is on ${at(end.found) || 'another port'}. The Excel dataset puts it on ${at(end.expected)}.`
      : `Label ${label} is on the wrong port.`;
  }
  if (end.status === 'not_found') return `Label ${label} was not read at ${at(end.expected) || 'this end'}. Take a close-up of that port.`;
  if (end.status === 'not_in_sheet') return `Label ${label} was read here, but no cable in the Excel dataset carries it.`;
  return '';
}

/**
 * Everything flagged on either rack, as rows: { key, kind, side, rack,
 * device, unit, portNo, detail, cropUrl }. From each rack's sheet check
 * (missing cable, unexpected cable, uncapped, wrong unit) and from the A-end
 * and Z-end label check (wrong port, label not found, label not in the
 * dataset). A check end whose missing cable the sheet check already has is
 * not listed twice.
 */
export function pairFlags({ a, z, flagsA, flagsZ, check }) {
  const rows = [];
  const seen = new Set();
  const racks = { a, z };
  const crops = { a: flagsA?.deviceCrops || {}, z: flagsZ?.deviceCrops || {} };
  const rackName = (side) => racks[side]?.rackLabel || (side === 'a' ? 'Rack A' : 'Rack Z');
  for (const [side, list] of [['a', flagsA], ['z', flagsZ]]) {
    for (const f of list?.flags || []) {
      const key = `${side}|${norm(f.device)}|${f.portNo ?? ''}|${f.kind}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push({ key, kind: f.kind, side, rack: rackName(side), device: f.device, unit: f.unit ?? null, portNo: f.portNo ?? null, detail: sheetDetail(f), cropUrl: f.cropUrl || null });
    }
  }
  for (const c of check?.cables || []) {
    for (const e of ['a', 'z']) {
      const end = c[e];
      if (!end || !['wrong_port', 'not_found', 'not_in_sheet'].includes(end.status)) continue;
      const side = rackOfEnd(end, a, z) || e;
      const at = end.found || end.expected || {};
      const p = portNo(at.port);
      const key = `${side}|${norm(at.device)}|${p ?? ''}|${end.status}|${norm(c.cableLabel)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const dev = (racks[side]?.devices || []).find((d) => norm(d.sheetName) === norm(at.device) || norm(d.name) === norm(at.device)) || null;
      rows.push({
        key,
        kind: end.status,
        side,
        rack: rackName(side),
        device: at.device || 'Device not named',
        unit: dev?.unitTop ?? null,
        portNo: p,
        cableLabel: c.cableLabel || null,
        detail: checkDetail(c.cableLabel || 'with no text', end),
        cropUrl: (dev && crops[side][dev.deviceId]) || null,
      });
    }
  }
  return rows.sort((x, y) => (x.side === y.side ? 0 : x.side === 'a' ? -1 : 1)
    || FLAG_ORDER.indexOf(x.kind) - FLAG_ORDER.indexOf(y.kind)
    || collator.compare(String(x.device), String(y.device))
    || (x.portNo ?? 0) - (y.portNo ?? 0));
}
