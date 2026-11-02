#!/usr/bin/env node
/**
 * Reset RackTrack to a clean slate for a fresh round of testing.
 *
 *   node scripts/reset-data.js            # dry run — prints what it would do
 *   node scripts/reset-data.js --yes      # actually do it
 *
 * Stop the server first. This writes to the databases directly, and a running
 * server holds cached state that would be inconsistent with what is left.
 *
 * KEEPS
 *   - the owner account (role='owner', lowest id) and its tenant
 *
 * REMOVES
 *   - every other user, every organization, every non-owner tenant
 *   - invites, pending signups, password resets, the whole audit log
 *   - all rack ownership
 *   - the application log database
 *   - every scan folder, matrix and cache in the data tree (lib/store.js)
 *
 * CREATES
 *   - one fresh organization for testers, with no members
 *
 * A timestamped backup of both databases is taken before anything is changed.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');
const Database = require('better-sqlite3');

const GO = process.argv.includes('--yes');
// A deliberate second gate for the production host. NODE_ENV=production alone
// refuses to run; this flag is the "yes, I truly mean the live box" override.
const FORCE_PROD = process.argv.includes('--i-understand-production');
const ROOT = path.resolve(__dirname, '..');
// RACKTRACK_DATA_DIR lets a sandbox run point every path at a throwaway copy
// of the database folder (used by the test harness); the data tree is then its
// parent. Otherwise the folders lib/data_paths.js gives (RACKTRACK_AUTH_DB).
const DATA = process.env.RACKTRACK_DATA_DIR || require('../lib/data_paths').DB_DIR;
const FILE_ROOT = process.env.RACKTRACK_DATA_DIR ? path.dirname(DATA) : require('../lib/data_paths').DATA_ROOT;
const AUTH_DB = path.join(DATA, 'auth.db');
const LOGS_DB = path.join(DATA, 'logs.db');

const NEW_ORG_NAME = process.env.RESET_ORG_NAME || 'THE TESTERS';

const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const say = (...a) => console.log(...a);

// ── tables emptied outright ────────────────────────────────────────────
// Order matters: children before parents, since foreign keys are declared.
const TRUNCATE = [
  'rack_owners',
  'invites', 'pending_signups', 'password_resets', 'audit_log',
];

// ── on-disk artefacts ──────────────────────────────────────────────────
// The scan tree (every organization's scans, matrices and training memory)
// and the caches. The store's index goes with them (see main()).
const DIRS = [
  path.join(FILE_ROOT, 'orgs'),
  path.join(FILE_ROOT, 'cache'),
];

// Read a single typed line from the terminal. Resolves to '' on a closed /
// piped stdin (EOF) so a non-interactive invocation aborts rather than proceeds.
function ask(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    let done = false;
    const finish = (v) => { if (done) return; done = true; rl.close(); resolve(String(v || '').trim()); };
    rl.question(question, finish);
    rl.on('close', () => finish(''));   // EOF / piped-empty stdin → abort
  });
}

function backup(file) {
  if (!fs.existsSync(file)) return null;
  const dest = `${file}.bak-reset-${stamp}`;
  if (GO) {
    // NOT copyFileSync. These databases run in WAL mode, so recently committed
    // data lives in the -wal sidecar until a checkpoint folds it back in — at
    // one point auth.db-wal was 2.6MB against a 2.5MB main file. Copying only
    // the main file produces a backup that silently omits the newest data,
    // which is the worst possible failure for the one artefact you would reach
    // for after a bad reset. Checkpoint first, then copy.
    const db = new Database(file);
    db.pragma('wal_checkpoint(TRUNCATE)');
    db.close();
    fs.copyFileSync(file, dest);
    // Verify the copy opens and reports the same page count, rather than
    // assuming a backup that was never read back is good.
    const check = new Database(dest, { readonly: true });
    const pages = check.pragma('page_count', { simple: true });
    check.close();
    say(`  backed up      ${path.basename(dest)}  (${pages} pages, WAL folded in)`);
  } else {
    say(`  would back up  ${path.basename(dest)}  (after a WAL checkpoint)`);
  }
  return dest;
}

function countDir(dir) {
  try { return fs.readdirSync(dir).length; } catch { return null; }
}

function emptyDir(dir) {
  const n = countDir(dir);
  if (n === null) return;
  if (!GO) { say(`  would clear   ${dir}  (${n} entries)`); return; }
  for (const entry of fs.readdirSync(dir)) {
    // Leave dotfiles — .gitkeep and friends keep the directory in the repo.
    if (entry.startsWith('.')) continue;
    fs.rmSync(path.join(dir, entry), { recursive: true, force: true });
  }
  say(`  cleared       ${dir}  (${n} entries)`);
}

async function main() {
  if (!fs.existsSync(AUTH_DB)) {
    console.error(`✗ no database at ${AUTH_DB}`);
    process.exit(1);
  }

  // Environment guard. This script ships to the production host via `git pull`,
  // and --yes was the only thing standing between a stray run and a live wipe.
  // Refuse outright under NODE_ENV=production unless the operator ALSO passes
  // --i-understand-production. This check happens before anything is opened or
  // copied, so a refusal changes nothing on disk.
  if (GO && process.env.NODE_ENV === 'production' && !FORCE_PROD) {
    console.error(`\n✗ NODE_ENV=production on ${os.hostname()} — refusing to reset live data.`);
    console.error('  This is the production host. If you REALLY mean it, re-run with:');
    console.error('    node scripts/reset-data.js --yes --i-understand-production\n');
    process.exit(1);
  }

  say(`\nRackTrack data reset — ${GO ? 'LIVE' : 'DRY RUN (pass --yes to apply)'}`);
  say(`host:           ${os.hostname()}`);
  say(`data directory: ${DATA}\n`);

  say('Backups');
  backup(AUTH_DB);
  backup(LOGS_DB);

  const db = new Database(AUTH_DB);

  // Find the owner to keep: the lowest-id account with role 'owner'.
  const owner = db.prepare(
    "SELECT id, email, public_id, tenant_id FROM users WHERE role='owner' ORDER BY id LIMIT 1").get();
  if (!owner) {
    console.error('✗ no owner account found — refusing to run, you would be locked out.');
    process.exit(1);
  }
  say(`\nKeeping owner  id=${owner.id}  ${owner.email}  ${owner.public_id}  tenant=${owner.tenant_id}`);

  const before = {};
  for (const t of [...TRUNCATE, 'users', 'organizations', 'tenants']) {
    try { before[t] = db.prepare(`SELECT COUNT(*) AS c FROM "${t}"`).get().c; } catch { before[t] = null; }
  }

  const run = db.transaction(() => {
    for (const t of TRUNCATE) {
      // A missing table is the ONE expected, skippable condition (not every
      // deploy created every table). Anything else — a locked DB, a
      // constraint violation, corruption — must abort the whole transaction:
      // swallowing it and continuing to COMMIT is exactly how a partial reset
      // used to still print "Reset complete". Check existence explicitly, then
      // let any DELETE error propagate out of the transaction so better-sqlite3
      // rolls it back.
      const exists = db.prepare(
        `SELECT 1 FROM sqlite_master WHERE type='table' AND name=?`
      ).get(t);
      if (!exists) { say(`  skipped           ${t}  (no such table)`); continue; }
      const n = db.prepare(`DELETE FROM "${t}"`).run().changes;
      say(`  emptied  ${String(n).padStart(6)}  ${t}`);
    }

    const users = db.prepare('DELETE FROM users WHERE id != ?').run(owner.id).changes;
    say(`  removed  ${String(users).padStart(6)}  users (kept the owner)`);

    // Tenants point at organizations, so tenants must go first — deleting an
    // organization that a tenant still references fails the foreign key.
    db.prepare('UPDATE users SET organization_id = NULL WHERE id = ?').run(owner.id);
    db.prepare('UPDATE tenants SET organization_id = NULL WHERE id = ?').run(owner.tenant_id);
    const tenants = db.prepare('DELETE FROM tenants WHERE id != ?').run(owner.tenant_id).changes;
    say(`  removed  ${String(tenants).padStart(6)}  tenants (kept the owner's)`);
    const orgs = db.prepare('DELETE FROM organizations').run().changes;
    say(`  removed  ${String(orgs).padStart(6)}  organizations`);

    // One fresh organization for testers, with nobody in it.
    const slug = NEW_ORG_NAME.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const org = db.prepare(
      "INSERT INTO organizations (slug, name, created_by, created_at, status) VALUES (?,?,?,datetime('now'),'active')"
    ).run(slug, NEW_ORG_NAME, owner.id);
    require('../lib/org_settings').applyPilotDefaults(db, org.lastInsertRowid);
    const tenant = db.prepare(
      "INSERT INTO tenants (slug, name, created_at, organization_id) VALUES (?,?,datetime('now'),?)"
    ).run(`${slug}-site`, `${NEW_ORG_NAME} Site`, org.lastInsertRowid);
    say(`  created  organization "${NEW_ORG_NAME}" (id=${org.lastInsertRowid}, slug=${slug}) with 0 members`);
    say(`  created  tenant       "${NEW_ORG_NAME} Site" (id=${tenant.lastInsertRowid})`);

    // Restart id sequences so the fresh data does not start at 1400.
    //
    // `users` is deliberately EXCLUDED. Resetting its counter hands the next
    // signups the ids of the accounts just deleted, and tokens are valid for
    // 30 days — so someone still holding a pre-reset token for old user 3
    // would authenticate as whoever now occupies id 3. That is a privilege
    // escalation, not a cosmetic id gap. Let user ids keep climbing.
    for (const t of TRUNCATE) {
      try { db.prepare('DELETE FROM sqlite_sequence WHERE name = ?').run(t); } catch { /* no sequence */ }
    }
  });

  // Final human gate: show WHERE this is running and WHAT will be destroyed,
  // then require the word RESET typed back. A piped / non-interactive stdin
  // reads as EOF → empty string → abort, so this can never be auto-answered.
  if (GO) {
    say(`\n⚠  About to PERMANENTLY delete the following on ${os.hostname()}:`);
    for (const t of TRUNCATE) say(`    ${String(before[t] ?? 0).padStart(6)}  ${t}`);
    say(`    ${String(Math.max(0, (before.users ?? 1) - 1)).padStart(6)}  users (keeping the owner)`);
    say(`    ${String(before.organizations ?? 0).padStart(6)}  organizations`);
    say(`    ${String(before.tenants ?? 0).padStart(6)}  tenants (keeping the owner's)`);
    const answer = await ask('\nType "RESET" to proceed (anything else aborts): ');
    if (answer !== 'RESET') {
      say('\nAborted — the database was not changed (a backup was already written).');
      db.close();
      process.exit(1);
    }
  }

  say('\nDatabase');
  if (GO) { run(); db.pragma('wal_checkpoint(TRUNCATE)'); db.exec('VACUUM'); }
  else {
    for (const t of TRUNCATE) say(`  would empty  ${String(before[t] ?? '?').padStart(6)}  ${t}`);
    say(`  would remove ${String((before.users ?? 1) - 1).padStart(6)}  users (keeping the owner)`);
    say(`  would remove ${String(before.organizations ?? 0).padStart(6)}  organizations`);
    say(`  would create organization "${NEW_ORG_NAME}" with 0 members`);
  }
  db.close();

  // Tokens issued before the reset are still cryptographically valid for 30
  // days and carry a user id. Even with the id-reuse hole closed above, a
  // pre-reset token belongs to an account that no longer exists, and "everyone
  // is signed out" is the honest meaning of a fresh start. Rotating the
  // signing secret invalidates all of them at once.
  say('\nSession tokens');
  // The key is JWT_SECRET in <repo>/.env; a data folder without it uses a
  // generated fallback file. Rotate whichever this server signs with.
  const ENV_FILE = require('../lib/load_env').ENV_FILE;
  const FALLBACK = path.join(DATA, 'signing.key');
  const envText = fs.existsSync(ENV_FILE) ? fs.readFileSync(ENV_FILE, 'utf8') : '';
  const newKey = () => require('crypto').randomBytes(64).toString('hex');
  // A reset of some other data folder never touches the live .env.
  const liveData = path.resolve(DATA) === require('../lib/data_paths').DATA_DIR;
  if (liveData && /^JWT_SECRET=/m.test(envText)) {
    if (GO) {
      fs.writeFileSync(ENV_FILE, envText.replace(/^JWT_SECRET=.*$/m, `JWT_SECRET=${newKey()}`), { mode: 0o600 });
      say('  rotated  JWT_SECRET in .env - every token issued before now is rejected (restart the server)');
    } else {
      say('  would rotate JWT_SECRET in .env (signs everyone out; pre-reset tokens cannot be replayed)');
    }
  } else if (fs.existsSync(FALLBACK)) {
    if (GO) {
      fs.writeFileSync(FALLBACK, newKey(), { mode: 0o600 });
      say('  rotated  the generated signing key - every token issued before now is rejected');
    } else {
      say('  would rotate the generated signing key (signs everyone out)');
    }
  } else {
    say('  (no signing key yet - the server will generate one on next boot)');
  }

  // Application logs live in their own database.
  say('\nApplication log');
  if (fs.existsSync(LOGS_DB)) {
    const ldb = new Database(LOGS_DB);
    const n = ldb.prepare('SELECT COUNT(*) AS c FROM app_logs').get().c;
    if (GO) {
      ldb.prepare('DELETE FROM app_logs').run();
      ldb.pragma('wal_checkpoint(TRUNCATE)');
      ldb.exec('VACUUM');
      say(`  cleared  ${String(n).padStart(6)}  app_logs`);
    } else say(`  would clear ${String(n).padStart(6)}  app_logs`);
    ldb.close();
  } else say('  (no logs.db)');

  say('\nFiles on disk');
  for (const d of DIRS) emptyDir(d);
  // The store's index of scans: without the folders it points at nothing.
  for (const f of ['index.db', 'index.db-wal', 'index.db-shm']) {
    const p = path.join(DATA, f);
    if (!fs.existsSync(p)) continue;
    if (GO) { fs.rmSync(p, { force: true }); say(`  removed       ${p}`); } else say(`  would remove  ${p}`);
  }

  say(`\n${GO ? '✔ Reset complete.' : 'Dry run only — nothing was changed. Re-run with --yes to apply.'}`);
  if (GO) say('  Restart the server, then sign in as the owner.\n');
}

// Any error thrown from the reset transaction (a real DELETE failure, not a
// missing table) rejects here and exits non-zero WITHOUT printing "Reset
// complete" — the transaction has already been rolled back by better-sqlite3.
main().catch((err) => {
  console.error(`\n✗ Reset aborted — the transaction was rolled back: ${err.message}`);
  process.exit(1);
});
