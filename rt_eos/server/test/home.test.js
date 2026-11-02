/**
 * GET /api/home and GET /api/sites/:siteId/overview.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-home-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'home-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

const ids = {};
const users = {};
let server;
let port;

function scan(scanId, siteId, userId, at) {
  const p = store.newScan({ scanId, siteId, at });
  fs.writeFileSync(p.photo, Buffer.from([0xff, 0xd8, 0xff, 0xd9]));
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({ devices: [{}, {}, {}], units_detected: [] }));
  store.writeMeta(scanId, { scan_id: scanId, timestamp: at });
  tenant.claimRack(siteId, scanId, userId);
}

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('hm-org', 'Home Org', 'active')").run().lastInsertRowid);
  ids.org2 = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('hm-org2', 'Away Org', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.site = Number(site.run('hm-site', 'Rack Build Facility', ids.org).lastInsertRowid);
  ids.site2 = Number(site.run('hm-site2', 'Away Site', ids.org2).lastInsertRowid);
  const add = (name, role, org, t) => {
    db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
                VALUES (?, ?, 'x', 1, ?, ?, ?, 1)`).run(`${name}@example.test`, name, role, org, t);
    users[name] = db.prepare('SELECT * FROM users WHERE username = ?').get(name);
  };
  add('hm_barry', 'site_manager', ids.org, ids.site);
  add('hm_mandar', 'member', ids.org, ids.site);
  add('hm_jaya', 'member', ids.org, ids.site);
  add('hm_away', 'member', ids.org2, ids.site2);

  scan('RK-HOME0001', ids.site, users.hm_mandar.id, '2026-09-28T09:00:00Z');
  scan('RK-HOME0002', ids.site, users.hm_jaya.id, '2026-09-28T10:00:00Z');
  scan('RK-HOME0003', ids.site, users.hm_mandar.id, '2026-09-28T11:00:00Z');
  scan('RK-HOME0009', ids.site2, users.hm_away.id, '2026-09-28T12:00:00Z');
  store.renameRackOfScan('RK-HOME0001', 'R01');
  store.renameRackOfScan('RK-HOME0003', 'R01');
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { port = server.address().port; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

function get(p, user) {
  const headers = user ? { Authorization: `Bearer ${auth.makeToken(users[user])}` } : {};
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port, path: p, headers }, (res) => {
      let out = '';
      res.on('data', (c) => { out += c; });
      res.on('end', () => { let j = null; try { j = JSON.parse(out); } catch { /* not JSON */ } resolve({ status: res.statusCode, json: j, body: out }); });
    }).on('error', reject);
  });
}

test('home for a site manager: the whole site, the matrix card and checks', async () => {
  const r = await get('/api/home', 'hm_barry');
  assert.equal(r.status, 200, r.body);
  assert.equal(r.json.role, 'site_manager');
  assert.deepEqual(r.json.site, { id: ids.site, name: 'Rack Build Facility' });
  assert.deepEqual(r.json.recent.map((s) => s.scanId), ['RK-HOME0003', 'RK-HOME0002', 'RK-HOME0001']);
  const first = r.json.recent[0];
  assert.equal(first.rackLabel, 'R01');
  assert.equal(first.takenAt, '2026-09-28T11:00:00Z');
  assert.equal(first.thumbUrl, '/api/scans/RK-HOME0003/files/thumb.jpg');
  assert.equal(first.deviceCount, 3);
  assert.deepEqual(r.json.worklist, { count: 0 });
  assert.equal(r.json.matrix, null, 'no matrix imported yet');
  assert.deepEqual(r.json.checks, { count: 0, last: null });
});

test('home for a technician: only his own scans, no manager cards', async () => {
  const r = await get('/api/home', 'hm_mandar');
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.recent.map((s) => s.scanId), ['RK-HOME0003', 'RK-HOME0001']);
  assert.equal(r.json.matrix, null);
  assert.equal(r.json.checks, null);
  assert.equal((await get('/api/home')).status, 401);
});

test('site overview: one row per rack with its newest scan, and the people', async () => {
  const r = await get(`/api/sites/${ids.site}/overview`, 'hm_mandar');
  assert.equal(r.status, 200, r.body);
  assert.equal(r.json.site.name, 'Rack Build Facility');
  assert.deepEqual(r.json.racks.map((x) => [x.rackLabel, x.scanId]), [['R01', 'RK-HOME0003'], [null, 'RK-HOME0002']]);
  assert.equal(r.json.racks[0].thumbUrl, '/api/scans/RK-HOME0003/files/thumb.jpg');
  assert.deepEqual(r.json.people.map((p) => p.username), ['hm_barry', 'hm_jaya', 'hm_mandar']);
  assert.deepEqual(Object.keys(r.json.people[0]).sort(), ['id', 'name', 'role', 'username']);
});

test('a site the caller cannot see is not found', async () => {
  assert.equal((await get(`/api/sites/${ids.site}/overview`, 'hm_away')).status, 404);
  assert.equal((await get('/api/sites/999999/overview', 'hm_barry')).status, 404);
  assert.equal((await get('/api/sites/abc/overview', 'hm_barry')).status, 400);
});
