'use strict';

// The Excel places the devices the photo only places roughly (the office rack,
// 29 Sep 2026: everything below the firewall read one unit low, a switch seen
// twice, the firewall read as a router).
const test = require('node:test');
const assert = require('node:assert/strict');
const sheetCheck = require('../lib/sheet_check');

const RACK = 'SP-HYB-RM01-R01-R1';
const cable = (a, aUnit, z, zUnit) => ({ cable_label: `${a}-${z}`, a_device: a, a_port: '1', a_rack: RACK, a_unit: String(aUnit), z_device: z, z_port: '2', z_rack: RACK, z_unit: String(zUnit) });
const DOC = {
  cables: [
    cable('SP-R1-U19-FW', 19, 'SP-R1-U13-SW01', 13),
    cable('SP-R1-U18-SW04', 18, 'SP-R1-U10-PP4', 10),
    cable('SP-R1-U20-ACT', 20, 'SP-R1-U19-FW', 19),
  ],
};
const dev = (deviceId, type, units, labelText = null) => ({ deviceId, type, units, unitTop: Math.max(...units), unitBottom: Math.min(...units), labelText, ports: [] });

function view() {
  return {
    rackLabel: RACK,
    visible: [
      dev(1, 'Router', [19], 'SP-R1-U19-FW'),
      dev(2, 'Switch', [18]),
      dev(3, 'Switch', [17], 'SP-R1-U18-SW04'),
      dev(4, 'Switch', [12], 'SP-R1-U13-SW01'),
      dev(5, 'Patch Panel', [9, 10]),
    ],
  };
}

test('a device the Excel names is placed at the Excel unit when the photo is one unit off', () => {
  const v = sheetCheck.resolve(view(), DOC);
  const byName = Object.fromEntries(v.visible.filter((d) => d.sheetName).map((d) => [d.sheetName, d]));
  assert.deepEqual(byName['SP-R1-U18-SW04'].units, [18]);
  assert.deepEqual(byName['SP-R1-U13-SW01'].units, [13]);
  assert.deepEqual(byName['SP-R1-U10-PP4'].units, [10, 11]);
  assert.equal(byName['SP-R1-U19-FW'].type, 'Firewall');
});

test('an unnamed device on a unit a placed device now holds is the same device seen twice', () => {
  const v = sheetCheck.resolve(view(), DOC);
  assert.equal(v.visible.find((d) => d.deviceId === 2), undefined);
});

test('resolving twice starts from the photo and gives the same answer', () => {
  const v = view();
  sheetCheck.resolve(v, DOC);
  sheetCheck.resolve(v, DOC);
  assert.deepEqual(v.visible.map((d) => [d.deviceId, d.units]), [[1, [19]], [3, [18]], [4, [13]], [5, [10, 11]]]);
});

test('badly numbered units are put right by the order from the top', () => {
  // The photo numbered everything two to three units high; the order is right.
  const v = { rackLabel: RACK, visible: [dev(1, 'Router', [22]), dev(3, 'Switch', [20]), dev(4, 'Switch', [15]), dev(5, 'Patch Panel', [12, 13])] };
  const out = sheetCheck.check(v, DOC);
  const at = Object.fromEntries(v.visible.map((d) => [d.deviceId, [d.sheetName, d.units]]));
  assert.deepEqual(at[1], ['SP-R1-U19-FW', [19]]);
  assert.deepEqual(at[3], ['SP-R1-U18-SW04', [18]]);
  assert.deepEqual(at[4], ['SP-R1-U13-SW01', [13]]);
  assert.deepEqual(at[5], ['SP-R1-U10-PP4', [10, 11]]);
  assert.ok(out.units.every((u) => u.status === 'ok'));
});

test('two switches swapped: the labels disagree with the order, and one is a wrong unit', () => {
  const v = { rackLabel: RACK, visible: [dev(1, 'Router', [19], 'SP-R1-U19-FW'), dev(3, 'Switch', [18], 'SP-R1-U13-SW01'), dev(4, 'Switch', [13], 'SP-R1-U18-SW04'), dev(5, 'Patch Panel', [9, 10])] };
  const out = sheetCheck.check(v, DOC);
  assert.ok(out.units.some((u) => u.status === 'wrong_unit'));
});

test('a device the Excel puts in the rack that the photo did not find is listed as not seen', () => {
  const out = sheetCheck.check(view(), DOC);
  assert.deepEqual(out.notSeen, [{ name: 'SP-R1-U20-ACT', unit: 20 }]);
});
