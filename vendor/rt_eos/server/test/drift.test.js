'use strict';

// The drift check of one port (lib/drift.js): structured data in, each field
// compared with the Excel row, the fields that differ named.
const test = require('node:test');
const assert = require('node:assert/strict');
const drift = require('../lib/drift');

const OFFICE = 'SP-HYB-RM01-R01-R1';
const doc = {
  cables: [
    { row_no: 2, cable_label: 'R1-SW02-23', a_device: 'SP-R1-U13-SW02', a_port: 'Port23', a_rack: OFFICE, z_device: 'SP-R1-U4-PP2', z_port: '37A', z_rack: OFFICE,
      cable_type: 'Cat6 UTP', connector: 'RJ45', cable_color: 'Blue' },
    { row_no: 3, cable_label: 'FC-003', a_device: 'SW-CORE-01', a_port: '3', z_device: 'ODF-01', z_port: '12', cable_type: 'LC-LC OM4' },
  ],
};
const actual = (over = {}) => ({ device: 'sp-r01-u13-sw02', port: 23, status: 'connected', connector: 'RJ45', cableColor: 'Red', ...over });

test('the spec example: only the colour differs, and it is named', () => {
  const r = drift.checkAgainst(doc, actual());
  assert.equal(r.result, 'drift');
  assert.deepEqual(r.mismatches, ['cableColor']);
  const f = r.fields.find((x) => x.field === 'cableColor');
  assert.deepEqual([f.expected, f.actual], ['Blue', 'Red']);
  assert.equal(r.excelRow, 2);
  assert.match(r.summary, /^DRIFT DETECTED\nDevice: sp-r01-u13-sw02\nPort: 23\nMismatch:\nCable colour\n {2}Expected: Blue\n {2}Actual: Red$/);
});

test('everything the same: no drift, the cable type compared by its kind', () => {
  const r = drift.checkAgainst(doc, actual({ cableColor: 'blue', connector: 'RJ-45' }));
  assert.equal(r.result, 'no_drift');
  assert.deepEqual(r.mismatches, []);
  assert.equal(r.fields.find((x) => x.field === 'cableType').result, 'match');
  assert.match(r.summary, /NO DRIFT[\s\S]*Actual configuration matches the expected configuration\./);
});

test('status: an empty port the Excel has a cable on, and a cable the Excel does not have', () => {
  const missing = drift.checkAgainst(doc, actual({ status: 'empty', cableColor: null, connector: null }));
  assert.equal(missing.result, 'drift');
  assert.deepEqual(missing.mismatches, ['status']);
  assert.equal(missing.fields.find((x) => x.field === 'cableColor').result, 'not_read', 'nothing to read on an empty port');

  const extra = drift.checkAgainst(doc, actual({ port: 24 }));
  assert.equal(extra.result, 'drift');
  assert.deepEqual(extra.mismatches, ['status']);
  assert.deepEqual([extra.fields[2].expected, extra.fields[2].actual], ['Empty', 'Connected']);

  const fine = drift.checkAgainst(doc, actual({ port: 24, status: 'empty' }));
  assert.equal(fine.result, 'no_drift');
});

test('a column the Excel does not have cannot drift; a status not read is not checked', () => {
  const r = drift.checkAgainst(doc, { device: 'SW-CORE-01', port: 3, status: 'connected', connector: 'RJ-45', cableColor: 'Yellow' });
  assert.equal(r.fields.find((x) => x.field === 'cableColor').result, 'not_in_excel');
  assert.equal(r.fields.find((x) => x.field === 'connector').result, 'not_in_excel');
  assert.deepEqual(r.mismatches, ['cableType'], 'a copper plug where the Excel has fibre');

  const unsure = drift.checkAgainst(doc, actual({ status: 'unknown', cableColor: null }));
  assert.equal(unsure.result, 'not_checked');
  const rough = drift.checkAgainst(doc, actual({ status: 'empty', statusSure: false, cableColor: null }));
  assert.equal(rough.result, 'not_checked', 'a rough read of an empty port is not a missing cable');
  assert.equal(rough.fields.find((x) => x.field === 'status').actual, 'Empty (photo not sure)');
});

test('label and other end, when they were read', () => {
  const ok = drift.compare(
    { device: 'SP-R1-U13-SW02', port: 23, status: 'connected', label: 'R1-SW02-23', otherEnd: { device: 'SP-R1-U4-PP2', port: '37A' } },
    drift.referenceOf(doc.cables[0], 'a'),
  );
  assert.equal(ok.fields.find((x) => x.field === 'cableLabel').result, 'match');
  assert.equal(ok.fields.find((x) => x.field === 'otherEnd').result, 'match');

  const bad = drift.compare(
    { device: 'SP-R1-U13-SW02', port: 23, status: 'connected', label: 'R1-SW02-24', labelMatched: false, otherEnd: { device: 'SP-R1-U4-PP2', port: '38A' } },
    drift.referenceOf(doc.cables[0], 'a'),
  );
  assert.deepEqual(bad.mismatches.sort(), ['cableLabel', 'otherEnd']);
});

test('a device the Excel does not name, or no Excel, is not checked', () => {
  assert.equal(drift.checkAgainst(doc, actual({ device: 'SP-R9-SW77' })).result, 'not_checked');
  assert.match(drift.checkAgainst(null, actual()).reason, /no Excel dataset/);
  assert.equal(drift.checkAgainst(doc, { device: 'SP-R1-U13-SW02' }).result, 'not_checked');
});

test('ports: same text or same number, a panel code only as itself', () => {
  assert.equal(drift.samePort('Port23', 23), true);
  assert.equal(drift.samePort('Gi1/0/26', 'gi1-0-26'), true);
  assert.equal(drift.samePort('37A', '37A'), true);
  assert.equal(drift.samePort('37A', 37), false);
  assert.equal(drift.key('SP-R01-U013-SW02'), drift.key('sp-r1-u13-sw02'));
  assert.equal(drift.family('LC-LC OM4'), 'fibre');
  assert.equal(drift.family('RJ-45'), 'copper');
  assert.equal(drift.family('Cat6 UTP'), 'copper');
});

test('the unit: where the Excel puts the device against where the photo finds it, one unit either way', () => {
  const ref = drift.referenceOf(doc.cables[0], 'a');
  const at = (units) => drift.compare({ device: 'SP-R1-U13-SW02', port: 23, status: 'connected', units, unitExpected: 13 }, ref);
  assert.equal(at([14]).fields.find((f) => f.field === 'unit').result, 'match');
  const off = at([17]);
  assert.equal(off.result, 'drift');
  assert.deepEqual(off.mismatches, ['unit']);
  assert.deepEqual([off.fields.find((f) => f.field === 'unit').expected, off.fields.find((f) => f.field === 'unit').actual], ['U13', 'U17']);
  assert.equal(drift.compare({ device: 'X', port: 1, status: 'connected' }, ref).fields.some((f) => f.field === 'unit'), false, 'no unit known, no unit row');
});

test('office rack: grey and white are one colour, and a Cat6 patch is an RJ45 connector', () => {
  const row = { row_no: 9, cable_label: '24A', a_device: 'SP-R1-U15-SW02', a_port: 'Port7', z_device: 'SP-R1-U1-PP1', z_port: '24A', cable_type: 'Cat6 patch', cable_color: 'Grey' };
  const ref = drift.referenceOf(row, 'a');
  assert.equal(ref.connector, 'RJ45');
  const read = (over) => drift.compare({ device: 'SP-R1-U15-SW02', port: 7, status: 'connected', connector: 'RJ-45', cableColor: 'White', ...over }, ref);
  assert.equal(read({}).result, 'no_drift');
  const blue = read({ cableColor: 'Blue' });
  assert.deepEqual(blue.mismatches, ['cableColor']);
  assert.match(blue.summary, /Cable colour\n {2}Expected: Grey\n {2}Actual: Blue/);
});

test('a colour that differs only on the rack photo is not checked, with the reason; a close-up makes it drift', () => {
  const row = { row_no: 9, cable_label: '70A', a_device: 'SP-R1-U18-SW04', a_port: 'Port16', z_device: 'SP-R1-U7-PP3', z_port: '70A', cable_type: 'Cat6 patch', cable_color: 'Grey' };
  const ref = drift.referenceOf(row, 'a');
  const photo = drift.compare({ device: 'SP-R1-U18-SW04', port: 16, status: 'connected', cableColor: 'Blue', colourSure: false }, ref);
  assert.equal(photo.result, 'not_checked');
  assert.equal(photo.reason, 'Cable colour not clear on the photo (Excel Grey, read Blue)');
  const closeUp = drift.compare({ device: 'SP-R1-U18-SW04', port: 16, status: 'connected', cableColor: 'Blue', colourSure: true }, ref);
  assert.deepEqual(closeUp.mismatches, ['cableColor']);
});
