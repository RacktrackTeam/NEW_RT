/**
 * What a site manager can see of his own Site, end to end through the real
 * app on a throwaway database (28 Sep 2026).
 *
 * The proof run of the EOS pilot found three gaps, held shut here:
 *   1. GET /api/scans as a site manager was [] although his technicians had
 *      scanned racks at his Site; the scan's result opened, the history did
 *      not list it. A member must still see only his own scans.
 *   2. GET /api/orgs/:id/members was 403 for him. He may now read the people
 *      of his own Site, and nobody else's.
 *   3. GET /api/scan-sites said rackCount 0 after a scan at the Site, because
 *      it counted only racks entered in the Site's setup.
 *
 * Both directions are asserted for each: the right person gets in, and the
 * wrong one does not.
 */

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const DB_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-site-manager-'));
process.env.RACKTRACK_AUTH_DB = path.join(DB_DIR, 'auth.db');
process.env.LOG_DB_PATH = ':memory:';
process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.JWT_SECRET = 'test-secret-for-site-manager-access';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');
const auth = require('../auth');
const tenant = require('../lib/tenant');
const estate = require('../lib/estate');

after(() => { setImmediate(() => process.exit(0)); });

const store = require('../lib/store');
const tag = (Date.now() % 1e8).toString(36).toUpperCase().padStart(6, '0');
const RACK = {
  s1a: `RK-SMA${tag}`,   // scanned at Site 1 by tech1
  s1b: `RK-SMB${tag}`,   // scanned at Site 1 by tech2
  s2:  `RK-SMC${tag}`,   // scanned at Site 2 (same organization) by tech3
  b1:  `RK-SMD${tag}`,   // scanned at another organization's Site
};

const ids = {};

before(() => {
  const db = auth.db;
  const org = db.prepare("INSERT INTO organizations (slug, name, status) VALUES (?, ?, 'active')");
  ids.orgA = Number(org.run(`sm-a-${tag}`.toLowerCase(), 'Org A').lastInsertRowid);
  ids.orgB = Number(org.run(`sm-b-${tag}`.toLowerCase(), 'Org B').lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.s1 = Number(site.run(`sm-s1-${tag}`.toLowerCase(), 'Site One', ids.orgA).lastInsertRowid);
  ids.s2 = Number(site.run(`sm-s2-${tag}`.toLowerCase(), 'Site Two', ids.orgA).lastInsertRowid);
  ids.b1 = Number(site.run(`sm-b1-${tag}`.toLowerCase(), 'B Site', ids.orgB).lastInsertRowid);
  const defaultTenant = db.prepare("SELECT id FROM tenants WHERE slug = 'default'").get().id;

  const user = db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
                           VALUES (?, ?, 'x', 1, ?, ?, ?, 1)`);
  const add = (name, role, orgId, tenantId) => {
    ids[name] = Number(user.run(`${name}.${tag}@example.test`.toLowerCase(), `${name}_${tag}`.toLowerCase(), role, orgId, tenantId).lastInsertRowid);
  };
  add('owner', 'owner', null, defaultTenant);
  add('admin', 'org_admin', ids.orgA, null);
  add('barry', 'site_manager', ids.orgA, ids.s1);
  add('tech1', 'member', ids.orgA, ids.s1);
  add('tech2', 'member', ids.orgA, ids.s1);
  add('tech3', 'member', ids.orgA, ids.s2);
  add('mgrB', 'site_manager', ids.orgB, ids.b1);

  tenant.claimRack(ids.s1, RACK.s1a, ids.tech1);
  tenant.claimRack(ids.s1, RACK.s1b, ids.tech2);
  tenant.claimRack(ids.s2, RACK.s2, ids.tech3);
  tenant.claimRack(ids.b1, RACK.b1, ids.mgrB);

  const siteOf = { [RACK.s1a]: ids.s1, [RACK.s1b]: ids.s1, [RACK.s2]: ids.s2, [RACK.b1]: ids.b1 };
  for (const rackId of Object.values(RACK)) {
    const p = store.newScan({ scanId: rackId, siteId: siteOf[rackId] });
    store.writeMeta(rackId, { timestamp: new Date().toISOString() });
    fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({ devices: [], units_detected: [] }));
    store.writeResult(rackId, { scan_id: rackId, api: { rackId, devices: [] } });
  }
});

after(() => {
  for (const rackId of Object.values(RACK)) {
    try { store.deleteScan(rackId); } catch { /* best effort */ }
  }
  try { fs.rmSync(DB_DIR, { recursive: true, force: true }); } catch { /* best effort */ }
});

// ── Harness ─────────────────────────────────────────────────────────

function listen() {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

function getAs(port, who, p) {
  const row = auth.db.prepare('SELECT * FROM users WHERE id = ?').get(ids[who]);
  const token = auth.makeToken(row);
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: p, headers: { Authorization: `Bearer ${token}` } }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => {
        let body = null;
        try { body = JSON.parse(raw); } catch { body = raw; }
        resolve({ status: res.statusCode, body });
      });
    });
    req.on('error', reject);
  });
}

async function withServer(t) {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  return port;
}

const ours = (list, key = 'rackId') => new Set((list || []).map((x) => x[key]).filter((id) => Object.values(RACK).includes(id)));

// ── 1. Scan history ─────────────────────────────────────────────────

test('a site manager sees every scan taken at his Site, and no other Site', async (t) => {
  const port = await withServer(t);
  const r = await getAs(port, 'barry', '/api/scans');
  assert.equal(r.status, 200);
  assert.deepEqual(ours(r.body.scans), new Set([RACK.s1a, RACK.s1b]),
    'both technicians\' scans at Site One, nothing from Site Two or Org B');
});

test('a site manager can open those scans, and not a sibling Site\'s', async (t) => {
  const port = await withServer(t);
  for (const rackId of [RACK.s1a, RACK.s1b]) {
    assert.equal((await getAs(port, 'barry', `/api/scan/${rackId}/result`)).status, 200, `${rackId} result`);
    assert.equal((await getAs(port, 'barry', `/api/scan/${rackId}`)).status, 200, `${rackId} detail`);
  }
  assert.equal((await getAs(port, 'barry', `/api/scan/${RACK.s2}/result`)).status, 404);
  assert.equal((await getAs(port, 'barry', `/api/scan/${RACK.b1}/result`)).status, 404);
});

test('a member still sees only his own scans', async (t) => {
  const port = await withServer(t);
  const r1 = await getAs(port, 'tech1', '/api/scans');
  assert.equal(r1.status, 200);
  assert.deepEqual(ours(r1.body.scans), new Set([RACK.s1a]));
  const r2 = await getAs(port, 'tech2', '/api/scans');
  assert.deepEqual(ours(r2.body.scans), new Set([RACK.s1b]));
});

test('an org admin sees every Site of his organization, and the owner sees all', async (t) => {
  const port = await withServer(t);
  const a = await getAs(port, 'admin', '/api/scans');
  assert.equal(a.status, 200);
  assert.deepEqual(ours(a.body.scans), new Set([RACK.s1a, RACK.s1b, RACK.s2]));
  const o = await getAs(port, 'owner', '/api/scans');
  assert.deepEqual(ours(o.body.scans), new Set(Object.values(RACK)));
});

test('GET /api/racks follows the same rule', async (t) => {
  const port = await withServer(t);
  assert.deepEqual(ours((await getAs(port, 'barry', '/api/racks')).body.racks), new Set([RACK.s1a, RACK.s1b]));
  assert.deepEqual(ours((await getAs(port, 'admin', '/api/racks')).body.racks), new Set([RACK.s1a, RACK.s1b, RACK.s2]));
  assert.deepEqual(ours((await getAs(port, 'mgrB', '/api/racks')).body.racks), new Set([RACK.b1]));
});

// ── 2. The member list ──────────────────────────────────────────────

test('a site manager can list the people of his own Site', async (t) => {
  const port = await withServer(t);
  const r = await getAs(port, 'barry', `/api/orgs/${ids.orgA}/members`);
  assert.equal(r.status, 200);
  const got = new Set(r.body.members.map((m) => m.id));
  assert.deepEqual(got, new Set([ids.barry, ids.tech1, ids.tech2]),
    'himself and his two technicians; not Site Two, not the org admin');
  assert.ok(r.body.members.every((m) => m.tenant_id === ids.s1));
});

test('a site manager cannot list another organization, and a member cannot list at all', async (t) => {
  const port = await withServer(t);
  assert.equal((await getAs(port, 'barry', `/api/orgs/${ids.orgB}/members`)).status, 403);
  assert.equal((await getAs(port, 'tech1', `/api/orgs/${ids.orgA}/members`)).status, 403);
});

test('an org admin still lists his whole organization', async (t) => {
  const port = await withServer(t);
  const r = await getAs(port, 'admin', `/api/orgs/${ids.orgA}/members`);
  assert.equal(r.status, 200);
  const got = new Set(r.body.members.map((m) => m.id));
  for (const who of ['admin', 'barry', 'tech1', 'tech2', 'tech3']) assert.ok(got.has(ids[who]), who);
  assert.ok(!got.has(ids.mgrB));
});

// ── 3. The Site's rack count ────────────────────────────────────────

test('the scan screen counts the racks scanned at a Site', async (t) => {
  const port = await withServer(t);
  const r = await getAs(port, 'tech1', '/api/scan-sites');
  assert.equal(r.status, 200);
  const site = r.body.sites.find((s) => s.id === ids.s1);
  assert.ok(site, 'Site One is offered');
  assert.equal(site.rackCount, 2);
  assert.deepEqual(ours(site.racks), new Set([RACK.s1a, RACK.s1b]));
  // The racks of Site Two are not Site One's.
  assert.ok(!site.racks.some((x) => x.rackId === RACK.s2));
});

test('a rack both scanned and entered in setup counts once, under its setup row', async (t) => {
  estate.upsertRack(ids.s1, { rack_id: RACK.s1a, name: 'Rack One', source: 'typed' }, ids.barry);
  const port = await withServer(t);
  const r = await getAs(port, 'barry', '/api/scan-sites');
  const site = r.body.sites.find((s) => s.id === ids.s1);
  assert.equal(site.rackCount, 2);
  const one = site.racks.filter((x) => x.rackId === RACK.s1a);
  assert.equal(one.length, 1);
  assert.equal(one[0].name, 'Rack One');
  assert.ok(one[0].id, 'the setup row keeps its id for the pick-a-rack list');
});

// ── 5. The organization list names who runs it ─────────────────────

test('/api/orgs names the site manager when an organization has no org admin', async (t) => {
  const port = await withServer(t);
  const r = await getAs(port, 'owner', '/api/orgs');
  assert.equal(r.status, 200);
  const a = r.body.organizations.find((o) => o.id === ids.orgA);
  const b = r.body.organizations.find((o) => o.id === ids.orgB);
  assert.equal(a.admin_role, 'org_admin', 'an org admin comes first');
  assert.equal(b.admin_role, 'site_manager');
  assert.equal(b.admin_username, `mgrB_${tag}`.toLowerCase());
});
