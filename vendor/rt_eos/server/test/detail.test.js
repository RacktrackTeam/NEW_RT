/**
 * The read-only views of routes/detail.js: one scan in full (units, devices,
 * ports with the label read and the sheet's cable, cables seen) and the
 * site's switches. Each is tried by a caller who may and one who may not.
 *
 * One rack, R01, at a site with a patching matrix; a second rack at another
 * organisation's site that must never show. The two public lookups are
 * stubbed; everything else is the shipped code on a throwaway data folder.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-detail-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'detail-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const scanView = require('../lib/scan_view');
const deviceLookup = require('../lib/device_lookup');

const A = 'RK-DETL0A01';
const B = 'RK-DETL0B01';
const AWAY = 'RK-DETL0X01';
const ids = {};
const users = {};
let server;
let base;

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

function ports(n, states, extra = {}) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const x = 20 + i * 30;
    out.push({ box: [x, 10, x + 24, 34], status: states[i + 1] || 'empty', class_name: 'RJ45', confidence: 0.8, index: i + 1, ...(extra[i + 1] || {}) });
  }
  return out;
}

async function makeScan(scanId, siteId, userId, devices, label) {
  const p = store.newScan({ scanId, siteId, at: '2026-09-29T08:00:00Z' });
  await sharp({ create: { width: 1000, height: 800, channels: 3, background: '#b0b0b0' } }).jpeg().toFile(p.photo);
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({ devices, units_detected: [] }));
  store.writeMeta(scanId, { scan_id: scanId, timestamp: '2026-09-29T08:00:00Z', username: 'dt_mandar' });
  tenant.claimRack(siteId, scanId, userId);
  if (label) store.renameRackOfScan(scanId, label);
}

function writeMatrix(orgId, siteId) {
  const cable = (label, aDev, aPort, aRack, aUnit, zDev, zPort, zRack, zUnit, type = 'LC-LC OM4') => ({
    row_no: 0, cable_label: label, a_device: aDev, a_port: aPort, a_rack: aRack, a_unit: aUnit,
    z_device: zDev, z_port: zPort, z_rack: zRack, z_unit: zUnit, cable_type: type, notes: '',
  });
  const cables = [
    cable('FC-001', 'SW-CORE-01', 'Gi1/0/1', 'R01', '42', 'PP-B01', '1', 'R02', '30'),
    cable('FC-002', 'SW-CORE-01', 'Gi1/0/2', 'R01', '42', 'PP-B01', '2', 'R02', '30'),
    cable('FC-003', 'SW-CORE-01', 'Gi1/0/3', 'R01', '42', 'PP-B01', '3', 'R02', '30'),
    cable('FC-004', 'PP-A01', '1', 'R01', '40', 'PP-B01', '4', 'R02', '30'),
  ];
  cables.forEach((c, i) => { c.row_no = i + 2; });
  const dir = path.join(store.siteDir(orgId, siteId), 'matrix');
  fs.mkdirSync(dir, { recursive: true });
  store.writeJsonAtomic(path.join(dir, 'cables.json'), {
    import: { id: 'def456def456', file_name: 'm.xlsx', sheet_name: 'S', header_row: 0, uploaded_by: 'dt_barry', uploaded_at: '2026-09-29T07:00:00Z', row_count: 4, cable_count: 4, problem_count: 0 },
    mapping: {}, cables, problems: [],
  });
}

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('dt-org', 'Detail Org', 'active')").run().lastInsertRowid);
  ids.org2 = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('dt-org2', 'Other Org', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.site = Number(site.run('dt-site', 'Rack Build Facility', ids.org).lastInsertRowid);
  ids.site2 = Number(site.run('dt-site2', 'Other Site', ids.org2).lastInsertRowid);
  const add = (name, role, org, t) => {
    db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
                VALUES (?, ?, 'x', 1, ?, ?, ?, 1)`).run(`${name}@example.test`, name, role, org, t);
    users[name] = db.prepare('SELECT * FROM users WHERE username = ?').get(name);
  };
  add('dt_barry', 'site_manager', ids.org, ids.site);
  add('dt_mandar', 'member', ids.org, ids.site);
  add('dt_away', 'member', ids.org2, ids.site2);

  await makeScan(A, ids.site, users.dt_mandar.id, [
    { class_name: 'Switch', confidence: 0.91, box: [100, 50, 900, 130], units: ['u42'], port_count: 6,
      ports: ports(6, { 1: 'connected', 2: 'connected', 3: 'empty', 4: 'empty', 5: 'empty', 6: 'connected' }, { 6: { cable_connector: 'LC', cable_color: 'Aqua' } }) },
    { class_name: 'Empty', confidence: 0.5, box: [100, 130, 900, 200], units: ['u41'], ports: [] },
    { class_name: 'Patch Panel', confidence: 0.5, box: [100, 200, 900, 270], units: ['u40'], port_count: 2, ports: ports(2, {}) },
  ], 'R01');
  await makeScan(B, ids.site, users.dt_mandar.id, [
    { class_name: 'Patch Panel', confidence: 0.6, box: [100, 50, 900, 130], units: ['u30'], port_count: 2, ports: ports(2, {}) },
  ], 'R02');
  await makeScan(AWAY, ids.site2, users.dt_away.id, [
    { class_name: 'Switch', confidence: 0.9, box: [100, 50, 900, 130], units: ['u10'], port_count: 4, ports: ports(4, {}) },
  ], 'X01');
  writeMatrix(ids.org, ids.site);

  // A close-up of the switch: FC-001 on its own port, FC-003 on port 2.
  const zdir = store.file(A, 'zones');
  fs.mkdirSync(zdir, { recursive: true });
  store.writeJsonAtomic(path.join(zdir, 'aaaaaaaaaaaa.json'), {
    zoneId: 'aaaaaaaaaaaa', deviceId: 1, from: 1, to: 6, at: '2026-09-29T08:05:00Z', by: 'dt_mandar',
    labels: [{ text: 'FC-001', confidence: 0.95, portNo: 1, box: [0, 0, 1, 1] }, { text: 'FC-003', confidence: 0.9, portNo: 2, box: [0, 0, 1, 1] }],
    ports: [],
  });
  scanView.setUncapped(A, 1, 4, true, { username: 'dt_mandar' });

  deviceLookup.setRunner('specs', async () => ({ ok: false, note: 'not in the base' }));

  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

async function call(p, user) {
  const headers = user ? { Authorization: `Bearer ${auth.makeToken(users[user])}` } : {};
  const res = await fetch(base + p, { headers });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return { status: res.status, json, text };
}

test('detail: signed out 401, a stranger 404', async () => {
  assert.equal((await call(`/api/scans/${A}/detail`)).status, 401);
  assert.equal((await call(`/api/scans/${A}/detail`, 'dt_away')).status, 404);
  assert.equal((await call('/api/scans/NOT-AN-ID/detail', 'dt_mandar')).status, 404);
});

test('detail: units top to bottom, with what the sheet expects in each', async () => {
  const r = await call(`/api/scans/${A}/detail`, 'dt_mandar');
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.rackLabel, 'R01');
  assert.equal(r.json.matrix.importId, 'def456def456');
  assert.deepEqual(r.json.units.map((u) => [u.unit, u.kind, u.deviceId, u.expected, u.expectedStatus]), [
    [42, 'device', 1, 'SW-CORE-01', 'ok'],
    [41, 'empty', null, null, null],
    [40, 'device', 3, 'PP-A01', 'ok'],
  ]);
  assert.deepEqual(r.json.devices.map((d) => [d.deviceId, d.name, d.sheetStatus]), [[1, 'SW-CORE-01', 'ok'], [3, 'PP-A01', 'ok']]);
  assert.equal(r.json.devices[0].zonesTaken, 1);
});

test('detail: each port carries the label read, the sheet cable and a status', async () => {
  const r = await call(`/api/scans/${A}/detail`, 'dt_mandar');
  const sw = r.json.devices[0];
  const port = (n) => sw.ports.find((p) => p.portNo === n);
  assert.equal(port(1).status, 'matched');
  assert.equal(port(1).label.text, 'FC-001');
  assert.equal(port(1).label.source, 'close-up');
  assert.deepEqual(port(1).expected.other, { end: 'z', device: 'PP-B01', port: '1', rack: 'R02', unit: 30 });
  assert.equal(port(2).status, 'wrong_port', 'FC-003 belongs on port 3');
  assert.equal(port(2).labelBelongsTo.cableLabel, 'FC-003');
  assert.equal(port(2).expected.cableLabel, 'FC-002');
  assert.equal(port(3).status, 'missing_cable');
  assert.equal(port(4).status, 'uncapped');
  assert.equal(port(4).uncapped.markedBy, 'dt_mandar');
  assert.equal(port(5).status, 'empty');
  assert.equal(port(6).status, 'not_in_sheet');
  assert.equal(port(6).cableConnector, 'LC');
  assert.equal(port(6).cableColor, 'Aqua');
  assert.equal(r.json.counts.connected, 3);
  assert.equal(r.json.counts.labelsRead, 2);
  assert.equal(r.json.flags.missingCable, 2, 'FC-003 on the switch and FC-004 on the panel');
  assert.equal(r.json.devices[1].ports[0].status, 'missing_cable');
  assert.equal(r.json.flags.uncapped, 1);
});

test('detail: every cable seen, with its far end from the sheet', async () => {
  const r = await call(`/api/scans/${A}/detail`, 'dt_mandar');
  const c = r.json.cables;
  assert.deepEqual(c.map((x) => [x.from.portNo, x.label, x.status]), [[1, 'FC-001', 'matched'], [2, 'FC-003', 'wrong_port'], [6, null, 'not_in_sheet']]);
  assert.equal(c[0].to.device, 'PP-B01');
  assert.equal(c[0].type, 'LC-LC OM4');
  assert.equal(c[2].to, null);
  assert.equal(c[2].colour, 'Aqua');
});

test('switches: the site\'s switches only, never another organisation\'s', async () => {
  assert.equal((await call('/api/switches')).status, 401);
  const r = await call('/api/switches', 'dt_mandar');
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.site.id, ids.site);
  assert.equal(r.json.matrix, true);
  assert.deepEqual(r.json.switches.map((s) => [s.scanId, s.rackLabel, s.deviceId, s.type, s.name, s.unitTop]), [[A, 'R01', 1, 'Switch', 'SW-CORE-01', 42]]);
  const sw = r.json.switches[0];
  assert.equal(sw.ports.total, 6);
  assert.equal(sw.ports.connected, 3);
  assert.deepEqual(sw.ports.byType, [{ type: 'RJ45', count: 6 }]);
  assert.equal(sw.specsStatus, 'no_model');
  assert.match(sw.cropUrl, /crops\/device-1\.jpg$/);
  const away = await call('/api/switches', 'dt_away');
  assert.deepEqual(away.json.switches.map((s) => s.scanId), [AWAY]);
  assert.equal((await call(`/api/switches?siteId=${ids.site2}`, 'dt_mandar')).status, 404);
});

test('switches of one scan: the same card, for that scan only', async () => {
  assert.equal((await call(`/api/scans/${A}/switches`)).status, 401);
  assert.equal((await call(`/api/scans/${A}/switches`, 'dt_away')).status, 404);
  const r = await call(`/api/scans/${A}/switches`, 'dt_mandar');
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.rackLabel, 'R01');
  assert.equal(r.json.matrix, true);
  assert.deepEqual(r.json.switches.map((s) => [s.deviceId, s.name, s.unitTop, s.ports.total]), [[1, 'SW-CORE-01', 42, 6]]);
  assert.match(r.json.switches[0].cropUrl, /crops\/device-1\.jpg$/);
});

test('flags of one scan: each flag with its device, port and a picture of the port', async () => {
  assert.equal((await call(`/api/scans/${A}/flags`, 'dt_away')).status, 404);
  const r = await call(`/api/scans/${A}/flags`, 'dt_mandar');
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.matrix, true);
  const kinds = r.json.flags.map((f) => [f.kind, f.device, f.portNo]);
  assert.deepEqual(kinds, [
    ['missing_cable', 'SW-CORE-01', 3],
    ['missing_cable', 'PP-A01', 1],
    ['not_in_sheet', 'SW-CORE-01', 6],
    ['uncapped', 'SW-CORE-01', 4],
  ]);
  const f = r.json.flags[0];
  assert.equal(f.unit, 42);
  assert.equal(f.cableLabel, 'FC-003');
  assert.match(f.cropUrl, /crops\/port-1-3\.jpg$/);
  assert.match(r.json.deviceCrops['1'], /crops\/device-1\.jpg$/);
  const img = await fetch(base + f.cropUrl, { headers: { Authorization: `Bearer ${auth.makeToken(users.dt_mandar)}` } });
  assert.equal(img.status, 200);
});

test('switches: RackTrack\'s switch card, the make and model a person typed, and the full specification table', async () => {
  // A person typed the make and model (the switch screen's own store,
  // engine/device_overrides.json under the unit).
  const file = path.join(store.file(A, 'engine'), 'device_overrides.json');
  const before = store.readJson(file, {}) || {};
  store.writeJsonAtomic(file, { ...before, U42: { make: 'MikroTik', model: 'CRS326-24G-2S+RM' } });
  deviceLookup.setRunner('specs', async () => ({ ok: true, specs: { heightU: 1, portCount: 26, portTypes: [], power: null, datasheetUrl: 'https://example.invalid/ds', productUrl: 'https://example.invalid/ds', vendor: 'MikroTik', model: 'CRS326-24G-2S+RM', table: { Ports: '26', Layer: 'L3' } } }));
  try {
    const first = await call(`/api/scans/${A}/switches`, 'dt_mandar');
    assert.equal(first.status, 200, first.text);
    assert.equal(first.json.switches[0].specsStatus, 'looking');
    await deviceLookup.settle('specs', 'MikroTik', 'CRS326-24G-2S+RM');
    const r = await call(`/api/scans/${A}/switches`, 'dt_mandar');
    const sw = r.json.switches[0];
    assert.equal(sw.type, 'Switch');
    assert.equal(sw.positionKey, 'U42');
    assert.equal(sw.make, 'MikroTik');
    assert.equal(sw.model, 'CRS326-24G-2S+RM');
    assert.equal(sw.awaitingLabel, false, 'a make and model are known');
    assert.equal(sw.specsStatus, 'found');
    assert.deepEqual(sw.specs.table, { Ports: '26', Layer: 'L3' });
    // The card carries only the specification answer: nothing else is looked up for a model.
    assert.deepEqual(Object.keys(sw).filter((k) => /^specs/i.test(k)).sort(), ['specs', 'specsNote', 'specsStatus']);
  } finally {
    store.writeJsonAtomic(file, before);
    deviceLookup.setRunner('specs', async () => ({ ok: false, note: 'not in the base' }));
  }
});

async function post(p, user, body) {
  const res = await fetch(base + p, { method: 'POST', headers: { 'content-type': 'application/json', Authorization: `Bearer ${auth.makeToken(users[user])}` }, body: JSON.stringify(body) });
  let json = null;
  try { json = await res.json(); } catch { /* not JSON */ }
  return { status: res.status, json };
}

test('two racks read together: the pair is kept on both scans and listed with them', async () => {
  assert.equal((await post('/api/pairs', 'dt_mandar', { aScanId: A, zScanId: AWAY })).status, 404, 'a scan the person may not see');
  assert.equal((await post('/api/pairs', 'dt_mandar', { aScanId: A, zScanId: A })).status, 400);
  const r = await post('/api/pairs', 'dt_mandar', { aScanId: A, zScanId: B });
  assert.equal(r.status, 200);
  assert.equal(r.json.aScanId, A);
  const list = await call('/api/scans', 'dt_mandar');
  assert.equal(list.status, 200);
  const byId = Object.fromEntries(list.json.scans.map((s) => [s.rackId, s]));
  assert.equal(byId[A].pair.with, B);
  assert.equal(byId[A].pair.side, 'a');
  assert.equal(byId[B].pair.with, A);
  assert.equal(byId[B].pair.side, 'z');
});
