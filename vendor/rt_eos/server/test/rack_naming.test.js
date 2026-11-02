/**
 * The rack naming ladder (lib/rack_naming.js) and the rack height rules
 * (lib/rack_height.js).
 *
 * What is proved:
 *   - rung 1: the rack's own label read on the rail wins over everything else;
 *   - rung 2: device labels name a rack only by a clear majority (two or more,
 *     and more than half of the labels found in the dataset); one label, or
 *     two racks with equal support, name nothing;
 *   - rung 3: the layout picks the clear best rack in the dataset, and refuses
 *     two racks that fit about as well;
 *   - rung 4: with nothing to go on, and with no dataset, it asks;
 *   - after an analysis (settleRack in app.js, the real one) the name found is
 *     recorded as rack_identity rows are, the rack folder is renamed through
 *     lib/store.js, meta.rackNaming says how, the height is decided, and the
 *     answer that opens the result carries both;
 *   - the height: the rail numbers win, the dataset is next (rounded up to a
 *     standard height), a count within one unit of a standard height snaps
 *     to it, and any other count is kept and said not to be standard.
 *
 * Everything runs on a throwaway data folder; the live data is never touched.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-naming-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'naming-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app, _internals } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const rackIdentity = require('../lib/rack_identity');
const naming = require('../lib/rack_naming');
const height = require('../lib/rack_height');

const DATASET = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'naming-dataset.json'), 'utf8')).cables;
const ids = {};

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

// The scan layout of rack R01-A12 as the photo reads it.
const R01_LAYOUT = [
  { units: [42], kind: 'Patch Panel', ports: 24 },
  { units: [40], kind: 'Switch', ports: 24 },
  { units: [38], kind: 'Firewall', ports: 8 },
];

// -- The ladder, rung by rung (decide is pure) ----------------------------
test('rung 1: the rail label wins, even when the device labels point elsewhere', () => {
  const r = naming.decide({
    rail: [{ text: 'R01-A12', conf: 0.91, source: 'rack rail' }],
    deviceTexts: [{ text: 'PP-24 A' }, { text: 'SW-B1' }, { text: 'PP-B02' }],
    layout: R01_LAYOUT,
    cables: DATASET,
  });
  assert.equal(r.named, true);
  assert.equal(r.method, 'rail_label');
  assert.equal(r.name, 'R01-A12');
  assert.equal(r.ask, false);
  assert.ok(r.confidence >= 0.9);
  assert.match(r.reasons[0], /read on the rail/);
});

test('rung 1: a rail reading one character off a single dataset rack names it; a clear reading of a new rack is used as read', () => {
  const known = [{ id: 5, name: 'SP-HYB-RM01-R01-R1', facilityId: null }];
  const near = naming.decide({ rail: [{ text: 'SP-HYB-AM01-R01-R1', conf: 0.7, source: 'rack rail' }], knownRacks: known, cables: DATASET });
  assert.equal(near.method, 'rail_label');
  assert.equal(near.name, 'SP-HYB-RM01-R01-R1');
  assert.equal(near.knownRackId, 5);
  const alone = naming.decide({ rail: [{ text: 'rk-07', conf: 0.93, source: 'rack rail' }] });
  assert.equal(alone.method, 'rail_label');
  assert.equal(alone.name, 'RK-07');
  // Read, but not clearly: not used on its own.
  const faint = naming.decide({ rail: [{ text: 'RK-07', conf: 0.5, source: 'rack rail' }] });
  assert.equal(faint.named, false);
});

test('rung 1: a rail label equal to a rack set up for the site binds to that rack', () => {
  const r = naming.decide({ rail: [{ text: 'r01 a12', conf: 0.7, source: 'rack rail' }], knownRacks: [{ id: 77, name: 'R01-A12', facilityId: null }] });
  assert.equal(r.method, 'rail_label');
  assert.equal(r.knownRackId, 77);
});

test('rung 2: the device labels name the rack their clear majority agrees on', () => {
  const r = naming.decide({
    rail: [],
    deviceTexts: [{ text: 'sw-a1' }, { text: 'PP-A01' }, { text: 'FW A1' }, { text: 'EOS-F-0145' }, { text: 'PP-24 A' }, { text: 'NOISE-99' }],
    layout: [],
    cables: DATASET,
  });
  assert.equal(r.named, true);
  assert.equal(r.method, 'device_labels');
  assert.equal(r.name, 'R01-A12');
  assert.equal(r.tried[0].method, 'rail_label');
  assert.match(r.reasons[0], /4 of the 5 labels/);
});

test('rung 2: one label, or two racks with equal support, name nothing', () => {
  const one = naming.byDeviceLabels([{ text: 'SW-A1' }], DATASET);
  assert.equal(one.named, undefined);
  assert.match(one.outcome, /only one label/);
  // A cable between the two racks supports both ends equally.
  const tie = naming.byDeviceLabels([{ text: 'EOS-F-0141' }, { text: 'EOS-F-0142' }], DATASET);
  assert.equal(tie.named, undefined);
  assert.match(tie.outcome, /equally/);
  // Three for R01, three for R02: a tie, refused.
  const split = naming.byDeviceLabels([{ text: 'SW-A1' }, { text: 'PP-A01' }, { text: 'SW-B1' }, { text: 'PP-B02' }, { text: 'FW-A1' }, { text: 'PP-24 A' }], DATASET);
  assert.equal(split.named, undefined);
  // Three for R01 and two for R02: three of five is more than half.
  const most = naming.byDeviceLabels([{ text: 'SW-A1' }, { text: 'PP-A01' }, { text: 'SW-B1' }, { text: 'PP-B02' }, { text: 'FW-A1' }], DATASET);
  assert.equal(most.named, true);
  assert.equal(most.name, 'R01-A12');
});

test('rung 2: cable flags print the rack, and every flag counts (two flags are not one label)', () => {
  const flag = (text) => ({ text, where: 'cable flag' });
  const r = naming.byDeviceLabels([flag('R02-B07'), flag('R02-B07'), flag('SW-A1')], DATASET);
  assert.equal(r.named, true);
  assert.equal(r.name, 'R02-B07');
  // The same two texts as device labels are one label each: a tie, refused.
  const tie = naming.byDeviceLabels([{ text: 'R02-B07' }, { text: 'R02-B07' }, { text: 'SW-A1' }], DATASET);
  assert.equal(tie.named, undefined);
});

test('rung 3: the layout picks the clear best rack in the dataset', () => {
  const r = naming.decide({ rail: [], deviceTexts: [{ text: 'nothing here' }], layout: R01_LAYOUT, cables: DATASET });
  assert.equal(r.named, true);
  assert.equal(r.method, 'layout');
  assert.equal(r.name, 'R01-A12');
  assert.equal(r.scores[0].name, 'R01-A12');
  assert.equal(r.scores[0].score, 1);
  assert.ok(r.scores[1].score < naming.LAYOUT_MIN_SCORE);
  assert.deepEqual(r.tried.map((t) => t.method), ['rail_label', 'device_labels']);
});

test('rung 3: two racks that fit the layout about as well are a tie, and a tie is refused', () => {
  const row = (label, aDev, aRack, aUnit, zDev, zRack, zUnit) => ({ cable_label: label, a_device: aDev, a_port: '1', a_rack: aRack, a_unit: aUnit, z_device: zDev, z_port: '1', z_rack: zRack, z_unit: zUnit });
  const twins = [
    row('T-1', 'SW-X1', 'RX-1', '10', 'PP-X1', 'RX-1', '12'),
    row('T-2', 'SW-Y1', 'RX-2', '10', 'PP-Y1', 'RX-2', '12'),
  ];
  const layout = [{ units: [12], kind: 'Patch Panel', ports: 24 }, { units: [10], kind: 'Switch', ports: 24 }];
  const r = naming.decide({ layout, cables: twins });
  assert.equal(r.named, false);
  assert.equal(r.ask, true);
  assert.match(r.tried[2].outcome, /about as well/);
});

test('rung 4: nothing to go on, or no dataset, and it asks', () => {
  const none = naming.decide({ rail: [], deviceTexts: [{ text: 'SW-A1' }], layout: R01_LAYOUT, cables: null });
  assert.equal(none.named, false);
  assert.equal(none.ask, true);
  assert.deepEqual(none.tried.map((t) => t.method), ['rail_label', 'device_labels', 'layout']);
  assert.match(none.why, /no Excel dataset/);
});

test('kinds are read from dataset names the way a technician writes them', () => {
  assert.equal(naming.kindOfName('SW-CORE-01'), 'Switch');
  assert.equal(naming.kindOfName('PP-24 A'), 'Patch Panel');
  assert.equal(naming.kindOfName('R01-FW02'), 'Firewall');
  assert.equal(naming.kindOfName('CORE-01'), null);
});

// -- The rack height ------------------------------------------------------
test('height: the rail numbers win', () => {
  const h = height.decideHeight({
    railNumbers: [{ n: 22, yPct: 3 }, { n: 12, yPct: 48 }, { n: 1, yPct: 97.5 }],
    datasetMaxUnit: 40, detectedCount: 41,
  });
  assert.deepEqual([h.unitsTotal, h.source, h.standard], [22, 'rail_numbers', true]);
  // Numbers that run the wrong way are not a ladder.
  const upside = height.decideHeight({ railNumbers: [{ n: 1, yPct: 3 }, { n: 12, yPct: 50 }, { n: 22, yPct: 97 }], detectedCount: 20 });
  assert.equal(upside.source, 'detected');
});

test('height: a 12U rack read off its rails through the noise of the tick marks, and the photo rows put on the rails', () => {
  // What the reader gave on the office test photo T3: 12 down to 01 on the
  // rails, and stray 1s, 4s and 7s off the tick marks between them.
  const rails = [[12, 13.96], [7, 25.22], [10, 27.2], [7, 31.69], [9, 33.84], [7, 38.42], [8, 40.53], [7, 45.02], [7, 46.99], [1, 50.27], [6, 53.46],
    [1, 56.78], [5, 60.01], [4, 66.43], [4, 75.18], [1, 82.54], [1, 85.86], [12, 14.09], [10, 27.38], [1, 30.79], [9, 34.11], [4, 51.66], [4, 66.92]]
    .map(([n, yPct]) => ({ n, yPct }));
  const ladder = height.railLadder(rails);
  assert.equal(ladder.top, 12);
  // The engine counted 14 rows (the top frame as a row): every device one unit high.
  const devices = [[[13], 14.1], [[12], 21.0], [[7, 8], 53.3], [[5], 65.4], [[4], 72.9]].map(([units, yPct]) => ({ units, yPct }));
  const h = height.decideHeight({ railNumbers: rails, detectedCount: 14, maxDeviceUnit: 14, devices });
  assert.deepEqual([h.unitsTotal, h.source, h.standard, h.unitOffset], [12, 'rail_numbers', true, -1]);
  assert.match(h.note, /lowered by 1 to match the rails/);
});

test('height: the dataset is next, rounded up to a standard height', () => {
  assert.deepEqual(pick(height.decideHeight({ datasetMaxUnit: 40, detectedCount: 30 })), [42, 'dataset', true]);
  assert.deepEqual(pick(height.decideHeight({ datasetMaxUnit: 21, detectedCount: 30 })), [22, 'dataset', true]);
  assert.equal(height.datasetMaxUnitOf(DATASET, 'r01 a12'), 42);
  assert.equal(height.datasetMaxUnitOf(DATASET, 'R02-B07'), 39);
});

test('height: a count within one unit of a standard height snaps to it; any other count is kept and said not standard', () => {
  assert.deepEqual(pick(height.decideHeight({ detectedCount: 41 })), [42, 'detected', true]);
  assert.deepEqual(pick(height.decideHeight({ detectedCount: 21 })), [22, 'detected', true]);
  assert.deepEqual(pick(height.decideHeight({ detectedCount: 42 })), [42, 'detected', true]);
  const odd = height.decideHeight({ detectedCount: 30 });
  assert.deepEqual(pick(odd), [30, 'detected', false]);
  assert.match(odd.note, /not a standard rack size/);
  // Never lower than a unit a device was numbered on.
  assert.equal(height.decideHeight({ datasetMaxUnit: 20, maxDeviceUnit: 23 }).unitsTotal, 23);
  assert.equal(height.decideHeight({}).unitsTotal, null);
});

function pick(h) { return [h.unitsTotal, h.source, h.standard]; }

// -- After an analysis, through the real settleRack -----------------------
before(() => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('nm-org', 'Naming Org', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.site = Number(site.run('nm-site', 'Naming Site', ids.org).lastInsertRowid);
  ids.bare = Number(site.run('nm-bare', 'Site Without Dataset', ids.org).lastInsertRowid);
  const dir = path.join(store.siteDir(ids.org, ids.site), 'matrix');
  fs.mkdirSync(dir, { recursive: true });
  store.writeJsonAtomic(path.join(dir, 'cables.json'), {
    import: { id: 'nm0000000001', file_name: 'dataset.xlsx', sheet_name: 'S', header_row: 0, uploaded_by: 'nm', uploaded_at: '2026-09-29T07:00:00Z', row_count: DATASET.length, cable_count: DATASET.length, problem_count: 0 },
    mapping: {}, cables: DATASET, problems: [],
  });
});

const dev = (cls, unit, box, ports = 0) => ({ class_name: cls, units: [`u${unit}`], box, confidence: 0.9, port_count: ports });

/** A scan folder as an analysis leaves it, with its labels already read. */
function makeScan(scanId, siteId, { side = { labels: [] }, front = { labels: [] }, unitsDetected = 5, devices } = {}) {
  const p = store.newScan({ scanId, orgId: ids.org, siteId, at: '2026-09-29T08:00:00Z' });
  fs.writeFileSync(p.photo, 'not really a photo');
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({
    devices: devices || [dev('Patch Panel', 42, [0, 0, 100, 10], 24), dev('Switch', 40, [0, 20, 100, 30], 24), dev('Firewall', 38, [0, 40, 100, 50], 8)],
    units_detected: Array.from({ length: unitsDetected }, (_, i) => `u${42 - i}`),
  }));
  fs.writeFileSync(path.join(p.engine, 'side_labels.json'), JSON.stringify(side));
  fs.writeFileSync(path.join(p.engine, 'labels-front.json'), JSON.stringify(front));
  store.writeMeta(scanId, { scan_id: scanId, timestamp: '2026-09-29T08:00:00Z' });
  tenant.claimRack(siteId, scanId, null);
  return p;
}

test('after an analysis: a rail label names the rack, it is recorded, the folder renamed, and the height decided', async () => {
  const scanId = 'RK-NAME0001';
  makeScan(scanId, ids.site, {
    side: {
      labels: [{ text: 'R01-A12', side: 'rail', conf: 0.94, yPct: 2 }],
      unit_numbers: [{ n: 42, yPct: 2 }, { n: 30, yPct: 30 }, { n: 20, yPct: 55 }, { n: 1, yPct: 98 }],
    },
  });
  const out = await _internals.settleRack(scanId, { tenantId: ids.site });
  assert.equal(out.rackNaming.method, 'rail_label');
  assert.equal(out.rackNaming.name, 'R01-A12');
  assert.equal(out.rackNaming.ask, false);
  assert.equal(out.rackHeight.unitsTotal, 42);
  assert.equal(out.rackHeight.source, 'rail_numbers');
  // Recorded the way a person's answer is, with how it was found.
  const bound = rackIdentity.confirmedRack(ids.site, scanId);
  assert.equal(bound.rack.name, 'R01-A12');
  assert.equal(bound.source, 'label');
  assert.equal(store.scanInfo(scanId).rackKey, store.slugify('R01-A12'));
  const meta = store.readMeta(scanId);
  assert.equal(meta.rackNaming.method, 'rail_label');
  assert.equal(meta.rackHeight.unitsTotal, 42);
  const result = store.readResult(scanId);
  assert.equal(result.rack.units_total, 42);
  assert.equal(result.rack.named_by, 'rail_label');
  assert.equal(result.api.rack.unitsTotal, 42);
  // Settling again leaves it as it is.
  const again = await _internals.settleRack(scanId, { tenantId: ids.site });
  assert.equal(again.rackNaming.name, 'R01-A12');
  assert.equal(again.rackNaming.method, 'rail_label');
});

test('after an analysis: no rail label, the device labels place a second scan of the same rack on the rack already set up', async () => {
  const scanId = 'RK-NAME0002';
  makeScan(scanId, ids.site, {
    side: { labels: [{ text: 'SW-A1', side: 'right', conf: 0.9, yPct: 30 }, { text: 'PP-A01', side: 'right', conf: 0.9, yPct: 10 }] },
    front: { labels: [{ text: 'FW-A1', conf: 0.8, bbox: { x: 10, y: 42, w: 30, h: 5 } }] },
  });
  const out = await _internals.settleRack(scanId, { tenantId: ids.site });
  assert.equal(out.rackNaming.method, 'device_labels');
  assert.equal(out.rackNaming.name, 'R01-A12');
  const first = rackIdentity.confirmedRack(ids.site, 'RK-NAME0001');
  const bound = rackIdentity.confirmedRack(ids.site, scanId);
  assert.equal(bound.rack.id, first.rack.id, 'one rack, two scans');
  assert.equal(bound.source, 'device_labels');
  // No rail numbers: the dataset says U42 is used, so 42U.
  assert.equal(out.rackHeight.source, 'dataset');
  assert.equal(out.rackHeight.unitsTotal, 42);
});

test('after an analysis: no labels at all, the layout names the rack from the dataset', async () => {
  const scanId = 'RK-NAME0003';
  makeScan(scanId, ids.site, { unitsDetected: 41 });
  const out = await _internals.settleRack(scanId, { tenantId: ids.site });
  assert.equal(out.rackNaming.method, 'layout');
  assert.equal(out.rackNaming.name, 'R01-A12');
  assert.equal(rackIdentity.confirmedRack(ids.site, scanId).source, 'layout');
});

test('after an analysis: at a site with no dataset and nothing read, it asks, and nothing is recorded', async () => {
  const scanId = 'RK-NAME0004';
  makeScan(scanId, ids.bare, { unitsDetected: 21, devices: [dev('Switch', 21, [0, 0, 100, 10], 24), dev('Switch', 19, [0, 20, 100, 30], 24)] });
  const out = await _internals.settleRack(scanId, { tenantId: ids.bare });
  assert.equal(out.rackNaming.ask, true);
  assert.equal(out.rackNaming.name, null);
  assert.equal(out.rackNaming.tried.length, 3);
  assert.equal(rackIdentity.confirmedRack(ids.bare, scanId), null);
  // 21 counted is within one of the standard 22U.
  assert.equal(out.rackHeight.unitsTotal, 22);
  assert.equal(out.rackHeight.source, 'detected');
});

test('the server never touched the live data folder', () => {
  assert.ok(store.siteDir(ids.org, ids.site).startsWith(ROOT));
  assert.ok(app);
});
