#!/usr/bin/env node
/**
 * Seed racktrack-app's sign-in database: the platform owner, one organization
 * with one site, its organization admin and one technician.
 *
 *   RACKTRACK_AUTH_DB=<path> RT_SEED_PASSWORD=<password> node scripts/seed-auth.js
 *        [--org "Demo Organization"] [--site "Hyderabad DC"] [--suffix 2]
 *
 * Altered from seed-local-pilot.js (same helpers, same modules: auth.js for
 * the schema and public ids, lib/estate.js and lib/estate_profile.js for the
 * site setup and the organization profile). What changed: the roster is the
 * four accounts below, and no password is in the code. Every account gets
 * RT_SEED_PASSWORD; without it a strong one is generated. The password is
 * written to RT_SEED_PASSWORD_FILE (default: seed-password.txt beside the
 * database folder) with mode 600 and never printed.
 *
 * Re-runnable: accounts are found by username and brought back in line.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

function fail(msg, code = 1) {
  console.error(`seed-auth: ${msg}`);
  process.exit(code);
}

const argv = process.argv.slice(2);
const arg = (name, dflt) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? String(argv[i + 1]).trim() : dflt;
};
const ORG_NAME = arg('org', 'Demo Organization');
const SITE_NAME = arg('site', 'Hyderabad DC');

const dbArg = process.env.RACKTRACK_AUTH_DB;
if (!dbArg) fail('Set RACKTRACK_AUTH_DB. There is no default file.');
const TARGET = path.resolve(dbArg);
fs.mkdirSync(path.dirname(TARGET), { recursive: true });
if (!process.env.LOG_DB_PATH) process.env.LOG_DB_PATH = ':memory:';
if (!process.env.LOG_LEVEL) process.env.LOG_LEVEL = 'warn';

function generatePassword() {
  // Meets the app's rule: 8+ characters, upper, lower, digit, special.
  return `Rt-${crypto.randomBytes(6).toString('base64url')}a9Z`;
}
const PASSWORD = process.env.RT_SEED_PASSWORD || generatePassword();
const PASSWORD_FILE = path.resolve(process.env.RT_SEED_PASSWORD_FILE
  || path.join(path.dirname(path.dirname(TARGET)), 'seed-password.txt'));

// --suffix 2 seeds another organization or site with admin2 and tech2 (the tests use it); the owner stays one.
const SUFFIX = arg('suffix', '').replace(/[^a-z0-9]/gi, '');
const ROSTER = [
  { username: 'owner', email: 'owner@racktrack.local', role: 'owner' },
  { username: `admin${SUFFIX}`, email: `admin${SUFFIX}@demo.local`, role: 'org_admin' },
  { username: `tech${SUFFIX}`, email: `tech${SUFFIX}@demo.local`, role: 'member' },
].map((r) => ({ ...r, password: PASSWORD }));

const dataPaths = require('../lib/data_paths');
if (dataPaths.AUTH_DB !== TARGET) fail(`lib/data_paths.js resolved ${dataPaths.AUTH_DB}, not ${TARGET}. Stopping.`);
const auth = require('../auth');
const estate = require('../lib/estate');
const profile = require('../lib/estate_profile');
const bcrypt = require('bcryptjs');

const db = auth.db;
const BCRYPT_COST = Number(process.env.BCRYPT_COST) || 12;

// ── helpers copied from seed-local-pilot.js ──────────────────
function slugFor(name, fallback) {
  const base = String(name || '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || fallback;
  return `${base}-${crypto.randomBytes(2).toString('hex')}`;
}

function checkPassword(pw) {
  return typeof pw === 'string' && pw.length >= 8 && /[A-Z]/.test(pw) && /[a-z]/.test(pw)
    && /[0-9]/.test(pw) && /[^A-Za-z0-9]/.test(pw);
}

function upsertUser(entry, orgId, tenantId) {
  if (!auth.USERNAME_RE.test(entry.username)) fail(`bad username ${entry.username}`);
  if (!checkPassword(entry.password)) fail(`RT_SEED_PASSWORD does not meet the app's rules (8+ characters, upper, lower, digit, special)`);
  const existing = db.prepare('SELECT * FROM users WHERE username = ? COLLATE NOCASE').get(entry.username);
  if (!existing) {
    const hash = bcrypt.hashSync(entry.password, BCRYPT_COST);
    const r = db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id)
                          VALUES (?, ?, ?, 1, ?, ?, ?)`)
      .run(entry.email, entry.username, hash, entry.role, orgId, tenantId);
    auth.assignPublicId(r.lastInsertRowid, entry.role);
    return db.prepare('SELECT * FROM users WHERE id = ?').get(r.lastInsertRowid);
  }
  const passwordOk = bcrypt.compareSync(entry.password, existing.password_hash);
  db.prepare(`UPDATE users SET email = ?, username = ?, email_verified = 1, role = ?, organization_id = ?,
                tenant_id = ?, active = 1, password_set = 1, password_hash = ? WHERE id = ?`)
    .run(entry.email, entry.username, entry.role, orgId, tenantId,
      passwordOk ? existing.password_hash : bcrypt.hashSync(entry.password, BCRYPT_COST), existing.id);
  auth.assignPublicId(existing.id, entry.role);
  return db.prepare('SELECT * FROM users WHERE id = ?').get(existing.id);
}

// ── the roster ───────────────────────────────────────────────
const owner = upsertUser(ROSTER[0], null, auth.getDefaultTenantId());

let org = db.prepare('SELECT * FROM organizations WHERE name = ?').get(ORG_NAME);
if (!org) {
  const r = db.prepare("INSERT INTO organizations (slug, name, created_by, status) VALUES (?, ?, ?, 'active')")
    .run(slugFor(ORG_NAME, 'org'), ORG_NAME, owner.id);
  require('../lib/org_settings').applyPilotDefaults(db, r.lastInsertRowid);
  org = db.prepare('SELECT * FROM organizations WHERE id = ?').get(r.lastInsertRowid);
} else if (org.status !== 'active') {
  db.prepare("UPDATE organizations SET status = 'active' WHERE id = ?").run(org.id);
}

let site = db.prepare('SELECT * FROM tenants WHERE organization_id = ? AND name = ?').get(org.id, SITE_NAME);
if (!site) {
  const r = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)')
    .run(slugFor(SITE_NAME, 'site'), SITE_NAME, org.id);
  site = db.prepare('SELECT * FROM tenants WHERE id = ?').get(r.lastInsertRowid);
}

const admin = upsertUser(ROSTER[1], org.id, null);
const tech = upsertUser(ROSTER[2], org.id, site.id);

// The organization profile and the site's first-run setup, so nobody is
// sent into the setup popup (as in seed-local-pilot.js).
const orgNow = profile.orgProfile(org.id);
const wantOrg = { timezone: 'Asia/Kolkata', country: 'IN', primary_contact_name: 'Admin', primary_contact_email: admin.email };
const orgChanges = Object.fromEntries(Object.entries(wantOrg).filter(([k, v]) => orgNow[k] !== v));
if (Object.keys(orgChanges).length) profile.updateOrgProfile(org.id, orgChanges, owner.id);
const dcNow = estate.snapshot(site.id).datacentre;
if (!dcNow.address) estate.updateDatacentre(site.id, { address: 'Hyderabad, India', timezone: 'Asia/Kolkata' }, admin.id);
const approverNow = estate.getApprover(site.id);
if (!approverNow || Number(approverNow.user_id) !== Number(admin.id)) estate.setApprover(site.id, { user_id: admin.id }, admin.id);
const contacts = profile.tenantProfile(site.id).contacts || [];
if (!contacts.some((c) => c.role === 'spoc')) {
  profile.putSection(site.id, 'contacts', [{ name: 'Admin', role: 'spoc', email: admin.email, phone: null, hours: null, notes: null }, ...contacts], admin.id);
}
if (!estate.getRules(site.id).accepted_at) estate.acceptRules(site.id, { accepted: true }, admin.id);

const snap = estate.snapshot(site.id);
if (!snap.completeness.canScan || !snap.datacentre.setup_completed_at) {
  fail(`setup did not complete for the site: ${JSON.stringify(snap.completeness.mandatory)}`);
}

fs.mkdirSync(path.dirname(PASSWORD_FILE), { recursive: true });
fs.writeFileSync(PASSWORD_FILE, `${PASSWORD}\n`, { mode: 0o600 });
fs.chmodSync(PASSWORD_FILE, 0o600);

console.log(`Database:     ${TARGET}`);
console.log(`Organization: ${org.name} (id ${org.id}, active)`);
console.log(`Site:         ${site.name} (id ${site.id}), setup complete`);
console.log(`Accounts:     owner (platform owner), admin${SUFFIX} (organization admin), tech${SUFFIX} (technician of the site)`);
console.log(`Password:     the same for all three, in ${PASSWORD_FILE} (mode 600). Not printed.`);
db.pragma('wal_checkpoint(TRUNCATE)');
process.exit(0);
