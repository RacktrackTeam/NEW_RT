/**
 * Organisation setup, slice two: the organisation profile, the optional
 * tenant profile sections, the pattern checker and the vendor catalogue -
 * against the real modules, the real router, and a seeded database.
 *
 * Same shape as setup.test.js: a throwaway auth.db seeded before the modules
 * load, the router mounted behind a stub gate, every rule asserted in both
 * directions. The harness mirrors the two body parsers app.js mounts, so the
 * logo cap is exercised through the same limit production has.
 */

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const Database = require('better-sqlite3');

const DB_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-setup-profile-'));
const DB_PATH = path.join(DB_DIR, 'auth.db');
process.env.RACKTRACK_AUTH_DB = DB_PATH;

// Two organisations whose slugs share a base, so the derived short codes
// collide and the second has to be made unique. Three Sites, same cast as
// setup.test.js.
const ORG_A = 10, ORG_B = 20;
const SITE_A1 = 11, SITE_A2 = 12, SITE_B1 = 21;
const OWNER = 1, ADMIN_A = 2, MGR_A1 = 3, MEMBER_A1 = 4, ADMIN_B = 5, MEMBER_B1 = 6, MEMBER_A2 = 7;
const RACK_A1 = 'RK-AAAA1111';

before(() => {
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.exec(`
    CREATE TABLE organizations (id INTEGER PRIMARY KEY, slug TEXT, name TEXT, status TEXT DEFAULT 'active');
    CREATE TABLE tenants (id INTEGER PRIMARY KEY, slug TEXT, name TEXT, organization_id INTEGER);
    CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, role TEXT,
                        tenant_id INTEGER, organization_id INTEGER, active INTEGER DEFAULT 1);
    CREATE TABLE rack_owners (rack_id TEXT, tenant_id INTEGER, created_by INTEGER,
                              created_at TEXT DEFAULT (datetime('now')),
                              PRIMARY KEY (tenant_id, rack_id));
  `);
  const o = db.prepare('INSERT INTO organizations (id,slug,name) VALUES (?,?,?)');
  o.run(ORG_A, 'acme-datacentres-3f2a', 'Acme Datacentres');
  o.run(ORG_B, 'acme-datacentres-9b1c', 'Acme Datacentres (B)');
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
  db.prepare('INSERT INTO rack_owners (rack_id,tenant_id,created_by) VALUES (?,?,?)').run(RACK_A1, SITE_A1, MEMBER_A1);
  db.close();
});

after(() => { try { fs.rmSync(DB_DIR, { recursive: true, force: true }); } catch { /* best effort */ } });

// ── Harness ─────────────────────────────────────────────────────────
const estate = require('../lib/estate');
const profile = require('../lib/estate_profile');
const express = require('express');

const app = express();
// The same two parsers app.js mounts, in the same order.
app.use('/api/setup/org', express.json({ limit: '320kb' }));
app.use(express.json());
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

const rawRow = (tenantId, section) => {
  const db = new Database(DB_PATH, { readonly: true });
  const row = db.prepare('SELECT * FROM tenant_profile WHERE tenant_id = ? AND section = ?').get(tenantId, section);
  db.close();
  return row || null;
};

const ORG_A_URL = `/api/setup/org/${ORG_A}/profile`;
const ORG_B_URL = `/api/setup/org/${ORG_B}/profile`;
const A1 = `/api/setup/${SITE_A1}`;
const A2 = `/api/setup/${SITE_A2}`;
const PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const logoOf = (bytes) => `data:image/png;base64,${Buffer.alloc(bytes, 1).toString('base64')}`;

// ── Organisation profile ────────────────────────────────────────────
test('organisation profile: short codes derived from the slug, made unique; who may see it', async () => {
  let r = await call('GET', ORG_A_URL, { user: OWNER });
  assert.equal(r.status, 200, 'the owner reads any organisation');
  assert.equal(r.json.profile.id, ORG_A);
  assert.equal(r.json.profile.short_code, 'ACMEDATA', 'slug minus its 4-hex suffix, upper-cased, 8 chars');
  assert.equal(r.json.profile.timezone, null);
  assert.equal(r.json.profile.country, null);
  assert.equal(r.json.profile.logo_data, null);
  assert.equal(r.json.profile.profile_updated_at, null);
  r = await call('GET', ORG_B_URL, { user: OWNER });
  assert.equal(r.json.profile.short_code, 'ACMEDATA-20', 'the same base in another organisation gets its id appended');

  assert.equal((await call('GET', ORG_A_URL, { user: ADMIN_A })).status, 200, 'the org admin reads their own');
  assert.equal((await call('GET', ORG_A_URL, { user: ADMIN_B })).status, 404, 'another org\'s admin must not learn it exists');
  assert.equal((await call('PUT', ORG_A_URL, { user: ADMIN_B, body: { timezone: 'Europe/London' } })).status, 404);
  assert.equal((await call('GET', ORG_A_URL, { user: MEMBER_B1 })).status, 404);
  // People inside the organisation already know it exists: 403, not 404.
  assert.equal((await call('GET', ORG_A_URL, { user: MGR_A1 })).status, 403, 'a site manager does not manage the organisation');
  assert.equal((await call('PUT', ORG_A_URL, { user: MEMBER_A1, body: { timezone: 'Europe/London' } })).status, 403);
  assert.equal((await call('GET', '/api/setup/org/abc/profile', { user: OWNER })).status, 400);
  assert.equal((await call('GET', '/api/setup/org/9999/profile', { user: OWNER })).status, 404);
  assert.equal((await call('GET', ORG_A_URL)).status, 401);
});

test('organisation profile: fields are validated and stored as given', async () => {
  const bad = async (body, why) => {
    const r = await call('PUT', ORG_A_URL, { user: ADMIN_A, body });
    assert.equal(r.status, 400, `${why}: ${JSON.stringify(body).slice(0, 60)} → ${r.status} ${r.raw.slice(0, 80)}`);
  };
  await bad({}, 'nothing to update');
  await bad({ timezone: 'Mars/Olympus' }, 'unknown timezone');
  await bad({ short_code: '' }, 'the short code cannot be cleared');
  await bad({ short_code: 'x' }, 'too short');
  await bad({ short_code: 'bad code!' }, 'letters, digits, dashes only');
  await bad({ website: 'not a site' }, 'not a web address');
  await bad({ phone: 'call me' }, 'not a phone number');
  await bad({ logo_data: 'data:text/plain;base64,QUJD' }, 'not an image');
  await bad({ logo_data: 'http://x/logo.png' }, 'not a data: URI');
  const r413 = await call('PUT', ORG_A_URL, { user: ADMIN_A, body: { logo_data: logoOf(201 * 1024) } });
  assert.equal(r413.status, 400, 'over the cap is refused by the validator, not the parser');
  assert.match(r413.json.error, /201 KB.*200 KB/);
  assert.equal((await call('PUT', ORG_A_URL, { user: ADMIN_A, body: { short_code: 'acmedata-20' } })).status, 409,
    'a short code another organisation holds');

  let r = await call('PUT', ORG_A_URL, { user: ADMIN_A, body: {
    timezone: 'Asia/Kolkata', country: 'in', website: 'racktrack.ai', phone: '+91 98765 43210',
    industry: 'Colocation', short_code: 'acme',
  } });
  assert.equal(r.status, 200);
  assert.equal(r.json.profile.timezone, 'Asia/Kolkata');
  assert.equal(r.json.profile.country, 'IN', 'a two-letter code is upper-cased');
  assert.equal(r.json.profile.website, 'racktrack.ai', 'stored as typed, no scheme invented');
  assert.equal(r.json.profile.phone, '+91 98765 43210');
  assert.equal(r.json.profile.industry, 'Colocation');
  assert.equal(r.json.profile.short_code, 'ACME');
  assert.equal(r.json.profile.profile_updated_by, ADMIN_A);
  assert.ok(r.json.profile.profile_updated_at);

  // A logo round-trips, the largest allowed one included, and null clears it.
  r = await call('PUT', ORG_A_URL, { user: OWNER, body: { logo_data: PNG } });
  assert.equal(r.status, 200);
  assert.equal(r.json.profile.logo_data, PNG);
  assert.equal(r.json.profile.profile_updated_by, OWNER);
  assert.equal((await call('GET', ORG_A_URL, { user: ADMIN_A })).json.profile.logo_data, PNG);
  r = await call('PUT', ORG_A_URL, { user: ADMIN_A, body: { logo_data: logoOf(200 * 1024) } });
  assert.equal(r.status, 200, 'exactly 200 KB decoded is allowed');
  assert.equal(r.json.profile.timezone, 'Asia/Kolkata', 'an update touches only what it names');
  r = await call('PUT', ORG_A_URL, { user: ADMIN_A, body: { logo_data: null, country: 'United Kingdom' } });
  assert.equal(r.json.profile.logo_data, null);
  assert.equal(r.json.profile.country, 'United Kingdom', 'a name is kept as a name');

  // A freed short code may be taken by another organisation.
  r = await call('PUT', ORG_B_URL, { user: ADMIN_B, body: { short_code: 'ACMEDATA' } });
  assert.equal(r.status, 200);
  assert.equal(r.json.profile.short_code, 'ACMEDATA');
  assert.equal(profile.orgProfileComplete(ORG_A), true);
  assert.equal(profile.orgProfileComplete(ORG_B), false, 'B has neither timezone nor country');
  assert.equal(profile.orgProfileComplete(9999), false);
});

test('/state says whether the organisation profile is filled, for the caller and per Site', async () => {
  let r = await call('GET', '/api/setup/state', { user: ADMIN_A });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.profile, { org: true }, 'A has timezone and country');
  for (const t of r.json.tenants) assert.deepEqual(t.profile, { org: true });

  r = await call('GET', '/api/setup/state', { user: ADMIN_B });
  assert.deepEqual(r.json.profile, { org: false });
  assert.deepEqual(r.json.tenants.map((t) => t.profile), [{ org: false }]);

  r = await call('GET', '/api/setup/state', { user: OWNER });
  assert.deepEqual(r.json.profile, { org: false }, 'the owner has no organisation of their own');
  const byId = Object.fromEntries(r.json.tenants.map((t) => [t.id, t.profile.org]));
  assert.deepEqual(byId, { [SITE_A1]: true, [SITE_A2]: true, [SITE_B1]: false });

  // A member's state is untouched: no profile, no tenants, and never a gate,
  // even though A1 has no spaces, approver or rules in this fixture.
  r = await call('GET', '/api/setup/state', { user: MEMBER_A1 });
  assert.deepEqual(r.json, { ok: true, needsSetup: false, blocked: false, reason: null });
});

// ── Tenant profile ──────────────────────────────────────────────────
const EMPTY_CONVENTIONS = {
  rack_pattern: null, device_pattern: null, asset_pattern: null, port_pattern: null,
  cable_colours: [], u_from_bottom: null, faces: null,
};
test('a fresh Site: every section empty, nothing written by anyone, and the same gate as the rest of setup', async () => {
  let r = await call('GET', `${A1}/profile`, { user: MEMBER_A1 });
  assert.equal(r.status, 200, 'a member of the Site reads');
  const p = r.json.profile;
  assert.deepEqual(p.contacts, []);
  assert.deepEqual(p.vendors, []);
  assert.deepEqual(p.conventions, EMPTY_CONVENTIONS);
  assert.deepEqual(p.updated, { contacts: null, vendors: null, conventions: null, facility: null });

  // The whole picture carries the same profile.
  r = await call('GET', A1, { user: MEMBER_A1 });
  assert.deepEqual(r.json.profile, p);
  assert.deepEqual(r.json.completeness.optional, { conventions: false, vendors: false, people: false });

  for (const user of [ADMIN_B, MEMBER_B1, MEMBER_A2]) {
    assert.equal((await call('GET', `${A1}/profile`, { user })).status, 404, `user ${user} is a stranger`);
    assert.equal((await call('PUT', `${A1}/profile/contacts`, { user, body: [] })).status, 404);
    assert.equal((await call('DELETE', `${A1}/profile/facility`, { user })).status, 404);
    assert.equal((await call('POST', `${A1}/conventions/check`, { user, body: { pattern: 'A' } })).status, 404);
  }
  assert.equal((await call('PUT', `${A1}/profile/contacts`, { user: MEMBER_A1, body: [] })).status, 403, 'a member cannot write');
  assert.equal((await call('DELETE', `${A1}/profile/contacts`, { user: MEMBER_A1 })).status, 403);
  assert.equal((await call('PUT', `${A1}/profile/colours`, { user: ADMIN_A, body: {} })).status, 404, 'no such section');
  assert.equal((await call('DELETE', `${A1}/profile/colours`, { user: ADMIN_A })).status, 404);
  assert.equal((await call('GET', `${A1}/profile`)).status, 401);
});

test('contacts round-trip: validated, replaced whole, audited; the people flag follows', async () => {
  const bad = async (body, why) => {
    const r = await call('PUT', `${A1}/profile/contacts`, { user: MGR_A1, body });
    assert.equal(r.status, 400, `${why} → ${r.status} ${r.raw.slice(0, 100)}`);
  };
  await bad({}, 'must be a list');
  await bad([{ role: 'on_site' }], 'name required');
  await bad([{ name: 'X' }], 'role required');
  await bad([{ name: 'X', role: 'janitor' }], 'unknown role');
  await bad([{ name: 'X', role: 'on_site', email: 'not-an-email' }], 'email shape');
  await bad([{ name: 'X', role: 'on_site', phone: 'ring me' }], 'phone shape');
  await bad(['X'], 'each entry an object');

  let r = await call('PUT', `${A1}/profile/contacts`, { user: MGR_A1, body: [
    { name: 'Priya', role: 'on_site', email: 'Priya@Example.test', phone: '+44 20 7946 0000', hours: 'Mon-Fri 08-18', extra: 'dropped' },
    { name: 'NOC', role: 'escalation' },
  ] });
  assert.equal(r.status, 200);
  assert.equal(r.json.section, 'contacts');
  assert.deepEqual(r.json.data[0], {
    name: 'Priya', role: 'on_site', email: 'priya@example.test', phone: '+44 20 7946 0000', hours: 'Mon-Fri 08-18', notes: null,
  });
  assert.deepEqual(r.json.data[1], { name: 'NOC', role: 'escalation', email: null, phone: null, hours: null, notes: null });
  assert.equal(r.json.updated.source, 'typed');
  assert.equal(r.json.updated.created_by, MGR_A1);
  assert.equal(r.json.updated.updated_by, MGR_A1);
  assert.ok(r.json.updated.created_at && r.json.updated.updated_at);
  assert.equal(r.json.completeness.optional.people, true);

  // Replaced whole by someone else, from an import: the row remembers both.
  assert.equal((await call('PUT', `${A1}/profile/contacts?source=guessed`, { user: ADMIN_A, body: [] })).status, 400);
  r = await call('PUT', `${A1}/profile/contacts?source=imported`, { user: ADMIN_A, body: [{ name: 'Sam', role: 'facilities' }] });
  assert.equal(r.status, 200);
  assert.equal(r.json.data.length, 1, 'the earlier two are gone');
  assert.equal(r.json.updated.source, 'imported');
  assert.equal(r.json.updated.created_by, MGR_A1, 'who first wrote the section is kept');
  assert.equal(r.json.updated.updated_by, ADMIN_A);
  const row = rawRow(SITE_A1, 'contacts');
  assert.equal(row.source, 'imported');
  assert.equal(row.created_by, MGR_A1);
  assert.equal(row.updated_by, ADMIN_A);

  r = await call('GET', `${A1}/profile`, { user: MEMBER_A1 });
  assert.equal(r.json.profile.contacts[0].name, 'Sam');
  assert.equal(r.json.profile.updated.contacts.updated_by, ADMIN_A);

  // An empty list is a section with nobody in it; a delete removes the row.
  r = await call('PUT', `${A1}/profile/contacts`, { user: MGR_A1, body: [] });
  assert.equal(r.json.completeness.optional.people, false);
  r = await call('DELETE', `${A1}/profile/contacts`, { user: MGR_A1 });
  assert.equal(r.status, 200);
  assert.equal(r.json.existed, true);
  assert.equal(rawRow(SITE_A1, 'contacts'), null);
  assert.equal((await call('DELETE', `${A1}/profile/contacts`, { user: MGR_A1 })).json.existed, false);
  // ...and one contact again, so the summary at the end sees a person.
  r = await call('PUT', `${A1}/profile/contacts`, { user: MGR_A1, body: [{ name: 'Priya', role: 'approver', email: 'priya@example.test' }] });
  assert.equal(r.json.completeness.optional.people, true);
});

test('vendors round-trip: a catalogue name or free text, models tidied; the vendors flag follows', async () => {
  const bad = async (body, why) => {
    const r = await call('PUT', `${A1}/profile/vendors`, { user: ADMIN_A, body });
    assert.equal(r.status, 400, `${why} → ${r.status} ${r.raw.slice(0, 100)}`);
  };
  await bad({}, 'must be a list');
  await bad([{ models: ['X'] }], 'name required');
  await bad([{ name: 'Cisco', models: 'C9300' }], 'models must be a list');
  await bad([{ name: 'Cisco', contact_email: 'tac' }], 'email shape');

  let r = await call('PUT', `${A1}/profile/vendors`, { user: ADMIN_A, body: [
    { name: 'Cisco', models: ['C9300-48P', ' C9200L-24T ', ''], contact_name: 'TAC', contact_email: 'TAC@cisco.example', support_ref: 'CON-123' },
    { name: "Bob's Cables" },
  ] });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.data[0], {
    name: 'Cisco', models: ['C9300-48P', 'C9200L-24T'], contact_name: 'TAC', contact_email: 'tac@cisco.example',
    contact_phone: null, support_ref: 'CON-123',
  });
  assert.deepEqual(r.json.data[1], { name: "Bob's Cables", models: [], contact_name: null, contact_email: null, contact_phone: null, support_ref: null });
  assert.equal(r.json.completeness.optional.vendors, true);
  assert.equal(estate.completeness(SITE_A1).optional.vendors, true);
  assert.equal(estate.completeness(SITE_A2).optional.vendors, false, 'another Site is untouched');
});

test('conventions: patterns are checked by the rule the live checker uses; the flag needs a pattern', async () => {
  let r = await call('PUT', `${A1}/profile/conventions`, { user: ADMIN_A, body: { faces: 'both', u_from_bottom: false } });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.data, { ...EMPTY_CONVENTIONS, faces: 'both', u_from_bottom: false });
  assert.equal(r.json.completeness.optional.conventions, false, 'faces alone is not a naming convention');

  const bad = async (body, why) => {
    const rr = await call('PUT', `${A1}/profile/conventions`, { user: ADMIN_A, body });
    assert.equal(rr.status, 400, `${why} → ${rr.status} ${rr.raw.slice(0, 100)}`);
  };
  await bad([], 'must be an object');
  await bad({ rack_pattern: '[abc' }, 'a regex that does not compile');
  await bad({ faces: 'top' }, 'faces vocabulary');
  await bad({ u_from_bottom: 'maybe' }, 'a boolean');
  await bad({ cable_colours: [{ meaning: 'uplink' }] }, 'a colour needs a colour');
  await bad({ cable_colours: 'blue' }, 'cable_colours is a list');

  r = await call('PUT', `${A1}/profile/conventions`, { user: MGR_A1, body: {
    rack_pattern: 'RK-####', port_pattern: '^(Gi|Te)\\d+/\\d+/\\d+$',
    cable_colours: [{ color: 'blue', meaning: 'uplink' }, { color: 'red' }], faces: 'front', u_from_bottom: true,
  } });
  assert.equal(r.status, 200);
  assert.equal(r.json.data.rack_pattern, 'RK-####');
  assert.equal(r.json.data.port_pattern, '^(Gi|Te)\\d+/\\d+/\\d+$');
  assert.equal(r.json.data.device_pattern, null);
  assert.deepEqual(r.json.data.cable_colours, [{ color: 'blue', meaning: 'uplink' }, { color: 'red', meaning: null }]);
  assert.equal(r.json.data.u_from_bottom, true);
  assert.equal(r.json.completeness.optional.conventions, true);
});

test('conventions check: a regex or a literal template, decided by what it contains, bounded in time', async () => {
  const check = async (pattern, example, user = MEMBER_A1) => {
    const r = await call('POST', `${A1}/conventions/check`, { user, body: { pattern, example } });
    assert.equal(r.status, 200, `${pattern} → ${r.status} ${r.raw.slice(0, 100)}`);
    return r.json;
  };
  // Literal: # a digit, A a letter, the rest itself; the whole example.
  let c = await check('RK-####', 'RK-0001');
  assert.deepEqual([c.ok, c.mode, c.matches], [true, 'literal', true], JSON.stringify(c));
  assert.match(c.reason, /matches/);
  assert.equal((await check('RK-####', 'RK-A001')).matches, false);
  assert.equal((await check('RK-####', 'RK-00011')).matches, false, 'the whole example, not a prefix');
  assert.equal((await check('RK-####', 'xRK-0001')).matches, false);
  assert.equal((await check('A##', 'R01')).matches, true);
  assert.equal((await check('A##', 'RACK')).matches, false);
  assert.equal((await check('Gi#/#/##', 'Gi1/0/24')).matches, true, 'a slash inside is not a regex delimiter');
  assert.equal((await check('R01.02', 'R01.02')).matches, true, 'a dot is a dot in a template');
  assert.equal((await check('R01.02', 'R01x02')).matches, false);

  // Regex: wrapped in slashes, or carrying a metacharacter; anchored for you.
  c = await check('^RK-\\d{4}$', 'RK-0001');
  assert.deepEqual([c.ok, c.mode, c.matches], [true, 'regex', true], JSON.stringify(c));
  assert.equal((await check('^RK-\\d{4}$', 'RK-1')).matches, false);
  assert.equal((await check('(Gi|Te)\\d+/\\d+/\\d+', 'Gi1/0/1')).matches, true);
  assert.equal((await check('(Gi|Te)\\d+/\\d+/\\d+', 'xGi1/0/1')).matches, false, 'anchored even without ^ $');
  assert.equal((await check('/rk-\\d+/i', 'RK-12')).matches, true, 'slashes with an i flag');
  assert.equal((await check('rk-\\d+', 'RK-12')).matches, false, 'case-sensitive without the flag');

  // A pattern that cannot be used says so at 200, so a form shows the reason live.
  c = await check('[abc', 'abc');
  assert.equal(c.ok, false);
  assert.equal(c.matches, false);
  assert.match(c.reason, /Not a valid regular expression/);
  c = await check('(a+)+$', 'a'.repeat(40) + 'b');
  assert.equal(c.ok, false, 'catastrophic backtracking is reported, not waited for');
  assert.match(c.reason, /too long/);
  c = await check('RK-####', '');
  assert.deepEqual([c.ok, c.matches], [true, false]);
  assert.match(c.reason, /example/i);

  // Malformed requests are 400; the reader-level member may call it; the same rule guards PUT.
  assert.equal((await call('POST', `${A1}/conventions/check`, { user: MEMBER_A1, body: {} })).status, 400);
  assert.equal((await call('POST', `${A1}/conventions/check`, { user: MEMBER_A1, body: { pattern: 'A'.repeat(201) } })).status, 400);
  assert.equal((await call('POST', `${A1}/conventions/check`, { user: MEMBER_A1, body: { pattern: 'A', example: 'x'.repeat(201) } })).status, 400);
  assert.equal(profile.compilePattern('RK-####').mode, 'literal');
  assert.equal(profile.compilePattern('RK-\\d{4}').mode, 'regex');
});

// ── Catalogue ───────────────────────────────────────────────────────
test('the vendor catalogue: every maker switch_ocr knows, names only, sorted, signed-in only', async () => {
  assert.equal((await call('GET', '/api/setup/catalogue/vendors')).status, 401);
  const r = await call('GET', '/api/setup/catalogue/vendors', { user: MEMBER_B1 });
  assert.equal(r.status, 200, 'any signed-in user, whatever their Site');
  assert.equal(r.json.count, 117);
  assert.equal(r.json.vendors.length, 117);
  const names = r.json.vendors.map((v) => v.name);
  assert.ok(names.includes('Cisco') && names.includes('D-Link') && names.includes('Ubiquiti'));
  const sorted = [...names].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }));
  assert.deepEqual(names, sorted, 'sorted case-insensitively');
  assert.ok(r.json.vendors.every((v) => Object.keys(v).length === 1), 'display names only, no aliases or patterns');
  assert.equal(profile.vendorCatalogue(), profile.vendorCatalogue(), 'read once, then served from memory');
});

// ── Completeness summary ────────────────────────────────────────────
test('optional flags at the end: A1 filled in, A2 and B1 not', () => {
  assert.deepEqual(estate.completeness(SITE_A1).optional, { conventions: true, vendors: true, people: true });
  assert.deepEqual(estate.completeness(SITE_A2).optional, { conventions: false, vendors: false, people: false });
  assert.deepEqual(estate.completeness(SITE_B1).optional, { conventions: false, vendors: false, people: false });
  assert.deepEqual(profile.flags(SITE_A1), { conventions: true, vendors: true, people: true });
  assert.deepEqual(profile.flags(9999), { conventions: false, vendors: false, people: false });
});


test('facility section and org primary contact round-trip; a space carries kind, floor, room and row', async () => {
  const fac = { code: 'RDG-1', address_line1: '1 Station Road', city: 'Reading', region: 'Berkshire', postcode: 'RG1 1AA', country: 'gb', provider: 'Equinix', access_notes: 'Ask for the NOC', hours: 'Mon-Fri 08:00-18:00' };
  const r1 = await call('PUT', `/api/setup/${SITE_A1}/profile/facility`, { user: ADMIN_A, body: fac });
  assert.equal(r1.status, 200, JSON.stringify(r1.json));
  assert.equal(r1.json.data.country, 'GB');
  assert.equal(r1.json.data.provider, 'Equinix');
  const r2 = await call('GET', `/api/setup/${SITE_A1}`, { user: ADMIN_A });
  assert.equal(r2.json.profile.facility.code, 'RDG-1');
  const bad = await call('PUT', `/api/setup/${SITE_A1}/profile/facility`, { user: ADMIN_A, body: { code: 'x'.repeat(41) } });
  assert.equal(bad.status, 400);

  const o1 = await call('PUT', `/api/setup/org/${ORG_A}/profile`, { user: ADMIN_A, body: { primary_contact_name: 'Priya Nair', primary_contact_email: 'priya@example.test' } });
  assert.equal(o1.status, 200, JSON.stringify(o1.json));
  assert.equal(o1.json.profile.primary_contact_email, 'priya@example.test');
  const o2 = await call('PUT', `/api/setup/org/${ORG_A}/profile`, { user: ADMIN_A, body: { primary_contact_email: 'not-an-email' } });
  assert.equal(o2.status, 400);

  const sp = await call('POST', `/api/setup/${SITE_A1}/spaces`, { user: ADMIN_A, body: { name: 'Hall 2', kind: 'hall', floor: '1', room: 'H2', row: 'A', rack_count: 12 } });
  assert.equal(sp.status, 201, JSON.stringify(sp.json));
  assert.equal(sp.json.space.kind, 'hall');
  assert.equal(sp.json.space.row, 'A');
  const up = await call('PUT', `/api/setup/${SITE_A1}/spaces/${sp.json.space.id}`, { user: ADMIN_A, body: { kind: 'cage', row: 'B' } });
  assert.equal(up.status, 200);
  assert.equal(up.json.space.kind, 'cage');
  assert.equal(up.json.space.floor, '1', 'untouched fields stay');
  const badKind = await call('POST', `/api/setup/${SITE_A1}/spaces`, { user: ADMIN_A, body: { name: 'Odd', kind: 'garage' } });
  assert.equal(badKind.status, 400);
});

// The one contact setup asks for since 21 Sep 2026: the Site's single point
// of contact, kept beside whatever an older setup saved in the section.
test('a SPOC is a contact role of its own and sits beside the older roles', async () => {
  const A2 = `/api/setup/${SITE_A2}`;
  const r = await call('PUT', `${A2}/profile/contacts`, { user: ADMIN_A, body: [
    { name: 'Priya Nair', role: 'spoc', email: 'Priya.Nair@Example.test', phone: '+31 10 000 0000' },
    { name: 'Door', role: 'on_site' },
  ] });
  assert.equal(r.status, 200, r.raw.slice(0, 120));
  assert.deepEqual(r.json.data.map((c) => c.role), ['spoc', 'on_site']);
  assert.equal(r.json.data[0].email, 'priya.nair@example.test');
  assert.equal((await call('DELETE', `${A2}/profile/contacts`, { user: ADMIN_A })).status, 200);
});
