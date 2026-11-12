/**
 * GET /api/scan-sites - the Sites the scan screen offers, per role.
 *
 * Same shape as setup.test.js: a throwaway auth.db is seeded before the
 * modules load, the real router runs against the real estate module, and it is
 * mounted behind a stub that sets req.user from a header, the way app.js
 * mounts it behind auth.requireAuth.
 *
 * What is held here:
 *   1. a technician and a site manager get their own Site and nothing else,
 *      preselected;
 *   2. an organisation admin gets every Site of the organisation, and no Site
 *      of another one; with no Site of their own nothing is preselected;
 *   3. an owner inside an organisation gets that organisation's Sites; an
 *      owner with none gets every Site;
 *   3a. an admin or owner of an organization who sits on a shared tenant
 *      outside it is never offered that tenant, and never starts on it; naming
 *      it anyway keeps their own organization;
 *   4. a row is the Site's number as a person reads it, its name, its racks
 *      and its spaces - and never a coordinate or an address.
 */

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const Database = require('better-sqlite3');

const DB_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-scansites-'));
const DB_PATH = path.join(DB_DIR, 'auth.db');
process.env.RACKTRACK_AUTH_DB = DB_PATH;

const ORG_A = 10, ORG_B = 20;
const SITE_A1 = 31, SITE_A2 = 32, SITE_B1 = 41, SITE_LONE = 50;
const OWNER = 1, OWNER_A = 2, ADMIN_A = 3, ADMIN_A_HOMED = 4, MGR_A1 = 5, MEMBER_A2 = 6,
  MEMBER_B1 = 9, MEMBER_NOWHERE = 10,
  ADMIN_A_SHARED = 11, ADMIN_B_SHARED = 12, OWNER_A_SHARED = 13;
const RACK = 'RK-5B81BE87';

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
  t.run(SITE_A1, 'a1', 'Warehouse', ORG_A);
  t.run(SITE_A2, 'a2', 'Office-Sprintpark', ORG_A);
  t.run(SITE_B1, 'b1', 'B Site One', ORG_B);
  t.run(SITE_LONE, 'lone', 'Lone Site', null);
  const u = db.prepare('INSERT INTO users (id,username,email,role,tenant_id,organization_id) VALUES (?,?,?,?,?,?)');
  u.run(OWNER, 'owner', 'owner@example.test', 'owner', null, null);
  u.run(OWNER_A, 'owner_a', 'owner_a@example.test', 'owner', null, ORG_A);
  u.run(ADMIN_A, 'admin_a', 'admin_a@example.test', 'org_admin', null, ORG_A);
  u.run(ADMIN_A_HOMED, 'admin_a2', 'admin_a2@example.test', 'org_admin', SITE_A2, ORG_A);
  u.run(MGR_A1, 'mgr_a1', 'mgr_a1@example.test', 'site_manager', SITE_A1, ORG_A);
  u.run(MEMBER_A2, 'member_a2', 'member_a2@example.test', 'member', SITE_A2, ORG_A);
  u.run(MEMBER_B1, 'member_b1', 'member_b1@example.test', 'member', SITE_B1, ORG_B);
  u.run(MEMBER_NOWHERE, 'nowhere', 'nowhere@example.test', 'member', null, null);
  // Admins the way the owner makes them: in an organization, sitting on a
  // shared tenant that belongs to none (the default tenant of a live server).
  u.run(ADMIN_A_SHARED, 'admin_a3', 'admin_a3@example.test', 'org_admin', SITE_LONE, ORG_A);
  u.run(ADMIN_B_SHARED, 'admin_b', 'admin_b@example.test', 'org_admin', SITE_LONE, ORG_B);
  u.run(OWNER_A_SHARED, 'owner_a2', 'owner_a2@example.test', 'owner', SITE_LONE, ORG_A);
  db.close();
});

after(() => { try { fs.rmSync(DB_DIR, { recursive: true, force: true }); } catch { /* best effort */ } });

// ── Harness ─────────────────────────────────────────────────────────
const estate = require('../lib/estate');
const express = require('express');

const app = express();
// Stands in for auth.requireAuth: the seeded user row named by x-user, else 401.
app.use('/api/scan-sites', (req, res, next) => {
  const id = Number(req.headers['x-user'] || 0);
  if (!id) return res.status(401).json({ error: 'Authentication required' });
  const db = new Database(DB_PATH, { readonly: true });
  req.user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
  db.close();
  if (!req.user) return res.status(401).json({ error: 'User no longer exists' });
  next();
}, require('../routes/scan_sites'));

let server, port, room;
let rackRow;
before(() => new Promise((resolve) => {
  // Site A2 is the one with something in it: a room inside a floor, one rack
  // in the room, a SPOC, and a location that must never reach the phone.
  const floor = estate.createSpace(SITE_A2, { name: 'Floor 1' }, ADMIN_A);
  room = estate.createSpace(SITE_A2, { name: 'RM01', parent_id: floor.id }, ADMIN_A);
  rackRow = estate.upsertRack(SITE_A2, { rack_id: RACK, name: 'SP-HYB-RM01-R01-R1', space_id: room.id }, ADMIN_A);
  estate.setApprover(SITE_A2, { user_id: MEMBER_A2 }, ADMIN_A);
  estate.updateDatacentre(SITE_A2, { address: '1 Test Street', lat: 17.44, lng: 78.38 }, ADMIN_A);
  server = app.listen(0, '127.0.0.1', () => { port = server.address().port; resolve(); });
}));
after(() => server && server.close());

function list(user) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, method: 'GET', path: '/api/scan-sites',
      headers: user ? { 'x-user': String(user) } : {},
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
    req.end();
  });
}
const idsOf = (r) => r.json.sites.map((s) => s.id);

test('an anonymous caller is refused', async () => {
  assert.equal((await list(null)).status, 401);
});

test('a technician gets their one Site, preselected, in the shape the picker reads', async () => {
  const r = await list(MEMBER_A2);
  assert.equal(r.status, 200);
  assert.equal(r.json.ok, true);
  assert.equal(r.json.preselect, SITE_A2);
  assert.deepEqual(r.json.sites, [{
    id: SITE_A2,
    siteId: 'Site 32',
    name: 'Office-Sprintpark',
    rackCount: 1,
    racks: [{ id: rackRow.rack.id, rackId: RACK, name: 'SP-HYB-RM01-R01-R1', spaceId: room.id }],
    spaces: [
      { id: room.parent_id, name: 'Floor 1', depth: 0 },
      { id: room.id, name: 'RM01', depth: 1 },
    ],
    hasSpoc: true,
  }]);
});

test('a site manager gets their own Site only', async () => {
  for (const who of [MGR_A1]) {
    const r = await list(who);
    assert.equal(r.status, 200);
    assert.deepEqual(idsOf(r), [SITE_A1]);
    assert.equal(r.json.preselect, SITE_A1);
    assert.equal(r.json.sites[0].hasSpoc, false);
    assert.equal(r.json.sites[0].rackCount, 0);
  }
});

test('an organization admin gets every Site of the organization, by name, and has to choose', async () => {
  const r = await list(ADMIN_A);
  assert.deepEqual(idsOf(r), [SITE_A2, SITE_A1]);           // Office-Sprintpark, Warehouse
  assert.equal(r.json.preselect, null);
  assert.ok(!idsOf(r).includes(SITE_B1), 'no Site of another organization');
});

test('an organization admin with a Site of their own starts on it', async () => {
  const r = await list(ADMIN_A_HOMED);
  assert.deepEqual(idsOf(r), [SITE_A2, SITE_A1]);
  assert.equal(r.json.preselect, SITE_A2);
});

test('an owner inside an organization gets its Sites; an owner with none gets only Sites of no organization', async () => {
  const inside = await list(OWNER_A);
  assert.deepEqual(idsOf(inside), [SITE_A2, SITE_A1]);
  assert.equal(inside.json.preselect, null);

  // The platform owner does not scan for other organizations (25 September 2026).
  const none = await list(OWNER);
  assert.deepEqual(idsOf(none), [SITE_LONE]);
});

test('an admin who sits on a shared tenant outside the organization is never offered it', async () => {
  const several = await list(ADMIN_A_SHARED);
  assert.deepEqual(idsOf(several), [SITE_A2, SITE_A1]);
  assert.ok(!idsOf(several).includes(SITE_LONE), 'the shared tenant is not a Site of the organization');
  assert.equal(several.json.preselect, null, 'with several Sites they have to choose');

  const one = await list(ADMIN_B_SHARED);
  assert.deepEqual(idsOf(one), [SITE_B1]);
  assert.equal(one.json.preselect, SITE_B1, 'the organization\'s only Site, not the shared tenant');

  const owner = await list(OWNER_A_SHARED);
  assert.deepEqual(idsOf(owner), [SITE_A2, SITE_A1]);
  assert.equal(owner.json.preselect, null);
});

test('naming a tenant with no organization keeps the caller\'s own organization', () => {
  const scanSite = require('../lib/scan_site');
  for (const [sub, role] of [[ADMIN_A_SHARED, 'org_admin'], [OWNER_A_SHARED, 'owner']]) {
    const out = scanSite.resolve({ sub, role, tenantId: SITE_LONE, organizationId: ORG_A }, String(SITE_LONE));
    assert.equal(out.ok, true);
    assert.equal(out.auth.tenantId, SITE_LONE);
    assert.equal(out.auth.organizationId, ORG_A);
  }
  // A Site inside an organization is refused to an owner who has none.
  assert.equal(scanSite.resolve({ sub: OWNER, role: 'owner', tenantId: null, organizationId: null },
    SITE_A1).status, 404);
});

test('somebody with no Site gets an empty list, not an error', async () => {
  const r = await list(MEMBER_NOWHERE);
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.sites, []);
  assert.equal(r.json.preselect, null);
});

test('no coordinates and no address reach the phone', async () => {
  const r = await list(OWNER_A);
  const a2 = r.json.sites.find((s) => s.id === SITE_A2);
  for (const key of ['lat', 'lng', 'address', 'timezone', 'organization_id']) {
    assert.ok(!(key in a2), `${key} is not part of a row`);
  }
  assert.ok(!/17\.44|78\.38|Test Street/.test(r.raw), 'the Site\'s location is nowhere in the payload');
});
