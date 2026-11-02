/**
 * The physical-layer routes (routes/physical.js): zone close-ups, the sheet
 * check, uncapped ports, the A-end / Z-end label check, the worklist, device
 * cards, optics advice and reports. Each is tried by a caller who may and by
 * one who may not.
 *
 * Two racks at one site, R01 and R02, with a patching matrix of four cables.
 * The zone reader and the two public lookups are stubbed; everything else is
 * the shipped code on a throwaway data folder.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-physical-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'physical-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app, _internals } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const deviceLookup = require('../lib/device_lookup');

const A = 'RK-PHYS0A01';
const Z = 'RK-PHYS0Z01';
const AWAY = 'RK-PHYS0X01';
const ids = {};
const users = {};
let server;
let base;

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

// Ports in device-crop coordinates, 24 across in two rows of 12.
function ports(n, states) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const row = i % 2;
    const col = Math.floor(i / 2);
    const x = 20 + col * 30;
    const y = 10 + row * 30;
    out.push({ box: [x, y, x + 24, y + 24], status: states[i + 1] || 'empty', class_name: 'RJ45', confidence: 0.8, index: i + 1 });
  }
  return out;
}

async function makeScan(scanId, siteId, userId, devices, label) {
  const p = store.newScan({ scanId, siteId, at: '2026-09-29T08:00:00Z' });
  await sharp({ create: { width: 1000, height: 800, channels: 3, background: '#b0b0b0' } }).jpeg().toFile(p.photo);
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({ devices, units_detected: [] }));
  store.writeMeta(scanId, { scan_id: scanId, timestamp: '2026-09-29T08:00:00Z', username: 'hp_mandar' });
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
    import: { id: 'abc123abc123', file_name: 'm.xlsx', sheet_name: 'S', header_row: 0, uploaded_by: 'hp_barry', uploaded_at: '2026-09-29T07:00:00Z', row_count: 4, cable_count: 4, problem_count: 0 },
    mapping: {}, cables, problems: [],
  });
}

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('hp-org', 'Phys Org', 'active')").run().lastInsertRowid);
  ids.org2 = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('hp-org2', 'Other Org', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.site = Number(site.run('hp-site', 'Rack Build Facility', ids.org).lastInsertRowid);
  ids.site2 = Number(site.run('hp-site2', 'Other Site', ids.org2).lastInsertRowid);
  const add = (name, role, org, t) => {
    db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
                VALUES (?, ?, 'x', 1, ?, ?, ?, 1)`).run(`${name}@example.test`, name, role, org, t);
    users[name] = db.prepare('SELECT * FROM users WHERE username = ?').get(name);
  };
  add('hp_barry', 'site_manager', ids.org, ids.site);
  add('hp_mandar', 'member', ids.org, ids.site);
  add('hp_away', 'member', ids.org2, ids.site2);

  await makeScan(A, ids.site, users.hp_mandar.id, [
    { class_name: 'Switch', confidence: 0.91, box: [100, 50, 900, 130], units: ['u42'], port_count: 24,
      ports: ports(24, { 1: 'connected', 2: 'connected', 3: 'empty', 10: 'unknown' }),
      sfp_ports: [{ box: [700, 10, 730, 40], status: 'empty', class_name: 'SFP', confidence: 0.7 }, { box: [740, 10, 770, 40], status: 'empty', class_name: 'SFP', confidence: 0.7 }] },
    { class_name: 'Empty', confidence: 0.5, box: [100, 130, 900, 200], units: ['u41'], ports: [] },
    { class_name: 'Patch Panel', confidence: 0.5, box: [100, 200, 900, 270], units: ['u40'], port_count: 24, ports: ports(24, { 1: 'connected' }) },
  ], 'R01');
  await makeScan(Z, ids.site, users.hp_mandar.id, [
    { class_name: 'Patch Panel', confidence: 0.9, box: [100, 300, 900, 380], units: ['u30'], port_count: 24,
      ports: ports(24, { 1: 'connected', 2: 'connected', 3: 'empty', 4: 'connected', 12: 'unknown' }) },
  ], 'R02');
  await makeScan(AWAY, ids.site2, users.hp_away.id, [
    { class_name: 'Switch', confidence: 0.9, box: [100, 50, 900, 130], units: ['u10'], port_count: 24, ports: ports(24, {}) },
  ], 'X01');
  writeMatrix(ids.org, ids.site);

  // The zone reader: what the models would say about each close-up.
  _internals.setZoneReader(async ({ from }) => {
    const zoneOnA = from === 1 && reads.next === 'A';
    const labels = zoneOnA
      ? [{ text: 'FC-001', confidence: 0.95, portNo: 1, box: [10, 60, 60, 14] },
        { text: 'FC-002', confidence: 0.9, portNo: 5, box: [210, 60, 60, 14] },
        { text: 'ZZ-999', confidence: 0.4, portNo: 6, box: [260, 60, 60, 14] }]
      : [{ text: 'FC-001', confidence: 0.93, portNo: 1, box: [10, 60, 60, 14] },
        { text: 'FC-002', confidence: 0.88, portNo: 2, box: [60, 60, 60, 14] }];
    const st = zoneOnA ? { 1: 'connected', 2: 'connected', 3: 'empty', 4: 'empty', 5: 'connected', 6: 'connected' }
      : { 1: 'connected', 2: 'connected', 3: 'empty', 4: 'connected', 5: 'empty', 6: 'empty' };
    return {
      ok: true, image_size: { w: 400, h: 300 }, portsFrom: 'model', extraPorts: 0, labels,
      ports: [1, 2, 3, 4, 5, 6].map((n) => ({ portNo: from + n - 1, box: [n * 50, 10, 40, 40], state: st[n], sure: true })),
    };
  });
  deviceLookup.setRunner('specs', async (make, model) => (model === 'CRS326-24G-2S+RM'
    ? { ok: true, specs: { heightU: 1, portCount: 26, portTypes: [{ type: 'RJ45', count: 24 }, { type: 'SFP', count: 2 }], power: null, datasheetUrl: 'https://example.invalid/ds' } }
    : { ok: false, note: 'not in the base' }));

  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

const reads = { next: 'A' };

async function call(method, p, user, { json, form } = {}) {
  const headers = user ? { Authorization: `Bearer ${auth.makeToken(users[user])}` } : {};
  let body;
  if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  if (form) body = form;
  const res = await fetch(base + p, { method, headers, body });
  const buf = Buffer.from(await res.arrayBuffer());
  let j = null;
  try { j = JSON.parse(buf.toString('utf8')); } catch { /* not JSON */ }
  return { status: res.status, json: j, buf, type: res.headers.get('content-type') || '', text: buf.toString('utf8') };
}

async function zoneForm(deviceId, from, to) {
  const img = await sharp({ create: { width: 400, height: 300, channels: 3, background: '#808080' } }).jpeg().toBuffer();
  const f = new FormData();
  f.append('image', new Blob([img], { type: 'image/jpeg' }), 'close.jpg');
  f.append('deviceId', String(deviceId));
  f.append('from', String(from));
  f.append('to', String(to));
  return f;
}

let zoneA;

test('zones: suggested blocks per device with ports; strangers and the signed out are refused', async () => {
  assert.equal((await call('GET', `/api/scans/${A}/zones`)).status, 401);
  assert.equal((await call('GET', `/api/scans/${A}/zones`, 'hp_away')).status, 404);
  const r = await call('GET', `/api/scans/${A}/zones`, 'hp_mandar');
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.rackLabel, 'R01');
  assert.deepEqual(r.json.devices.map((d) => d.deviceId), [1, 3], 'the Empty row is not a device');
  const sw = r.json.devices[0];
  assert.equal(sw.name, 'SW-CORE-01', 'named from the sheet by its unit in R01');
  assert.equal(sw.unit, 'U42');
  assert.equal(sw.portCount, 24);
  assert.deepEqual(sw.zones.map((z) => [z.from, z.to, z.done]), [[1, 6, false], [7, 12, false], [13, 18, false], [19, 24, false]]);
  assert.equal(r.json.remaining, 8);
});

test('zones: a close-up is read, stored and served; bad ranges and strangers are refused', async () => {
  assert.equal((await call('POST', `/api/scans/${A}/zones`, 'hp_away', { form: await zoneForm(1, 1, 6) })).status, 404);
  assert.equal((await call('POST', `/api/scans/${A}/zones`, 'hp_mandar', { form: await zoneForm(1, 6, 1) })).status, 400);
  assert.equal((await call('POST', `/api/scans/${A}/zones`, 'hp_mandar', { form: await zoneForm(1, 20, 30) })).status, 400);
  assert.equal((await call('POST', `/api/scans/${A}/zones`, 'hp_mandar', { form: await zoneForm(2, 1, 6) })).status, 404, 'the Empty row takes no close-up');
  reads.next = 'A';
  const r = await call('POST', `/api/scans/${A}/zones`, 'hp_mandar', { form: await zoneForm(1, 1, 6) });
  assert.equal(r.status, 200, r.text);
  zoneA = r.json.zoneId;
  assert.match(zoneA, /^[a-f0-9]{12}$/);
  assert.equal(r.json.photoUrl, `/api/scans/${A}/files/zones/${zoneA}.jpg`);
  assert.deepEqual(r.json.labels.map((l) => [l.text, l.portNo]), [['FC-001', 1], ['FC-002', 5], ['ZZ-999', 6]]);
  assert.deepEqual(r.json.labels[0].box, [10, 60, 60, 14]);
  assert.equal(r.json.ports.length, 6);
  const photo = await call('GET', r.json.photoUrl, 'hp_mandar');
  assert.equal(photo.status, 200);
  assert.match(photo.type, /image\/jpeg/);
  assert.equal((await call('GET', r.json.photoUrl, 'hp_away')).status, 404);

  reads.next = 'Z';
  const rz = await call('POST', `/api/scans/${Z}/zones`, 'hp_mandar', { form: await zoneForm(1, 1, 6) });
  assert.equal(rz.status, 200, rz.text);

  const after = await call('GET', `/api/scans/${A}/zones`, 'hp_mandar');
  assert.deepEqual(after.json.devices[0].zones[0], { from: 1, to: 6, done: true, zoneId: zoneA });
  assert.equal(after.json.remaining, 7);
});

test('zones: a close-up can be removed, once', async () => {
  reads.next = 'A';
  const extra = await call('POST', `/api/scans/${A}/zones`, 'hp_mandar', { form: await zoneForm(1, 7, 12) });
  assert.equal(extra.status, 200);
  assert.equal((await call('DELETE', `/api/scans/${A}/zones/${extra.json.zoneId}`, 'hp_away')).status, 404);
  assert.equal((await call('DELETE', `/api/scans/${A}/zones/${extra.json.zoneId}`, 'hp_mandar')).status, 200);
  assert.equal((await call('DELETE', `/api/scans/${A}/zones/${extra.json.zoneId}`, 'hp_mandar')).status, 404);
  assert.equal((await call('DELETE', `/api/scans/${A}/zones/not-an-id`, 'hp_mandar')).status, 404);
});

test('sheet check: units, missing and unexpected cables, uncapped ports', async () => {
  assert.equal((await call('GET', `/api/scans/${A}/sheet-check`, 'hp_away')).status, 404);
  const bad = await call('POST', `/api/scans/${A}/ports/uncapped`, 'hp_mandar', { json: { deviceId: 1, portNo: 'x', uncapped: true } });
  assert.equal(bad.status, 400);
  assert.equal((await call('POST', `/api/scans/${A}/ports/uncapped`, 'hp_away', { json: { deviceId: 1, portNo: 4, uncapped: true } })).status, 404);
  const mark = await call('POST', `/api/scans/${A}/ports/uncapped`, 'hp_mandar', { json: { deviceId: 1, portNo: 4, uncapped: true } });
  assert.equal(mark.status, 200, mark.text);
  assert.equal(mark.json.uncapped.markedBy, 'hp_mandar');

  const r = await call('GET', `/api/scans/${A}/sheet-check`, 'hp_mandar');
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.rackLabel, 'R01');
  assert.equal(r.json.matrixImportId, 'abc123abc123');
  assert.deepEqual(r.json.units.map((u) => [u.deviceId, u.name, u.unitFound, u.unitExpected, u.status, u.matchedBy]),
    [[1, 'SW-CORE-01', 42, 42, 'ok', 'unit'], [3, 'PP-A01', 40, 40, 'ok', 'unit']]);
  assert.deepEqual(r.json.missing, [{ deviceId: 1, deviceName: 'SW-CORE-01', portNo: 3, cableLabel: 'FC-003', cableType: 'LC-LC OM4', end: 'a',
    otherEnd: { device: 'PP-B01', port: '3', rack: 'R02', unit: 30 } }]);
  assert.deepEqual(r.json.unexpected.map((u) => u.portNo), [5, 6]);
  assert.deepEqual(r.json.uncapped.map((u) => [u.deviceName, u.portNo, u.markedBy]), [['SW-CORE-01', 4, 'hp_mandar']]);

  const off = await call('POST', `/api/scans/${A}/ports/uncapped`, 'hp_mandar', { json: { deviceId: 'd1', portNo: 4, uncapped: false } });
  assert.equal(off.json.uncapped, null);
  assert.equal((await call('GET', `/api/scans/${A}/sheet-check`, 'hp_mandar')).json.uncapped.length, 0);
  await call('POST', `/api/scans/${A}/ports/uncapped`, 'hp_mandar', { json: { deviceId: 1, portNo: 4, uncapped: true } });
});

let checkId;

test('A-end / Z-end check: every status, the counts and the accuracy', async () => {
  assert.equal((await call('POST', '/api/checks', 'hp_away', { json: { aScanId: A, zScanId: Z } })).status, 404);
  assert.equal((await call('POST', '/api/checks', 'hp_barry', { json: { aScanId: A, zScanId: AWAY } })).status, 404);
  const r = await call('POST', '/api/checks', 'hp_barry', { json: { aScanId: A, zScanId: Z } });
  assert.equal(r.status, 200, r.text);
  checkId = r.json.id;
  assert.equal(r.json.aRack, 'R01');
  assert.equal(r.json.zRack, 'R02');
  assert.equal(r.json.by, 'hp_barry');
  assert.equal(r.json.matrixImportId, 'abc123abc123');
  const st = Object.fromEntries(r.json.cables.map((c) => [c.cableLabel, c.status]));
  assert.deepEqual(st, { 'FC-001': 'matched', 'FC-002': 'wrong_port', 'FC-003': 'missing_cable', 'FC-004': 'not_found', 'ZZ-999': 'not_in_sheet' });
  const fc2 = r.json.cables.find((c) => c.cableLabel === 'FC-002');
  assert.deepEqual(fc2.a.expected, { device: 'SW-CORE-01', port: 'Gi1/0/2', rack: 'R01' });
  assert.deepEqual(fc2.a.found, { device: 'SW-CORE-01', port: '5', source: 'close-up', read: 'FC-002', match: 'exact', box: null, side: 'a' });
  assert.equal(fc2.a.status, 'wrong_port');
  assert.equal(fc2.z.status, 'matched');
  assert.deepEqual(r.json.counts, { matched: 1, onTag: 0, wrongPort: 1, notInSheet: 1, notFound: 1, missingCable: 1, uncapped: 1, opticNoCap: 0, correctedReadings: 0, portFromTag: 0 });
  assert.deepEqual(r.json.accuracy, { labelsRead: 4, labelsExpected: 8, readRate: 0.5, unitPlacementOk: 3, unitPlacementTotal: 3 });
});

test('checks: listed and read by the site, exported, refused to strangers', async () => {
  const list = await call('GET', '/api/checks', 'hp_mandar');
  assert.equal(list.status, 200);
  assert.deepEqual(list.json.map((c) => [c.id, c.aRack, c.zRack, c.by]), [[checkId, 'R01', 'R02', 'hp_barry']]);
  assert.equal((await call('GET', '/api/checks', 'hp_away')).json.length, 0);
  assert.equal((await call('GET', `/api/checks/${checkId}`, 'hp_away')).status, 404);
  assert.equal((await call('GET', `/api/checks/${checkId}`)).status, 401);
  assert.equal((await call('GET', '/api/checks/zzz', 'hp_barry')).status, 404);
  const one = await call('GET', `/api/checks/${checkId}`, 'hp_mandar');
  assert.equal(one.status, 200);
  assert.equal(one.json.cables.length, 5);

  const csv = await call('GET', `/api/checks/${checkId}/export.csv`, 'hp_barry');
  assert.equal(csv.status, 200);
  assert.match(csv.type, /text\/csv/);
  const lines = csv.text.replace(/^\ufeff/, '').trim().split('\r\n');
  assert.equal(lines[0], 'Cable label,Connection ID,Cable ID,Cable status,End,Rack,Sheet device,Sheet port,Label expected,Found device,Found port,Found by,End status,Label read,How the label matched');
  assert.ok(lines.some((l) => l.startsWith('FC-002,,,wrong_port,A-end,R01,SW-CORE-01,Gi1/0/2,,SW-CORE-01,5,close-up,wrong_port')), lines.join('\n'));
  assert.equal((await call('GET', `/api/checks/${checkId}/export.csv`, 'hp_away')).status, 404);

  const pdf = await call('GET', `/api/checks/${checkId}/report.pdf`, 'hp_barry');
  assert.equal(pdf.status, 200);
  assert.equal(pdf.type, 'application/pdf');
  assert.equal(pdf.buf.subarray(0, 5).toString(), '%PDF-');
  assert.ok((pdf.buf.toString('latin1').match(/\/Type \/Page\b/g) || []).length >= 1, 'at least one page');
  assert.equal((await call('GET', `/api/checks/${checkId}/report.pdf`, 'hp_away')).status, 404);
});

test('a check needs the site matrix', async () => {
  // The other site has no matrix: its technician is told to import one.
  await makeScan('RK-PHYS0X02', ids.site2, users.hp_away.id, [{ class_name: 'Switch', confidence: 0.9, box: [1, 1, 50, 50], units: ['u9'], ports: [] }], 'X02');
  const r = await call('POST', '/api/checks', 'hp_away', { json: { aScanId: AWAY, zScanId: 'RK-PHYS0X02' } });
  assert.equal(r.status, 409);
  assert.match(r.json.error, /patching matrix/);
});

test('home: the worklist count and the checks card', async () => {
  const b = await call('GET', '/api/home', 'hp_barry');
  assert.equal(b.json.checks.count, 1);
  assert.deepEqual(b.json.checks.last, { id: checkId, aRack: 'R01', zRack: 'R02', matched: 1, problems: 4, at: b.json.checks.last.at });
  const w = await call('GET', '/api/worklist', 'hp_barry');
  assert.equal(b.json.worklist.count, w.json.items.length);
  const m = await call('GET', '/api/home', 'hp_mandar');
  assert.equal(m.json.checks, null);
  assert.ok(m.json.worklist.count > 0);
});

test('worklist: least sure first, with crops; strangers see nothing of it', async () => {
  const away = await call('GET', '/api/worklist', 'hp_away');
  assert.equal(away.status, 200);
  assert.ok(away.json.items.every((i) => !String(i.scanId).startsWith('RK-PHYS0A') && !String(i.scanId).startsWith('RK-PHYS0Z')));
  assert.equal((await call('GET', '/api/worklist')).status, 401);

  const r = await call('GET', '/api/worklist', 'hp_mandar');
  assert.equal(r.status, 200);
  const ids2 = r.json.items.map((i) => i.id);
  assert.ok(ids2.includes(`${A}.d3`), ids2.join(' '));
  assert.ok(ids2.includes(`${A}.p1-10`));
  assert.ok(ids2.includes(`${Z}.p1-12`));
  assert.ok(ids2.includes(`${A}.l${zoneA}-2`), 'the label read at 40 per cent');
  const conf = r.json.items.map((i) => i.confidence);
  assert.deepEqual(conf, [...conf].sort((x, y) => x - y));
  const dev = r.json.items.find((i) => i.id === `${A}.d3`);
  assert.equal(dev.kind, 'device');
  assert.equal(dev.current, 'Patch Panel');
  assert.ok(dev.options.includes('Switch'));
  assert.match(dev.cropUrl, /^\/api\/scans\/RK-PHYS0A01\/files\/crops\/wl-d3\.jpg$/);
  assert.equal((await call('GET', dev.cropUrl, 'hp_mandar')).status, 200);

  const only = await call('GET', `/api/worklist?scanId=${Z}`, 'hp_mandar');
  assert.ok(only.json.items.every((i) => i.scanId === Z));
});

test('worklist: confirm and correct write corrections; the item is then done', async () => {
  assert.equal((await call('POST', `/api/worklist/${A}.d3`, 'hp_away', { json: { action: 'confirm' } })).status, 404);
  assert.equal((await call('POST', `/api/worklist/${A}.d3`, 'hp_mandar', { json: { action: 'shrug' } })).status, 400);
  assert.equal((await call('POST', '/api/worklist/nonsense', 'hp_mandar', { json: { action: 'confirm' } })).status, 404);

  const c = await call('POST', `/api/worklist/${A}.d3`, 'hp_mandar', { json: { action: 'confirm' } });
  assert.equal(c.status, 200, c.text);
  assert.equal(c.json.ok, true);
  assert.ok(c.json.next && c.json.next.id);
  const rows = store.readCorrections(A);
  const dev = rows.find((x) => x.kind === 'device');
  assert.equal(dev.feedback_type, 'device', 'the same row the results screen writes');
  assert.equal(dev.is_correct, true);
  assert.equal(dev.device_index, 3);
  assert.equal(dev.user, 'hp_mandar');
  assert.equal((await call('POST', `/api/worklist/${A}.d3`, 'hp_mandar', { json: { action: 'confirm' } })).status, 409);

  const p = await call('POST', `/api/worklist/${A}.p1-10`, 'hp_mandar', { json: { action: 'correct', value: 'connected', uncapped: false } });
  assert.equal(p.status, 200, p.text);
  const ps = store.readCorrections(A).find((x) => x.kind === 'port_state');
  assert.deepEqual(ps.after, { state: 'connected' });

  const up = await call('POST', `/api/worklist/${Z}.p1-12`, 'hp_mandar', { json: { action: 'confirm', uncapped: true } });
  assert.equal(up.status, 200, up.text);
  const sc = await call('GET', `/api/scans/${Z}/sheet-check`, 'hp_mandar');
  assert.deepEqual(sc.json.uncapped.map((u) => [u.deviceName, u.portNo]), [['PP-B01', 12]]);

  const l = await call('POST', `/api/worklist/${A}.l${zoneA}-2`, 'hp_mandar', { json: { action: 'correct', value: 'FC-099' } });
  assert.equal(l.status, 200, l.text);
  const zone = store.readJson(path.join(store.file(A, 'zones'), `${zoneA}.json`));
  assert.equal(zone.labels[2].text, 'FC-099');
  assert.equal(zone.labels[2].confirmed, true);

  const left = (await call('GET', '/api/worklist', 'hp_mandar')).json.items.map((i) => i.id);
  for (const gone of [`${A}.d3`, `${A}.p1-10`, `${Z}.p1-12`, `${A}.l${zoneA}-2`]) assert.ok(!left.includes(gone), gone);
});

test('worklist: skip hides an item from that person only', async () => {
  const before = (await call('GET', '/api/worklist', 'hp_mandar')).json.items;
  if (!before.length) return;
  const id = before[0].id;
  assert.equal((await call('POST', `/api/worklist/${id}`, 'hp_mandar', { json: { action: 'skip' } })).status, 200);
  assert.ok(!(await call('GET', '/api/worklist', 'hp_mandar')).json.items.some((i) => i.id === id));
  assert.ok((await call('GET', '/api/worklist', 'hp_barry')).json.items.some((i) => i.id === id));
});

test('device card: looking first, then found; no model, strangers and unknown devices', async () => {
  // Give the switch a model, as a corrected reading would.
  const doc = store.readResult(A) || {};
  store.writeResult(A, { ...doc, api: { devices: [{ make: 'MikroTik', model: 'CRS326-24G-2S+RM' }, {}, {}] } });
  assert.equal((await call('GET', `/api/scans/${A}/devices/1`, 'hp_away')).status, 404);
  assert.equal((await call('GET', `/api/scans/${A}/devices/2`, 'hp_mandar')).status, 404, 'the Empty row is no device');
  assert.equal((await call('GET', `/api/scans/${A}/devices/99`, 'hp_mandar')).status, 404);
  const first = await call('GET', `/api/scans/${A}/devices/1`, 'hp_mandar');
  assert.equal(first.status, 200, first.text);
  assert.equal(first.json.specsStatus, 'looking');
  await deviceLookup.settle('specs', 'MikroTik', 'CRS326-24G-2S+RM');
  const r = await call('GET', `/api/scans/${A}/devices/d1`, 'hp_mandar');
  assert.equal(r.json.deviceId, 1);
  assert.equal(r.json.type, 'Switch');
  assert.equal(r.json.unitTop, 42);
  assert.equal(r.json.heightU, 1);
  assert.equal(r.json.specsStatus, 'found');
  assert.deepEqual(r.json.specs.portTypes, [{ type: 'RJ45', count: 24 }, { type: 'SFP', count: 2 }]);
  assert.equal(r.json.ports.length, 26);
  assert.deepEqual(r.json.ports[0], { portNo: 1, type: 'RJ45', state: 'connected', cable: null, uncapped: false, opticNoCap: false });
  // Where each detail came from: the kind and the unit from the photo, with
  // the device box's score; make and model as text read off the photo.
  assert.equal(r.json.fields.type.source, 'photo');
  assert.equal(r.json.fields.unit.source, 'photo');
  assert.equal(typeof r.json.fields.type.confidence, 'number');
  assert.equal(r.json.fields.make.source, 'photo_text');
  assert.equal(r.json.lookupsOff, false);
  assert.match(r.json.cropUrl, /crops\/device-1\.jpg$/);

  // A patch panel: specifications are for switching gear only.
  const pp = await call('GET', `/api/scans/${A}/devices/3`, 'hp_mandar');
  assert.equal(pp.json.switchGear, false);
  assert.equal(pp.json.specsStatus, 'not_switch');
});

test('device lookups by make and model', async () => {
  assert.equal((await call('GET', '/api/devices/specs?make=MikroTik&model=CRS326-24G-2S%2BRM')).status, 401);
  const s = await call('GET', '/api/devices/specs?make=MikroTik&model=CRS326-24G-2S%2BRM', 'hp_mandar');
  assert.equal(s.json.specsStatus, 'found');
  assert.equal((await call('GET', '/api/devices/specs?make=X', 'hp_mandar')).json.specsStatus, 'no_model');
  const miss = await call('GET', '/api/devices/specs?make=Acme&model=NOPE-1', 'hp_mandar');
  assert.equal(miss.json.specsStatus, 'looking');
  await deviceLookup.settle('specs', 'Acme', 'NOPE-1');
  assert.equal((await call('GET', '/api/devices/specs?make=Acme&model=NOPE-1', 'hp_mandar')).json.specsStatus, 'not_found');
});

test('optics advice follows the sheet cable type', async () => {
  assert.equal((await call('GET', `/api/scans/${A}/sfp-advice`, 'hp_away')).status, 404);
  const r = await call('GET', `/api/scans/${A}/sfp-advice`, 'hp_mandar');
  assert.equal(r.status, 200);
  const sw = r.json.items.find((i) => i.deviceName === 'SW-CORE-01' && i.portType === 'SFP');
  assert.equal(sw.count, 2);
  assert.equal(sw.suggestion.cable, 'LC-LC OM4');
  assert.match(sw.suggestion.optic, /SFP\+ 10 Gbps: 10GBASE-SR/, 'the model says 10G, the sheet says multimode');
  // Optics for switching gear only: never a patch panel's ports.
  const kinds = new Set(r.json.items.map((i) => i.deviceId));
  assert.ok(!kinds.has(3), 'the patch panel has no optics advice');
});

test('rack report: a PDF of several pages and one CSV row per port', async () => {
  assert.equal((await call('GET', `/api/scans/${A}/report.pdf`, 'hp_away')).status, 404);
  assert.equal((await call('GET', `/api/scans/${A}/report.csv`)).status, 401);
  const pdf = await call('GET', `/api/scans/${A}/report.pdf`, 'hp_mandar');
  assert.equal(pdf.status, 200);
  assert.equal(pdf.type, 'application/pdf');
  assert.equal(pdf.buf.subarray(0, 5).toString(), '%PDF-');
  assert.ok((pdf.buf.toString('latin1').match(/\/Type \/Page\b/g) || []).length >= 1, 'at least one page');
  const csv = await call('GET', `/api/scans/${A}/report.csv`, 'hp_mandar');
  assert.equal(csv.status, 200);
  const lines = csv.text.replace(/^\ufeff/, '').trim().split('\r\n');
  assert.match(lines[0], /^Rack,Unit,Device,Kind,Make,Model,Port,/);
  assert.equal(lines.length, 1 + 26 + 24);
  assert.equal(lines[0], 'Rack,Unit,Device,Kind,Make,Model,Port,Port type,State,State from,Cable kind,Cable colour,Label read,Flag,Sheet cable,Sheet other end,Sheet unit check,Uncapped,Optic fitted without cap,Sheet port type,Port type check,Sheet occupied,Sheet dust cap');
  assert.ok(lines.includes('R01,U42,SW-CORE-01,Switch,MikroTik,CRS326-24G-2S+RM,1,RJ45,Connected,close-up,,,FC-001,,FC-001,R02 PP-B01 1,ok,,,,,,'), lines.slice(0, 4).join('\n'));
  assert.ok(lines.includes('R01,U42,SW-CORE-01,Switch,MikroTik,CRS326-24G-2S+RM,3,RJ45,Empty,close-up,,,,Missing cable,FC-003,R02 PP-B01 3,ok,,,,,,'), lines.slice(0, 5).join('\n'));
});

test('deleting pilot data takes the checks with it', () => {
  const dir = store.checksDir(ids.org, ids.site);
  assert.ok(fs.readdirSync(dir).length >= 1);
  store.deleteOrgData(ids.org);
  assert.ok(!fs.existsSync(dir));
});

test('a scan is deleted by a site manager, not by a member who did not take it, and a stranger cannot see it', async () => {
  const D = 'RK-PHYS0D01';
  await makeScan(D, ids.site, users.hp_mandar.id, []);
  assert.equal((await call('DELETE', `/api/scans/${D}`, 'hp_away')).status, 404);
  const member = await call('DELETE', `/api/scans/${D}`, 'hp_mandar');
  assert.equal(member.status, 403);
  assert.ok(store.has(D));
  const manager = await call('DELETE', `/api/scans/${D}`, 'hp_barry');
  assert.equal(manager.status, 200);
  assert.equal(manager.json.scanId, D);
  assert.ok(!store.has(D));
  assert.equal((await call('DELETE', `/api/scans/${D}`, 'hp_barry')).status, 404);
});
