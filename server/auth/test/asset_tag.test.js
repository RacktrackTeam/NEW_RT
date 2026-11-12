'use strict';

// A rack asset label read off a device ("SP-R1-U18-SW04") is not a model:
// it is never looked up and never kept as the device's model. On 29 Sep 2026
// such a label was looked up and cached as a D-Link model.
const test = require('node:test');
const assert = require('node:assert/strict');
const deviceLookup = require('../lib/device_lookup');

test('asset labels are told apart from models', () => {
  for (const tag of ['SP-R1-U18-SW04', 'SP-R1-U1ESW04', 'SP-R1-U13-SW01', 'DC1-R02-U40-PP3', 'sp-r1-u24-pdu']) {
    assert.equal(deviceLookup.isAssetTag(tag), true, tag);
  }
  for (const model of ['TL-SG2428P', 'DGS-1210-52', 'C9300-48P', 'CRS317-1G-16S+RM', 'EX4300-48T', 'USW-Pro-24', 'TL-SG1024', '7280R3']) {
    assert.equal(deviceLookup.isAssetTag(model), false, model);
  }
});

test('an asset label is never looked up: the specifications say so', async () => {
  let ran = 0;
  deviceLookup.setRunner('specs', async () => { ran += 1; return { ok: true, specs: { portCount: 1 } }; });
  const sp = deviceLookup.specs('D-Link', 'SP-R1-U18-SW04');
  assert.equal(sp.specsStatus, 'not_found');
  assert.match(sp.note, /rack label/);
  await deviceLookup.settle('specs', 'D-Link', 'SP-R1-U18-SW04');
  assert.equal(ran, 0);
  deviceLookup.setRunner('specs', null);
});

test('a datasheet port line with no separators, and the same ports listed twice, reads once', () => {
  const t = deviceLookup.portTypesFrom('48 x 1000Base-T 4 x 1000M SFP 48 x 10/100/1000BASE-T ports 4 x SFP ports 4 x Max IP Interface');
  assert.deepEqual(t, [{ type: 'RJ45', count: 48 }, { type: 'SFP', count: 4 }]);
  assert.deepEqual(deviceLookup.portTypesFrom('24x 1G RJ45 + 4x 1G SFP'), [{ type: 'RJ45', count: 24 }, { type: 'SFP', count: 4 }]);
  const s = deviceLookup.specsFromAgent({ ok: true, response: { result: { port_count: 100, port_config: '48 x 1000Base-T 4 x SFP ports 48 x 10/100/1000BASE-T ports' } } });
  assert.equal(s.portCount, 52);
});
