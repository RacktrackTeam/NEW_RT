'use strict';

// The shorthand a technician types to find a cable without a photo
// (lib/matrix_find.js): rack, unit, device kind and number, port.
const test = require('node:test');
const assert = require('node:assert/strict');
const mf = require('../lib/matrix_find');

const OFFICE = 'SP-HYB-RM01-R01-R1';
const rows = [
  { cable_label: 'R1-SW01-07', a_device: 'SP-R1-U13-SW01', a_port: 'Port7', a_rack: OFFICE, a_unit: '13', z_device: 'SP-R1-U4-PP2', z_port: '42A', z_rack: OFFICE, z_unit: '4' },
  { cable_label: 'R1-SW01-10', a_device: 'SP-R1-U13-SW01', a_port: 'Port10', a_rack: OFFICE, a_unit: '13', z_device: 'SP-R1-U1-PP1', z_port: '10A', z_rack: OFFICE, z_unit: '1' },
  { cable_label: 'R1-FW-1', a_device: 'SP-R1-U19-FW', a_port: 'Port1', a_rack: OFFICE, a_unit: '19', z_device: 'SP-R1-U20-ROUTER', z_port: 'Port2', z_rack: OFFICE, z_unit: '20' },
  { cable_label: 'nvs-sgkc-sis-sw05b Gi1-0-26 B12', a_device: 'nvs-sgkc-sis-sw05b', a_port: 'Gi1-0-26', a_rack: 'B12', a_unit: '', z_device: 'b08-reg-gwlc02', z_port: 'Gig05P', z_rack: 'B08', z_unit: '' },
  { cable_label: 'nvs-sgkc-sis-sw05b Gi1-7 B12', a_device: 'nvs-sgkc-sis-sw05b', a_port: 'Gi1-7', a_rack: 'B12', a_unit: '', z_device: 'nvs-sgkc-sis-sw03b', z_port: 'E1-48', z_rack: 'B9', z_unit: '' },
];
const labels = (q) => mf.find(rows, q).map((c) => c.cable_label);

test('tokens: words, numbers and joined device words', () => {
  assert.deepEqual(mf.tokens('r1/u13/sw1/7'), ['R', '1', 'U', '13', 'SW', '1', '7']);
  assert.deepEqual(mf.tokens('patch panel 2 10a'), ['PATCHPANEL', '2', '10', 'A']);
  assert.deepEqual(mf.tokens('gi1/0/26'), ['GI', '1', '0', '26']);
});

test('parse: every part in order, or nothing for text that is not shorthand', () => {
  assert.deepEqual(mf.parse('r1/u13/sw1/7', [OFFICE]), { rack: 'R1', unit: 13, device: { kind: 'SW', n: 1 }, port: { number: 7, code: null, path: [7], words: '7' } });
  assert.deepEqual(mf.parse('pp1 10a').port, { number: 10, code: 'A', path: [10], words: '10A' });
  assert.deepEqual(mf.parse('b12 sw5 26', ['B12', 'B08']).rack, 'B12');
  assert.deepEqual(mf.parse('fw'), { rack: null, unit: null, device: { kind: 'FW', n: null }, port: null });
  assert.equal(mf.parse('sw1 port'), null, 'a port word with no number');
  assert.equal(mf.parse('hello world'), null);
  assert.equal(mf.parse('sw1 7 extra'), null, 'words left over');
});

test('find: the shorthand lands every part on one end of the cable', () => {
  assert.deepEqual(labels('r1/u13/sw1/7'), ['R1-SW01-07']);
  assert.deepEqual(labels('sw1 p7'), ['R1-SW01-07']);
  assert.deepEqual(labels('sw01 port 7'), ['R1-SW01-07']);
  assert.deepEqual(labels('pp1 10a'), ['R1-SW01-10']);
  assert.deepEqual(labels('10a'), ['R1-SW01-10'], 'a bare panel code searches every device');
  assert.deepEqual(labels('u4'), ['R1-SW01-07'], 'the unit from the name or the unit column');
  assert.deepEqual(labels('sw1'), ['R1-SW01-07', 'R1-SW01-10']);
  assert.deepEqual(labels('fw'), ['R1-FW-1']);
  assert.deepEqual(labels('router 2'), ['R1-FW-1'], 'the sheet does not number routers, so 2 is the port');
  assert.deepEqual([...mf.numberedKinds(rows)].sort(), ['PP', 'SW']);
  assert.deepEqual(labels('sw5 26'), ['nvs-sgkc-sis-sw05b Gi1-0-26 B12']);
  assert.deepEqual(labels('gi1/0/26'), ['nvs-sgkc-sis-sw05b Gi1-0-26 B12']);
  assert.deepEqual(labels('b12 sw5'), ['nvs-sgkc-sis-sw05b Gi1-0-26 B12', 'nvs-sgkc-sis-sw05b Gi1-7 B12']);
  assert.deepEqual(labels('b9'), ['nvs-sgkc-sis-sw05b Gi1-7 B12']);
  assert.deepEqual(labels('sw1 42a'), [], 'the port must be on the same end as the device');
  assert.deepEqual(labels('sw9'), []);
  assert.deepEqual(labels('7'), ['R1-SW01-07', 'nvs-sgkc-sis-sw05b Gi1-7 B12'], 'a bare number is a port number, not a panel code');
});

test('find: plain text still works, and the two are joined', () => {
  assert.deepEqual(labels('sw03b'), ['nvs-sgkc-sis-sw05b Gi1-7 B12']);
  assert.deepEqual(labels('R1-FW'), ['R1-FW-1']);
  assert.equal(mf.find(rows, '').length, rows.length);
});
