#!/usr/bin/env node
/**
 * Reconcile rack ownership (auth.db) against scan folders (the store's index).
 *
 * RackTrack keeps a rack in TWO stores that have no transactional link:
 *   - WHO owns it:   auth.db  →  rack_owners(tenant_id, rack_id, ...)
 *   - the scan data: the data tree  →  lib/store.js (index.db + scan folder)
 *
 * Because claiming ownership and writing the folder are separate, non-atomic
 * steps, the two can drift. This report is the read-only set-difference of the
 * two, so an operator can see drift before it becomes a support ticket:
 *
 *   OWNER, NO FOLDER  — a tenant owns a rack whose folder is gone. Authorized
 *                       reads of it resolve to a missing directory (broken rack).
 *   FOLDER, NO OWNER  — a scan folder nobody owns. Unreachable dead weight and
 *                       a GC candidate (see lib/orphan_gc.js).
 *
 * READ-ONLY BY DEFAULT: opens auth.db with { readonly: true } and only lists
 * the filesystem. It never writes, deletes, migrates, or touches production
 * data — safe to run against any checkout, including on the Mac dev copy.
 *
 *   Report:  cd server && node scripts/reconcile-racks.js
 *   CI/cron: add --strict to exit 1 when any drift is found.
 *
 * OPT-IN HEAL (owner-rows with no folder only): --prune-dangling-owners removes
 * rack_owners rows whose folder is gone — the "authorized read → missing dir"
 * case. It is a DRY RUN unless you ALSO pass --apply, and only then does it open
 * the DB read-write. FOLDER, NO OWNER is left to lib/orphan_gc.js (which owns
 * folder deletion + retention). Run heal on the SERVER after reviewing a report
 * — never blindly from a dev checkout, whose drift differs from production.
 *
 *   Preview heal: node scripts/reconcile-racks.js --prune-dangling-owners
 *   Apply heal:   node scripts/reconcile-racks.js --prune-dangling-owners --apply
 */
'use strict';
const path = require('path');
const fs = require('fs');
const Database = require('better-sqlite3');
const { RACK_ID_RE } = require('../lib/rack_access');

const DB_PATH = require('../lib/data_paths').AUTH_DB;
const store = require('../lib/store');

function ownedRackIds() {
  if (!fs.existsSync(DB_PATH)) {
    console.error(`auth.db not found at ${DB_PATH}`);
    process.exit(2);
  }
  const db = new Database(DB_PATH, { readonly: true });
  try {
    return new Set(
      db.prepare('SELECT DISTINCT rack_id FROM rack_owners').all().map(r => r.rack_id),
    );
  } finally {
    db.close();
  }
}

// Scans the store's index holds whose folder is really there.
function folderRackIds() {
  return new Set(store.listScans()
    .filter((sc) => RACK_ID_RE.test(sc.scanId))
    .filter((sc) => { try { return fs.statSync(sc.dir).isDirectory(); } catch { return false; } })
    .map((sc) => sc.scanId));
}

function main() {
  const strict = process.argv.includes('--strict');
  const prune = process.argv.includes('--prune-dangling-owners');
  const owned = ownedRackIds();
  const folders = folderRackIds();

  const ownerNoFolder = [...owned].filter(id => !folders.has(id)).sort();
  const folderNoOwner = [...folders].filter(id => !owned.has(id)).sort();

  console.log('Rack ownership ↔ artifact reconciliation');
  console.log(`  rack_owners rows (distinct): ${owned.size}`);
  console.log(`  scan folders:                ${folders.size}`);
  console.log('');

  console.log(`OWNER, NO FOLDER — owned but no scan data on disk (${ownerNoFolder.length}):`);
  if (ownerNoFolder.length === 0) console.log('  (none)');
  else ownerNoFolder.forEach(id => console.log(`  ${id}`));
  console.log('');

  console.log(`FOLDER, NO OWNER — scan data nobody owns / GC candidate (${folderNoOwner.length}):`);
  if (folderNoOwner.length === 0) console.log('  (none)');
  else folderNoOwner.forEach(id => console.log(`  ${id}`));
  if (folderNoOwner.length) console.log('  → clean up with lib/orphan_gc.js (owns folder deletion + retention).');

  const drift = ownerNoFolder.length + folderNoOwner.length;
  if (drift === 0) console.log('\nIn sync — no drift.');
  else console.log(`\n${drift} rack(s) out of sync.`);

  if (prune) healDanglingOwners(ownerNoFolder);
  if (strict && drift > 0) process.exit(1);
}

/** Remove rack_owners rows whose folder is gone. Dry-run unless --apply. */
function healDanglingOwners(ownerNoFolder) {
  const apply = process.argv.includes('--apply');
  console.log(`\n── Heal: prune ${ownerNoFolder.length} dangling owner row(s) ${apply ? '(APPLY)' : '(dry run — pass --apply to act)'} ──`);
  if (ownerNoFolder.length === 0) { console.log('  nothing to prune.'); return; }
  if (!apply) {
    ownerNoFolder.forEach(id => console.log(`  would delete rack_owners WHERE rack_id = ${id}`));
    return;
  }
  const db = new Database(DB_PATH);   // read-write, only in --apply
  try {
    const stmt = db.prepare('DELETE FROM rack_owners WHERE rack_id = ?');
    const tx = db.transaction(ids => { let n = 0; for (const id of ids) n += stmt.run(id).changes; return n; });
    const removed = tx(ownerNoFolder);
    console.log(`  deleted ${removed} owner row(s).`);
  } finally {
    db.close();
  }
}

main();
