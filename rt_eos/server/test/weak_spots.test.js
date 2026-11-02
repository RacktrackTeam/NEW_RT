/**
 * The known weak spots of a rack photo, worked on (1 Oct 2026):
 *   - a tag face folded over its port: the port from the other face of the
 *     same tag, or from the only Excel cable on that device not yet seen;
 *   - a label hanging between two ports: placed on the nearer when the gap
 *     says so clearly, else kept with both ports named;
 *   - a device's own sticker names it from the Excel, the short form too;
 *   - a device whose ports or caps a rack photo cannot read asks for one
 *     close-up instead of many "not clear" rows, and the close-up then
 *     fills ports, caps, a missing cable and a wrong port.
 * Pure library tests on views built in memory; nothing on disk.
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const checks = require('../lib/checks');
const sheetCheck = require('../lib/sheet_check');
const scanDetail = require('../lib/scan_detail');
const scanView = require('../lib/scan_view');

const RACK = 'B12';
const row = (rowNo, label, a, aPort, aRack, z, zPort, zRack) => ({ row_no: rowNo, cable_label: label, a_device: a, a_port: aPort, a_rack: aRack, a_unit: '', z_device: z, z_port: zPort, z_rack: zRack, z_unit: '' });
const port = (n, state = 'connected', extra = {}) => ({ portNo: n, kind: 'main', type: 'RJ45', state, stateSource: 'photo', confidence: 0.9, box: [100 + n * 20, 50, 116 + n * 20, 66], ...extra });

function rackView({ flagLines = [], ports = 26, zones = [], frontLabels = [] } = {}) {
  const sw = { deviceId: 1, type: 'Switch', units: [8], unitTop: 8, unitBottom: 8, labelText: 'nvs-sgkc-sis-sw05b', labelSource: 'sticker',
    box: [100, 40, 800, 80], hasPorts: true, portCount: ports, ports: Array.from({ length: ports }, (_, i) => port(i + 1)) };
  return { scanId: 'RK-WEAK0001', siteId: 1, rackLabel: RACK, devices: [sw], visible: [sw], zones, frontLabels, flagLines, unitsTotal: 42 };
}

/* ── A tag face folded over its port ─────────────────────────────── */

const FOLD_DOC = { import: { id: 'f1' }, cables: [
  row(2, 'C-26', 'nvs-sgkc-sis-sw05b', 'Gi1-0-26', RACK, 'b08-reg-gwlc02', 'Gig05P', 'B08'),
  row(3, 'C-07', 'nvs-sgkc-sis-sw05b', 'Gi1-7', RACK, 'nvs-sgkc-sis-sw03b', 'E1-48', 'B9'),
  row(4, 'C-11', 'nvs-sgkc-sis-sw05b', 'Gi1-11', RACK, 'nvs-sgkc-sis-sw05a', 'Gi1-11', 'B11'),
] };

test('portHiddenEnd: device and rack with no port number is a folded face; anything else is not', () => {
  assert.deepEqual(checks.portHiddenEnd({ text: 's-Sw05b Gi B12', end: null }),
    { device: 's-Sw05b', position: 'B12', port: null, portNo: null, portHidden: true, printed: 'Gi' });
  assert.equal(checks.portHiddenEnd({ text: 'nvs-sgkc-sis-sw05b B12', end: null }).device, 'nvs-sgkc-sis-sw05b');
  for (const text of ['Name. sis-rt02', 'Rack A03 RU18', 'SW05B G23', 'G3 LOC Rack A03 RU28', 'CAT6 75 B88']) {
    assert.equal(checks.portHiddenEnd({ text, end: null }), null, text);
  }
  assert.equal(checks.portHiddenEnd({ text: 'nvs-sgkc-sis-sw05b Gi1-0-26 B12', end: { device: 'nvs-sgkc-sis-sw05b', port: 'Gi1-0-26', position: 'B12', portNo: 26 } }), null);
});

test('a folded face takes its port from the other face of its tag: matched, said so, in the check and on the port', () => {
  const v = rackView({ flagLines: [
    { text: 's-Sw05b Gi B12', box: [600, 90, 620, 200], conf: 0.94, flag: 4, end: null },
    { text: 'b08-reg-gwlc02 Gig05P B08', box: [622, 90, 640, 200], conf: 0.97, flag: 4, end: { device: 'b08-reg-gwlc02', port: 'Gig05P', position: 'B08', portNo: 5 } },
  ] });
  const faces = checks.foldedFaces(v, FOLD_DOC);
  assert.equal(faces.length, 1);
  assert.equal(faces[0].match, 'other_face');
  assert.equal(faces[0].cable.cable_label, 'C-26');
  assert.equal(faces[0].end.portNo, 26);
  assert.equal(faces[0].end.deviceRepaired, 'nvs-sgkc-sis-sw05b');

  const r = checks.run({ a: v, z: v, doc: FOLD_DOC, by: 't' });
  const c = r.cables.find((x) => x.cableLabel === 'C-26');
  assert.equal(c.a.status, 'matched');
  assert.equal(c.a.found.match, 'other_face');
  assert.equal(c.a.found.portFrom, 'other_face');
  assert.equal(c.a.found.port, '26');
  assert.equal(r.counts.portFromTag, 1);
  assert.equal(r.counts.correctedReadings, 0, 'a port from the Excel is not a corrected reading');

  const det = scanDetail.build(rackView({ flagLines: v.flagLines }), FOLD_DOC);
  const p26 = det.devices[0].ports.find((p) => p.portNo === 26);
  assert.equal(p26.status, 'matched');
  assert.equal(p26.label.portFrom, 'other_face');
  assert.equal(p26.label.match, 'other_face');
});

test('a folded face with no other face takes the only Excel cable on its device not yet seen', () => {
  const v = rackView({ flagLines: [
    { text: 'nvs-sgkc-sis-sw05b B12', box: [600, 90, 620, 200], conf: 0.9, flag: 9, end: null },
    // Two of the three cables are read in full elsewhere on the photo.
    { text: 'nvs-sgkc-sis-sw05b Gi1-0-26 B12', box: [200, 90, 220, 200], conf: 0.97, flag: 1, end: { device: 'nvs-sgkc-sis-sw05b', port: 'Gi1-0-26', position: 'B12', portNo: 26 } },
    { text: 'sw05b Gi1-7 B12', box: [300, 90, 320, 200], conf: 0.97, flag: 2, end: { device: 'sw05b', port: 'Gi1-7', position: 'B12', portNo: 7 } },
  ] });
  const faces = checks.foldedFaces(v, FOLD_DOC);
  assert.equal(faces.length, 1);
  assert.equal(faces[0].match, 'only_cable');
  assert.equal(faces[0].cable.cable_label, 'C-11');
  assert.equal(faces[0].end.portNo, 11);
  // With two cables not yet seen, nothing is taken.
  const w = rackView({ flagLines: v.flagLines.slice(0, 2) });
  assert.equal(checks.foldedFaces(w, FOLD_DOC).length, 0);
});

test('a folded face whose device is in a rack not photographed is still evidence for that end (on_tag)', () => {
  const doc = { import: { id: 'f2' }, cables: [row(2, 'C-X', 'nvs-sgkc-sis-sw03b', 'E1-48', 'B9', 'nvs-sgkc-sis-sw05b', 'Gi1-7', RACK)] };
  const v = rackView({ flagLines: [
    { text: 'sw03b-x B9', box: [600, 90, 620, 200], conf: 0.9, flag: 3, end: null },
    { text: 'sw05b Gi1-7 B12', box: [622, 90, 640, 200], conf: 0.97, flag: 3, end: { device: 'sw05b', port: 'Gi1-7', position: 'B12', portNo: 7 } },
  ] });
  // "sw03b-x" is no Excel name: nothing is taken.
  assert.equal(checks.foldedFaces(v, doc).length, 0);
  v.flagLines[0].text = 'sis-sw03b B9';
  const r = checks.run({ a: v, z: v, doc, by: 't' });
  const c = r.cables.find((x) => x.cableLabel === 'C-X');
  assert.equal(c.a.status, 'on_tag');
  assert.equal(c.a.found.match, 'other_face');
  assert.equal(c.a.found.port, '48');
});

/* ── A label between two ports ───────────────────────────────────── */

test('portUnder: a label clearly nearer one port is placed; one half way is left between the two', () => {
  const d = rackView({ ports: 6 }).visible[0];
  // Ports 3 and 4 have centres x 168 and 188 (y 58), 16 high.
  assert.deepEqual(checks.portUnder(d, 169, 75), { portNo: 3, between: null }, 'straight under port 3');
  assert.deepEqual(checks.portUnder(d, 178, 75), { portNo: null, between: [3, 4] }, 'half way');
  assert.deepEqual(checks.portUnder(d, 173, 60), { portNo: 3, between: null }, 'a quarter of the way: port 3');
  assert.deepEqual(checks.portUnder(d, 900, 60), { portNo: null, between: null }, 'near no port');
});

const BETWEEN_DOC = { import: { id: 'b1' }, cables: [
  row(2, 'FC-001', 'nvs-sgkc-sis-sw05b', 'Gi1-0-4', RACK, 'pp1', '4', RACK),
  row(3, 'FC-009', 'nvs-sgkc-sis-sw05b', 'Gi1-0-9', RACK, 'pp1', '9', RACK),
] };

test('a label read between two ports: kept unplaced with both ports, on the A to Z and on both port cards', () => {
  const bbox = (cx) => ({ x: cx - 15, y: 70, w: 30, h: 8 });
  const v = rackView({ ports: 10, frontLabels: [
    { text: 'FC-001', conf: 0.9, bbox: bbox(178) }, // between ports 3 and 4: the Excel's port 4 is one of them
    { text: 'FC-009', conf: 0.9, bbox: bbox(178 + 40) }, // between ports 5 and 6: the Excel says 9
  ] });
  const r = checks.run({ a: v, z: v, doc: BETWEEN_DOC, by: 't' });
  const one = r.cables.find((c) => c.cableLabel === 'FC-001');
  assert.equal(one.a.status, 'not_found');
  assert.deepEqual(one.a.found.between, [3, 4]);
  assert.match(one.aExplained, /between ports 3 and 4/);
  const nine = r.cables.find((c) => c.cableLabel === 'FC-009');
  assert.equal(nine.a.status, 'wrong_port', 'neither 5 nor 6 is the Excel\'s port 9');
  assert.match(nine.aExplained, /between ports 5 and 6, the Excel says nvs-sgkc-sis-sw05b port Gi1-0-9/);

  const det = scanDetail.build(rackView({ ports: 10, frontLabels: v.frontLabels }), BETWEEN_DOC);
  const ports = det.devices[0].ports;
  assert.deepEqual(ports.find((p) => p.portNo === 3).labelsBetween, [{ text: 'FC-001', between: [3, 4], source: 'rack photo' }]);
  assert.deepEqual(ports.find((p) => p.portNo === 4).labelsBetween[0].between, [3, 4]);
  assert.equal(ports.find((p) => p.portNo === 1).labelsBetween, null);
  const cable = det.cables.find((c) => c.label === 'FC-001');
  assert.deepEqual(cable.from.between, [3, 4]);
});

test('a close-up label the reader left between two ports keeps both ports', () => {
  const v = rackView({ ports: 10, zones: [{ deviceId: 1, labels: [{ text: 'FC-001', confidence: 0.9, portNo: null, between: [4, 5] }] }] });
  const labels = checks.labelsOf(v, BETWEEN_DOC);
  assert.deepEqual(labels.map((l) => [l.text, l.portNo, l.between]), [['FC-001', null, [4, 5]]]);
});

/* ── A device's own sticker names it from the Excel ──────────────── */

const STICKER_DOC = { import: { id: 's1' }, cables: [
  row(2, 'R-1', 'nvs-sgkc-sis-rt02', 'TE1', 'B07', 'nvs-sgkc-sis-sw05b', 'Et2', RACK),
  row(3, 'R-2', 'nvs-sgkc-sis-mt02', 'TE0', 'B07', 'nvs-sgkc-sis-sw01d', 'ETH1-17', 'B07'),
  row(4, 'R-3', 'nvs-sgkc-sis-rt01', 'Gi0/0/3', 'B07', 'nvs-sgkc-sis-sw05b', 'Gi1-0-14', RACK),
] };
function routerView(stickers) {
  const devs = stickers.map((labelText, i) => ({ deviceId: i + 1, type: 'Router', units: [10 - 2 * i], unitTop: 10 - 2 * i, unitBottom: 10 - 2 * i,
    labelText, labelSource: labelText ? 'sticker' : null, box: [0, i * 100, 500, i * 100 + 80], ports: [], hasPorts: true }));
  return { rackLabel: 'B07', visible: devs, devices: devs, zones: [], frontLabels: [], flagLines: [], unitsTotal: 42 };
}

test('the short name on a device\'s own sticker names it from the Excel by its tail (matchedBy sticker)', () => {
  const v = routerView(['sis-rt02', 'Name: sis-rt01', 'an-rt05']);
  v.visible[1].labelText = 'sis-rt01';
  sheetCheck.resolve(v, STICKER_DOC);
  const [a, b, c] = v.visible;
  assert.equal(a.sheetName, 'nvs-sgkc-sis-rt02');
  assert.equal(a.matchedBy, 'sticker');
  assert.equal(a.labelMatch, 'tail');
  assert.equal(b.sheetName, 'nvs-sgkc-sis-rt01');
  assert.equal(c.sheetName, null, 'an-rt05 is no Excel device');
  assert.equal(sheetCheck.nameHowWords(a), 'the name on its own sticker, matched on the part printed');
});

test('the sticker tail rule: one character off on a long tail, never a short or a shared tail', () => {
  const byName = sheetCheck.sheetDevices(STICKER_DOC);
  assert.equal(sheetCheck.stickerTail('sis-rt02', byName, new Set()).value.name, 'nvs-sgkc-sis-rt02');
  assert.equal(sheetCheck.stickerTail('s1s-rtO2', byName, new Set()).value.name, 'nvs-sgkc-sis-rt02', 'the camera\'s usual confusions');
  assert.equal(sheetCheck.stickerTail('sis-mt02', byName, new Set()).value.name, 'nvs-sgkc-sis-mt02');
  assert.equal(sheetCheck.stickerTail('rt02', byName, new Set()), null, 'one part is too short to say');
  assert.equal(sheetCheck.stickerTail('sis-rt02', byName, new Set(['NVSSGKCSISRT02'])), null, 'a name taken is not given twice');
  // sis-xt02 is one character off both rt02 and mt02: refused.
  assert.equal(sheetCheck.stickerTail('sis-xt02', byName, new Set()), null);
});

test('a cable tag read into a device\'s text is not its sticker name', () => {
  assert.equal(scanView.stickerName(null, [], 'nvs-sgkc-sis-sw05a Gi1-11 B11 nvs-sgkc-sis-sw05b Gi1-0-26B12 PXH'), null);
  assert.equal(scanView.stickerName(null, [], 'sakc-sis-rt07 Gi0-1 B10 Name: SIS-SW05B S/N: FDO2203B0WU').name, 'SIS-SW05B');
  assert.equal(scanView.stickerName(null, [], 'CISCO nvs-sgkc-wan-inet-rt01 2001708').name, 'nvs-sgkc-wan-inet-rt01');
});

test('the only cable left is not taken when the other face of the tag names another far device', () => {
  const v = rackView({ flagLines: [
    { text: 's-Sw05b Gi B12', box: [600, 90, 620, 200], conf: 0.94, flag: 4, end: null },
    { text: 's-Sw01d E1', box: [622, 90, 640, 200], conf: 0.94, flag: 4, end: { device: 's-Sw01d', port: 'E1', position: null, portNo: 1 } },
    { text: 'nvs-sgkc-sis-sw05b Gi1-0-26 B12', box: [200, 90, 220, 200], conf: 0.97, flag: 1, end: { device: 'nvs-sgkc-sis-sw05b', port: 'Gi1-0-26', position: 'B12', portNo: 26 } },
    { text: 'sw05b Gi1-7 B12', box: [300, 90, 320, 200], conf: 0.97, flag: 2, end: { device: 'sw05b', port: 'Gi1-7', position: 'B12', portNo: 7 } },
  ] });
  // C-11 runs to sw05a, the other face names sw01d: no cable is given.
  assert.equal(checks.foldedFaces(v, FOLD_DOC).length, 0);
});
