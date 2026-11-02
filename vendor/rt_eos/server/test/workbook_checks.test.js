'use strict';

// The rack check with the manager's workbook (lib/workbook.js, lib/sheet_model.js):
// devices from DEVICE_INVENTORY, their other names, RU height, and each port's
// expectations (occupied, dust cap, port type), and a cable's patch panel hop.
const test = require('node:test');
const assert = require('node:assert/strict');
const sheetCheck = require('../lib/sheet_check');
const sheetModel = require('../lib/sheet_model');
const rackNaming = require('../lib/rack_naming');
const labelMatch = require('../lib/label_match');

const RACK = 'A01';
const end = (extra = {}) => ({ device_id: '', port_number: '', expected_occupied: '', expected_dust_cap: '', port_type: '', panel_name: '', panel_position: '', panel_ru: '', ...extra });
const cable = (label, a, aPort, aUnit, z, zPort, zUnit, aEnd = {}, zEnd = {}) => ({
  cable_label: label, a_device: a, a_port: aPort, a_rack: RACK, a_unit: String(aUnit),
  z_device: z, z_port: zPort, z_rack: 'Z01', z_unit: String(zUnit), checked: true,
  a: end(aEnd), z: end(zEnd),
});
const DOC = {
  format: 'manager',
  racks: [
    { record_status: 'Active', rack_id: 'RACK-A01', rack_name: 'A01', height_u: '42', expected_label: 'A-01' },
    { record_status: 'Active', rack_id: 'RACK-Z01', rack_name: 'Z01', height_u: '42', expected_label: '' },
  ],
  devices: [
    { record_status: 'Active', rack_id: 'RACK-A01', device_id: 'DEV-LEAF01', device_name: 'Leaf-01', ru_start: '20', ru_height: '2', expected_label: 'LEAF01-A' },
    { record_status: 'Active', rack_id: 'RACK-A01', device_id: 'DEV-SPARE', device_name: 'Spare-SW', ru_start: '30', ru_height: '1', expected_label: '' },
    { record_status: 'Decommissioned', rack_id: 'RACK-A01', device_id: 'DEV-OLD', device_name: 'Old-SW', ru_start: '35', ru_height: '1', expected_label: '' },
  ],
  ports: [
    { record_status: 'Active', rack_id: 'RACK-A01', device_id: 'DEV-LEAF01', port_name: 'Ethernet1/5', port_number: '5', expected_occupied: 'Yes', expected_dust_cap: 'N/A', port_type: '' },
    { record_status: 'Active', rack_id: 'RACK-A01', device_id: 'DEV-LEAF01', port_name: 'Ethernet1/6', port_number: '6', expected_occupied: 'No', expected_dust_cap: 'No', port_type: '' },
    { record_status: 'Active', rack_id: 'RACK-A01', device_id: 'DEV-LEAF01', port_name: 'Ethernet1/7', port_number: '7', expected_occupied: 'No', expected_dust_cap: 'Yes', port_type: 'RJ45' },
  ],
  cables: [
    cable('FBR-001', 'Leaf-01', 'Ethernet1/1', 20, 'Spine-01', 'Ethernet1/1', 30,
      { device_id: 'DEV-LEAF01', port_number: '1', port_type: 'QSFP28', expected_occupied: 'Yes', panel_name: 'PP-A01-01', panel_position: '01', panel_ru: '10' }),
  ],
};

const port = (portNo, state, extra = {}) => ({ portNo, state, stateSource: 'photo', confidence: 0.8, type: 'SFP', sfpType: null, ...extra });
const dev = (deviceId, type, units, labelText, ports = []) => ({ deviceId, type, units, unitTop: Math.max(...units), unitBottom: Math.min(...units), labelText, ports });

function view() {
  return {
    rackLabel: RACK,
    visible: [
      dev(1, 'Switch', [20], 'LEAF01-A', [
        port(1, 'connected', { sfpType: 'SFP' }),
        port(5, 'empty'),
        port(6, 'connected', { capState: 'empty' }),
        port(7, 'empty', { capState: 'empty', type: 'RJ45' }),
      ]),
      dev(2, 'Patch Panel', [10], null, [port(1, 'empty'), port(2, 'connected')]),
    ],
  };
}

test('a device is named by the label printed on it (Expected_Device_Label) and takes its RU height', () => {
  const v = sheetCheck.resolve(view(), DOC);
  const leaf = v.visible.find((d) => d.deviceId === 1);
  assert.equal(leaf.sheetName, 'Leaf-01');
  assert.equal(leaf.heightExpected, 2);
  assert.deepEqual(leaf.units, [20, 21], 'two units high, as RU_Height says');
});

test('an inventory device with no cable is expected; a decommissioned one is not', () => {
  const out = sheetCheck.check(view(), DOC);
  assert.ok(out.notSeen.some((x) => x.name === 'Spare-SW' && x.unit === 30));
  assert.ok(!out.notSeen.some((x) => x.name === 'Old-SW'));
});

test('a port the workbook expects occupied reads empty: missing, with no cable row needed', () => {
  const out = sheetCheck.check(view(), DOC);
  const m = out.missing.find((x) => x.deviceName === 'Leaf-01' && x.portNo === 5);
  assert.ok(m, JSON.stringify(out.missing));
  assert.equal(m.expectedBy, 'port');
});

test('a port the workbook expects empty holds a cable: an extra cable', () => {
  const out = sheetCheck.check(view(), DOC);
  assert.ok(out.unexpected.some((x) => x.deviceName === 'Leaf-01' && x.portNo === 6 && x.expectedBy === 'port'));
});

test('Expected_Dust_Cap decides: No is never a missing cap, Yes counts even on a copper port', () => {
  const out = sheetCheck.check(view(), DOC);
  assert.ok(!out.uncapped.some((x) => x.portNo === 6));
  assert.ok(out.uncapped.some((x) => x.portNo === 7 && x.markedBy === 'photo'));
});

test('a cage the photo reads as SFP where the workbook says QSFP28 is the wrong port type', () => {
  const out = sheetCheck.check(view(), DOC);
  const w = out.wrongType.find((x) => x.portNo === 1);
  assert.ok(w, JSON.stringify(out.wrongType));
  assert.equal(w.expected, 'QSFP28');
  assert.equal(w.found, 'SFP');
  assert.ok(!out.wrongType.some((x) => x.portNo === 7), 'RJ45 read as RJ45 is right');
});

test('the patch panel a cable passes is expected at its unit, and its position occupied', () => {
  const v = view();
  const out = sheetCheck.check(v, DOC);
  const panel = v.visible.find((d) => d.deviceId === 2);
  assert.equal(panel.sheetName, 'PP-A01-01', 'named by the unit it stands in');
  const m = out.missing.find((x) => x.deviceName === 'PP-A01-01' && x.portNo === 1);
  assert.ok(m, JSON.stringify(out.missing));
  assert.equal(m.cableLabel, 'FBR-001');
  assert.equal(m.expectedBy, 'panel');
});

test('placement rows carry the inventory to rack naming and rack height', () => {
  const rows = sheetModel.placementRows(DOC);
  assert.ok(rows.some((r) => r.a_device === 'Leaf-01' && r.a_rack === 'A01' && r.a_unit === '21'), 'its top unit');
  assert.ok(rows.some((r) => r.a_device === 'LEAF01-A' && r.a_rack === 'A01'), 'its printed label');
  assert.ok(rows.some((r) => r.a_device === '' && r.a_rack === 'Z01' && r.a_unit === '42'), 'the rack height');
  assert.ok(!rows.some((r) => r.a_device === 'Old-SW'));
});

test('the label on the rack (Expected_Rack_Label) names the rack', () => {
  const r = rackNaming.decide({
    rail: [{ text: 'A-01', conf: 0.9, source: 'rack rail' }],
    cables: DOC.cables.concat(sheetModel.placementRows(DOC)),
    rackAliases: sheetModel.rackAliases(DOC),
  });
  assert.equal(r.named, true);
  assert.equal(r.name, 'A01');
});

test('several names of one thing are one answer, never a tie', () => {
  const v = { id: 1 };
  const m = labelMatch.createMatcher([['DEV-LEAF-01', v], ['DEV-LEAF-O1X', v]]);
  assert.equal(m.match('DEV-LEAF-01X')?.value, v);
});

test('an old dataset without inventories checks as before', () => {
  const old = { cables: [{ cable_label: 'C1', a_device: 'SW1', a_port: '1', a_rack: RACK, a_unit: '5', z_device: 'PP1', z_port: '1', z_rack: RACK, z_unit: '3' }] };
  assert.deepEqual(sheetModel.placementRows(old), []);
  const out = sheetCheck.check({ rackLabel: RACK, visible: [dev(1, 'Switch', [5], 'SW1', [port(1, 'empty')])] }, old);
  assert.deepEqual(out.wrongType, []);
  assert.equal(out.missing.length, 1);
});

test('a cable manager in the inventory is never reported as not seen', () => {
  const doc = { ...DOC, devices: [...DOC.devices, { record_status: 'Active', rack_id: 'RACK-A01', device_id: 'CM1', device_name: 'CM1', role: 'Cable Manager', ru_start: '25', ru_height: '1' }] };
  const out = sheetCheck.check(view(), doc);
  assert.ok(!out.notSeen.some((x) => x.name === 'CM1'));
  assert.ok(out.notSeen.some((x) => x.name === 'Spare-SW'));
});
