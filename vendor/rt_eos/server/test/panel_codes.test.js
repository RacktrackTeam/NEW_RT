'use strict';

// A patch panel's printed position codes (42A) placed on ports by the site's
// convention: codes run across the rack in blocks of 24, the number is the
// column, the letter the row (A upper, B lower), and the photo numbers a
// two-row panel column by column.
const test = require('node:test');
const assert = require('node:assert/strict');
const sheetCheck = require('../lib/sheet_check');
const scanDetail = require('../lib/scan_detail');

test('a code names a column and a row, in the photo\'s port numbering', () => {
  assert.equal(sheetCheck.codeToPort('42A', 48), 35);
  assert.equal(sheetCheck.codeToPort('42B', 48), 36);
  assert.equal(sheetCheck.codeToPort('25A', 48), 1);
  assert.equal(sheetCheck.codeToPort('48B', 48), 48);
  assert.equal(sheetCheck.codeToPort('53A', 48), 9);
  assert.equal(sheetCheck.codeToPort('21B', 24), 21);
  assert.equal(sheetCheck.codeToPort('21B', 0), 21);
  assert.equal(sheetCheck.codeToPort('Port4', 48), null);
  assert.equal(sheetCheck.codeToPort('48B', 40), null);
});

const RACK = 'SP-HYB-RM01-R01-R1';
const cable = (a, aPort, aUnit, z, zPort, zUnit) => ({ cable_label: `${a}-${z}`, a_device: a, a_port: aPort, a_rack: RACK, a_unit: String(aUnit), z_device: z, z_port: zPort, z_rack: RACK, z_unit: String(zUnit) });
const ports = (n, empty = []) => Array.from({ length: n }, (_, i) => ({ portNo: i + 1, kind: 'main', type: 'RJ45', state: empty.includes(i + 1) ? 'empty' : 'connected', stateSource: 'photo', confidence: 0.9, box: null }));
const dev = (deviceId, type, units, labelText, n, empty) => ({ deviceId, type, units, unitTop: Math.max(...units), unitBottom: Math.min(...units), labelText, ports: ports(n, empty) });

test('a coded panel port the Excel lists is told missing when the photo shows it empty', () => {
  const doc = { cables: [cable('SP-R1-U17-SW03', 'Port5', 17, 'SP-R1-U4-PP2', '42A', 4)] };
  const view = { rackLabel: RACK, visible: [dev(1, 'Switch', [17], 'SP-R1-U17-SW03', 28), dev(2, 'Patch Panel', [4, 5], 'SP-R1-U4-PP2', 48, [35])] };
  const out = sheetCheck.check(view, doc);
  assert.deepEqual(out.missing.map((m) => [m.deviceName, m.portNo, m.cableLabel]), [['SP-R1-U4-PP2', 35, 'SP-R1-U17-SW03-SP-R1-U4-PP2']]);
  // A coded device is never accused of an extra cable (the switch, with plain port numbers, is).
  assert.ok(out.unexpected.every((u) => u.deviceName !== 'SP-R1-U4-PP2'));
});

test('the other end of a switch port carries the panel port the code places it on', () => {
  const doc = { cables: [cable('SP-R1-U17-SW03', 'Port5', 17, 'SP-R1-U4-PP2', '42A', 4)] };
  const view = { scanId: 'RK-1', rackLabel: RACK, visible: [dev(1, 'Switch', [17], 'SP-R1-U17-SW03', 28), dev(2, 'Patch Panel', [4, 5], 'SP-R1-U4-PP2', 48)],
    devices: [], zones: [], frontLabels: [], flagLines: [], units: [], unitsTotal: 24, height: null, naming: null, takenAt: null, by: null, siteId: null };
  view.devices = view.visible;
  const d = scanDetail.build(view, doc);
  const sw = d.devices.find((x) => x.name === 'SP-R1-U17-SW03');
  const p5 = sw.ports.find((p) => p.portNo === 5);
  assert.equal(p5.expected.other.device, 'SP-R1-U4-PP2');
  assert.equal(p5.expected.other.port, '42A');
  assert.equal(p5.expected.other.portNo, 35);
  assert.equal(p5.expected.other.deviceId, 2);
  const pp = d.devices.find((x) => x.name === 'SP-R1-U4-PP2');
  assert.equal(pp.ports.find((p) => p.portNo === 35).expected.other.device, 'SP-R1-U17-SW03');
});

// A 48-port panel on the photo: 24 columns 30 px apart, the upper row at y 10-30,
// the lower at y 40-60, numbered column by column; some ports left unread.
const boxedPorts = (missing = []) => {
  const out = [];
  for (let c = 1; c <= 24; c += 1) {
    for (const [row, y] of [['A', 10], ['B', 40]]) {
      const portNo = (c - 1) * 2 + (row === 'B' ? 2 : 1);
      if (missing.includes(portNo)) continue;
      out.push({ portNo, kind: 'main', type: 'RJ45', state: 'empty', stateSource: 'photo', confidence: 0.9, box: [c * 30, y, c * 30 + 20, y + 20] });
    }
  }
  return out;
};

test('with boxes, a code is placed by the column and row on the photo, so unread ports shift nothing', () => {
  const full = { deviceId: 2, type: 'Patch Panel', ports: boxedPorts() };
  assert.equal(sheetCheck.portForCode('42A', full), 35);
  assert.equal(sheetCheck.portForCode('42B', full), 36);
  // Ports 3 to 12 (columns 2 to 6) were not read: 42A still names the port in column 18.
  const gappy = { deviceId: 3, type: 'Patch Panel', ports: boxedPorts([3, 4, 5, 6, 7, 8, 9, 10, 11, 12]) };
  assert.equal(gappy.ports.length, 38);
  assert.equal(sheetCheck.portForCode('42A', gappy), 35);
  assert.equal(sheetCheck.codeToPort('42A', gappy.ports.length), 35, 'counting alone happens to agree here');
  assert.equal(sheetCheck.portForCode('27B', gappy), null, 'a port the photo did not read is not placed');
  // A one-row panel: the letter does not pick a row.
  const oneRow = { deviceId: 4, type: 'Patch Panel', ports: boxedPorts().filter((p) => p.portNo % 2 === 1).map((p, i) => ({ ...p, portNo: i + 1 })) };
  assert.equal(sheetCheck.portForCode('18B', oneRow), 18);
});

test('a doubled reading of one port does not shrink the pitch, and an unread port is placed by its column', () => {
  // The upper row read only at columns 1, 5 and 24; port 2 of the lower row read twice.
  const ports = boxedPorts().filter((p) => p.portNo % 2 === 0 || [1, 9, 47].includes(p.portNo));
  ports.push({ ...ports.find((p) => p.portNo === 4), portNo: 99, box: [64, 41, 84, 61] });
  const dev = { deviceId: 5, type: 'Patch Panel', ports };
  assert.equal(sheetCheck.portForCode('42B', dev), 36, 'the lower row port is found by its column');
  assert.equal(sheetCheck.portForCode('42A', dev), null, 'the upper port was not read');
  const box = sheetCheck.positionForCode('42A', dev);
  assert.ok(box, 'but its place on the panel is known');
  assert.equal(Math.round((box[0] + box[2]) / 2), 18 * 30 + 10, 'column 18 at the row pitch');
  assert.equal(Math.round((box[1] + box[3]) / 2), 20, 'in the upper row');
  assert.equal(sheetCheck.positionForCode('42A', { deviceId: 6, type: 'Patch Panel', ports: [] }), null, 'nothing read on the photo, nothing to place by');
});
