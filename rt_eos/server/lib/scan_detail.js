/**
 * One scan in full, for the result tabs (Units, Devices, Ports, Cables) and
 * the two-rack map: every unit top to bottom, every device, every port with
 * the label read on it and the cable the patching matrix puts there, and
 * every cable seen.
 *
 *   build(view, doc) -> the GET /scans/:scanId/detail answer
 *
 * view is lib/scan_view.load; doc the site's active matrix import or null.
 * Nothing here is made up: a port without a label read has label null, a
 * port the sheet does not name has expected null, and without a matrix every
 * port's status is only what the photo shows (connected, empty, unknown).
 *
 * Port status against the sheet (only when there is a matrix):
 *   matched        the label read on the port is the sheet's cable for it
 *   wrong_port     the label read belongs to a cable the sheet puts elsewhere
 *   missing_cable  the sheet has a cable here and the port is empty
 *   not_found      the sheet has a cable here, one is plugged in (or it could
 *                  not be told), and no label was read on it
 *   not_in_sheet   a cable is plugged in that the sheet does not have
 *   uncapped       empty, no cable in the sheet, and the dust cap is off
 *   optic_no_cap   not plugged in, no cable in the sheet, and a person marked
 *                  an optic fitted without its dust cap
 *   empty          empty, no cable in the sheet
 *   unknown        the photo could not tell, no cable in the sheet
 * Without a matrix: connected, empty, uncapped, optic_no_cap or unknown.
 * Each port of a device the sheet names also carries drift: the field-by-field
 * check of what was read on it against its Excel row (lib/drift.js), null
 * otherwise.
 * Every port also carries its two cap marks (uncapped, opticNoCap) whatever
 * its status, so a flag is never hidden behind a worse one, and the photo's
 * own dust-cap read (capState: connected, empty, capped, uncertain or
 * too_small; capReason in plain words; null when the check did not run).
 * counts.capUnsure and flags.capUnsure count the ports the photo could not
 * settle; flags.uncapped counts a person's marks and the photo's findings
 * together (lib/sheet_check.js).
 */
'use strict';

const scanView = require('./scan_view');
const sheetCheck = require('./sheet_check');
const checks = require('./checks');
const labelMatch = require('./label_match');
const fieldSources = require('./field_sources');
const drift = require('./drift');

const { norm, portNumber } = scanView;

const unitOf = (u) => {
  const m = String(u ?? '').match(/\d+/);
  return m ? Number(m[0]) : null;
};

/**
 * The sheet's cables by label. get(text) -> the cable row or null; the label
 * read is matched with the camera's usual misreadings allowed for
 * (lib/label_match.js). how(text) says how it matched.
 */
function cablesByLabel(doc) {
  const entries = [];
  for (const c of doc?.cables || []) {
    for (const t of [c.cable_label, c.label_a, c.label_z, c.label_alias]) if (t) entries.push([t, c]);
  }
  const m = labelMatch.createMatcher(entries);
  // A label read may be a plain text or a label object; a flag line (with
  // an end) is tried as the whole line, then its device and port, then its
  // port and position (lib/checks.js candidatesOf).
  // A tag face whose port was hidden already carries the Excel row it was
  // given its port from (checks.foldedFaces).
  const hit = (l) => (l && typeof l === 'object' ? (l.cable ? { value: l.cable, how: l.match } : checks.matchLabel(m, l)) : m.match(l));
  return {
    get: (l) => hit(l)?.value || null,
    how: (l) => hit(l)?.how || null,
  };
}

/**
 * The end of cable c that sits on device `name` port `portNo`, or null.
 * byLabel: the cable's label was read on this port, so an end whose port is
 * a patch-panel position code (14A, 50B) is taken on the device alone: the
 * code is printed on the panel's strip, it is not the port's count along the
 * panel (checks.positionCode).
 */
function endOn(c, name, portNo, rack, byLabel = false, portLabel = null, device = null) {
  for (const e of ['a', 'z']) {
    if (norm(c[`${e}_device`]) !== norm(name)) continue;
    const r = c[`${e}_rack`];
    if (r && rack && !sheetCheck.sameRack(r, rack)) continue;
    if (byLabel && checks.positionCode(c[`${e}_port`])) return e;
    // A printed position code ("53A") is not port 53: it is the port where
    // that code was read, else the port the site's code convention places it
    // on (sheet_check.portForCode: the panel's column and row on the photo).
    if (checks.positionCode(c[`${e}_port`])) {
      if (portLabel != null && norm(portLabel) === norm(c[`${e}_port`])) return e;
      if (device && sheetCheck.portForCode(c[`${e}_port`], device) === portNo) return e;
      continue;
    }
    if (portNumber(c[`${e}_port`]) === portNo) return e;
  }
  return null;
}

function otherEnd(c, e) {
  const o = e === 'a' ? 'z' : 'a';
  return {
    end: o,
    device: c[`${o}_device`] || null,
    port: c[`${o}_port`] != null && c[`${o}_port`] !== '' ? String(c[`${o}_port`]) : null,
    rack: c[`${o}_rack`] || null,
    unit: unitOf(c[`${o}_unit`]),
  };
}

function expectedOf(c, e) {
  return {
    cableLabel: c.cable_label || null,
    cableType: c.cable_type || null,
    rowNo: c.row_no ?? null,
    end: e,
    other: otherEnd(c, e),
  };
}

/** The best label per port of each device: "deviceId:portNo" -> label. Close-ups come first. */
function labelsByPort(view, doc) {
  const out = new Map();
  const loose = [];
  for (const l of checks.labelsOf(view, doc)) {
    if (l.portNo == null) { loose.push(l); continue; }
    const k = `${l.deviceId}:${l.portNo}`;
    const had = out.get(k);
    const better = !had || (had.source !== 'close-up' && l.source === 'close-up')
      || (had.source === l.source && (l.confidence ?? 0) > (had.confidence ?? 0));
    if (better) out.set(k, l);
  }
  return { byPort: out, loose };
}

function portStatus({ doc, port, expected, label, labelCable, labelEnd }) {
  const state = port.state;
  const capped = (fallback) => (port.uncapped && state === 'empty' ? 'uncapped' : port.opticNoCap ? 'optic_no_cap' : fallback);
  if (!doc) {
    if (state === 'connected') return 'connected';
    if (state === 'empty') return capped('empty');
    return capped('unknown');
  }
  if (label) {
    if (!labelCable) return 'not_in_sheet';
    return labelEnd ? 'matched' : 'wrong_port';
  }
  // A missing cable needs a port state that can be relied on (close-up,
  // person, or read clearly); a rough guess stays 'not found'.
  const sure = port.stateSource === 'close-up' || port.stateSource === 'person'
    || (typeof port.confidence === 'number' && port.confidence >= 0.7);
  if (expected) return state === 'empty' && sure ? 'missing_cable' : 'not_found';
  if (state === 'connected') return sure ? 'not_in_sheet' : 'connected';
  if (state === 'empty') return capped('empty');
  return capped('unknown');
}

/** Rows the rack photo has, top unit first. */
function unitRows(view, doc) {
  const total0 = Number.isInteger(view.unitsTotal) && view.unitsTotal > 0 ? view.unitsTotal : null;
  // With the rack's height known, the rows the engine adds for the frame
  // above the top unit are not rows of the rack; its devices still count.
  const nums = total0 ? view.visible.flatMap((d) => d.units) : view.devices.flatMap((d) => d.units);
  const rack = view.rackLabel;
  const expected = new Map();
  if (doc && rack) {
    for (const [, dev] of sheetCheck.sheetDevices(doc)) {
      for (const end of dev.ends) {
        if (end.unit != null && sheetCheck.sameRack(end.rack, rack) && !expected.has(end.unit)) expected.set(end.unit, dev.name);
      }
    }
  }
  const all = [...nums, ...expected.keys()].filter((n) => n != null);
  const total = total0;
  if (!all.length && !total) return [];
  // The whole rack when its height is known (lib/rack_height.js): every unit
  // from the top down to U1, the ones with nothing in them marked empty.
  // Without it, the span the photo and the sheet cover, as before.
  const top = total ? Math.max(total, ...all) : Math.max(...all);
  const bottom = total ? 1 : Math.min(...all);
  const rows = [];
  for (let u = top; u >= bottom; u -= 1) {
    const d = view.visible.find((x) => x.units.includes(u));
    const hidden = d ? null : view.devices.find((x) => x.hidden && x.units.includes(u));
    const kind = d ? 'device' : hidden ? (hidden.type === 'Unidentified' ? 'unidentified' : 'empty') : total ? 'empty' : 'not_read';
    const exp = expected.get(u) || null;
    const same = exp && d && d.sheetName && norm(d.sheetName) === norm(exp);
    rows.push({
      unit: u,
      kind,
      deviceId: d ? d.deviceId : null,
      expected: exp,
      expectedStatus: !exp ? null : same ? 'ok' : d ? 'different' : 'missing',
    });
  }
  return rows;
}

/**
 * A rack photo taken from across the room reads a data-centre switch's ports
 * and dust caps badly: no port at all, most ports "could not tell", or most
 * caps not clear. That is a limit of the distance, not a fault, so instead of
 * a row of "not clear" per port the device asks for one close-up, which reads
 * its ports, caps and labels (routes/physical.js zones). Null when the photo
 * read it well enough, or close-ups already cover every port read. Only for
 * patching gear (a PDU's outlets are not read this way).
 *   why: "no_ports" | "ports" (half or more not clear) | "caps" (three or
 *   more empty ports, half or more of them with the cap not clear)
 */
const UNCLEAR_SHARE = 0.5;
const UNCLEAR_CAPS_MIN = 3;
const PATCHED = new Set(['Switch', 'Patch Panel', 'Router', 'Firewall', 'Gateway']);
function closeUpNeed(d, ports, name) {
  if (!d.hasPorts || !PATCHED.has(d.type)) return null;
  const text = `Take a close-up of ${name} to read its ports and caps`;
  if (!ports.length) return { why: 'no_ports', text, notClear: 0, capsNotClear: 0 };
  if (ports.every((p) => p.stateSource === 'close-up' || p.stateSource === 'person')) return null;
  const fromPhoto = ports.filter((p) => p.stateSource === 'photo');
  const notClear = fromPhoto.filter((p) => p.state !== 'connected' && p.state !== 'empty').length;
  const open = fromPhoto.filter((p) => p.state !== 'connected' && !p.uncapped && !p.opticNoCap && p.capState !== 'connected');
  const capsNotClear = open.filter((p) => p.capState === 'uncertain' || p.capState === 'too_small').length;
  if (notClear && notClear >= UNCLEAR_SHARE * ports.length) return { why: 'ports', text, notClear, capsNotClear };
  if (capsNotClear >= UNCLEAR_CAPS_MIN && capsNotClear >= UNCLEAR_SHARE * open.length) return { why: 'caps', text, notClear, capsNotClear };
  return null;
}

function build(view, doc) {
  if (doc) sheetCheck.resolve(view, doc);
  const sheet = sheetCheck.check(view, doc);
  const byLabel = cablesByLabel(doc);
  const { byPort, loose } = labelsByPort(view, doc);
  const rack = view.rackLabel;
  const unitStatus = new Map(sheet.units.map((u) => [u.deviceId, u]));
  const nameOf = (d) => d.sheetName || scanView.shortName(d);

  const cables = [];
  // A cable tag read on the photo names this end (device and port) on one
  // face and the far end on the other (the same flag): when the Excel has
  // no row for a port, its own tag still says where the cable goes.
  const tagFaces = (view.flagLines || []).filter((l) => l?.end?.device && l.end.port);
  // A face whose port is hidden (the tag folded over it) still names the far
  // device and rack: the other end is said without a port.
  const hiddenFaces = (view.flagLines || []).map((l) => ({ l, h: checks.portHiddenEnd(l) })).filter((x) => x.h);
  const fromTag = (d, portNo) => {
    const names = [d.sheetName, d.labelText, d.name].filter(Boolean);
    const mine = (dev) => names.some((n) => checks.nameFits(dev, n));
    const near = tagFaces.find((l) => l.end.portNo === portNo && mine(l.end.device));
    if (!near) return null;
    const far = near.flag != null ? tagFaces.find((l) => l.flag === near.flag && l !== near && !mine(l.end.device)) : null;
    const farHidden = !far && near.flag != null ? hiddenFaces.find((x) => x.l.flag === near.flag && x.l !== near && !mine(x.h.device)) : null;
    return {
      read: near.text,
      box: Array.isArray(near.box) ? near.box : null,
      other: far ? { device: far.end.device, port: far.end.port, rack: far.end.position || null, read: far.text, box: Array.isArray(far.box) ? far.box : null }
        : farHidden ? { device: farHidden.h.device, port: null, portHidden: true, rack: farHidden.h.position || null, read: farHidden.l.text, box: Array.isArray(farHidden.l.box) ? farHidden.l.box : null } : null,
    };
  };
  // A label left between two ports (checks.portUnder, zone_read.py) is
  // shown on both, as hanging between them; it is placed on neither.
  const betweenOn = new Map();
  for (const l of loose) {
    if (!Array.isArray(l.between)) continue;
    for (const n of l.between) {
      const k = `${l.deviceId}:${n}`;
      if (!betweenOn.has(k)) betweenOn.set(k, []);
      betweenOn.get(k).push({ text: l.text, between: l.between, source: l.source });
    }
  }
  const devices = view.visible.map((d) => {
    const ports = d.ports.map((p) => {
      const l = byPort.get(`${d.deviceId}:${p.portNo}`) || null;
      let expected = null;
      let reference = null;
      if (doc && d.sheetName) {
        for (const c of doc.cables || []) {
          const e = endOn(c, d.sheetName, p.portNo, rack, false, l ? l.text : null, d);
          if (e) { expected = expectedOf(c, e); reference = drift.referenceOf(c, e); break; }
        }
      }
      const labelCable = l ? byLabel.get(l) : null;
      const labelEnd = labelCable && d.sheetName ? endOn(labelCable, d.sheetName, p.portNo, rack, true) : null;
      const status = portStatus({ doc, port: p, expected, label: l, labelCable, labelEnd });
      const out = {
        portNo: p.portNo,
        kind: p.kind,
        box: Array.isArray(p.box) ? p.box : null,
        type: p.type,
        state: p.state,
        stateSource: p.stateSource,
        confidence: p.confidence,
        cableConnector: p.cableConnector ?? null,
        cableColor: p.cableColor ?? null,
        label: l ? { text: l.text, confidence: l.confidence ?? null, source: l.source, match: labelCable ? byLabel.how(l) : null, ...(l.end ? { end: l.end } : {}),
          ...(l.portFrom ? { portFrom: l.portFrom } : {}), ...(l.between ? { between: l.between } : {}) } : null,
        expected,
        tag: !expected ? fromTag(d, p.portNo) : null,
        labelsBetween: !l && betweenOn.has(`${d.deviceId}:${p.portNo}`) ? betweenOn.get(`${d.deviceId}:${p.portNo}`) : null,
        labelBelongsTo: labelCable && !labelEnd ? {
          cableLabel: labelCable.cable_label,
          explained: `Label ${l.text} found on ${nameOf(d)} port ${p.portNo}, the Excel says ${[['a', labelCable.a_device, labelCable.a_port], ['z', labelCable.z_device, labelCable.z_port]].map(([, dv, pt]) => `${dv || 'no device'}${pt ? ` port ${pt}` : ''}`).join(' to ')}`,
          a: { device: labelCable.a_device || null, port: labelCable.a_port != null ? String(labelCable.a_port) : null, rack: labelCable.a_rack || null },
          z: { device: labelCable.z_device || null, port: labelCable.z_port != null ? String(labelCable.z_port) : null, rack: labelCable.z_rack || null },
        } : null,
        status,
        uncapped: p.uncapped,
        opticNoCap: p.opticNoCap || null,
        capState: p.capState ?? null,
        capReason: p.capReason ?? null,
        fields: {
          ...(p.fields || {}),
          label: l ? fieldSources.labelField({ confidence: l.confidence ?? null, match: labelCable ? byLabel.how(l) : null }) : null,
          otherEnd: expected || labelCable ? fieldSources.field('excel') : null,
        },
      };
      // The drift check of this port, field by field, from what was read on
      // it and the Excel row for it (lib/drift.js). Only for a device the
      // Excel names: anything else cannot drift from it.
      out.drift = doc && d.sheetName ? drift.compare(drift.actualOfPort(out, { device: d.sheetName, units: d.units, unitExpected: d.unitExpected }), reference) : null;
      if (p.state === 'connected' || l) {
        const c = labelCable || (expected ? byLabel.get(expected.cableLabel) : null);
        const e = c ? (labelEnd || (expected ? expected.end : null)) : null;
        cables.push({
          label: l ? l.text : null,
          labelSource: l ? l.source : null,
          labelMatch: l && labelCable ? byLabel.how(l) : null,
          sheetLabel: expected ? expected.cableLabel : null,
          from: { deviceId: d.deviceId, device: nameOf(d), unit: d.unitTop, portNo: p.portNo },
          to: c && e ? otherEnd(c, e) : null,
          type: (c && c.cable_type) || p.cableConnector || null,
          colour: p.cableColor || null,
          status,
        });
      }
      return out;
    });
    const u = unitStatus.get(d.deviceId);
    return {
      deviceId: d.deviceId,
      type: d.type,
      name: nameOf(d),
      box: Array.isArray(d.box) ? d.box : null,
      units: d.units,
      unitTop: d.unitTop,
      unitBottom: d.unitBottom,
      heightU: d.heightU,
      make: d.make,
      model: d.model,
      confidence: d.confidence,
      source: d.source,
      labelText: d.labelText,
      sheetName: d.sheetName || null,
      matchedBy: d.matchedBy || null,
      labelMatch: d.labelMatch || null,
      fields: {
        ...(d.fields || {}),
        sheetName: d.sheetName ? fieldSources.field('excel', null, sheetCheck.nameHowWords(d)) : null,
      },
      unitExpected: d.unitExpected ?? null,
      sheetStatus: doc ? (u ? u.status : 'not_in_sheet') : null,
      hasPorts: d.hasPorts,
      portCount: d.portCount,
      zonesTaken: view.zones.filter((z) => Number(z.deviceId) === d.deviceId).length,
      closeUp: closeUpNeed(d, ports, nameOf(d)),
      counts: {
        ports: ports.length,
        connected: ports.filter((p) => p.state === 'connected').length,
        empty: ports.filter((p) => p.state === 'empty').length,
        unknown: ports.filter((p) => p.state !== 'connected' && p.state !== 'empty').length,
      },
      ports,
    };
  });

  // Labels read on a device but not placed on a port: still a cable seen.
  for (const l of loose) {
    const d = devices.find((x) => x.deviceId === l.deviceId);
    const c = byLabel.get(l);
    const e = c && d?.sheetName ? ['a', 'z'].find((x) => norm(c[`${x}_device`]) === norm(d.sheetName)) : null;
    // Read on a named device that is neither end of its cable: wherever on
    // that device it hangs, it is not where the Excel puts it.
    const elsewhere = !!(c && d?.sheetName && !e);
    cables.push({
      label: l.text,
      labelSource: l.source,
      labelMatch: c ? byLabel.how(l) : null,
      sheetLabel: c ? c.cable_label : null,
      from: { deviceId: l.deviceId, device: d ? d.name : l.device, unit: d ? d.unitTop : null, portNo: null, ...(l.between ? { between: l.between } : {}) },
      to: c && e ? otherEnd(c, e) : null,
      type: c ? c.cable_type || null : null,
      colour: null,
      status: !doc ? 'connected' : c ? (elsewhere ? 'wrong_port' : 'not_found') : 'not_in_sheet',
      explained: elsewhere ? `Label ${l.text} found on ${d.name}, the Excel says ${c.a_device || 'no device'}${c.a_port ? ` port ${c.a_port}` : ''} to ${c.z_device || 'no device'}${c.z_port ? ` port ${c.z_port}` : ''}` : null,
      sheetEnds: c ? { a: { device: c.a_device || null, port: c.a_port != null ? String(c.a_port) : null }, z: { device: c.z_device || null, port: c.z_port != null ? String(c.z_port) : null } } : null,
    });
  }

  // A cable end on a device of this photo gets the port it lands on: a plain
  // number as given, a panel's printed code by the site's convention.
  const byNameInView = new Map(view.visible.filter((d) => d.sheetName).map((d) => [norm(d.sheetName), d]));
  const placeEnd = (end) => {
    if (!end || !end.device) return;
    const dev = byNameInView.get(norm(end.device));
    if (!dev) return;
    const coded = checks.positionCode(end.port);
    const n = coded ? sheetCheck.portForCode(end.port, dev) : portNumber(end.port);
    if (n != null) { end.portNo = n; end.deviceId = dev.deviceId; return; }
    // The port was not read: its place on the panel is still known from the column.
    if (coded) {
      const box = sheetCheck.positionForCode(end.port, dev);
      if (box) { end.deviceId = dev.deviceId; end.box = box; end.placedBy = 'column'; }
    }
  };
  for (const d of devices) {
    for (const p of d.ports) {
      if (p.expected?.other) placeEnd(p.expected.other);
      if (p.labelBelongsTo) { placeEnd(p.labelBelongsTo.a); placeEnd(p.labelBelongsTo.z); }
    }
  }
  const allPorts = devices.flatMap((d) => d.ports);
  const units = unitRows(view, doc);
  return {
    scanId: view.scanId,
    rackLabel: rack || null,
    rackNameField: rack ? (fieldSources.rackNameField(view.naming && view.naming.name ? view.naming : null) || fieldSources.field('person', null, 'named by a person')) : null,
    unitsTotal: view.unitsTotal ?? null,
    heightSource: view.height?.source ?? null,
    heightNote: view.height?.note ?? null,
    heightStandard: view.height ? !!view.height.standard : null,
    siteId: view.siteId ?? null,
    takenAt: view.takenAt || null,
    by: view.by || null,
    matrix: doc ? { importId: doc.import?.id ?? null, fileName: doc.import?.file_name ?? null, cableCount: (doc.cables || []).length } : null,
    zones: view.zones.length,
    units,
    devices,
    cables,
    counts: {
      units: units.length,
      devices: devices.length,
      ports: allPorts.length,
      connected: allPorts.filter((p) => p.state === 'connected').length,
      empty: allPorts.filter((p) => p.state === 'empty').length,
      unknown: allPorts.filter((p) => p.state !== 'connected' && p.state !== 'empty').length,
      cables: cables.length,
      labelsRead: allPorts.filter((p) => p.label).length + loose.length,
      capUnsure: allPorts.filter((p) => p.capState === 'uncertain').length,
      closeUps: devices.filter((d) => d.closeUp).length,
    },
    flags: {
      wrongUnit: sheet.units.filter((u) => u.status === 'wrong_unit').length,
      missingCable: sheet.missing.length,
      unexpectedCable: sheet.unexpected.length,
      wrongType: sheet.wrongType ? sheet.wrongType.length : 0,
      uncapped: sheet.uncapped.length,
      opticNoCap: sheet.opticNoCap.length,
      capUnsure: sheet.capUnsure ? sheet.capUnsure.length : 0,
      notSeen: sheet.notSeen ? sheet.notSeen.length : 0,
    },
    notSeen: sheet.notSeen || [],
  };
}

module.exports = { build, portStatus, unitRows, closeUpNeed };
