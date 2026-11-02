#!/usr/bin/env node
/**
 * Remove an organization COMPLETELY - its users, sites (tenants), invites, the
 * org row itself, and the rack rows that hang off its Sites. References we
 * keep (audit rows) have their pointers nulled instead.
 *
 * ⚠️ DESTRUCTIVE. Refuses to touch the 'racktrack' org.
 *
 *   Dry run (default):  cd server && node scripts/remove-org.js <slug-or-name> ...
 *   Apply:              cd server && node scripts/remove-org.js <slug-or-name> ... --yes
 *
 *     e.g. node scripts/remove-org.js sprintpark-1de4 --yes
 *
 * foreign_keys is kept ON so ON DELETE CASCADE actually fires and nothing is
 * orphaned; the explicit child-first deletes below make the teardown order
 * deterministic. Set RACKTRACK_AUTH_DB to run against a throwaway copy - the
 * test harness does this; never point it at the live data/db.
 */
'use strict';
const path = require('path');
const fs = require('fs');
const Database = require('better-sqlite3');

const argv   = process.argv.slice(2);
const GO     = argv.includes('--yes');
const idents = argv.filter((a) => !a.startsWith('--'));
if (!idents.length) {
  console.error('Usage: node scripts/remove-org.js <slug-or-name> ... [--yes]');
  console.error('  Without --yes this is a DRY RUN and changes nothing.');
  process.exit(1);
}

const DB_PATH = require('../lib/data_paths').AUTH_DB;
if (!fs.existsSync(DB_PATH)) { console.error(`✗ no database at ${DB_PATH}`); process.exit(1); }

const PROTECTED = new Set(['racktrack']); // never delete the live org

// Ids come straight out of the DB as integers, so inlining them in an IN ()
// list is injection-safe; '0' is a set that matches nothing (empty arrays).
const inInts = (arr) => (arr.length ? arr.join(',') : '0');

// ── Backup (WAL-safe) ───────────────────────────────────────────────────
// Only for a real run. A bare copyFileSync misses everything still sitting in
// the -wal sidecar (which has run to multiple MB against a small main file), so
// checkpoint first, then copy, then read the copy back to prove it opens.
function backup() {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const dest = `${DB_PATH}.pre-remove-org-${stamp}.bak`;
  const cdb = new Database(DB_PATH);
  cdb.pragma('wal_checkpoint(TRUNCATE)');
  cdb.close();
  fs.copyFileSync(DB_PATH, dest);
  const check = new Database(dest, { readonly: true });
  const pages = check.pragma('page_count', { simple: true });
  check.close();
  console.log(`Backed up auth.db → ${path.basename(dest)}  (${pages} pages, WAL folded in)`);
}

if (GO) backup();

const db = new Database(DB_PATH);
// KEEP foreign keys ON. Turning them OFF (as this script used to) does not defer
// checks - it disables ON DELETE CASCADE entirely, so deleting users left their
// child rows behind as orphans.
db.pragma('foreign_keys = ON');

const has = (t) => !!db.prepare(`SELECT name FROM sqlite_master WHERE type='table' AND name=?`).get(t);

function planFor(ident) {
  const org = db.prepare(
    `SELECT id, name, slug, status FROM organizations WHERE slug = ? COLLATE NOCASE OR name = ? COLLATE NOCASE`
  ).get(ident, ident);
  if (!org) { console.log(`  · '${ident}': no such org - skipped`); return null; }
  if (PROTECTED.has(String(org.slug).toLowerCase())) {
    console.log(`  · '${ident}' is protected (${org.slug}) - refused`); return null;
  }
  const users = db.prepare(`SELECT COUNT(*) c FROM users   WHERE organization_id = ?`).get(org.id).c;
  const sites = db.prepare(`SELECT COUNT(*) c FROM tenants WHERE organization_id = ?`).get(org.id).c;
  const invs  = has('invites')
    ? db.prepare(`SELECT COUNT(*) c FROM invites WHERE organization_id = ?`).get(org.id).c : 0;
  return { org, users, sites, invs };
}

// ── One org, one transaction ────────────────────────────────────────────
const removeOne = db.transaction((org) => {
  const orgId = org.id;
  const userIds   = db.prepare(`SELECT id FROM users   WHERE organization_id = ?`).all(orgId).map((r) => r.id);
  const tenantIds = db.prepare(`SELECT id FROM tenants WHERE organization_id = ?`).all(orgId).map((r) => r.id);
  const U = inInts(userIds);
  const T = inInts(tenantIds);

  const del = (table, whereSql) =>
    has(table) ? db.prepare(`DELETE FROM ${table} WHERE ${whereSql}`).run().changes : 0;

  // Tenant-scoped rack data goes; kept tables (audit) have their pointers nulled.
  del('rack_owners', `tenant_id IN (${T})`);
  if (has('audit_log'))       db.prepare(`UPDATE audit_log       SET tenant_id  = NULL WHERE tenant_id  IN (${T})`).run();
  if (has('pending_signups')) db.prepare(`UPDATE pending_signups SET tenant_id  = NULL WHERE tenant_id  IN (${T})`).run();
  if (has('rack_owners'))   db.prepare(`UPDATE rack_owners   SET created_by = NULL WHERE created_by IN (${U})`).run();
  if (has('invites'))       db.prepare(`UPDATE invites       SET invited_by = NULL WHERE invited_by IN (${U})`).run();
  // organizations.created_by → users has no cascade; null any that point into
  // this org's members so deleting them can't trip a RESTRICT.
  db.prepare(`UPDATE organizations SET created_by = NULL WHERE created_by IN (${U})`).run();

  const invc = del('invites', `organization_id = ${orgId}`);
  const usrc = db.prepare(`DELETE FROM users   WHERE organization_id = ?`).run(orgId).changes;
  const tenc = db.prepare(`DELETE FROM tenants WHERE organization_id = ?`).run(orgId).changes;
  db.prepare(`DELETE FROM organizations WHERE id = ?`).run(orgId);
  console.log(`  ✓ removed org '${org.name}' (${org.slug}, id ${orgId}): ${usrc} user(s), ${tenc} site(s), ${invc} invite(s)`);
});

console.log(GO ? 'Removing:' : 'DRY RUN (pass --yes to apply):');
for (const ident of idents) {
  const plan = planFor(ident);
  if (!plan) continue;
  if (GO) {
    removeOne(plan.org);
  } else {
    console.log(`  · would remove '${plan.org.name}' (${plan.org.slug}, id ${plan.org.id}, ${plan.org.status}): `
      + `${plan.users} user(s), ${plan.sites} site(s), ${plan.invs} invite(s), plus their rack rows`);
  }
}

console.log('\n=== Remaining organizations ===');
for (const o of db.prepare(`SELECT id, name, slug, status FROM organizations ORDER BY id`).all()) {
  console.log(`  ${o.id}  ${o.name}  (${o.slug})  ${o.status}`);
}
console.log(GO ? '\nDone. Restart the server if it was running.'
               : '\nDry run only - nothing was changed. Re-run with --yes to apply.');
db.close();
