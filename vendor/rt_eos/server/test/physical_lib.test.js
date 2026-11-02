/**
 * The pure parts behind the device card and the optics advice: reading a
 * port configuration, the agent's answer, the slot a model name implies, and
 * the zone blocks a device is split into.
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');

process.env.RACKTRACK_AUTH_DB = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'rt-physlib-')), 'auth.db');

const { portTypesFrom, specsFromAgent, modelSimilarity } = require('../lib/device_lookup');
const { slotFromModel, fibreMode } = require('../lib/sfp_advice');
const { deviceIndexOf, portNumber, norm } = require('../lib/scan_view');
const { suggestedZones } = require('../routes/physical');
const { csvCell } = require('../lib/checks');

test('a port configuration becomes port types with counts', () => {
  assert.deepEqual(portTypesFrom('24x 1G RJ45 + 2x 10G SFP+'), [{ type: 'RJ45', count: 24 }, { type: 'SFP', count: 2 }]);
  assert.deepEqual(portTypesFrom('48x 25G SFP28, 8x 100G QSFP28'), [{ type: 'SFP', count: 48 }, { type: 'QSFP', count: 8 }]);
  assert.deepEqual(portTypesFrom(null), []);
});

test('the agent answer maps onto the contract specs; a miss is null', () => {
  const specs = specsFromAgent({ ok: true, response: { result: { vendor: 'MikroTik', model: 'CRS326', rack_units: 1, port_count: 26, port_config: '24x 1G RJ45 + 2x 10G SFP+', power_max_w: 19, datasheet_url: 'https://x.invalid/d' } } });
  assert.equal(specs.heightU, 1);
  assert.equal(specs.portCount, 26);
  assert.equal(specs.power, '19 W max');
  assert.equal(specs.datasheetUrl, 'https://x.invalid/d');
  // RackTrack's full table, under its own words, with the datasheet's extras.
  assert.deepEqual(specs.table, { Ports: '26', 'Port config': '24x 1G RJ45 + 2x 10G SFP+', 'Power max (W)': '19', 'Rack units': '1' });
  const extra = specsFromAgent({ ok: true, response: { result: { model: 'X', port_count: 8, extra_specs: { Ports: 'ignored', Weight: '2 kg' } } } });
  assert.deepEqual(extra.table, { Ports: '8', Weight: '2 kg' });
  assert.equal(specsFromAgent({ ok: false }), null);
  assert.ok(modelSimilarity('CRS326-246', 'CRS326-24G') > 0.5);
  assert.ok(modelSimilarity('C9300', 'CRS305') < 0.5);
});

test('slot and fibre from names', () => {
  assert.equal(slotFromModel('MikroTik', 'CRS326-24G-2S+RM'), 'SFP+');
  assert.equal(slotFromModel('Arista', '7280CR3-32P4'), null);
  assert.equal(slotFromModel('Cisco', 'N9K 100G'), 'QSFP28');
  assert.equal(fibreMode('LC-LC OS2'), 'singlemode');
  assert.equal(fibreMode('LC-LC OM4'), 'multimode');
  assert.equal(fibreMode('Cat6'), null);
});

test('ids, port numbers and labels compare as a person reads them', () => {
  assert.equal(deviceIndexOf('d3'), 3);
  assert.equal(deviceIndexOf(12), 12);
  assert.equal(deviceIndexOf('x'), null);
  assert.equal(deviceIndexOf('0'), null);
  assert.equal(portNumber('Gi1/0/18'), 18);
  assert.equal(portNumber('Et7'), 7);
  assert.equal(portNumber(''), null);
  assert.equal(norm(' fc-001 '), norm('FC001'));
});

test('zones are blocks of 12, or 6 for a panel of 24 or fewer', () => {
  assert.deepEqual(suggestedZones(24).map((z) => [z.from, z.to]), [[1, 6], [7, 12], [13, 18], [19, 24]]);
  assert.deepEqual(suggestedZones(48).map((z) => [z.from, z.to]), [[1, 12], [13, 24], [25, 36], [37, 48]]);
  assert.deepEqual(suggestedZones(16).map((z) => [z.from, z.to]), [[1, 6], [7, 12], [13, 16]]);
});

test('a CSV cell that a spreadsheet would run as a formula is neutralised', () => {
  assert.equal(csvCell('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"');
  assert.equal(csvCell('a,b'), '"a,b"');
  assert.equal(csvCell('FC-001'), 'FC-001');
});
