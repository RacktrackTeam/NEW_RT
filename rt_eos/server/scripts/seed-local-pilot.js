#!/usr/bin/env node
/**
 * Seed the local RT-EOS pilot accounts into an empty database file.
 *
 *   node scripts/seed-local-pilot.js --db <path> [--org "EOS Pilot"]
 *        [--site "Main"] [--address "..."] [--timezone Asia/Kolkata]
 *        [--country IN] [--reset]
 *
 * --db can be left out when RACKTRACK_AUTH_DB is set; one of the two is
 * required, there is no default file, so the live database is only ever
 * seeded when it is named. Passwords are never printed.
 *
 * Then start the server on the same file the usual way:
 *
 *   RACKTRACK_AUTH_DB=<path> PORT=<port> node app.js
 *
 * Every module takes its files from lib/data_paths.js, so the server reads
 * and writes that file, and keeps logs.db and scan-jobs.json in
 * the same folder. (Before 28 Sep 2026 some modules ignored RACKTRACK_AUTH_DB,
 * and this script had to redirect them; that is no longer needed.)
 *
 * Still shared by every server on this checkout: nothing else - every scan
 * file lives in the data tree beside the database (lib/store.js). Rack ids are made from the organization
 * id, so a pilot database whose organization is id 1 gives the same rack id
 * for the same photo as any other database whose organization is id 1.
 *
 * What the seed builds (and nothing else):
 *   - the platform owner: owner (role owner, no organization)
 *   - one active organization, named by --org, with its profile filled
 *   - one site in that organization, named by --site, with first-run setup
 *     complete: an address and time zone, Barry as the site contact (SPOC,
 *     which the server stores as the site approver and as the "spoc" entry in
 *     the site contacts), and the site rules accepted
 *   - barry: the site manager of that site
 *   - 12 technicians: role member of that site
 *
 * The app also keeps a site called "default" with no organization. auth.js
 * creates it on every start, so it is left alone here.
 *
 * It uses the app's own modules for everything they provide: auth.js creates
 * the schema and hands out the public ids (OWN-0001, USR-0001 ...),
 * lib/estate.js records the address, the SPOC and the rules and stamps
 * setup_completed_at, lib/estate_profile.js fills the organization profile and
 * the site contacts. Passwords are hashed with bcryptjs at the same cost
 * auth.js uses. Only the rows auth.js writes inline in its routes (a user, an
 * organization, a site) are written here with the same INSERT the route uses.
 *
 * Re-runnable: running it twice leaves the same result. Existing accounts are
 * found by username and brought back in line; nothing is duplicated. If the
 * one organization or the one site already exists under another name, it is
 * renamed to the name given.
 *
 * It refuses to run when the target already holds users outside this roster,
 * more than one organization, or more than one site besides "default". It
 * prints what it found and exits with code 2. --reset backs the file up
 * beside itself, deletes it and builds it again; it is never done unless you
 * pass it.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

function fail(msg, code = 1) {
  console.error(`seed-local-pilot: ${msg}`);
  process.exit(code);
}

function parseArgs(argv) {
  const out = { reset: false, help: false };
  const takes = ['org', 'site', 'db', 'address', 'timezone', 'country'];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--reset') { out.reset = true; continue; }
    if (a === '--help' || a === '-h') { out.help = true; continue; }
    const m = /^--([a-z]+)(?:=(.*))?$/.exec(a);
    if (m && takes.includes(m[1])) {
      const v = m[2] !== undefined ? m[2] : argv[++i];
      if (v === undefined || v === '') fail(`--${m[1]} needs a value`);
      out[m[1]] = v;
      continue;
    }
    fail(`Unknown argument: ${a}`);
  }
  return out;
}

const USAGE = 'Usage: node scripts/seed-local-pilot.js --db <path> [--org "EOS Pilot"] '
  + '[--site "Main"] [--address "..."] [--timezone Asia/Kolkata] [--country IN] [--reset]\n'
  + 'Then:  RACKTRACK_AUTH_DB=<path> PORT=<port> node app.js';

const args = parseArgs(process.argv.slice(2));
if (args.help) { console.log(USAGE); process.exit(0); }

const ORG_NAME  = String(args.org || 'EOS Pilot').trim();
const SITE_NAME = String(args.site || 'Main').trim();
const ADDRESS   = String(args.address || 'Address to be confirmed').trim();
const TIMEZONE  = String(args.timezone || 'Asia/Kolkata').trim();
const COUNTRY   = String(args.country || 'IN').trim();
if (!ORG_NAME || ORG_NAME.length > 120) fail('--org must be 1 to 120 characters');
if (!SITE_NAME || SITE_NAME.length > 120) fail('--site must be 1 to 120 characters');

const dbArg = args.db || process.env.RACKTRACK_AUTH_DB;
if (!dbArg) fail(`Give --db <path> or set RACKTRACK_AUTH_DB. There is no default file.\n${USAGE}`);
const TARGET = path.resolve(dbArg);

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const TECHS = ['mandar', 'shankar', 'srikanth', 'jaya', 'geethika', 'prasanna',
  'bushi', 'bargavi', 'krishna', 'thanmai', 'jahnavi', 'sravan'];
const ROSTER = [
  { username: 'owner', password: 'Owner@12345', email: 'owner@racktrack.local', role: 'owner' },
  { username: 'barry', password: 'Barry@12345', email: 'barry@eos.local', role: 'site_manager' },
  ...TECHS.map((u) => ({ username: u, password: `${cap(u)}@12345`, email: `${u}@eos.local`, role: 'member' })),
];
const ROSTER_NAMES = new Set(ROSTER.map((r) => r.username));

const Database = require('better-sqlite3');

function inspect(file) {
  if (!fs.existsSync(file) || fs.statSync(file).size === 0) return null;
  const db = new Database(file, { readonly: true, fileMustExist: true });
  try {
    const has = (t) => !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(t);
    const users = has('users') ? db.prepare('SELECT username, role FROM users ORDER BY id').all() : [];
    const orgs = has('organizations') ? db.prepare('SELECT id, name FROM organizations ORDER BY id').all() : [];
    const sites = has('tenants') ? db.prepare("SELECT id, name, slug FROM tenants WHERE slug <> 'default' ORDER BY id").all() : [];
    return { users, orgs, sites };
  } finally {
    db.close();
  }
}

function problemsIn(found) {
  if (!found) return [];
  const out = [];
  const strangers = found.users.filter((u) => !ROSTER_NAMES.has(String(u.username).toLowerCase()));
  if (strangers.length) {
    out.push(`${strangers.length} user(s) outside the roster: `
      + strangers.map((u) => `${u.username} (${u.role})`).join(', '));
  }
  if (found.orgs.length > 1) {
    out.push(`${found.orgs.length} organizations: ${found.orgs.map((o) => o.name).join(', ')}`);
  }
  if (found.sites.length > 1) {
    out.push(`${found.sites.length} sites besides "default": ${found.sites.map((s) => s.name).join(', ')}`);
  }
  return out;
}

const found = problemsIn(inspect(TARGET));
if (found.length && !args.reset) {
  console.error(`The database at ${TARGET} is not an empty or pilot-only database:`);
  for (const f of found) console.error(`  - ${f}`);
  console.error('Nothing was changed. Point --db at a new file, or pass --reset to back this one up and rebuild it.');
  process.exit(2);
}

if (args.reset && fs.existsSync(TARGET)) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backup = `${TARGET}.pre-seed-${stamp}.bak`;
  const db = new Database(TARGET);
  db.pragma('wal_checkpoint(TRUNCATE)');
  db.close();
  fs.copyFileSync(TARGET, backup);
  for (const f of [TARGET, `${TARGET}-wal`, `${TARGET}-shm`]) {
    if (fs.existsSync(f)) fs.unlinkSync(f);
  }
  console.log(`--reset: backed up to ${backup} and removed the old file.`);
}

fs.mkdirSync(path.dirname(TARGET), { recursive: true });

// Every module reads its path from lib/data_paths.js when it is first loaded,
// so this has to be set before any of them is required.
process.env.RACKTRACK_AUTH_DB = TARGET;
if (!process.env.LOG_DB_PATH) process.env.LOG_DB_PATH = ':memory:';
if (!process.env.LOG_LEVEL) process.env.LOG_LEVEL = 'warn';

const dataPaths = require('../lib/data_paths');
if (dataPaths.AUTH_DB !== TARGET) fail(`lib/data_paths.js resolved ${dataPaths.AUTH_DB}, not ${TARGET}. Stopping.`);

const auth = require('../auth');
const estate = require('../lib/estate');
const profile = require('../lib/estate_profile');
const bcrypt = require('bcryptjs');

if (path.resolve(auth.db.name) !== TARGET) fail(`auth.js opened ${auth.db.name}, not ${TARGET}. Stopping.`);
const db = auth.db;
const BCRYPT_COST = Number(process.env.BCRYPT_COST) || 12;

function slugFor(name, fallback) {
  const base = String(name || '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || fallback;
  return `${base}-${crypto.randomBytes(2).toString('hex')}`;
}

function shortCodeFor(name) {
  const code = String(name).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
  return code.length >= 2 ? code : null;
}

function checkPassword(pw) {
  return typeof pw === 'string' && pw.length >= 8 && /[A-Z]/.test(pw) && /[a-z]/.test(pw)
    && /[0-9]/.test(pw) && /[^A-Za-z0-9]/.test(pw);
}

/** Create the user or bring an existing one back in line. Returns its row. */
function upsertUser(entry, orgId, tenantId) {
  if (!auth.USERNAME_RE.test(entry.username)) fail(`bad username ${entry.username}`);
  if (!checkPassword(entry.password)) fail(`the password for ${entry.username} does not meet the app's rules`);
  const existing = db.prepare('SELECT * FROM users WHERE username = ? COLLATE NOCASE').get(entry.username);
  if (!existing) {
    const hash = bcrypt.hashSync(entry.password, BCRYPT_COST);
    const r = db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id)
                          VALUES (?, ?, ?, 1, ?, ?, ?)`)
      .run(entry.email, entry.username, hash, entry.role, orgId, tenantId);
    auth.assignPublicId(r.lastInsertRowid, entry.role);
    return { row: db.prepare('SELECT * FROM users WHERE id = ?').get(r.lastInsertRowid), made: true };
  }
  const passwordOk = bcrypt.compareSync(entry.password, existing.password_hash);
  db.prepare(`UPDATE users SET email = ?, username = ?, email_verified = 1, role = ?, organization_id = ?,
                tenant_id = ?, active = 1, password_set = 1, password_hash = ? WHERE id = ?`)
    .run(entry.email, entry.username, entry.role, orgId, tenantId,
      passwordOk ? existing.password_hash : bcrypt.hashSync(entry.password, BCRYPT_COST), existing.id);
  auth.assignPublicId(existing.id, entry.role);
  return { row: db.prepare('SELECT * FROM users WHERE id = ?').get(existing.id), made: false };
}

const defaultTenantId = auth.getDefaultTenantId();
const ownerResult = upsertUser(ROSTER[0], null, defaultTenantId);
const owner = ownerResult.row;

let org = db.prepare('SELECT * FROM organizations ORDER BY id').all()[0] || null;
if (!org) {
  const r = db.prepare("INSERT INTO organizations (slug, name, created_by, status) VALUES (?, ?, ?, 'active')")
    .run(slugFor(ORG_NAME, 'org'), ORG_NAME, owner.id);
  require('../lib/org_settings').applyPilotDefaults(db, r.lastInsertRowid);
  org = db.prepare('SELECT * FROM organizations WHERE id = ?').get(r.lastInsertRowid);
} else if (org.name !== ORG_NAME || org.status !== 'active') {
  db.prepare("UPDATE organizations SET name = ?, status = 'active' WHERE id = ?").run(ORG_NAME, org.id);
}

let site = db.prepare("SELECT * FROM tenants WHERE slug <> 'default' ORDER BY id").all()[0] || null;
if (!site) {
  const r = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)')
    .run(slugFor(SITE_NAME, 'site'), SITE_NAME, org.id);
  site = db.prepare('SELECT * FROM tenants WHERE id = ?').get(r.lastInsertRowid);
} else if (site.name !== SITE_NAME || Number(site.organization_id) !== Number(org.id)) {
  db.prepare('UPDATE tenants SET name = ?, organization_id = ? WHERE id = ?').run(SITE_NAME, org.id, site.id);
}

const made = ownerResult.made ? ['owner'] : [];
const users = { owner };
for (const entry of ROSTER.slice(1)) {
  const { row, made: isNew } = upsertUser(entry, org.id, site.id);
  users[entry.username] = row;
  if (isNew) made.push(entry.username);
}
const barry = users.barry;

// The organization profile, so the org gate does not ask for it.
const orgNow = profile.orgProfile(org.id);
const wantOrg = {
  timezone: TIMEZONE, country: COUNTRY.length === 2 ? COUNTRY.toUpperCase() : COUNTRY,
  primary_contact_name: 'Barry', primary_contact_email: barry.email,
};
const code = shortCodeFor(ORG_NAME);
if (code) wantOrg.short_code = code;
const orgChanges = Object.fromEntries(Object.entries(wantOrg).filter(([k, v]) => orgNow[k] !== v));
if (Object.keys(orgChanges).length) {
  try {
    profile.updateOrgProfile(org.id, orgChanges, owner.id);
  } catch (err) {
    if (orgChanges.short_code && /short code/i.test(err.message)) {
      delete orgChanges.short_code;
      if (Object.keys(orgChanges).length) profile.updateOrgProfile(org.id, orgChanges, owner.id);
    } else {
      throw err;
    }
  }
}

// The site's first-run setup: address and time zone, Barry as SPOC (approver
// and the "spoc" contact), rules accepted.
const dcNow = estate.snapshot(site.id).datacentre;
if (dcNow.address !== ADDRESS || dcNow.timezone !== TIMEZONE) {
  estate.updateDatacentre(site.id, { address: ADDRESS, timezone: TIMEZONE }, barry.id);
}
const approverNow = estate.getApprover(site.id);
if (!approverNow || Number(approverNow.user_id) !== Number(barry.id)) {
  estate.setApprover(site.id, { user_id: barry.id }, barry.id);
}
const contacts = profile.tenantProfile(site.id).contacts || [];
const spoc = { name: 'Barry', role: 'spoc', email: barry.email, phone: null, hours: null, notes: null };
const others = contacts.filter((c) => c.role !== 'spoc');
const current = contacts.find((c) => c.role === 'spoc');
const sameSpoc = current && current.name === spoc.name && current.email === spoc.email
  && contacts.filter((c) => c.role === 'spoc').length === 1;
if (!sameSpoc) profile.putSection(site.id, 'contacts', [spoc, ...others], barry.id);
if (!estate.getRules(site.id).accepted_at) {
  estate.acceptRules(site.id, { accepted: true }, barry.id);
}

// Nobody may be sent into the setup stepper.
const snap = estate.snapshot(site.id);
if (!snap.completeness.canScan || !snap.datacentre.setup_completed_at) {
  fail(`setup did not complete for the site: ${JSON.stringify(snap.completeness.mandatory)}`);
}
const orgGate = profile.orgProfileComplete(org.id);
const setupState = {
  owner: estate.stateFor(owner).needsSetup,
  barry: estate.stateFor(barry).needsSetup,
  technician: estate.stateFor(users[TECHS[0]]).needsSetup,
};
if (Object.values(setupState).some(Boolean) || !orgGate) {
  fail(`the setup gate still asks for setup: ${JSON.stringify({ ...setupState, orgProfile: orgGate })}`);
}

const orgFinal = db.prepare('SELECT * FROM organizations WHERE id = ?').get(org.id);
const siteFinal = db.prepare('SELECT * FROM tenants WHERE id = ?').get(site.id);
const rows = db.prepare(`
  SELECT u.username, u.role, u.public_id, o.name AS org, t.name AS site
    FROM users u
    LEFT JOIN organizations o ON o.id = u.organization_id
    LEFT JOIN tenants t ON t.id = u.tenant_id
   ORDER BY CASE u.role WHEN 'owner' THEN 0 WHEN 'site_manager' THEN 1 ELSE 2 END, u.id`).all();

console.log(`Database:     ${TARGET}`);
console.log(`Organization: ${orgFinal.name} (id ${orgFinal.id}, ${orgFinal.status}, short code ${orgFinal.short_code || '-'})`);
console.log(`Site:         ${siteFinal.name} (id ${siteFinal.id}), SPOC barry, setup complete ${siteFinal.setup_completed_at} UTC`);
console.log(`New accounts this run: ${made.length ? made.join(', ') : 'none (all already there)'}`);
console.log('');
const w = { u: 10, r: 13, p: 9, o: Math.max(12, orgFinal.name.length) };
const line = (a, b, c, d, e) => `${a.padEnd(w.u)}  ${b.padEnd(w.r)}  ${c.padEnd(w.p)}  ${d.padEnd(w.o)}  ${e}`;
console.log(line('USERNAME', 'ROLE', 'ID', 'ORG', 'SITE'));
console.log(line('-'.repeat(w.u), '-'.repeat(w.r), '-'.repeat(w.p), '-'.repeat(w.o), '-'.repeat(12)));
for (const r of rows) {
  const siteName = r.role === 'owner' ? '-' : (r.site || '-');
  console.log(line(r.username, r.role, r.public_id || '-', r.org || '-', siteName));
}
console.log('');
console.log(`${rows.length} accounts. Passwords are not printed.`);
console.log('');
console.log('Start the server on this file (from the server folder):');
console.log(dataPaths.isDefault
  ? '  npm start   (this is data/db/auth.db)'
  : `  RACKTRACK_AUTH_DB=${JSON.stringify(TARGET)} PORT=<port> node app.js`);

db.pragma('wal_checkpoint(TRUNCATE)');
process.exit(0);
