/**
 * Cable flag lines in the A-end / Z-end check: a line read on the rack photo
 * names its device, port and position, so it is placed on the device whose
 * name it carries and looked up in the sheet as the whole line, then as its
 * device and port, then as its port and position. A line naming a device
 * not in this rack is the far end and is never placed here.
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const checks = require('../lib/checks');
const scanDetail = require('../lib/scan_detail');

const port = (portNo, state = 'connected') => ({ portNo, kind: 'main', box: [portNo * 10, 0, portNo * 10 + 8, 8], state, type: 'RJ45' });
function dev(deviceId, labelText, top, n) {
  return {
    deviceId, type: 'Switch', labelText, name: labelText, units: [22 - deviceId], unitTop: 22 - deviceId, unitBottom: 22 - deviceId,
    hasPorts: true, portCount: n, box: [100, top, 1700, top + 150], ports: Array.from({ length: n }, (_, i) => port(i + 1)),
  };
}
const R = 'A08';
function view({ flagLines = [], zones = [] } = {}) {
  const devices = [dev(1, 'SIS-SW05B', 360, 26), dev(2, 'nvs-sgkc-sis-sg01e2', 540, 24)];
  return { scanId: 'RK-AAAAAAAA', siteId: 1, rackLabel: R, devices, visible: devices, zones, frontLabels: [], flagLines, unitsTotal: 42 };
}
const end = (device, portText, position, portNo) => ({ device, port: portText, position, portNo });
const line = (box, text, e, conf = 0.95) => ({ box, text, raw: text, conf, orientation: 'vertical', turn: 90, end: e });

test('a flag line is placed on the device it names and on the port it names', () => {
  const v = view({ flagLines: [
    line([1179, 329, 1290, 469], 'nvs-sgkc-sis-sw05b Gi1-0-14 B12', end('nvs-sgkc-sis-sw05b', 'Gi1-0-14', 'B12', 14)),
    // The other line of the same flag: the far end, a device in another rack.
    line([1200, 329, 1310, 469], 'nvs-sgkc-dcm-02-c07-i1013 Eth1-48 C07', end('nvs-sgkc-dcm-02-c07-i1013', 'Eth1-48', 'C07', 48)),
    // A line whose device was cut off: placed on the device under it.
    line([600, 520, 640, 560], '1 LOM B12', end(null, 'LOM', 'B12', null)),
  ] });
  const found = checks.labelsOf(v);
  assert.deepEqual(found.map((l) => [l.device, l.portNo, l.source, l.placedBy]), [
    ['SIS-SW05B', 14, 'flag', 'name'],
    ['nvs-sgkc-sis-sg01e2', null, 'flag', 'position'],
  ]);
});

test('a flag line matches the sheet as the whole line, or as its device and port, or as its port and position', () => {
  const doc = { import: { id: 'imp1' }, cables: [
    { row_no: 2, cable_label: 'nvs-sgkc-sis-sw05b Gi1-0-14 B12', a_rack: R, a_device: 'SIS-SW05B', a_port: 'Gi1/0/14', z_rack: 'C07', z_device: 'nvs-sgkc-dcm-02-c07-i1013', z_port: '48' },
    { row_no: 3, cable_label: 'sw05b Gi1-0-26', a_rack: R, a_device: 'SIS-SW05B', a_port: '26', z_rack: 'B08', z_device: 'b08-reg-gwlc02', z_port: 'Gig0' },
    { row_no: 4, cable_label: 'Gi1-11 B12', a_rack: R, a_device: 'SIS-SW05B', a_port: '11', z_rack: 'B11', z_device: 'sw05a', z_port: '11' },
    // The sheet puts this cable on port 7; the flag was read on port 5.
    { row_no: 5, cable_label: 'nvs-sgkc-sis-sw05b Gi1-7 B12', a_rack: R, a_device: 'SIS-SW05B', a_port: '7', z_rack: 'B9', z_device: 'sw03b', z_port: '48' },
  ] };
  const v = view({ flagLines: [
    line([1179, 329, 1290, 469], 'nvs-sgkc-sis-sw05b Gi1-0-14 B12', end('nvs-sgkc-sis-sw05b', 'Gi1-0-14', 'B12', 14)),
    line([1479, 92, 1509, 314], 'nvs-sgkc-sis-sw05b Gi1-0-26 B12', end('nvs-sgkc-sis-sw05b', 'Gi1-0-26', 'B12', 26)),
    line([558, 104, 616, 292], 'nvs-sgkc-sis-sw05b Gi1-11 B12', end('nvs-sgkc-sis-sw05b', 'Gi1-11', 'B12', 11)),
    line([472, 109, 502, 207], 'sw05b Gi1-5 B12', end('sw05b', 'Gi1-5', 'B12', 5)),
  ] });
  const r = checks.run({ a: v, z: v, doc, by: 't' });
  const byLabel = Object.fromEntries(r.cables.map((c) => [c.cableLabel, c]));
  assert.equal(byLabel['nvs-sgkc-sis-sw05b Gi1-0-14 B12'].a.status, 'matched', 'whole line');
  assert.equal(byLabel['nvs-sgkc-sis-sw05b Gi1-0-14 B12'].a.found.source, 'flag');
  assert.equal(byLabel['sw05b Gi1-0-26'].a.status, 'matched', 'device and port');
  assert.equal(byLabel['Gi1-11 B12'].a.status, 'matched', 'port and position');
  assert.equal(byLabel['nvs-sgkc-sis-sw05b Gi1-7 B12'].a.status, 'not_found', 'the sheet\'s cable was not read; the read on port 5 is another cable');
  const stray = r.cables.find((c) => c.status === 'not_in_sheet');
  assert.equal(stray.cableLabel, 'sw05b Gi1-5 B12');
  assert.equal(r.accuracy.labelsRead, 3);
});

test('a close-up label with an end is kept whole and placed on the port the close-up paired it with', () => {
  const v = view({ zones: [{ deviceId: 1, labels: [
    { text: 'nvs-sgkc-sis-sw05b Gi1-0-14 B12', portNo: 14, confidence: 0.9, box: [0, 0, 10, 10], end: end('nvs-sgkc-sis-sw05b', 'Gi1-0-14', 'B12', 14) },
    // Names another device: the far end, not this device's label.
    { text: 'nvs-sgkc-sis-sg01e2 LOM B12', portNo: 14, confidence: 0.9, box: [0, 0, 10, 10], end: end('nvs-sgkc-sis-sg01e2', 'LOM', 'B12', null) },
    { text: 'Gigabit Switch', portNo: null, confidence: 0.9, box: [0, 0, 10, 10] },
  ] }] });
  const found = checks.labelsOf(v);
  assert.deepEqual(found.map((l) => [l.text, l.device, l.portNo, l.source]), [['nvs-sgkc-sis-sw05b Gi1-0-14 B12', 'SIS-SW05B', 14, 'close-up']]);
});

test('scan detail shows the flag line on its port with its end', () => {
  const doc = { import: { id: 'imp1' }, cables: [
    { row_no: 2, cable_label: 'sw05b Gi1-0-14', a_rack: R, a_device: 'SIS-SW05B', a_port: '14', z_rack: 'C07', z_device: 'i1013', z_port: '48' },
  ] };
  const v = view({ flagLines: [line([1179, 329, 1290, 469], 'nvs-sgkc-sis-sw05b Gi1-0-14 B12', end('nvs-sgkc-sis-sw05b', 'Gi1-0-14', 'B12', 14))] });
  const d = scanDetail.build(v, doc);
  const sw = d.devices.find((x) => x.labelText === 'SIS-SW05B');
  const p14 = sw.ports.find((p) => p.portNo === 14);
  assert.equal(p14.label.text, 'nvs-sgkc-sis-sw05b Gi1-0-14 B12');
  assert.equal(p14.label.source, 'flag');
  assert.equal(p14.label.end.position, 'B12');
  assert.equal(p14.status, 'matched');
});

test('name fits: the flag name and the faceplate name are one name', () => {
  assert.ok(checks.nameFits('nvs-sgkc-sis-sw05b', 'SIS-SW05B'));
  assert.ok(checks.nameFits('sw05b', 'nvs-sgkc-sis-sw05b'));
  assert.ok(checks.nameFits('nvs-sgkc-sis-swO5b', 'SIS-SW05B'), 'O for 0 is allowed');
  assert.ok(!checks.nameFits('nvs-sgkc-sis-sw05a', 'SIS-SW05B'));
  assert.ok(!checks.nameFits('b', 'SIS-SW05B'), 'too short to say');
  assert.deepEqual(checks.candidatesOf({ text: 'sw05b Gi1-7 B12', end: end('sw05b', 'Gi1-7', 'B12', 7) }),
    ['sw05b Gi1-7 B12', 'sw05b Gi1-7', 'Gi1-7 B12', 'sw05b B12']);
});

test('with the sheet, a cut or misread device token is repaired against the device names it knows', () => {
  const doc = { import: { id: 'imp1' }, cables: [
    { row_no: 2, cable_label: 'nvs-sgkc-sis-sw05b Gi1-0-14 B12', a_rack: R, a_device: 'nvs-sgkc-sis-sw05b', a_port: '14', z_rack: 'C07', z_device: 'nvs-sgkc-dcm-02-c07-i1013', z_port: '48' },
    { row_no: 3, cable_label: 'nvs-sgkc-sis-sg01e2 LOM B12', a_rack: R, a_device: 'nvs-sgkc-sis-sg01e2', a_port: 'LOM', z_rack: R, z_device: 'nvs-sgkc-sis-sw05b', z_port: '18' },
  ] };
  const vocab = checks.deviceVocabulary(view(), doc);
  assert.deepEqual(checks.repairDevice('sgkc-sis-sw05b', vocab), { name: 'nvs-sgkc-sis-sw05b', how: 'tail' });
  assert.deepEqual(checks.repairDevice('nvs-sgkc-sis-swO5b', vocab), { name: 'nvs-sgkc-sis-sw05b', how: 'normalised' });
  assert.deepEqual(checks.repairDevice('sw05b', vocab), { name: 'nvs-sgkc-sis-sw05b', how: 'tail' });
  assert.equal(checks.repairDevice('05b', vocab), null, 'three characters can confirm a misread digit');
  assert.equal(checks.repairDevice('61002', vocab), null, 'fits nothing: left as read');
  assert.equal(checks.repairDevice('e2', vocab), null, 'too short to say');
  // Placed and matched through the repaired name: the read says "sis-sw05b", the sheet "nvs-sgkc-sis-sw05b".
  const v = view({ flagLines: [
    line([1179, 329, 1290, 469], 'sis-sw05b Gi1-0-14 B12', end('sis-sw05b', 'Gi1-0-14', 'B12', 14)),
    // The far end of the same cable, read on the same flag: named in the sheet, not in this rack, so not placed.
    line([1200, 329, 1310, 469], 'c07-i1013 Eth1-48 C07', end('c07-i1013', 'Eth1-48', 'C07', 48)),
  ] });
  const found = checks.labelsOf(v, doc);
  assert.equal(found.length, 1);
  assert.equal(found[0].end.deviceRepaired, 'nvs-sgkc-sis-sw05b');
  assert.equal(found[0].end.deviceMatch, 'tail');
  const r = checks.run({ a: v, z: v, doc, by: 't' });
  const c = r.cables.find((x) => x.cableLabel === 'nvs-sgkc-sis-sw05b Gi1-0-14 B12');
  assert.equal(c.a.status, 'matched');
  assert.equal(c.a.found.match, 'tail');
});

test('a port the Excel has no row for takes its other end from the other face of its own tag', () => {
  const v = view({ flagLines: [
    { ...line([400, 360, 430, 470], 'nvs-sgkc-sis-sw05b Gi1-0-2 B12', end('nvs-sgkc-sis-sw05b', 'Gi1-0-2', 'B12', 2)), flag: 4 },
    { ...line([432, 360, 460, 470], 'b08-reg-gwlc02 Gig03P B08', end('b08-reg-gwlc02', 'Gig03P', 'B08', 3)), flag: 4 },
  ] });
  const d = scanDetail.build(v, { import: { id: 'x' }, cables: [] });
  const p2 = d.devices[0].ports.find((x) => x.portNo === 2);
  assert.equal(p2.expected, null);
  assert.equal(p2.tag.read, 'nvs-sgkc-sis-sw05b Gi1-0-2 B12');
  assert.deepEqual({ ...p2.tag.other, box: undefined }, { device: 'b08-reg-gwlc02', port: 'Gig03P', rack: 'B08', read: 'b08-reg-gwlc02 Gig03P B08', box: undefined });
  assert.equal(d.devices[0].ports.find((x) => x.portNo === 3).tag, null, 'a port no tag names');
});
