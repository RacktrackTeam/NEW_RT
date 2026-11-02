/**
 * Removing an organisation that has been set up.
 *
 * The setup tables (spaces, known racks, rules, profile sections) and the
 * approver on a Site reference tenants and users, and foreign keys are ON.
 * On 15 Sep 2026 the owner's Remove failed with "FOREIGN KEY constraint
 * failed" (a 500) once an organisation had any setup data, and the
 * transaction rolled back, so nothing was removed. The delete now purges the
 * setup rows first.
 */
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

after(() => { setImmediate(() => process.exit(0)); });
process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-for-org-remove';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');
const auth = require('../auth');

function seedOwner() {
  const db = auth.db;
  const existing = db.prepare('SELECT * FROM users WHERE username = ?').get('org-remove-test-owner');
  if (existing) return existing;
  const tenantId = db.prepare(`SELECT id FROM tenants WHERE slug = 'default'`).get()?.id
                ?? db.prepare('SELECT id FROM tenants ORDER BY id LIMIT 1').get()?.id;
  db.prepare(`INSERT INTO users (email, username, password_hash, role, tenant_id, active)
              VALUES (?, ?, ?, 'owner', ?, 1)`)
    .run('org-remove-test-owner@example.com', 'org-remove-test-owner', 'x', tenantId);
  return db.prepare('SELECT * FROM users WHERE username = ?').get('org-remove-test-owner');
}

function listen() {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}
function call(port, token, method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const req = http.request({
      host: '127.0.0.1', port, method, path: p,
      headers: {
        authorization: `Bearer ${token}`, 'x-client-platform': 'native',
        ...(data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => { let json = null; try { json = JSON.parse(raw); } catch { /* not json */ } resolve({ status: res.statusCode, json, raw }); });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

test('an organisation with a set-up Site is removed whole: org, Site, members, spaces, rules, profile', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const token = auth.makeToken(seedOwner());
  const stamp = Date.now();

  const created = await call(port, token, 'POST', '/api/orgs', {
    name: `Remove Test ${stamp}`, adminUsername: `rm.admin.${stamp}`, adminEmail: `rm.admin.${stamp}@example.test`, adminPassword: 'Remove@2026!',
  });
  assert.equal(created.status, 200, created.raw);
  const orgId = created.json.organization.id;
  const site = await call(port, token, 'POST', `/api/orgs/${orgId}/sites`, { name: 'Remove Site' });
  assert.equal(site.status, 200, site.raw);
  const tid = site.json.site.id;
  const adminId = auth.db.prepare('SELECT id FROM users WHERE username = ?').get(`rm.admin.${stamp}`).id;

  // Every kind of setup row that references the Site or a member.
  assert.equal((await call(port, token, 'PUT', `/api/setup/${tid}/datacentre`, { address: '1 Test Road', timezone: 'Europe/London' })).status, 200);
  const sp = await call(port, token, 'POST', `/api/setup/${tid}/spaces`, { name: 'Hall 1', kind: 'hall', rack_count: 4 });
  assert.equal(sp.status, 201, sp.raw);
  assert.equal((await call(port, token, 'POST', `/api/setup/${tid}/spaces/${sp.json.space.id}/racks`, { rack_id: 'RK-RMTEST001', name: 'R01' })).status, 201);
  assert.equal((await call(port, token, 'PUT', `/api/setup/${tid}/approver`, { user_id: adminId })).status, 200);
  assert.equal((await call(port, token, 'PUT', `/api/setup/${tid}/rules`, { accepted: true })).status, 200);
  assert.equal((await call(port, token, 'PUT', `/api/setup/${tid}/profile/facility`, { city: 'Reading', country: 'GB' })).status, 200);
  assert.equal((await call(port, token, 'PUT', `/api/setup/org/${orgId}/profile`, { short_code: `RM${String(stamp).slice(-6)}`, timezone: 'Europe/London', country: 'GB' })).status, 200);

  // The admin has signed in, so a refresh token and a social-free session row exist for them.
  const signin = await call(port, '', 'POST', '/api/auth/login', { username: `rm.admin.${stamp}`, password: 'Remove@2026!' });
  assert.equal(signin.status, 200, signin.raw);
  if (auth.db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='refresh_tokens'").get()) {
    assert.ok(auth.db.prepare('SELECT COUNT(*) AS n FROM refresh_tokens WHERE user_id = ?').get(adminId).n >= 1, 'a refresh token exists for the admin');
  }

  const removed = await call(port, token, 'DELETE', `/api/orgs/${orgId}`);
  assert.equal(removed.status, 200, removed.raw);
  assert.equal(removed.json.removed.sites, 1);
  assert.ok(removed.json.removed.members >= 1);

  const db = auth.db;
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM organizations WHERE id = ?').get(orgId).n, 0, 'organisation gone');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM tenants WHERE organization_id = ?').get(orgId).n, 0, 'Site gone');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM users WHERE organization_id = ?').get(orgId).n, 0, 'members gone');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM spaces WHERE tenant_id = ?').get(tid).n, 0, 'spaces gone');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM racks_known WHERE tenant_id = ?').get(tid).n, 0, 'known racks gone');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM tenant_rules WHERE tenant_id = ?').get(tid).n, 0, 'rules gone');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM tenant_profile WHERE tenant_id = ?').get(tid).n, 0, 'profile gone');
});
