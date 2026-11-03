/**
 * Organisation setup, against the real estate module, the real router, and a
 * seeded database.
 *
 * Same shape as tenant_isolation.test.js: a throwaway auth.db is seeded before
 * the modules load, and every rule is asserted in BOTH directions - the right
 * people get in, the wrong people do not, and completeness moves from all
 * false to canScan true only when all three mandatory facts hold.
 *
 * The router is mounted behind a stub that sets req.user from a header, the
 * way app.js mounts it behind auth.requireAuth. auth.js opens its database at
 * a fixed path and cannot be pointed at this fixture; the mount itself (401
 * for an anonymous caller through the real gate) is covered by
 * setup_mount.test.js.
 */

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const Database = require('better-sqlite3');

const DB_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-setup-'));
const DB_PATH = path.join(DB_DIR, 'auth.db');
process.env.RACKTRACK_AUTH_DB = DB_PATH;

// Two organisations, three Sites. Org A has an admin with no Site of their
// own, a site manager and a member on A1, and a member on A2. Org B has an
// admin and a member on B1. The owner oversees everything.
const ORG_A = 10, ORG_B = 20;
const SITE_A1 = 11, SITE_A2 = 12, SITE_B1 = 21;
const OWNER = 1, ADMIN_A = 2, MGR_A1 = 3, MEMBER_A1 = 4, ADMIN_B = 5, MEMBER_B1 = 6, MEMBER_A2 = 7;
const RACK_A1 = 'RK-AAAA1111', RACK_B1 = 'RK-BBBB1111';

before(() => {
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.exec(`
    CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active');
    CREATE TABLE tenants (id INTEGER PRIMARY KEY, slug TEXT, name TEXT, organization_id INTEGER);
    CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, role TEXT,
                        tenant_id INTEGER, organization_id INTEGER, active INTEGER DEFAULT 1);
    CREATE TABLE rack_owners (rack_id TEXT, tenant_id INTEGER, created_by INTEGER,
                              created_at TEXT DEFAULT (datetime('now')),
                              PRIMARY KEY (tenant_id, rack_id));
  `);
  db.prepare('INSERT INTO organizations (id,name) VALUES (?,?)').run(ORG_A, 'Org A');
  db.prepare('INSERT INTO organizations (id,name) VALUES (?,?)').run(ORG_B, 'Org B');
  const t = db.prepare('INSERT INTO tenants (id,slug,name,organization_id) VALUES (?,?,?,?)');
  t.run(SITE_A1, 'a1', 'A Site One', ORG_A);
  t.run(SITE_A2, 'a2', 'A Site Two', ORG_A);
  t.run(SITE_B1, 'b1', 'B Site One', ORG_B);
  const u = db.prepare('INSERT INTO users (id,username,email,role,tenant_id,organization_id) VALUES (?,?,?,?,?,?)');
  u.run(OWNER, 'owner', 'owner@example.test', 'owner', null, null);
  u.run(ADMIN_A, 'admin_a', 'admin_a@example.test', 'org_admin', null, ORG_A);
  u.run(MGR_A1, 'mgr_a1', 'mgr_a1@example.test', 'site_manager', SITE_A1, ORG_A);
  u.run(MEMBER_A1, 'member_a1', 'member_a1@example.test', 'member', SITE_A1, ORG_A);
  u.run(ADMIN_B, 'admin_b', 'admin_b@example.test', 'org_admin', SITE_B1, ORG_B);
  u.run(MEMBER_B1, 'member_b1', 'member_b1@example.test', 'member', SITE_B1, ORG_B);
  u.run(MEMBER_A2, 'member_a2', 'member_a2@example.test', 'member', SITE_A2, ORG_A);
  const r = db.prepare('INSERT INTO rack_owners (rack_id,tenant_id,created_by) VALUES (?,?,?)');
  r.run(RACK_A1, SITE_A1, MEMBER_A1);
  r.run(RACK_B1, SITE_B1, MEMBER_B1);
  db.close();
});

after(() => { try { fs.rmSync(DB_DIR, { recursive: true, force: true }); } catch { /* best effort */ } });

// ── Harness ─────────────────────────────────────────────────────────
const estate = require('../lib/estate');
const express = require('express');

const app = express();
app.use(express.json());
// Stands in for auth.requireAuth: the seeded user row named by x-user, else 401.
app.use('/api/setup', (req, res, next) => {
  const id = Number(req.headers['x-user'] || 0);
  if (!id) return res.status(401).json({ error: 'Authentication required' });
  const db = new Database(DB_PATH, { readonly: true });
  req.user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  db.close();
  if (!req.user) return res.status(401).json({ error: 'User no longer exists' });
  next();
}, require('../routes/setup'));

let server, port;
before(() => new Promise((resolve) => {
  server = app.listen(0, '127.0.0.1', () => { port = server.address().port; resolve(); });
}));
after(() => server && server.close());

function call(method, urlPath, { user, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const req = http.request({
      host: '127.0.0.1', port, method, path: urlPath,
      headers: {
        ...(user ? { 'x-user': String(user) } : {}),
        ...(data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(raw); } catch { /* not JSON */ }
        resolve({ status: res.statusCode, json, raw });
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

const A1 = `/api/setup/${SITE_A1}`;

// ── Access ──────────────────────────────────────────────────────────
test('an anonymous caller is refused before any Site is looked at', async () => {
  assert.equal((await call('GET', `${A1}`)).status, 401);
  assert.equal((await call('GET', '/api/setup/state')).status, 401);
});

test('a fresh Site reports every mandatory fact false and cannot scan', async () => {
  const r = await call('GET', A1, { user: ADMIN_A });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.completeness.mandatory, { location: false, approver: false, rules: false });
  assert.equal(r.json.completeness.canScan, false);
  assert.deepEqual(r.json.completeness.counts, { spaces: 0, racksTyped: 0, racksKnown: 0 });
  assert.deepEqual(r.json.spaces, []);
  assert.equal(r.json.approver, null);
  assert.equal(r.json.rules.accepted_at, null);
  assert.equal(r.json.tenant.id, SITE_A1);
});

test('role matrix: who may read and write a Site\'s setup', async () => {
  // Writers.
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: ADMIN_A, body: { timezone: 'Europe/London' } })).status, 200,
    'the org admin of the Site\'s organisation writes');
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: MGR_A1, body: { address: '1 Rack Row' } })).status, 200,
    'the site manager of the Site writes');
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: OWNER, body: { lat: 51.5, lng: -0.12 } })).status, 200,
    'the platform owner writes');
  // A member reads and no more.
  assert.equal((await call('GET', A1, { user: MEMBER_A1 })).status, 200, 'a member of the Site reads');
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: MEMBER_A1, body: { address: 'x' } })).status, 403,
    'a member of the Site cannot write');
  assert.equal((await call('POST', `${A1}/spaces`, { user: MEMBER_A1, body: { name: 'Hall' } })).status, 403);
  // Strangers get 404, never 403: a 403 would confirm the Site exists.
  for (const user of [ADMIN_B, MEMBER_B1, MEMBER_A2]) {
    const r = await call('GET', A1, { user });
    assert.equal(r.status, 404, `user ${user} must not learn Site ${SITE_A1} exists (got ${r.status})`);
    assert.equal((await call('PUT', `${A1}/datacentre`, { user, body: { address: 'x' } })).status, 404);
  }
  // A Site that is not there at all looks the same as one you may not see.
  assert.equal((await call('GET', '/api/setup/9999', { user: OWNER })).status, 404);
  assert.equal((await call('GET', '/api/setup/abc', { user: OWNER })).status, 400);

  // What the writers wrote is there.
  const r = await call('GET', A1, { user: MEMBER_A1 });
  assert.equal(r.json.datacentre.timezone, 'Europe/London');
  assert.equal(r.json.datacentre.address, '1 Rack Row');
  assert.equal(r.json.datacentre.lat, 51.5);
  assert.equal(r.json.datacentre.lng, -0.12);
});

test('datacentre fields are validated, and nothing is geocoded', async () => {
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: ADMIN_A, body: { timezone: 'Mars/Olympus' } })).status, 400);
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: ADMIN_A, body: { lat: 91 } })).status, 400);
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: ADMIN_A, body: { lng: -181 } })).status, 400);
  assert.equal((await call('PUT', `${A1}/datacentre`, { user: ADMIN_A, body: {} })).status, 400, 'nothing to update');
  // An address alone leaves the coordinates exactly as they were.
  const r = await call('PUT', `${A1}/datacentre`, { user: ADMIN_A, body: { address: '2 Rack Row' } });
  assert.equal(r.json.datacentre.lat, 51.5);
});

// ── Spaces ──────────────────────────────────────────────────────────
let hall, rowA;
test('spaces nest, refuse duplicates at one level, and cannot be moved beneath themselves', async () => {
  let r = await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'Hall 1', rack_count: 10, facility_id: 'H1' } });
  assert.equal(r.status, 201);
  hall = r.json.space;
  assert.equal(hall.tenant_id, SITE_A1);
  assert.equal(hall.parent_id, null);
  assert.equal(hall.source, 'typed');
  assert.equal(hall.created_by, MGR_A1);
  assert.ok(hall.created_at, 'every row says when it was created');

  r = await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'Row A', parent_id: hall.id } });
  assert.equal(r.status, 201);
  rowA = r.json.space;
  assert.equal(rowA.parent_id, hall.id);

  // Same name, same level - refused. Same name under a different parent - fine.
  assert.equal((await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'hall 1' } })).status, 409,
    'names compare case-insensitively at one level');
  r = await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'Hall 1', parent_id: hall.id } });
  assert.equal(r.status, 201, 'the same name under a different parent is a different space');
  const nested = r.json.space;

  // The tree comes back nested.
  r = await call('GET', A1, { user: MEMBER_A1 });
  assert.equal(r.json.spaces.length, 1, 'one root');
  assert.equal(r.json.spaces[0].id, hall.id);
  const kids = r.json.spaces[0].children.map((c) => c.id).sort((a, b) => a - b);
  assert.deepEqual(kids, [rowA.id, nested.id].sort((a, b) => a - b));

  // Rename, and refuse a cycle.
  r = await call('PUT', `${A1}/spaces/${rowA.id}`, { user: ADMIN_A, body: { name: 'Row A1' } });
  assert.equal(r.status, 200);
  assert.equal(r.json.space.name, 'Row A1');
  assert.equal((await call('PUT', `${A1}/spaces/${hall.id}`, { user: ADMIN_A, body: { parent_id: rowA.id } })).status, 400,
    'a space cannot be moved beneath its own child');
  assert.equal((await call('PUT', `${A1}/spaces/${hall.id}`, { user: ADMIN_A, body: { parent_id: hall.id } })).status, 400,
    'nor beneath itself');

  // Bad input.
  assert.equal((await call('POST', `${A1}/spaces`, { user: MGR_A1, body: {} })).status, 400, 'name is required');
  assert.equal((await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'X', rack_count: -1 } })).status, 400);
  assert.equal((await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'X', source: 'guessed' } })).status, 400);

  // A space of another Site is not a valid parent, and cannot be edited from here.
  const b = await call('POST', `/api/setup/${SITE_B1}/spaces`, { user: ADMIN_B, body: { name: 'B Hall' } });
  assert.equal(b.status, 201);
  assert.equal((await call('POST', `${A1}/spaces`, { user: MGR_A1, body: { name: 'Y', parent_id: b.json.space.id } })).status, 404);
  assert.equal((await call('PUT', `${A1}/spaces/${b.json.space.id}`, { user: ADMIN_A, body: { name: 'Z' } })).status, 404);

  // Leaves delete; the empty nested duplicate goes.
  assert.equal((await call('DELETE', `${A1}/spaces/${nested.id}`, { user: ADMIN_A })).status, 200);
});

test('a space holding racks or child spaces refuses to be deleted', async () => {
  // Hall 1 has Row A1 inside it.
  let r = await call('DELETE', `${A1}/spaces/${hall.id}`, { user: ADMIN_A });
  assert.equal(r.status, 409, 'child spaces block deletion');

  // Record a rack in Row A1, then try to delete the row.
  r = await call('POST', `${A1}/spaces/${rowA.id}/racks`, { user: MGR_A1, body: { rack_id: RACK_A1, name: 'R01' } });
  assert.equal(r.status, 201);
  r = await call('DELETE', `${A1}/spaces/${rowA.id}`, { user: ADMIN_A });
  assert.equal(r.status, 409, 'a known rack blocks deletion');
  assert.match(r.json.error, /rack/i);
});

// ── Racks ───────────────────────────────────────────────────────────
test('racks are upserted by rack_id: the second call updates, never duplicates', async () => {
  let r = await call('POST', `${A1}/spaces/${rowA.id}/racks`, { user: MGR_A1, body: { rack_id: RACK_A1, u_height: 45 } });
  assert.equal(r.status, 200, 'already known → 200, not 201');
  assert.equal(r.json.created, false);
  assert.equal(r.json.rack.name, 'R01', 'a binding that names nothing keeps the typed name');
  assert.equal(r.json.rack.u_height, 45);

  const rows = estate.listRacks(SITE_A1);
  assert.equal(rows.length, 1, 'one row for one rack id');
  assert.equal(rows[0].rack_id, RACK_A1);
  assert.equal(rows[0].created_by, MGR_A1);

  // Moving the rack to another space is the same call with a different space.
  r = await call('POST', `${A1}/spaces/${hall.id}/racks`, { user: MGR_A1, body: { rack_id: RACK_A1 } });
  assert.equal(r.status, 200);
  assert.equal(r.json.rack.space_id, hall.id);
  assert.equal(estate.listRacks(SITE_A1).length, 1);
  // ...and back.
  await call('POST', `${A1}/spaces/${rowA.id}/racks`, { user: MGR_A1, body: { rack_id: RACK_A1 } });

  // Shape is checked: a rack id is RK- plus a hash, nothing else.
  assert.equal((await call('POST', `${A1}/spaces/${rowA.id}/racks`, { user: MGR_A1, body: { rack_id: '../../etc' } })).status, 400);
  assert.equal((await call('POST', `${A1}/spaces/${rowA.id}/racks`, { user: MGR_A1, body: {} })).status, 400);
  // The same rack id in ANOTHER Site is that Site's own row - ids are scoped.
  const b1 = await call('GET', `/api/setup/${SITE_B1}`, { user: ADMIN_B });
  const bHall = b1.json.spaces[0];
  r = await call('POST', `/api/setup/${SITE_B1}/spaces/${bHall.id}/racks`, { user: ADMIN_B, body: { rack_id: RACK_A1 } });
  assert.equal(r.status, 201);
  assert.equal(r.json.created, true);
  assert.equal(estate.listRacks(SITE_A1).length, 1, 'Site A1 still has exactly its one row');
});

test('candidates lists the racks known in a space with the count the admin typed', async () => {
  let r = await call('GET', `${A1}/candidates?spaceId=${rowA.id}`, { user: MEMBER_A1 });
  assert.equal(r.status, 200);
  assert.equal(r.json.count, 1);
  assert.equal(r.json.racks[0].rack_id, RACK_A1);
  assert.equal(r.json.space.id, rowA.id);
  assert.equal(r.json.expected, null, 'no rack_count was typed for the row');

  r = await call('GET', `${A1}/candidates?spaceId=${hall.id}`, { user: MEMBER_A1 });
  assert.equal(r.json.count, 0);
  assert.equal(r.json.expected, 10);

  r = await call('GET', `${A1}/candidates`, { user: MEMBER_A1 });
  assert.equal(r.json.count, 1, 'no spaceId → every rack the Site knows');
  assert.equal(r.json.space, null);

  // Another Site's space is not there.
  const b1 = await call('GET', `/api/setup/${SITE_B1}`, { user: ADMIN_B });
  assert.equal((await call('GET', `${A1}/candidates?spaceId=${b1.json.spaces[0].id}`, { user: MEMBER_A1 })).status, 404);
  assert.equal((await call('GET', `${A1}/candidates?spaceId=abc`, { user: MEMBER_A1 })).status, 400);
});

// ── Completeness ────────────────────────────────────────────────────
test('completeness moves to canScan only when all three mandatory facts hold', async () => {
  // Location is already true: Hall 1 has rack_count 10 and a rack is known.
  let c = estate.completeness(SITE_A1);
  assert.deepEqual(c.mandatory, { location: true, approver: false, rules: false });
  assert.equal(c.canScan, false);
  assert.equal(c.counts.spaces, 2);
  assert.equal(c.counts.racksTyped, 10);
  assert.equal(c.counts.racksKnown, 1);
  assert.equal(estate.getTenant(SITE_A1).setup_completed_at, null);

  // Approver: only a member of the Site or an admin of its organisation.
  assert.equal((await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { user_id: MEMBER_B1 } })).status, 400,
    'a user from another organisation cannot approve here');
  assert.equal((await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { user_id: MEMBER_A2 } })).status, 400,
    'a member of a sibling Site cannot approve here');
  assert.equal((await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: {} })).status, 400);
  assert.equal((await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { user_id: MEMBER_A1, email: 'x@y.z' } })).status, 400,
    'one or the other, not both');
  assert.equal((await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { email: 'not-an-email' } })).status, 400);
  let r = await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { user_id: ADMIN_A } });
  assert.equal(r.status, 200, 'the org admin of the Site\'s organisation may be the approver');
  r = await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { user_id: MEMBER_A1 } });
  assert.equal(r.status, 200);
  assert.equal(r.json.approver.user_id, MEMBER_A1);
  assert.equal(r.json.approver.email, 'member_a1@example.test');
  assert.deepEqual(r.json.completeness.mandatory, { location: true, approver: true, rules: false });
  assert.equal(r.json.completeness.canScan, false);

  // Rules must be accepted, with the two product rules not negotiable.
  assert.equal((await call('PUT', `${A1}/rules`, { user: ADMIN_A, body: { accepted: false } })).status, 400);
  assert.equal((await call('PUT', `${A1}/rules`, { user: ADMIN_A, body: { accepted: true, photo_retention_days: 0 } })).status, 400);
  r = await call('PUT', `${A1}/rules`, { user: MGR_A1, body: {
    accepted: true, photo_retention_days: 30, u_from_bottom: false,
    approve_before_write: false, never_delete: false,
  } });
  assert.equal(r.status, 200);
  assert.equal(r.json.rules.photo_retention_days, 30);
  assert.equal(r.json.rules.default_u_height, 42, 'untouched fields keep their default');
  assert.equal(r.json.rules.u_from_bottom, false);
  assert.equal(r.json.rules.approve_before_write, true, 'always on, whatever the body said');
  assert.equal(r.json.rules.never_delete, true);
  assert.ok(r.json.rules.accepted_at);
  assert.equal(r.json.rules.accepted_by, MGR_A1);

  // All three hold.
  c = r.json.completeness;
  assert.deepEqual(c.mandatory, { location: true, approver: true, rules: true });
  assert.equal(c.canScan, true);
  assert.ok(estate.getTenant(SITE_A1).setup_completed_at, 'stamped the first time the Site may scan');

  // Accepting again keeps the earlier fields and re-stamps the acceptance.
  r = await call('PUT', `${A1}/rules`, { user: ADMIN_A, body: { accepted: true, default_u_height: 48 } });
  assert.equal(r.json.rules.photo_retention_days, 30);
  assert.equal(r.json.rules.default_u_height, 48);
  assert.equal(r.json.rules.accepted_by, ADMIN_A);

  // An email approver who is not a user yet also satisfies the fact.
  r = await call('PUT', `${A1}/approver`, { user: ADMIN_A, body: { email: 'Approver@Example.test' } });
  assert.equal(r.json.approver.user_id, null);
  assert.equal(r.json.approver.email, 'approver@example.test');
  assert.equal(r.json.completeness.mandatory.approver, true);
});

test('optional flags stay false until a profile section is filled', () => {
  const a1 = estate.completeness(SITE_A1);
  // Filled sections flip these; setup_profile.test.js covers that side.
  assert.deepEqual(a1.optional, { conventions: false, vendors: false, people: false });
  assert.deepEqual(estate.completeness(SITE_A2).optional, { conventions: false, vendors: false, people: false });

  assert.equal(estate.completeness(9999), null);
});

// ── Per-principal state ─────────────────────────────────────────────
test('/state for an admin: their organisation\'s Sites, each with completeness, and nothing else', async () => {
  let r = await call('GET', '/api/setup/state', { user: ADMIN_A });
  assert.equal(r.status, 200);
  const ids = r.json.tenants.map((t) => t.id).sort((a, b) => a - b);
  assert.deepEqual(ids, [SITE_A1, SITE_A2]);
  assert.ok(!ids.includes(SITE_B1), 'must not reach the other organisation');
  const a1 = r.json.tenants.find((t) => t.id === SITE_A1);
  const a2 = r.json.tenants.find((t) => t.id === SITE_A2);
  assert.equal(a1.canScan, true);
  assert.equal(a1.completeness.canScan, true);
  assert.equal(a2.canScan, false);
  assert.equal(r.json.needsSetup, true, 'A2 still lacks the mandatory three');
  assert.equal(r.json.blocked, false, 'an admin is never blocked; they fix it');

  // The other org's admin sees only their own Site.
  r = await call('GET', '/api/setup/state', { user: ADMIN_B });
  assert.deepEqual(r.json.tenants.map((t) => t.id), [SITE_B1]);
  assert.equal(r.json.needsSetup, true);

  // The owner sees every Site.
  r = await call('GET', '/api/setup/state', { user: OWNER });
  assert.deepEqual(r.json.tenants.map((t) => t.id).sort((a, b) => a - b), [SITE_A1, SITE_A2, SITE_B1]);
});

test('/state for a site manager or member: never gated, and no other Site\'s data', async () => {
  let r = await call('GET', '/api/setup/state', { user: MGR_A1 });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json, { ok: true, needsSetup: false, blocked: false, reason: null });

  r = await call('GET', '/api/setup/state', { user: MEMBER_A1 });
  assert.deepEqual(r.json, { ok: true, needsSetup: false, blocked: false, reason: null });

  // B1 still lacks the mandatory three, and its member is not told so: the
  // admin sets up before inviting anyone, so a technician is never gated.
  r = await call('GET', '/api/setup/state', { user: MEMBER_B1 });
  assert.deepEqual(r.json, { ok: true, needsSetup: false, blocked: false, reason: null });
  assert.equal('tenants' in r.json, false, 'never a list of Sites for a member');

  // The summary that rides on /api/auth/me says the same thing.
  const summary = (id) => {
    const db = new Database(DB_PATH, { readonly: true });
    const u = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
    db.close();
    return estate.setupSummary(u);
  };
  assert.deepEqual(summary(MEMBER_B1), { needsSetup: false, blocked: false, reason: null });
  assert.deepEqual(summary(MEMBER_A1), { needsSetup: false, blocked: false, reason: null });
  assert.deepEqual(summary(ADMIN_A), { needsSetup: true, blocked: false, reason: null });
  // The platform owner is never sent into setup, however many Sites are
  // incomplete: approving organisations is the owner's job, setting them up
  // is the organisation admin's. The owner still sees every Site's state.
  const owner = { role: 'owner', tenant_id: null, organization_id: null };
  assert.deepEqual(estate.setupSummary(owner), { needsSetup: false, blocked: false, reason: null });
  const ownerState = estate.stateFor(owner);
  assert.equal(ownerState.needsSetup, false);
  assert.ok(ownerState.tenants.length >= 3 && ownerState.tenants.some((t) => !t.canScan),
    'the owner still sees incomplete Sites in the list');
  assert.deepEqual(estate.setupSummary({ role: 'member', tenant_id: null }),
    { needsSetup: false, blocked: false, reason: null }, 'even a Site-less member is not gated');
});

test('accessLevel: the rule the router applies, in both directions', () => {
  const user = (over) => ({ role: 'member', tenant_id: null, organization_id: null, ...over });
  assert.equal(estate.accessLevel(user({ role: 'owner' }), SITE_B1), 'write');
  assert.equal(estate.accessLevel(user({ role: 'org_admin', organization_id: ORG_A }), SITE_A2), 'write');
  assert.equal(estate.accessLevel(user({ role: 'org_admin', organization_id: ORG_A }), SITE_B1), null);
  assert.equal(estate.accessLevel(user({ role: 'org_admin', organization_id: 999, tenant_id: SITE_A1 }), SITE_A1), 'write',
    'an org_admin stranded on a Site still manages the Site they hold');
  assert.equal(estate.accessLevel(user({ role: 'site_manager', tenant_id: SITE_A1, organization_id: ORG_A }), SITE_A1), 'write');
  assert.equal(estate.accessLevel(user({ role: 'site_manager', tenant_id: SITE_A1, organization_id: ORG_A }), SITE_A2), null,
    'a site manager manages ONE Site, not the organisation');
  assert.equal(estate.accessLevel(user({ tenant_id: SITE_A1 }), SITE_A1), 'read');
  assert.equal(estate.accessLevel(user({ tenant_id: SITE_A1 }), SITE_A2), null);
  assert.equal(estate.accessLevel(user({ tenant_id: String(SITE_A1) }), SITE_A1), 'read', 'ids compare by value');
  assert.equal(estate.accessLevel(null, SITE_A1), null);
  assert.equal(estate.accessLevel(user({ role: 'owner' }), 9999), null, 'no such Site');
});

// Setup asks for a Site's location and no longer for its spaces (21 Sep 2026).
// A Site of its own, in an organisation of its own, so nothing an earlier test
// typed or scanned can be what places it.
test('a location alone places a Site: address, then the SPOC as approver, then the rules', async () => {
  const ORG_C = 30, SITE_C1 = 31, ADMIN_C = 8, SPOC_C1 = 9;
  const db = new Database(DB_PATH);
  db.prepare('INSERT INTO organizations (id,name) VALUES (?,?)').run(ORG_C, 'Org C');
  db.prepare('INSERT INTO tenants (id,slug,name,organization_id) VALUES (?,?,?,?)').run(SITE_C1, 'c1', 'C Site One', ORG_C);
  const u = db.prepare('INSERT INTO users (id,username,email,role,tenant_id,organization_id) VALUES (?,?,?,?,?,?)');
  u.run(ADMIN_C, 'admin_c', 'admin_c@example.test', 'org_admin', null, ORG_C);
  u.run(SPOC_C1, 'spoc_c1', 'spoc_c1@example.test', 'site_manager', SITE_C1, ORG_C);
  db.close();
  const admin = { role: 'org_admin', tenant_id: null, organization_id: ORG_C };
  assert.equal(estate.setupSummary(admin).needsSetup, true);

  const C1 = `/api/setup/${SITE_C1}`;
  let r = await call('PUT', `${C1}/datacentre`, { user: ADMIN_C, body: { timezone: 'Europe/Amsterdam' } });
  assert.equal(r.json.completeness.mandatory.location, false, 'a time zone is not a location');

  r = await call('PUT', `${C1}/datacentre`, { user: ADMIN_C, body: { address: '12 Harbour Road, 3011 AA, Netherlands' } });
  assert.equal(r.status, 200);
  assert.equal(r.json.completeness.mandatory.location, true);
  assert.equal(r.json.completeness.canScan, false, 'the SPOC and the rules are still owed');

  r = await call('PUT', `${C1}/approver`, { user: ADMIN_C, body: { user_id: SPOC_C1 } });
  assert.equal(r.status, 200);
  r = await call('PUT', `${C1}/rules`, { user: ADMIN_C, body: { accepted: true } });
  assert.deepEqual(r.json.completeness.mandatory, { location: true, approver: true, rules: true });
  assert.equal(r.json.completeness.canScan, true);
  assert.deepEqual(r.json.completeness.counts, { spaces: 0, racksTyped: 0, racksKnown: 0 }, 'and no space was ever made');
  assert.ok(estate.getTenant(SITE_C1).setup_completed_at, 'completion is stamped without a space');
  assert.deepEqual(estate.setupSummary(admin), { needsSetup: false, blocked: false, reason: null }, 'the gate lifts for that organisation');
});
