/**
 * Organization data settings and deleting pilot data:
 *   GET/PUT /api/orgs/:orgId/data-settings, POST /api/orgs/:orgId/delete-pilot-data,
 * the daily retention job, and "never train" keeping corrections out of the
 * training store.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-orgdata-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'orgdata-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app, _internals } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const orgSettings = require('../lib/org_settings');

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

const ids = {};
const users = {};
let server;
let port;

function scan(scanId, siteId, at) {
  const p = store.newScan({ scanId, siteId, at });
  fs.writeFileSync(p.photo, Buffer.from([0xff, 0xd8, 0xff, 0xd9]));
  store.writeMeta(scanId, { scan_id: scanId, timestamp: at });
  tenant.claimRack(siteId, scanId, null);
}

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('od-org', 'EOS Pilot Org', 'active')").run().lastInsertRowid);
  ids.org2 = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('od-org2', 'Other Org', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.site = Number(site.run('od-site', 'Hall', ids.org).lastInsertRowid);
  ids.site2 = Number(site.run('od-site2', 'Hall', ids.org2).lastInsertRowid);
  const add = (name, role, org, t) => {
    db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
                VALUES (?, ?, 'x', 1, ?, ?, ?, 1)`).run(`${name}@example.test`, name, role, org, t);
    users[name] = db.prepare('SELECT * FROM users WHERE username = ?').get(name);
  };
  add('od_owner', 'owner', null, null);
  add('od_admin', 'org_admin', ids.org, ids.site);
  add('od_manager', 'site_manager', ids.org, ids.site);
  add('od_admin2', 'org_admin', ids.org2, ids.site2);

  scan('RK-ORGD0001', ids.site, new Date(Date.now() - 40 * 86400000).toISOString());
  scan('RK-ORGD0002', ids.site, new Date(Date.now() - 1 * 86400000).toISOString());
  scan('RK-ORGD0009', ids.site2, new Date(Date.now() - 40 * 86400000).toISOString());
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { port = server.address().port; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

function call(method, p, user, json) {
  const headers = user ? { Authorization: `Bearer ${auth.makeToken(users[user])}` } : {};
  let body = null;
  if (json !== undefined) { body = Buffer.from(JSON.stringify(json)); headers['Content-Type'] = 'application/json'; headers['Content-Length'] = body.length; }
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: p, method, headers }, (res) => {
      let out = '';
      res.on('data', (c) => { out += c; });
      res.on('end', () => { let j = null; try { j = JSON.parse(out); } catch { /* not JSON */ } resolve({ status: res.statusCode, json: j, body: out }); });
    });
    req.on('error', reject);
    req.end(body || undefined);
  });
}

test('data settings start off, and the org admin changes them', async () => {
  const g = await call('GET', `/api/orgs/${ids.org}/data-settings`, 'od_admin');
  assert.equal(g.status, 200, g.body);
  assert.deepEqual(g.json, { neverTrain: false, retentionDays: null, onlineLookups: true });
  const p = await call('PUT', `/api/orgs/${ids.org}/data-settings`, 'od_admin', { neverTrain: true, retentionDays: 30 });
  assert.equal(p.status, 200, p.body);
  assert.deepEqual(p.json, { neverTrain: true, retentionDays: 30, onlineLookups: true });
  assert.deepEqual((await call('GET', `/api/orgs/${ids.org}/data-settings`, 'od_owner')).json, { neverTrain: true, retentionDays: 30, onlineLookups: true });
  // Online lookups off, and on again; nothing else moves.
  const off = await call('PUT', `/api/orgs/${ids.org}/data-settings`, 'od_admin', { onlineLookups: false });
  assert.deepEqual(off.json, { neverTrain: true, retentionDays: 30, onlineLookups: false });
  assert.equal(orgSettings.onlineLookups(ids.org), false);
  assert.equal(orgSettings.onlineLookups(ids.org2), true);
  assert.equal((await call('PUT', `/api/orgs/${ids.org}/data-settings`, 'od_admin', { onlineLookups: 'no' })).status, 400);
  assert.equal((await call('PUT', `/api/orgs/${ids.org}/data-settings`, 'od_admin', { onlineLookups: true })).json.onlineLookups, true);
});

test('a new organization starts with never train on, 90 days kept and lookups on', () => {
  const org = auth.createOrganization('Defaults Org');
  assert.deepEqual(orgSettings.get(org.id), { neverTrain: true, retentionDays: 90, onlineLookups: true });
  process.env.RT_DEFAULT_RETENTION_DAYS = '45';
  try {
    const org2 = auth.createOrganization('Defaults Org Two');
    assert.equal(orgSettings.get(org2.id).retentionDays, 45);
  } finally { delete process.env.RT_DEFAULT_RETENTION_DAYS; }
});

test('bad values are refused; only the owner and the org admin may', async () => {
  assert.equal((await call('PUT', `/api/orgs/${ids.org}/data-settings`, 'od_admin', { retentionDays: -3 })).status, 400);
  assert.equal((await call('PUT', `/api/orgs/${ids.org}/data-settings`, 'od_admin', { neverTrain: 'yes' })).status, 400);
  assert.equal((await call('GET', `/api/orgs/${ids.org}/data-settings`, 'od_manager')).status, 403);
  assert.equal((await call('GET', `/api/orgs/${ids.org}/data-settings`, 'od_admin2')).status, 404);
  assert.equal((await call('GET', `/api/orgs/${ids.org}/data-settings`)).status, 401);
});

test('never train: a correction is not written to the training store', () => {
  assert.equal(orgSettings.neverTrain(ids.org), true);
  assert.equal(orgSettings.neverTrain(ids.org2), false);
});

test('the daily retention job deletes scans older than the setting, and only that organization\'s', () => {
  const out = _internals.runDataRetention();
  assert.equal(out.deletedScans, 1);
  assert.equal(store.has('RK-ORGD0001'), false);
  assert.equal(store.has('RK-ORGD0002'), true, 'a newer scan stays');
  assert.equal(store.has('RK-ORGD0009'), true, 'another organization without retention keeps its scans');
});

test('delete pilot data: a wrong confirmation deletes nothing', async () => {
  const r = await call('POST', `/api/orgs/${ids.org}/delete-pilot-data`, 'od_admin', { confirm: 'eos pilot org' });
  assert.equal(r.status, 400);
  assert.equal(store.has('RK-ORGD0002'), true);
  assert.equal((await call('POST', `/api/orgs/${ids.org}/delete-pilot-data`, 'od_manager', { confirm: 'EOS Pilot Org' })).status, 403);
});

test('delete pilot data: every scan and file of the organization, not the accounts', async () => {
  const matrixDir = path.join(store.siteDir(ids.org, ids.site), 'matrix');
  fs.mkdirSync(matrixDir, { recursive: true });
  fs.writeFileSync(path.join(matrixDir, 'cables.json'), '{"import":null}');
  const r = await call('POST', `/api/orgs/${ids.org}/delete-pilot-data`, 'od_admin', { confirm: 'EOS Pilot Org' });
  assert.equal(r.status, 200, r.body);
  assert.equal(r.json.deletedScans, 1);
  assert.ok(r.json.deletedFiles >= 3);
  assert.equal(store.listScans({ orgId: ids.org }).length, 0);
  assert.ok(!fs.existsSync(matrixDir));
  assert.equal(auth.db.prepare('SELECT COUNT(*) AS n FROM rack_owners WHERE rack_id = ?').get('RK-ORGD0002').n, 0);
  assert.ok(auth.db.prepare('SELECT id FROM users WHERE username = ?').get('od_manager'), 'accounts stay');
  assert.equal(store.has('RK-ORGD0009'), true, 'another organization is untouched');
});

test('default worker count follows free memory', () => {
  assert.equal(_internals.defaultWorkerCount(16 * 1024 ** 3), 2);
  assert.equal(_internals.defaultWorkerCount(8 * 1024 ** 3), 2);
  assert.equal(_internals.defaultWorkerCount(4 * 1024 ** 3), 1);
});
