/**
 * A cable's label is checked at each end on its own: a code printed at the
 * patch panel end, read there, is never compared with the switch end, and a
 * patch-panel position code (59A) is matched on the panel, not by number.
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const checks = require('../lib/checks');
const scanDetail = require('../lib/scan_detail');

const port = (portNo, state = 'connected') => ({ portNo, kind: 'main', box: [portNo * 10, 0, portNo * 10 + 8, 8], state, type: 'RJ45' });
function view(zones) {
  const dev = (deviceId, labelText, unit, n) => ({
    deviceId, type: labelText.includes('PP') ? 'Patch Panel' : 'Switch', labelText, units: [unit], unitTop: unit, unitBottom: unit,
    hasPorts: true, portCount: n, box: [0, 0, 400, 20], ports: Array.from({ length: n }, (_, i) => port(i + 1)),
  });
  const devices = [dev(1, 'SP-R1-U18-SW04', 18, 24), dev(2, 'SP-R1-U13-SW01', 13, 24), dev(3, 'SP-R1-U7-PP3', 7, 24)];
  return { scanId: 'RK-AAAAAAAA', siteId: 1, rackLabel: 'SP-HYB-RM01-R01-R1', devices, visible: devices, zones, frontLabels: [], unitsTotal: 24 };
}
const R = 'SP-HYB-RM01-R01-R1';
const doc = {
  import: { id: 'imp1' },
  cables: [
    { row_no: 2, cable_label: '53A', a_rack: R, a_device: 'SP-R1-U18-SW04', a_port: 'GigabitEthernet 1', z_rack: R, z_device: 'SP-R1-U7-PP3', z_port: '53A' },
    // The demo mismatch: really on SW04 port 8, the sheet says SW01 Port6.
    { row_no: 3, cable_label: '59A', a_rack: R, a_device: 'SP-R1-U13-SW01', a_port: 'Port6', z_rack: R, z_device: 'SP-R1-U7-PP3', z_port: '59A' },
  ],
};
const zones = [
  { deviceId: 1, labels: [{ text: '53A', portNo: 1 }, { text: '59A', portNo: 8 }] },
  { deviceId: 3, labels: [{ text: '53A', portNo: 1 }, { text: '59A', portNo: 13 }] },
];

test('both ends: the panel end read at the panel matches, the switch end is checked at the switch', () => {
  const v = view(zones);
  const r = checks.run({ a: v, z: v, doc, by: 't' });
  const byLabel = Object.fromEntries(r.cables.map((c) => [c.cableLabel, c]));
  assert.equal(byLabel['53A'].a.status, 'matched');
  assert.equal(byLabel['53A'].z.status, 'matched', 'position code 53A matched on the panel, not by number');
  assert.equal(byLabel['53A'].status, 'matched');
  assert.equal(byLabel['59A'].z.status, 'matched');
  assert.equal(byLabel['59A'].a.status, 'wrong_port');
  assert.equal(byLabel['59A'].a.found.device, 'SP-R1-U18-SW04');
  assert.match(byLabel['59A'].aExplained, /found on SP-R1-U18-SW04 port 8, the Excel says SP-R1-U13-SW01 port Port6/);
  assert.equal(byLabel['59A'].status, 'wrong_port');
});

test('a label read only at the switch leaves the panel end not found, never wrong', () => {
  const v = view([{ deviceId: 1, labels: [{ text: '53A', portNo: 1 }] }]);
  const r = checks.run({ a: v, z: v, doc, by: 't' });
  const c = r.cables.find((x) => x.cableLabel === '53A');
  assert.equal(c.a.status, 'matched');
  assert.equal(c.z.status, 'not_found');
});

test('scan detail: panel ports with position codes match, the moved cable is a wrong port with both ends said', () => {
  const d = scanDetail.build(view(zones), doc);
  const pp = d.devices.find((x) => x.sheetName === 'SP-R1-U7-PP3');
  assert.equal(pp.ports.find((p) => p.portNo === 1).status, 'matched');
  assert.equal(pp.ports.find((p) => p.portNo === 13).status, 'matched');
  const sw = d.devices.find((x) => x.sheetName === 'SP-R1-U18-SW04');
  assert.equal(sw.ports.find((p) => p.portNo === 1).status, 'matched');
  const p8 = sw.ports.find((p) => p.portNo === 8);
  assert.equal(p8.status, 'wrong_port');
  assert.match(p8.labelBelongsTo.explained, /Label 59A found on SP-R1-U18-SW04 port 8, the Excel says SP-R1-U13-SW01 port Port6 to SP-R1-U7-PP3 port 59A/);
});

test('positionCode', () => {
  assert.ok(checks.positionCode('14A'));
  assert.ok(checks.positionCode(' 50b '));
  assert.ok(!checks.positionCode('Port14'));
  assert.ok(!checks.positionCode('14'));
});

test('a device label refused as a near-tie is taken once the other name is claimed', () => {
  const v = view([{ deviceId: 1, labels: [{ text: '59A', portNo: null }] }]);
  v.devices[0].labelText = 'SP-RI-U15-SWO4';
  v.devices.push({ deviceId: 4, type: 'Switch', labelText: 'SP-R1-U15-SW02', units: [15], unitTop: 15, unitBottom: 15, hasPorts: true, portCount: 24, box: [0, 0, 1, 1], ports: [] });
  const d2 = { ...doc, cables: [...doc.cables, { row_no: 4, cable_label: 'X1', a_device: 'SP-R1-U15-SW02', a_port: 'Port1', z_device: 'SP-R1-U18-SW04', z_port: 'GigabitEthernet 2' }] };
  const d = scanDetail.build(v, d2);
  assert.equal(d.devices.find((x) => x.deviceId === 1).sheetName, 'SP-R1-U18-SW04');
  const c = d.cables.find((x) => x.label === '59A');
  assert.equal(c.status, 'wrong_port', 'read on SW04, the Excel says SW01');
  assert.match(c.explained, /Label 59A found on SP-R1-U18-SW04, the Excel says SP-R1-U13-SW01 port Port6/);
});

// The cable's own tag, read on the rack photo (pipeline/cable_flags.py), is
// evidence for an end the photo could not place.
const TAG_DOC = {
  import: { id: 'imp2' },
  cables: [
    // The far end is in a rack that was not photographed.
    { row_no: 2, cable_label: 'CBL-1', a_rack: R, a_device: 'SP-R1-U18-SW04', a_port: 'Et5', z_rack: 'B08', z_device: 'nvs-sgkc-wan-t03', z_port: 'Te1' },
    // Both ends in this rack, on devices the photo did not show.
    { row_no: 3, cable_label: 'CBL-2', a_rack: R, a_device: 'nvs-sgkc-sis-mt02', a_port: 'TE0', z_rack: R, z_device: 'nvs-sgkc-sis-sw01d', z_port: 'ETH1-17' },
    // A tag printing another rack's position is not this end.
    { row_no: 4, cable_label: 'CBL-3', a_rack: R, a_device: 'nvs-sgkc-sis-sw05b', a_port: 'Gi1-11', z_rack: 'B11', z_device: 'nvs-sgkc-sis-sw05a', z_port: 'Gi1-11' },
  ],
};
const TAGS = [
  { text: 'nvs-sgkc-wan-t03 Te1 B08', box: [10, 2, 20, 6], conf: 0.95, end: { device: 'nvs-sgkc-wan-t03', port: 'Te1', position: 'B08', portNo: 1 } },
  { text: 'mvs-s9kc-sis-mt02 TE0', box: [30, 2, 40, 6], conf: 0.9, end: { device: 'mvs-s9kc-sis-mt02', port: 'TE0', position: null, portNo: 0 } },
  { text: 'nvs-s9kc-sis-sw01d ETH1-17', box: [50, 2, 60, 6], conf: 0.9, end: { device: 'nvs-s9kc-sis-sw01d', port: 'ETH1-17', position: null, portNo: 17 } },
  { text: 'nvs-sgkc-sis-sw05a Gi1-11 B09', box: [70, 2, 80, 6], conf: 0.9, end: { device: 'nvs-sgkc-sis-sw05a', port: 'Gi1-11', position: 'B09', portNo: 11 } },
];

test('a tag read in the rack prints an end the photo could not place: on_tag, with the reading repaired', () => {
  const v = { ...view([]), flagLines: TAGS };
  const r = checks.run({ a: v, z: v, doc: TAG_DOC, by: 't' });
  const byLabel = Object.fromEntries(r.cables.map((c) => [c.cableLabel, c]));
  // The far end, in a rack not photographed, is printed on the tag read here.
  assert.equal(byLabel['CBL-1'].a.status, 'not_found', 'the switch end read nothing on port 5');
  assert.equal(byLabel['CBL-1'].z.status, 'on_tag');
  assert.deepEqual(byLabel['CBL-1'].z.found, { device: 'nvs-sgkc-wan-t03', port: '1', source: 'tag', read: 'nvs-sgkc-wan-t03 Te1 B08', match: 'exact', inRack: R, box: [10, 2, 20, 6], side: 'a' });
  assert.equal(byLabel['CBL-1'].status, 'on_tag');
  // Both ends on the tags, the device names two and one character off.
  assert.equal(byLabel['CBL-2'].a.status, 'on_tag');
  assert.equal(byLabel['CBL-2'].a.found.match, 'two_edits');
  assert.equal(byLabel['CBL-2'].z.status, 'on_tag');
  assert.equal(byLabel['CBL-2'].z.found.match, 'one_edit');
  assert.equal(byLabel['CBL-2'].status, 'on_tag');
  // The sw05a tag prints B09, the sheet says B11: not this end.
  assert.equal(byLabel['CBL-3'].z.status, null);
  assert.equal(byLabel['CBL-3'].a.status, 'not_found');
  assert.equal(byLabel['CBL-3'].status, 'not_found');
  assert.equal(r.counts.onTag, 2);
  assert.equal(r.counts.notFound, 1);
  assert.equal(r.accuracy.labelsRead, 2, 'the two ends in this rack read off their tags');
  assert.equal(r.accuracy.labelsExpected, 4);
  assert.ok(!r.cables.some((c) => c.status === 'not_in_sheet' && c.cableLabel === 'nvs-sgkc-wan-t03 Te1 B08'), 'a tag face used as evidence is not a new cable');
  assert.deepEqual(r.cables.map((c) => c.status), ['not_found', 'on_tag', 'on_tag'], 'tags sort after the problems');
});

test('readings that are not cable labels are left out: a serial, a cut flag, a faint line with no device, a faint read', () => {
  const inside = (x) => ({ x, y: 5, w: 20, h: 5 });
  const v = { ...view([]),
    frontLabels: [
      { text: 'FDO220380WU', conf: 0.67, bbox: inside(10) },
      { text: 'S6BLCC', conf: 0.26, bbox: inside(60) },
      { text: 'CBL-77', conf: 0.9, bbox: inside(110) },
    ],
    flagLines: [
      { text: 'S/N FDO2203B0WU', box: [10, 2, 40, 6], conf: 0.97, end: null },
      { text: 'SP-R1-U18-SW04 Gi1-0', raw: 'SP-R1-U18-SW04 Gi1-0-', box: [50, 2, 60, 6], conf: 0.97, end: { device: 'SP-R1-U18-SW04', port: 'Gi1-0', position: null, portNo: 0 } },
      { text: 'VIA 114 T12', box: [70, 2, 80, 6], conf: 0.64, end: { device: null, port: 'VIA 114', position: 'T12', portNo: 114 } },
      { text: 'Gi1-0-14 B12', box: [90, 2, 100, 6], conf: 0.9, end: { device: null, port: 'Gi1-0-14', position: 'B12', portNo: 14 } },
      // No device and a position no sheet rack carries: a sticker, not a tag.
      { text: 'CAT6 75 B88', box: [110, 2, 120, 6], conf: 0.84, end: { device: null, port: 'CAT6 75', position: 'B88', portNo: 75 } },
      { text: 'TS011 B01', box: [130, 2, 140, 6], conf: 0.88, end: { device: null, port: 'TS011', position: 'B01', portNo: 11 } },
    ] };
  const sheet = { ...doc, cables: [...doc.cables, { row_no: 9, cable_label: 'X', a_rack: 'B12', a_device: 'sw05b', a_port: '1', z_rack: 'B12', z_device: 'pp1', z_port: '2' }] };
  const texts = checks.labelsOf(v, sheet).map((l) => l.text);
  assert.deepEqual(texts, ['Gi1-0-14 B12', 'CBL-77']);
});

test('busy: what the server is still working on for a scan', () => {
  const busy = require('../lib/busy');
  const k = busy.labelsKey('rk-test1');
  assert.equal(k, 'labels:RK-TEST1');
  assert.equal(busy.is(k), false);
  busy.mark(k);
  assert.equal(busy.is(k), true);
  assert.ok(busy.list().includes(k));
  busy.clear(k);
  assert.equal(busy.is(k), false);
});

test('a tag not in the sheet is offered to add: its own end, and the far end from the other face of the same tag', () => {
  const v = { ...view([]), flagLines: [
    { text: 'SP-R1-U18-SW04 Gi1-0-9 B12', box: [10, 2, 20, 6], conf: 0.95, flag: 7, end: { device: 'SP-R1-U18-SW04', port: 'Gi1-0-9', position: 'B12', portNo: 9 } },
    { text: 'b08-reg-gwlc02 Gig03P B08', box: [22, 2, 30, 6], conf: 0.95, flag: 7, end: { device: 'b08-reg-gwlc02', port: 'Gig03P', position: 'B08', portNo: 3 } },
  ] };
  const r = checks.run({ a: v, z: v, doc, by: 't' });
  const c = r.cables.find((x) => x.cableLabel === 'SP-R1-U18-SW04 Gi1-0-9 B12');
  assert.equal(c.status, 'not_in_sheet');
  assert.deepEqual(c.add, { cable_label: 'SP-R1-U18-SW04 Gi1-0-9 B12', a_device: 'SP-R1-U18-SW04', a_port: 'Gi1-0-9', a_rack: 'B12', z_device: 'b08-reg-gwlc02', z_port: 'Gig03P', z_rack: 'B08' });
});
