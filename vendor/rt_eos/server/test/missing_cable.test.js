/**
 * Missing cable: the Excel dataset lists a cable on a port (at its A-end or
 * its Z-end) and the photo reads that port as empty. Every such port must be
 * flagged, with the cable label and where its other end goes, in the sheet
 * check, the scan detail, the flags list and both downloads.
 *
 * One rack, R05, with SW-EDGE-05 in U20, against the fixture dataset
 * test/fixtures/missing-cable-dataset.json:
 *   MC-101  port 1, A-end, plugged in        -> no flag
 *   MC-102  port 2, A-end, empty             -> Missing cable, to PP-CORE-01 port 8
 *   MC-103  port 4, Z-end, empty             -> Missing cable, to PP-CORE-01 port 9
 *   MC-104  port 5, A-end, could not tell    -> not a missing cable
 *   MC-105  port 3, but in rack R99          -> not this rack, no flag
 *   port 6  empty, no cable in the dataset   -> no flag
 * Everything runs on a throwaway data folder.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-missing-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'missing-cable-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');

const A = 'RK-MISS0A01';
const ids = {};
const users = {};
let server;
let base;

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

function ports(n, states) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const x = 20 + i * 30;
    out.push({ box: [x, 10, x + 24, 34], status: states[i + 1] || 'empty', class_name: 'RJ45', confidence: 0.8, index: i + 1 });
  }
  return out;
}

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('mc-org', 'Missing Org', 'active')").run().lastInsertRowid);
  ids.site = Number(db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)').run('mc-site', 'Missing Site', ids.org).lastInsertRowid);
  db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
              VALUES ('mc_tech@example.test', 'mc_tech', 'x', 1, 'member', ?, ?, 1)`).run(ids.org, ids.site);
  users.mc_tech = db.prepare('SELECT * FROM users WHERE username = ?').get('mc_tech');

  const p = store.newScan({ scanId: A, siteId: ids.site, at: '2026-09-29T08:00:00Z' });
  await sharp({ create: { width: 1000, height: 800, channels: 3, background: '#b0b0b0' } }).jpeg().toFile(p.photo);
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({
    units_detected: [],
    devices: [{ class_name: 'Switch', confidence: 0.9, box: [100, 50, 900, 130], units: ['u20'], port_count: 6,
      ports: ports(6, { 1: 'connected', 2: 'empty', 3: 'empty', 4: 'empty', 5: 'unknown', 6: 'empty' }) }],
  }));
  store.writeMeta(A, { scan_id: A, timestamp: '2026-09-29T08:00:00Z', username: 'mc_tech' });
  tenant.claimRack(ids.site, A, users.mc_tech.id);
  store.renameRackOfScan(A, 'R05');

  const dataset = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'missing-cable-dataset.json'), 'utf8'));
  const dir = path.join(store.siteDir(ids.org, ids.site), 'matrix');
  fs.mkdirSync(dir, { recursive: true });
  store.writeJsonAtomic(path.join(dir, 'cables.json'), dataset);

  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

async function get(p) {
  const res = await fetch(base + p, { headers: { Authorization: `Bearer ${auth.makeToken(users.mc_tech)}` } });
  const buf = Buffer.from(await res.arrayBuffer());
  let json = null;
  try { json = JSON.parse(buf.toString('utf8')); } catch { /* not JSON */ }
  return { status: res.status, json, text: buf.toString('utf8'), buf, type: res.headers.get('content-type') };
}

test('sheet check: an empty port the dataset has a cable on is a missing cable, at the A-end or the Z-end', async () => {
  const r = await get(`/api/scans/${A}/sheet-check`);
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.matrixImportId, 'mc0000000001');
  assert.deepEqual(r.json.units.map((u) => [u.name, u.status, u.matchedBy]), [['SW-EDGE-05', 'ok', 'unit']]);
  assert.deepEqual(r.json.missing, [
    { deviceId: 1, deviceName: 'SW-EDGE-05', portNo: 2, cableLabel: 'MC-102', cableType: 'Cat6 UTP', end: 'a',
      otherEnd: { device: 'PP-CORE-01', port: '8', rack: 'R09', unit: 2 } },
    { deviceId: 1, deviceName: 'SW-EDGE-05', portNo: 4, cableLabel: 'MC-103', cableType: 'Cat6 UTP', end: 'z',
      otherEnd: { device: 'PP-CORE-01', port: '9', rack: 'R09', unit: 2 } },
  ]);
  assert.deepEqual(r.json.unexpected, [], 'port 1 is plugged in and the dataset has MC-101 there');
});

test('detail: the same ports carry the missing cable status and the expected cable; the others do not', async () => {
  const r = await get(`/api/scans/${A}/detail`);
  assert.equal(r.status, 200, r.text);
  const sw = r.json.devices[0];
  const st = Object.fromEntries(sw.ports.map((p) => [p.portNo, p.status]));
  assert.deepEqual(st, { 1: 'not_found', 2: 'missing_cable', 3: 'empty', 4: 'missing_cable', 5: 'not_found', 6: 'empty' });
  const p4 = sw.ports.find((p) => p.portNo === 4);
  assert.equal(p4.expected.cableLabel, 'MC-103');
  assert.deepEqual(p4.expected.other, { end: 'a', device: 'PP-CORE-01', port: '9', rack: 'R09', unit: 2 });
  assert.deepEqual(p4.box, [210, 60, 234, 84], 'the port box on the rack photo, for the Overview ring');
  assert.deepEqual(sw.box, [100, 50, 900, 130]);
  assert.equal(r.json.flags.missingCable, 2);
});

test('flags: each missing cable with its label, its other end and a picture of the port', async () => {
  const r = await get(`/api/scans/${A}/flags`);
  assert.equal(r.status, 200, r.text);
  const missing = r.json.flags.filter((f) => f.kind === 'missing_cable');
  assert.deepEqual(missing.map((f) => [f.portNo, f.cableLabel, f.otherEnd.device, f.otherEnd.port]), [[2, 'MC-102', 'PP-CORE-01', '8'], [4, 'MC-103', 'PP-CORE-01', '9']]);
  assert.match(missing[0].cropUrl, /crops\/port-1-2\.jpg$/);
});

test('downloads: the CSV says Missing cable on those two ports, and the PDF is made', async () => {
  const csv = await get(`/api/scans/${A}/report.csv`);
  assert.equal(csv.status, 200);
  const lines = csv.text.replace(/^\ufeff/, '').trim().split('\r\n');
  assert.equal(lines.length, 1 + 6, 'one row per port');
  const flagged = lines.filter((l) => l.includes(',Missing cable,'));
  assert.deepEqual(flagged, [
    'R05,U20,SW-EDGE-05,Switch,,,2,RJ45,Empty,photo,,,,Missing cable,MC-102,R09 PP-CORE-01 8,ok,,,,,,',
    'R05,U20,SW-EDGE-05,Switch,,,4,RJ45,Empty,photo,,,,Missing cable,MC-103,R09 PP-CORE-01 9,ok,,,,,,',
  ]);
  const pdf = await get(`/api/scans/${A}/report.pdf`);
  assert.equal(pdf.status, 200);
  assert.equal(pdf.type, 'application/pdf');
  assert.equal(pdf.buf.subarray(0, 5).toString(), '%PDF-');
});
