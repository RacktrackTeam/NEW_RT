/**
 * Barry's key test: a label the camera misreads (RO1 for R01, CAB-O1O3 for
 * CAB-0103, one character dropped) still finds its row in the Excel dataset,
 * says it was matched after correcting the reading, and a reading that could
 * be two rows is refused rather than guessed. Pure: no server, no files.
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

process.env.RACKTRACK_AUTH_DB = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'rt-labelmatch-')), 'auth.db');

const { canon, oneEdit, editsWithin, createMatcher, howWords } = require('../lib/label_match');
const checks = require('../lib/checks');
const sheetCheck = require('../lib/sheet_check');
const scanDetail = require('../lib/scan_detail');
const rackNaming = require('../lib/rack_naming');

test('the comparison form sets aside case, separators and the usual confusions', () => {
  assert.equal(canon('RO1'), canon('R01'));
  assert.equal(canon('cab-o1o3'), canon('CAB-0103'));
  assert.equal(canon('SW_CORE.01'), canon('5W-C0RE-01'));
  assert.equal(canon('PP/A1'), canon('PP-AI'));
  assert.equal(canon('B8'), canon('88'));
  assert.equal(canon('Z2G6'), canon('2266'));
  assert.notEqual(canon('FC-001'), canon('FC-002'));
  assert.ok(oneEdit('CAB0103', 'CAB013'));
  assert.ok(oneEdit('CAB0103', 'CAB0113'));
  assert.ok(!oneEdit('CAB0103', 'CAB0103'));
  assert.ok(!oneEdit('CAB0103', 'CAB0311'));
});

test('exact, corrected and one-edit matches say how they matched', () => {
  const m = createMatcher([['CAB-0103', 1], ['R01-PP-A01', 2], ['FC-001', 3]]);
  assert.equal(m.match('cab 0103').how, 'exact');
  assert.equal(m.match('cab 0103').text, 'CAB-0103');
  const fixed = m.match('CAB-O1O3');
  assert.equal(fixed.value, 1);
  assert.equal(fixed.how, 'normalised');
  assert.equal(fixed.read, 'CAB-O1O3');
  assert.equal(m.match('RO1-PP-AO1').value, 2);
  assert.equal(m.match('RO1-PP-AO1').how, 'normalised');
  // One character dropped, on a label of six or more.
  assert.equal(m.match('CAB-013').how, 'one_edit');
  assert.equal(m.match('R01-P-A01').value, 2);
  // Too short for a one-character guess: FC-01 is not FC-001.
  assert.equal(m.match('FC-01'), null);
  assert.equal(howWords('normalised'), 'matched after correcting the reading');
  assert.equal(howWords('exact'), null);
});

test('an ambiguous reading is refused, never guessed', () => {
  const m = createMatcher([['CAB-0103', 1], ['CAB-0104', 2]]);
  // CAB-0105 is one character from both.
  assert.equal(m.match('CAB-0105'), null);
  assert.match(m.why('CAB-0105'), /one character from 2 labels/);
  // Two dataset labels equal once corrected: neither is taken for a third reading.
  const twins = createMatcher([['RO1-A', 1], ['R01-A', 2]]);
  assert.equal(twins.match('R0I-A'), null);
  assert.match(twins.why('R0I-A'), /could be/);
  // But the exact one still wins when read exactly.
  assert.equal(twins.match('R01-A').value, 2);
});

// A rack in the shape lib/scan_view.load gives, made up in memory.
function view({ scanId, rackLabel, devices, zones = [] }) {
  const all = devices.map((d, i) => ({
    deviceId: i + 1, index: i + 1, type: 'Switch', hidden: false, box: [0, i * 40, 900, i * 40 + 30],
    units: d.units, unitTop: Math.max(...d.units), unitBottom: Math.min(...d.units), heightU: d.units.length,
    make: null, model: null, labelText: d.label || null, confidence: 0.9, hasPorts: true, portCount: d.ports.length,
    ports: d.ports.map((state, j) => ({ portNo: j + 1, kind: 'main', type: 'LC', state, stateSource: 'photo', confidence: 0.8, box: null, uncapped: null })),
  }));
  return { scanId, siteId: 7, orgId: 1, rackLabel, devices: all, visible: all, zones, frontLabels: [], uncapped: {} };
}

const DOC = {
  import: { id: 1 },
  cables: [
    { row_no: 2, cable_label: 'CAB-0103', a_rack: 'R01', a_device: 'SW-CORE-01', a_port: '1', a_unit: 'U40', z_rack: 'R02', z_device: 'PP-B-01', z_port: '1', z_unit: 'U42' },
    { row_no: 3, cable_label: 'CAB-0104', a_rack: 'R01', a_device: 'SW-CORE-01', a_port: '2', a_unit: 'U40', z_rack: 'R02', z_device: 'PP-B-01', z_port: '2', z_unit: 'U42' },
    { row_no: 4, cable_label: 'FIB-7781', a_rack: 'R01', a_device: 'SW-CORE-01', a_port: '3', a_unit: 'U40', z_rack: 'R02', z_device: 'PP-B-01', z_port: '3', z_unit: 'U42' },
  ],
};

test('the A-end / Z-end check matches misread labels and marks them', () => {
  const a = view({
    scanId: 'RK-A', rackLabel: 'R01',
    devices: [{ units: [40], label: 'SW-C0RE-01', ports: ['connected', 'connected', 'connected'] }],
    zones: [{ deviceId: 1, labels: [
      { text: 'CAB-O1O3', portNo: 1, confidence: 0.7 }, // O for 0: corrected
      { text: 'FIB-778', portNo: 3, confidence: 0.6 }, // one digit lost: one edit
      { text: 'CAB-0105', portNo: 2, confidence: 0.6 }, // one from 0103 and 0104: refused
    ] }],
  });
  const z = view({
    scanId: 'RK-Z', rackLabel: 'R02',
    devices: [{ units: [42], label: 'PP-B-01', ports: ['connected', 'connected', 'connected'] }],
    zones: [{ deviceId: 1, labels: [{ text: 'CAB-0103', portNo: 1 }, { text: 'FIB-7781', portNo: 3 }] }],
  });
  const c = checks.run({ a, z, doc: DOC, by: 'test' });
  const row = (label) => c.cables.find((x) => x.cableLabel === label);
  // The device's own label was misread too (0 for O): it is still SW-CORE-01.
  assert.equal(a.visible[0].sheetName, 'SW-CORE-01');
  assert.equal(a.visible[0].labelMatch, 'normalised');
  assert.equal(row('CAB-0103').status, 'matched');
  assert.equal(row('CAB-0103').a.found.match, 'normalised');
  assert.equal(row('CAB-0103').a.found.read, 'CAB-O1O3');
  assert.equal(row('CAB-0103').z.found.match, 'exact');
  assert.equal(row('FIB-7781').status, 'matched');
  assert.equal(row('FIB-7781').a.found.match, 'one_edit');
  // The near-tie is not placed on either cable: it stays a label not in the sheet.
  assert.equal(row('CAB-0105').status, 'not_in_sheet');
  assert.equal(row('CAB-0104').a.status, 'not_found');
  assert.equal(c.counts.correctedReadings, 2);
  const csv = checks.toCsv(c);
  assert.match(csv, /CAB-O1O3,matched after correcting the reading/);
});

test('the scan detail pairs a misread close-up label with its sheet cable', () => {
  const a = view({
    scanId: 'RK-A', rackLabel: 'R01',
    devices: [{ units: [40], label: 'SW-CORE-01', ports: ['connected', 'connected', 'connected'] }],
    zones: [{ deviceId: 1, labels: [{ text: 'CAB-O1O3', portNo: 1 }, { text: 'CAB-0104', portNo: 2 }] }],
  });
  const d = scanDetail.build(a, DOC);
  const p1 = d.devices[0].ports.find((p) => p.portNo === 1);
  const p2 = d.devices[0].ports.find((p) => p.portNo === 2);
  assert.equal(p1.status, 'matched');
  assert.equal(p1.label.match, 'normalised');
  assert.equal(p2.label.match, 'exact');
  assert.equal(d.devices[0].labelMatch, 'exact');
});

test('the sheet check names a device from a misread label, and says so', () => {
  const a = view({ scanId: 'RK-A', rackLabel: 'R01', devices: [{ units: [40], label: '5W-CORE-OI', ports: ['connected'] }] });
  const out = sheetCheck.check(a, DOC);
  assert.equal(out.units[0].name, 'SW-CORE-01');
  assert.equal(out.units[0].labelMatch, 'normalised');
  assert.equal(out.units[0].labelRead, '5W-CORE-OI');
});

test('the rack naming ladder counts misread device labels, and says so', () => {
  const r = rackNaming.decide({
    deviceTexts: [{ text: 'SW-C0RE-0I' }, { text: 'CAB-O1O3' }],
    cables: DOC.cables,
  });
  assert.equal(r.named, true);
  assert.equal(r.method, 'device_labels');
  assert.equal(r.name, 'R01');
  assert.match(r.reasons[0], /matched after correcting the reading/);
  // A reading one character from two cables is no vote at all.
  const tie = rackNaming.byDeviceLabels([{ text: 'CAB-0105' }, { text: 'SW-CORE-01' }], DOC.cables);
  assert.equal(tie.named, undefined);
  // The rail label RO1 read with an O is R01.
  const rail = rackNaming.decide({ rail: [{ text: 'RO2', conf: 0.7, source: 'rack rail' }], cables: DOC.cables });
  assert.equal(rail.name, 'R02');
  assert.match(rail.reasons[0], /after correcting the reading/);
});

test('editsWithin: at most k characters apart', () => {
  assert.equal(editsWithin('MV559KCWANN05', 'NV556KCWANN05', 2), true);
  assert.equal(editsWithin('MV559KCWANN05', 'NV556KCWANN05', 1), false);
  assert.equal(editsWithin('ABC', 'ABC', 0), true);
  assert.equal(editsWithin('ABCD', 'ABXCD', 1), true, 'one inserted');
  assert.equal(editsWithin('ABCD', 'AB', 2), true, 'two dropped');
  assert.equal(editsWithin('ABCD', 'A', 2), false, 'three dropped');
  assert.equal(howWords('two_edits'), 'matched after correcting the reading (two characters)');
});

test('a long device token two characters off is repaired against the one name that close', () => {
  const vocab = checks.deviceVocabulary({ visible: [] }, { cables: [
    { a_device: 'nvs-sgkc-wan-n05', z_device: 'nvs-sgkc-wan-t03' },
    { a_device: 'nvs-sgkc-sis-sw01d', z_device: 'nvs-sgkc-sis-sw01c' },
  ] });
  assert.deepEqual(checks.repairDevice('mvs-s9kc-wan-n05', vocab), { name: 'nvs-sgkc-wan-n05', how: 'two_edits' });
  assert.equal(checks.repairDevice('mvs-s9kc-sis-sw01', vocab), null, 'two names that close: left as read');
  assert.equal(checks.repairDevice('mvs-n05', vocab), null, 'too short for two characters');
});
