/**
 * Renaming and removing one Site of an organisation.
 *
 * The owner asked for both on 22 Sep 2026: setup could add a Site and fill it
 * in, but never change its name or take one away, so a Site made by mistake
 * stayed for good. A rename touches the name only - the slug is what older
 * rows, output folders and remembered choices point at. A removal is refused
 * while anything still stands on the Site, and says what, because that is the
 * answer somebody clearing up actually needs.
 */
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

after(() => { setImmediate(() => process.exit(0)); });
process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-for-site-edit';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');
const auth = require('../auth');

function seedOwner() {
  const db = auth.db;
  const existing = db.prepare('SELECT * FROM users WHERE username = ?').get('site-edit-test-owner');
  if (existing) return existing;
  const tenantId = db.prepare("SELECT id FROM tenants WHERE slug = 'default'").get()?.id
                ?? db.prepare('SELECT id FROM tenants ORDER BY id LIMIT 1').get()?.id;
  db.prepare(`INSERT INTO users (email, username, password_hash, role, tenant_id, active)
              VALUES (?, ?, ?, 'owner', ?, 1)`)
    .run('site-edit-test-owner@example.com', 'site-edit-test-owner', 'x', tenantId);
  return db.prepare('SELECT * FROM users WHERE username = ?').get('site-edit-test-owner');
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

test('a Site is renamed without losing what points at it, and is only removed once nothing stands on it', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const token = auth.makeToken(seedOwner());
  const stamp = Date.now();

  const created = await call(port, token, 'POST', '/api/orgs', {
    name: `Site Edit ${stamp}`, adminUsername: `se.admin.${stamp}`, adminEmail: `se.admin.${stamp}@example.test`, adminPassword: 'SiteEdit@2026!',
  });
  assert.equal(created.status, 200, created.raw);
  const orgId = created.json.organization.id;

  const made = await call(port, token, 'POST', `/api/orgs/${orgId}/sites`, { name: 'Old Name' });
  assert.equal(made.status, 200, made.raw);
  const tid = made.json.site.id;
  const slug = auth.db.prepare('SELECT slug FROM tenants WHERE id = ?').get(tid).slug;

  // Renaming says the new name and keeps the slug, which is what older rows point at.
  const renamed = await call(port, token, 'PATCH', `/api/orgs/${orgId}/sites/${tid}`, { name: 'New Name' });
  assert.equal(renamed.status, 200, renamed.raw);
  assert.equal(renamed.json.site.name, 'New Name');
  const row = auth.db.prepare('SELECT name, slug FROM tenants WHERE id = ?').get(tid);
  assert.equal(row.name, 'New Name');
  assert.equal(row.slug, slug, 'the slug is left alone');
  assert.equal((await call(port, token, 'GET', `/api/setup/${tid}`)).json.tenant.name, 'New Name');

  // A name is required, and a Site of another organisation is not this one's to touch.
  assert.equal((await call(port, token, 'PATCH', `/api/orgs/${orgId}/sites/${tid}`, { name: '  ' })).status, 400);
  assert.equal((await call(port, token, 'PATCH', `/api/orgs/${orgId}/sites/999999`, { name: 'x' })).status, 404);

  // A Site that still holds something is refused, in words that say what.
  const sp = await call(port, token, 'POST', `/api/setup/${tid}/spaces`, { name: 'Hall 1', kind: 'hall', rack_count: 2 });
  assert.equal(sp.status, 201, sp.raw);
  const refused = await call(port, token, 'DELETE', `/api/orgs/${orgId}/sites/${tid}`);
  assert.equal(refused.status, 409, refused.raw);
  assert.match(refused.json.error, /New Name still holds .*space/);
  assert.equal(refused.json.holds.spaces, 1);
  assert.equal(auth.db.prepare('SELECT COUNT(*) AS n FROM tenants WHERE id = ?').get(tid).n, 1, 'nothing was removed');

  // Cleared, it goes, and its own settings rows go with it.
  assert.equal((await call(port, token, 'DELETE', `/api/setup/${tid}/spaces/${sp.json.space.id}`)).status, 200);
  assert.equal((await call(port, token, 'PUT', `/api/setup/${tid}/rules`, { accepted: true })).status, 200);
  assert.equal((await call(port, token, 'PUT', `/api/setup/${tid}/profile/facility`, { city: 'Reading' })).status, 200);
  const gone = await call(port, token, 'DELETE', `/api/orgs/${orgId}/sites/${tid}`);
  assert.equal(gone.status, 200, gone.raw);
  assert.equal(gone.json.removed, true);
  assert.equal(auth.db.prepare('SELECT COUNT(*) AS n FROM tenants WHERE id = ?').get(tid).n, 0, 'the Site is gone');
  assert.equal(auth.db.prepare('SELECT COUNT(*) AS n FROM tenant_rules WHERE tenant_id = ?').get(tid).n, 0);
  assert.equal(auth.db.prepare('SELECT COUNT(*) AS n FROM tenant_profile WHERE tenant_id = ?').get(tid).n, 0);

  // The people of a Site hold it too: that Site cannot go while they are on it.
  const other = await call(port, token, 'POST', `/api/orgs/${orgId}/sites`, { name: 'Has People' });
  const otherId = other.json.site.id;
  assert.equal((await call(port, token, 'POST', `/api/sites/${otherId}/members`, {
    username: `se.tech.${stamp}`, email: `se.tech.${stamp}@example.test`, password: 'SiteEdit@2026!', role: 'member',
  })).status, 200);
  const held = await call(port, token, 'DELETE', `/api/orgs/${orgId}/sites/${otherId}`);
  assert.equal(held.status, 409, held.raw);
  assert.match(held.json.error, /Has People still holds 1 person/);

  await call(port, token, 'DELETE', `/api/orgs/${orgId}`);
});
