#!/usr/bin/env node
/**
 * Full-corpus backup for RackTrack.
 *
 * reset-data.js only ever backed up the two .db files, only when a reset ran,
 * and only onto the same volume. The scan-image corpus — every rack photo,
 * render and result in the scan tree — had no backup at all. A
 * volume loss or a bad `rm` destroyed it irrecoverably. This script closes
 * that gap.
 *
 * What it captures, into a single timestamped snapshot directory:
 *   - data/db/auth.db, logs.db and index.db   (WAL-checkpointed first, then copied
 *     and verified — same proven approach reset-data.js uses, so the newest
 *     committed rows in the -wal sidecar are never silently dropped)
 *   - data/orgs/   (the whole scan corpus, matrices and training memory)
 *   - manifest.json (timestamp, byte sizes, file counts, verified page counts)
 *
 * READ-ONLY at the source: it only reads the databases and directories and
 * writes into the backup directory. It never mutates, resets, or deletes
 * production data. Old *snapshots* are rotated out (keep the newest N).
 *
 * Destination — set an OFF-VOLUME path so a volume loss can't take the backup
 * with it:
 *   RACKTRACK_BACKUP_DIR=/mnt/backups/racktrack   (required unless --dry-run)
 *   RACKTRACK_BACKUP_KEEP=7                        (snapshots to retain; default 7)
 *
 *   Preview (writes nothing):  node scripts/backup.js --dry-run
 *   Run:                       RACKTRACK_BACKUP_DIR=/mnt/backups node scripts/backup.js
 *   Same-volume (discouraged): add --allow-same-volume
 *
 * Schedule it (cron / Task Scheduler) daily. On Windows production, a Scheduled
 * Task running this nightly is the intended cadence.
 */
'use strict';
const path = require('path');
const fs = require('fs');
const os = require('os');
const Database = require('better-sqlite3');

const DRY_RUN = process.argv.includes('--dry-run');
const ALLOW_SAME_VOLUME = process.argv.includes('--allow-same-volume');

const ROOT = path.resolve(__dirname, '..');            // server/
const REPO_ROOT = path.resolve(ROOT, '..');            // repo root
// RACKTRACK_AUTH_DB, else data/db/auth.db: see lib/data_paths.js.
const dataPaths = require('../lib/data_paths');
const DATA = dataPaths.DATA_DIR;
const AUTH_DB = dataPaths.AUTH_DB;
const LOGS_DB = dataPaths.LOG_DB;
// The scan tree: every organization's scans, matrices and training memory
// (lib/store.js). The caches are not worth keeping.
const ORGS = path.join(dataPaths.DATA_ROOT, 'orgs');

const KEEP = Math.max(1, parseInt(process.env.RACKTRACK_BACKUP_KEEP, 10) || 7);
const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
const say = (...a) => console.log(...a);

function fail(msg) { console.error(`\n✗ ${msg}`); process.exit(1); }

function dirSize(dir) {
  let bytes = 0, files = 0;
  const walk = (d) => {
    let ents;
    try { ents = fs.readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of ents) {
      const full = path.join(d, e.name);
      try {
        if (e.isDirectory()) walk(full);
        else if (e.isFile()) { bytes += fs.statSync(full).size; files++; }
      } catch { /* vanished mid-scan */ }
    }
  };
  if (fs.existsSync(dir)) walk(dir);
  return { bytes, files };
}

const mb = (b) => (b / 1e6).toFixed(1) + ' MB';

// Refuse a destination on the same filesystem as the source, unless overridden.
// Best-effort: compares st_dev of the backup root against the repo root.
function sameVolume(a, b) {
  try { return fs.statSync(a).dev === fs.statSync(b).dev; } catch { return false; }
}

function backupDb(file, snapDir) {
  if (!fs.existsSync(file)) { say(`  skip (absent)  ${path.basename(file)}`); return null; }
  const dest = path.join(snapDir, path.basename(file));
  if (DRY_RUN) { say(`  would copy DB  ${path.basename(file)}  (after WAL checkpoint)`); return null; }
  // WAL-checkpoint so the -wal sidecar is folded into the main file, then copy,
  // then reopen the copy and read its page_count to prove it's a valid DB.
  const db = new Database(file);
  db.pragma('wal_checkpoint(TRUNCATE)');
  db.close();
  fs.copyFileSync(file, dest);
  const check = new Database(dest, { readonly: true });
  const pages = check.pragma('page_count', { simple: true });
  check.close();
  // The verify-open leaves empty -wal/-shm sidecars next to the copy; the data
  // is fully inside the .db (checkpoint folded the source WAL in before copy),
  // so drop them to keep the snapshot to a single clean file per database.
  for (const side of ['-wal', '-shm']) {
    try { fs.rmSync(dest + side, { force: true }); } catch { /* ignore */ }
  }
  say(`  backed up DB   ${path.basename(file)}  (${pages} pages, WAL folded in)`);
  return { file: path.basename(file), pages, bytes: fs.statSync(dest).size };
}

function backupTree(src, snapDir, name) {
  const { bytes, files } = dirSize(src);
  if (!fs.existsSync(src)) { say(`  skip (absent)  ${name}/`); return { name, files: 0, bytes: 0 }; }
  if (DRY_RUN) { say(`  would copy     ${name}/  (${files} files, ${mb(bytes)})`); return { name, files, bytes }; }
  fs.cpSync(src, path.join(snapDir, name), { recursive: true, errorOnExist: false });
  say(`  copied         ${name}/  (${files} files, ${mb(bytes)})`);
  return { name, files, bytes };
}

function rotate(backupRoot) {
  let snaps;
  try {
    snaps = fs.readdirSync(backupRoot, { withFileTypes: true })
      .filter(e => e.isDirectory() && /^snapshot-/.test(e.name))
      .map(e => e.name).sort();          // ISO stamps sort chronologically
  } catch { return; }
  const excess = snaps.slice(0, Math.max(0, snaps.length - KEEP));
  for (const old of excess) {
    const p = path.join(backupRoot, old);
    if (DRY_RUN) { say(`  would prune    ${old}`); continue; }
    try { fs.rmSync(p, { recursive: true, force: true }); say(`  pruned old     ${old}`); }
    catch (e) { say(`  prune failed   ${old}: ${e.message}`); }
  }
}

function main() {
  const backupRoot = process.env.RACKTRACK_BACKUP_DIR
    || (DRY_RUN ? path.join(os.tmpdir(), 'racktrack-backup-preview') : null);
  if (!backupRoot) {
    fail('Set RACKTRACK_BACKUP_DIR to an off-volume path (or pass --dry-run to preview).');
  }
  if (!DRY_RUN && sameVolume(path.dirname(backupRoot) === backupRoot ? backupRoot : path.resolve(backupRoot, '.'), REPO_ROOT) && !ALLOW_SAME_VOLUME) {
    fail(`Backup dir ${backupRoot} is on the same volume as the data it protects — a volume loss would take both.\n  Point RACKTRACK_BACKUP_DIR at another disk, or pass --allow-same-volume to override.`);
  }

  const snapDir = path.join(backupRoot, `snapshot-${stamp}`);
  say(`RackTrack backup ${DRY_RUN ? '(DRY RUN — nothing written)' : ''}`);
  say(`  destination    ${snapDir}`);
  say('');

  if (!DRY_RUN) fs.mkdirSync(snapDir, { recursive: true });

  const manifest = { createdAt: new Date().toISOString(), snapshot: `snapshot-${stamp}`, databases: [], trees: [] };
  for (const db of [AUTH_DB, LOGS_DB, dataPaths.INDEX_DB]) {
    const r = backupDb(db, snapDir);
    if (r) manifest.databases.push(r);
  }
  manifest.trees.push(backupTree(ORGS, snapDir, 'orgs'));

  if (!DRY_RUN) {
    fs.writeFileSync(path.join(snapDir, 'manifest.json'), JSON.stringify(manifest, null, 2));
  }

  const totalBytes = manifest.trees.reduce((s, t) => s + t.bytes, 0)
    + manifest.databases.reduce((s, d) => s + (d.bytes || 0), 0);
  say('');
  say(`  total          ${mb(totalBytes)} across ${manifest.trees.reduce((s, t) => s + t.files, 0)} artifact files`);
  say('');
  say(`Rotation (keep ${KEEP}):`);
  rotate(backupRoot);
  say(DRY_RUN ? '\nDry run only — re-run without --dry-run to write the snapshot.' : '\n✓ Backup complete.');
}

main();
