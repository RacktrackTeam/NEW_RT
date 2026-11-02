'use strict';

// The cable tags hanging on a device name it (sheet_check.resolve, rung 1b2):
// a switch the photo could not identify carries five sw05b tags, so it is
// sw05b; a device whose own sticker prints another name keeps that name.
const test = require('node:test');
const assert = require('node:assert/strict');
const sheetCheck = require('../lib/sheet_check');

const RACK = 'B12';
const tag = (text, box, device, port, position, portNo) => ({ text, box, conf: 0.95, end: { device, port, position, portNo } });
const row = (label, a, aPort, aRack, z, zPort, zRack) => ({ cable_label: label, a_device: a, a_port: aPort, a_rack: aRack, a_unit: '', z_device: z, z_port: zPort, z_rack: zRack, z_unit: '' });
const doc = { cables: [
  row('nvs-sgkc-sis-sw05b Gi1-0-26 B12', 'nvs-sgkc-sis-sw05b', 'Gi1-0-26', RACK, 'b08-reg-gwlc02', 'Gig05P', 'B08'),
  row('nvs-sgkc-sis-sw05b Gi1-7 B12', 'nvs-sgkc-sis-sw05b', 'Gi1-7', RACK, 'nvs-sgkc-sis-sw03b', 'E1-48', 'B9'),
  row('nvs-sgkc-sis-mt02 TE0 B12', 'nvs-sgkc-sis-mt02', 'TE0', RACK, 'nvs-sgkc-sis-sw01d', 'ETH1-17', RACK),
] };
const port = (n) => ({ portNo: n, kind: 'main', type: 'RJ45', state: 'connected', stateSource: 'photo', confidence: 0.9, box: null });
function view(stickers) {
  const sw = { deviceId: 2, type: 'Switch', units: [8], unitTop: 8, unitBottom: 8, labelText: null, box: [197, 133, 1850, 318], ports: [], hasPorts: false, flagCarrier: true };
  const rt = { deviceId: 4, type: 'Router', units: [3], unitTop: 3, unitBottom: 3, labelText: null, box: [293, 1136, 1514, 1235], ports: [port(1), port(2)], hasPorts: true };
  const flagLines = [
    tag('nvs-sgkc-sis-sw05b Gi1-0-26 B12', [1479, 92, 1509, 314], 'nvs-sgkc-sis-sw05b', 'Gi1-0-26', 'B12', 26),
    tag('sw05b Gi1-7 B12', [471, 108, 502, 208], 'sw05b', 'Gi1-7', 'B12', 7),
    // The far ends printed on the same tags name devices of other racks.
    tag('b08-reg-gwlc02 Gig05P B08', [1496, 111, 1527, 296], 'b08-reg-gwlc02', 'Gig05P', 'B08', 5),
    tag('nvs-sgkc-sis-mt02 TE0 B12', [436, 1140, 467, 1230], 'nvs-sgkc-sis-mt02', 'TE0', 'B12', 0),
    ...stickers.map((text) => ({ text, box: [468, 1161, 502, 1175], conf: 0.99, end: null })),
  ];
  return { rackLabel: RACK, visible: [sw, rt], devices: [sw, rt], zones: [], frontLabels: [], flagLines, unitsTotal: 42 };
}

test('the tags on an unidentified switch name it; one tag over a router with no name of its own names that too', () => {
  const v = view([]);
  sheetCheck.resolve(v, doc);
  const [sw, rt] = v.visible;
  assert.equal(sw.sheetName, 'nvs-sgkc-sis-sw05b');
  assert.equal(sw.matchedBy, 'flags');
  assert.equal(rt.sheetName, 'nvs-sgkc-sis-mt02');
  assert.equal(rt.matchedBy, 'flags');
});

test('a device whose own sticker prints another name is not named by the tag hanging over it', () => {
  const v = view(['an-rt05', 'Rack A03 RU18']);
  sheetCheck.resolve(v, doc);
  const [sw, rt] = v.visible;
  assert.equal(sw.sheetName, 'nvs-sgkc-sis-sw05b', 'the switch has no sticker and keeps its tags');
  assert.equal(rt.sheetName, null, 'the sticker an-rt05 is not mt02');
  // A sticker that prints the tag's own name lets it through.
  const w = view(['Name: sis-mt02']);
  sheetCheck.resolve(w, doc);
  assert.equal(w.visible[1].sheetName, 'nvs-sgkc-sis-mt02');
});

test('a far end the Excel puts in another rack never names a device here', () => {
  const v = view([]);
  // Only the far faces hang on the router: b08-reg-gwlc02 lives in rack B08.
  v.flagLines = v.flagLines.filter((l) => !/sis-mt02/.test(l.text)).concat([
    { text: 'b08-reg-gwlc02 Gig05P B08', box: [436, 1140, 467, 1230], conf: 0.95, end: { device: 'b08-reg-gwlc02', port: 'Gig05P', position: 'B12', portNo: 5 } },
    { text: 'b08-reg-gwlc02 Gig06P B08', box: [440, 1140, 470, 1230], conf: 0.95, end: { device: 'b08-reg-gwlc02', port: 'Gig06P', position: 'B12', portNo: 6 } },
  ]);
  sheetCheck.resolve(v, doc);
  assert.notEqual(v.visible[1].sheetName, 'b08-reg-gwlc02');
});
