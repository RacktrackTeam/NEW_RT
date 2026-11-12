/**
 * The data store: the one module that builds a path into the data tree.
 *
 * Layout (owner decisions D1-D10, 28 Sep 2026), under DATA_ROOT from
 * lib/data_paths.js (one setting, RACKTRACK_AUTH_DB, decides it):
 *
 *   orgs/<org-slug>/
 *     org.json                     mirror of the organizations row
 *     training/                    the organization's correction memory
 *                                  (pipeline/active_learning store root)
 *     sites/<site-slug>/
 *       site.json                  mirror of the site (tenants) row
 *       matrix/                    the imported patching matrix (lib/matrix.js)
 *       checks/<check id>.json     A-end / Z-end label checks (lib/checks.js)
 *       racks/<rack-key>/
 *         rack.json                key, label, ids, created_at, latest_scan
 *         scans/<YYYY-MM-DD_HH-MM-SS>_<8 hex of the scan id>/
 *           photo.jpg              the picture as taken (upright JPEG)
 *           annotated.jpg          the drawn result, JPEG quality 85
 *           thumb.jpg              360 px wide, made on first request
 *           result.json            the one scan document
 *           meta.json              who, where, when, how
 *           corrections.jsonl      append only: {at, user, kind, target, before, after}
 *           uncapped.json          ports a person marked uncapped (dust cap off, no cable)
 *           zones/                 zone close-ups: <zone id>.jpg and <zone id>.json
 *           crops/                 device and port cutouts
 *           engine/                the engine's working files (regenerable)
 *           backups/               copies kept aside before a re-read
 *   cache/
 *     ocr/                         the OCR cache
 *     uploads/                     in-flight uploads, removed once photo.jpg exists
 *     lookups/                     public specification answers per model
 *   db/                            auth.db, logs.db, scan-jobs.json, index.db
 *
 * Rack key: the slug of the confirmed rack label; until a label is confirmed,
 * the scan id (RK-XXXXXXXX). A scan id in a URL is resolved only through the
 * index (index.db, table scans), never by walking the tree.
 *
 * Slugs: lower case, a-z 0-9 and hyphens, from the name; unique within the
 * parent by appending -2, -3. Stored on the row as folder_slug (the rows'
 * existing slug column carries a random suffix and is used elsewhere).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const Database = require('better-sqlite3');
const dataPaths = require('./data_paths');

const DATA_ROOT = dataPaths.DATA_ROOT;
const NO_ORG = 'no-organization';
const NO_SITE = 'no-site';

class NotFoundError extends Error {
  constructor(message) {
    super(message);
    this.status = 404;
    this.code = 'ENOSCAN';
  }
}

// ── Slugs ─────────────────────────────────────────────────────────────
function slugify(name, fallback = 'item') {
  const s = String(name || '')
    .normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/g, '');
  return s || fallback;
}

/** base, base-2, base-3 ... the first one `taken` does not hold. */
function uniqueSlug(base, taken) {
  const set = taken instanceof Set ? taken : new Set(taken || []);
  if (!set.has(base)) return base;
  for (let i = 2; i < 10000; i++) {
    const c = `${base}-${i}`;
    if (!set.has(c)) return c;
  }
  return `${base}-${crypto.randomBytes(3).toString('hex')}`;
}

// A rack key or a folder name: never empty, never a path.
const KEY_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$/;
function safeKey(key) {
  const k = String(key || '');
  if (!KEY_RE.test(k) || k.includes('..')) throw new Error(`Not a usable rack key: ${k.slice(0, 40)}`);
  return k;
}

// ── Databases ─────────────────────────────────────────────────────────
let _auth = null;
function authDb() {
  if (_auth) return _auth;
  fs.mkdirSync(path.dirname(dataPaths.AUTH_DB), { recursive: true });
  _auth = new Database(dataPaths.AUTH_DB);
  _auth.pragma('journal_mode = WAL');
  return _auth;
}

function hasTable(db, name) {
  return !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
}
function hasColumn(db, table, col) {
  return db.prepare(`PRAGMA table_info(${table})`).all().some((c) => c.name === col);
}
function ensureColumn(db, table, col, ddl) {
  if (hasTable(db, table) && !hasColumn(db, table, col)) db.exec(`ALTER TABLE ${table} ADD COLUMN ${ddl}`);
}

let _index = null;
function indexDb() {
  if (_index) return _index;
  fs.mkdirSync(path.dirname(dataPaths.INDEX_DB), { recursive: true });
  _index = new Database(dataPaths.INDEX_DB);
  _index.pragma('journal_mode = WAL');
  _index.exec(`
    CREATE TABLE IF NOT EXISTS scans (
      scan_id    TEXT PRIMARY KEY,
      org_id     INTEGER,
      site_id    INTEGER,
      rack_key   TEXT NOT NULL,
      folder     TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_scans_site ON scans(site_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_scans_org  ON scans(org_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_scans_rack ON scans(site_id, rack_key);
  `);
  return _index;
}

/** Create the tree and open the index. Safe to call more than once. */
function init() {
  for (const d of ['orgs', path.join('cache', 'ocr'), path.join('cache', 'uploads')]) {
    fs.mkdirSync(path.join(DATA_ROOT, d), { recursive: true });
  }
  indexDb();
  return DATA_ROOT;
}

// ── Organizations and sites ───────────────────────────────────────────
function orgRow(orgId) {
  if (orgId == null) return null;
  const db = authDb();
  if (!hasTable(db, 'organizations')) return null;
  ensureColumn(db, 'organizations', 'folder_slug', 'folder_slug TEXT');
  return db.prepare('SELECT * FROM organizations WHERE id = ?').get(Number(orgId)) || null;
}

function siteRow(siteId) {
  if (siteId == null) return null;
  const db = authDb();
  if (!hasTable(db, 'tenants')) return null;
  ensureColumn(db, 'tenants', 'folder_slug', 'folder_slug TEXT');
  return db.prepare('SELECT * FROM tenants WHERE id = ?').get(Number(siteId)) || null;
}

function orgFolder(orgId) {
  if (orgId == null) return NO_ORG;
  const row = orgRow(orgId);
  if (!row) return `org-${Number(orgId)}`;
  if (row.folder_slug) return row.folder_slug;
  const db = authDb();
  const taken = new Set(db.prepare('SELECT folder_slug FROM organizations WHERE folder_slug IS NOT NULL')
    .all().map((r) => r.folder_slug));
  taken.add(NO_ORG);
  const slug = uniqueSlug(slugify(row.name, `org-${row.id}`), taken);
  db.prepare('UPDATE organizations SET folder_slug = ? WHERE id = ? AND folder_slug IS NULL').run(slug, row.id);
  return db.prepare('SELECT folder_slug FROM organizations WHERE id = ?').get(row.id).folder_slug;
}

function siteFolder(siteId) {
  if (siteId == null) return NO_SITE;
  const row = siteRow(siteId);
  if (!row) return `site-${Number(siteId)}`;
  if (row.folder_slug) return row.folder_slug;
  const db = authDb();
  const taken = new Set(db.prepare(
    'SELECT folder_slug FROM tenants WHERE folder_slug IS NOT NULL AND organization_id IS ?')
    .all(row.organization_id ?? null).map((r) => r.folder_slug));
  taken.add(NO_SITE);
  const slug = uniqueSlug(slugify(row.name, `site-${row.id}`), taken);
  db.prepare('UPDATE tenants SET folder_slug = ? WHERE id = ? AND folder_slug IS NULL').run(slug, row.id);
  return db.prepare('SELECT folder_slug FROM tenants WHERE id = ?').get(row.id).folder_slug;
}

/** The organization a site belongs to (the row is the truth), else the one given. */
function orgOfSite(siteId, fallbackOrgId = null) {
  const row = siteRow(siteId);
  if (row) return row.organization_id ?? null;
  return fallbackOrgId ?? null;
}

function writeIfChanged(file, text) {
  try { if (fs.readFileSync(file, 'utf8') === text) return; } catch { /* not there yet */ }
  writeFileAtomic(file, text);
}

/** org.json: id, name, slug, created_at. Written when the folder is first used and whenever it changed. */
function writeOrgMirror(orgId) {
  const dir = path.join(DATA_ROOT, 'orgs', orgFolder(orgId));
  fs.mkdirSync(dir, { recursive: true });
  const row = orgRow(orgId);
  const doc = row
    ? { id: row.id, name: row.name, slug: orgFolder(orgId), created_at: row.created_at || null }
    : { id: orgId ?? null, name: orgId == null ? 'No organization' : null, slug: orgFolder(orgId), created_at: null };
  writeIfChanged(path.join(dir, 'org.json'), JSON.stringify(doc, null, 2) + '\n');
  return dir;
}

/** site.json: id, name, slug, address, contact. */
function writeSiteMirror(siteId) {
  const orgId = orgOfSite(siteId);
  const dir = path.join(writeOrgMirror(orgId), 'sites', siteFolder(siteId));
  fs.mkdirSync(dir, { recursive: true });
  const row = siteRow(siteId);
  const doc = row
    ? {
      id: row.id, name: row.name, slug: siteFolder(siteId),
      address: row.address ?? null,
      contact: row.approver_email ?? null,
    }
    : { id: siteId ?? null, name: siteId == null ? 'No site' : null, slug: siteFolder(siteId), address: null, contact: null };
  writeIfChanged(path.join(dir, 'site.json'), JSON.stringify(doc, null, 2) + '\n');
  return dir;
}

function orgDir(orgId) {
  return writeOrgMirror(orgId);
}

/** A site's folder. The organization comes from the site row when there is one. */
// eslint-disable-next-line no-unused-vars
function siteDir(orgId, siteId) {
  if (siteId == null) {
    const dir = path.join(writeOrgMirror(orgId), 'sites', NO_SITE);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  }
  return writeSiteMirror(siteId);
}

function rackDir(orgId, siteId, rackKey) {
  return path.join(siteDir(orgId, siteId), 'racks', safeKey(rackKey));
}

/** A site's A-end / Z-end label checks: <site>/checks/. */
function checksDir(orgId, siteId) {
  const dir = path.join(siteDir(orgId, siteId), 'checks');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function trainingDir(orgId) {
  return path.join(orgDir(orgId), 'training');
}

function cachePath(kind) {
  if (!['ocr', 'uploads', 'lookups'].includes(kind)) throw new Error(`Unknown cache: ${kind}`);
  const dir = path.join(DATA_ROOT, 'cache', kind);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// ── Atomic files ──────────────────────────────────────────────────────
function writeFileAtomic(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  fs.writeFileSync(tmp, data);
  fs.renameSync(tmp, file);
}
function writeJsonAtomic(file, obj) {
  writeFileAtomic(file, JSON.stringify(obj, null, 2));
}
function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}

// ── Scans ─────────────────────────────────────────────────────────────
function stampOf(at) {
  const d = at ? new Date(at) : new Date();
  const t = Number.isNaN(d.getTime()) ? new Date() : d;
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getUTCFullYear()}-${p(t.getUTCMonth() + 1)}-${p(t.getUTCDate())}_`
    + `${p(t.getUTCHours())}-${p(t.getUTCMinutes())}-${p(t.getUTCSeconds())}`;
}
function hexOf(scanId) {
  const h = String(scanId).replace(/^RK-/i, '').toLowerCase().replace(/[^a-z0-9]/g, '');
  return (h || 'scan').slice(0, 8);
}

function row(scanId) {
  if (typeof scanId !== 'string' || !scanId) return null;
  return indexDb().prepare('SELECT * FROM scans WHERE scan_id = ?').get(scanId) || null;
}

function has(scanId) {
  return !!row(scanId);
}

function dirOfRow(r) {
  return path.join(DATA_ROOT, r.folder);
}

/** Absolute scan folder, from the index. Throws NotFoundError for an unknown id. */
function scanDir(scanId) {
  const r = row(scanId);
  if (!r) throw new NotFoundError(`Scan ${String(scanId).slice(0, 40)} not found`);
  return dirOfRow(r);
}

const FILES = {
  photo: 'photo.jpg',
  annotated: 'annotated.jpg',
  thumb: 'thumb.jpg',
  result: 'result.json',
  meta: 'meta.json',
  corrections: 'corrections.jsonl',
  uncapped: 'uncapped.json',
  zones: 'zones',
  crops: 'crops',
  engine: 'engine',
  backups: 'backups',
};

/** Absolute path of one file of a scan: photo, annotated, thumb, result, meta, corrections, uncapped, zones, crops, engine, backups. */
function file(scanId, name) {
  const f = FILES[name];
  if (!f) throw new Error(`Unknown scan file: ${name}`);
  return path.join(scanDir(scanId), f);
}

function paths(scanId) {
  const dir = scanDir(scanId);
  const out = { dir };
  for (const [k, f] of Object.entries(FILES)) out[k] = path.join(dir, f);
  return out;
}

function refreshRackJson(dir, rackKey, extra = {}) {
  const scansDir = path.join(dir, 'scans');
  const siteIdRows = indexDb().prepare('SELECT scan_id, created_at, folder FROM scans WHERE rack_key = ? ORDER BY created_at DESC')
    .all(rackKey)
    .filter((r) => path.dirname(dirOfRow(r)) === scansDir);
  if (!siteIdRows.length) {
    // No scan left: the rack folder goes too.
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* already gone */ }
    return null;
  }
  const prev = readJson(path.join(dir, 'rack.json'), {}) || {};
  const doc = {
    key: rackKey,
    label: extra.label !== undefined ? extra.label : (prev.label ?? null),
    ids: siteIdRows.map((r) => r.scan_id),
    created_at: prev.created_at || siteIdRows[siteIdRows.length - 1].created_at,
    latest_scan: siteIdRows[0].scan_id,
  };
  writeJsonAtomic(path.join(dir, 'rack.json'), doc);
  return doc;
}

/**
 * Create the folder for a new scan and its index row. Returns its paths.
 * An id the index already holds gets its existing folder back.
 */
function newScan({ orgId = null, siteId = null, rackKey = null, scanId, at = null } = {}) {
  if (!scanId || typeof scanId !== 'string') throw new Error('newScan needs a scan id');
  if (has(scanId)) return paths(scanId);
  const key = safeKey(rackKey || scanId);
  const org = siteId != null ? orgOfSite(siteId, orgId) : (orgId ?? null);
  const rDir = rackDir(org, siteId, key);
  const createdAt = at ? new Date(at).toISOString() : new Date().toISOString();
  let name = `${stampOf(createdAt)}_${hexOf(scanId)}`;
  const scansDir = path.join(rDir, 'scans');
  fs.mkdirSync(scansDir, { recursive: true });
  const taken = new Set(fs.readdirSync(scansDir));
  name = uniqueSlug(name, taken);
  const dir = path.join(scansDir, name);
  fs.mkdirSync(path.join(dir, 'crops'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'engine'), { recursive: true });
  indexDb().prepare(`INSERT INTO scans (scan_id, org_id, site_id, rack_key, folder, created_at)
                     VALUES (?, ?, ?, ?, ?, ?)`)
    .run(scanId, org, siteId == null ? null : Number(siteId), key, path.relative(DATA_ROOT, dir), createdAt);
  refreshRackJson(rDir, key);
  return paths(scanId);
}

/** The index row as the rest of the server reads it. */
function scanInfo(scanId) {
  const r = row(scanId);
  if (!r) return null;
  return {
    scanId: r.scan_id, orgId: r.org_id, siteId: r.site_id, rackKey: r.rack_key,
    createdAt: r.created_at, dir: dirOfRow(r),
  };
}

function readResult(scanId) {
  return readJson(file(scanId, 'result'), null);
}
function writeResult(scanId, doc) {
  writeJsonAtomic(file(scanId, 'result'), doc);
  return doc;
}
function readMeta(scanId) {
  if (!has(scanId)) return null;
  return readJson(file(scanId, 'meta'), null);
}
function writeMeta(scanId, doc) {
  writeJsonAtomic(file(scanId, 'meta'), doc);
  return doc;
}

/** Append one correction: {at, user, kind, target, before, after, ...}. Never rewrites the file. */
function appendCorrection(scanId, entry) {
  const e = entry || {};
  const line = {
    at: e.at || new Date().toISOString(),
    user: e.user ?? null,
    kind: e.kind || 'unknown',
    target: e.target ?? null,
    before: e.before ?? null,
    after: e.after ?? null,
    ...e,
  };
  fs.appendFileSync(file(scanId, 'corrections'), JSON.stringify(line) + '\n');
  return line;
}

function readCorrections(scanId) {
  let raw = '';
  try { raw = fs.readFileSync(file(scanId, 'corrections'), 'utf8'); } catch { return []; }
  const out = [];
  for (const l of raw.split('\n')) {
    if (!l.trim()) continue;
    try { out.push(JSON.parse(l)); } catch { /* a torn last line */ }
  }
  return out;
}

function moveDir(from, to) {
  fs.mkdirSync(path.dirname(to), { recursive: true });
  try {
    fs.renameSync(from, to);
  } catch (err) {
    if (err.code !== 'EXDEV') throw err;
    fs.cpSync(from, to, { recursive: true });
    fs.rmSync(from, { recursive: true, force: true });
  }
}

/**
 * Move every scan of one rack of a site to another rack key (the label a
 * person confirmed). The folder moves, the index rows and rack.json follow;
 * scans already under the new key stay and the two sets merge.
 */
function renameRack(orgId, siteId, fromKey, toKey, { label } = {}) {
  const from = safeKey(fromKey);
  const to = safeKey(toKey);
  const db = indexDb();
  const rows = db.prepare('SELECT * FROM scans WHERE site_id IS ? AND rack_key = ?')
    .all(siteId == null ? null : Number(siteId), from);
  const toDir = rackDir(orgId, siteId, to);
  if (from === to) {
    if (label !== undefined && fs.existsSync(toDir)) refreshRackJson(toDir, to, { label });
    return { moved: 0, rackKey: to };
  }
  const fromDir = rackDir(orgId, siteId, from);
  const moves = [];
  try {
    for (const r of rows) {
      const src = dirOfRow(r);
      const taken = fs.existsSync(path.join(toDir, 'scans')) ? new Set(fs.readdirSync(path.join(toDir, 'scans'))) : new Set();
      const dst = path.join(toDir, 'scans', uniqueSlug(path.basename(src), taken));
      moveDir(src, dst);
      moves.push({ r, src, dst });
    }
    const upd = db.prepare('UPDATE scans SET rack_key = ?, folder = ? WHERE scan_id = ?');
    db.transaction(() => {
      for (const m of moves) upd.run(to, path.relative(DATA_ROOT, m.dst), m.r.scan_id);
    })();
  } catch (err) {
    for (const m of moves.reverse()) { try { moveDir(m.dst, m.src); } catch { /* best effort */ } }
    throw err;
  }
  refreshRackJson(toDir, to, label !== undefined ? { label } : {});
  refreshRackJson(fromDir, from);
  return { moved: moves.length, rackKey: to };
}

/** Move one scan to another rack key of its site (merging with the scans already there). */
function moveScanToRack(scanId, toKey, { label } = {}) {
  const info = scanInfo(scanId);
  if (!info) throw new NotFoundError(`Scan ${scanId} not found`);
  const to = safeKey(toKey);
  const toDir = rackDir(info.orgId, info.siteId, to);
  if (info.rackKey === to) {
    if (label !== undefined) refreshRackJson(toDir, to, { label });
    return { moved: 0, rackKey: to };
  }
  const oldRackDir = path.dirname(path.dirname(info.dir));
  const taken = fs.existsSync(path.join(toDir, 'scans')) ? new Set(fs.readdirSync(path.join(toDir, 'scans'))) : new Set();
  const dst = path.join(toDir, 'scans', uniqueSlug(path.basename(info.dir), taken));
  moveDir(info.dir, dst);
  try {
    indexDb().prepare('UPDATE scans SET rack_key = ?, folder = ? WHERE scan_id = ?')
      .run(to, path.relative(DATA_ROOT, dst), scanId);
  } catch (err) {
    moveDir(dst, info.dir);
    throw err;
  }
  refreshRackJson(toDir, to, label !== undefined ? { label } : {});
  refreshRackJson(oldRackDir, info.rackKey);
  return { moved: 1, rackKey: to };
}

/**
 * A person confirmed which rack a scan is: its folder is filed under the slug
 * of that label. A scan still under its own id (never confirmed) takes the
 * whole rack folder with it; a scan confirmed before moves on its own, so the
 * other scans of its old rack stay where they were confirmed.
 */
function renameRackOfScan(scanId, label) {
  const info = scanInfo(scanId);
  if (!info) throw new NotFoundError(`Scan ${scanId} not found`);
  const to = slugify(label, info.rackKey);
  if (info.rackKey === scanId) {
    return renameRack(info.orgId, info.siteId, info.rackKey, to, { label: String(label) });
  }
  return moveScanToRack(scanId, to, { label: String(label) });
}

/** Move one scan to another site (an admin chose it). Same rack key there. */
function moveScanToSite(scanId, siteId) {
  const info = scanInfo(scanId);
  if (!info) throw new NotFoundError(`Scan ${scanId} not found`);
  const newSite = siteId == null ? null : Number(siteId);
  if ((info.siteId ?? null) === newSite) return scanInfo(scanId);
  const org = orgOfSite(newSite, info.orgId);
  const oldRackDir = path.dirname(path.dirname(info.dir));
  const toRack = rackDir(org, newSite, info.rackKey);
  const taken = fs.existsSync(path.join(toRack, 'scans')) ? new Set(fs.readdirSync(path.join(toRack, 'scans'))) : new Set();
  const dst = path.join(toRack, 'scans', uniqueSlug(path.basename(info.dir), taken));
  moveDir(info.dir, dst);
  try {
    indexDb().prepare('UPDATE scans SET org_id = ?, site_id = ?, folder = ? WHERE scan_id = ?')
      .run(org, newSite, path.relative(DATA_ROOT, dst), scanId);
  } catch (err) {
    moveDir(dst, info.dir);
    throw err;
  }
  refreshRackJson(toRack, info.rackKey);
  refreshRackJson(oldRackDir, info.rackKey);
  return scanInfo(scanId);
}

/**
 * Index rows, newest first. Filters: orgId, siteId, siteIds (array), rackKey,
 * scanIds (array or Set), olderThan (ISO), limit.
 */
function listScans({ orgId, siteId, siteIds, rackKey, scanIds, olderThan, limit } = {}) {
  const where = [];
  const args = [];
  if (orgId !== undefined) { where.push('org_id IS ?'); args.push(orgId == null ? null : Number(orgId)); }
  if (siteId !== undefined) { where.push('site_id IS ?'); args.push(siteId == null ? null : Number(siteId)); }
  if (Array.isArray(siteIds)) {
    if (!siteIds.length) return [];
    where.push(`site_id IN (${siteIds.map(() => '?').join(',')})`);
    args.push(...siteIds.map(Number));
  }
  if (rackKey !== undefined) { where.push('rack_key = ?'); args.push(String(rackKey)); }
  if (olderThan) { where.push('created_at < ?'); args.push(String(olderThan)); }
  let sql = 'SELECT * FROM scans';
  if (where.length) sql += ` WHERE ${where.join(' AND ')}`;
  sql += ' ORDER BY created_at DESC';
  let rows = indexDb().prepare(sql).all(...args);
  if (scanIds) {
    const set = scanIds instanceof Set ? scanIds : new Set(scanIds);
    rows = rows.filter((r) => set.has(r.scan_id));
  }
  if (limit) rows = rows.slice(0, Number(limit));
  return rows.map((r) => ({
    scanId: r.scan_id, orgId: r.org_id, siteId: r.site_id, rackKey: r.rack_key,
    createdAt: r.created_at, dir: dirOfRow(r),
  }));
}

function countFiles(dir) {
  let n = 0;
  try {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) n += countFiles(p);
      else n += 1;
    }
  } catch { /* not there */ }
  return n;
}

/** Delete one scan: its folder, its index row, and its rack folder when it was the last. */
function deleteScan(scanId) {
  const info = scanInfo(scanId);
  if (!info) return { deleted: false, files: 0 };
  const files = countFiles(info.dir);
  fs.rmSync(info.dir, { recursive: true, force: true });
  indexDb().prepare('DELETE FROM scans WHERE scan_id = ?').run(scanId);
  refreshRackJson(path.dirname(path.dirname(info.dir)), info.rackKey);
  return { deleted: true, files };
}

/**
 * Delete every scan, matrix and file of one organization (never its accounts).
 * Returns { deletedScans, deletedFiles }.
 */
function deleteOrgData(orgId) {
  let deletedScans = 0;
  let deletedFiles = 0;
  const siteIds = new Set();
  for (const s of listScans({ orgId })) {
    if (s.siteId != null) siteIds.add(s.siteId);
    const r = deleteScan(s.scanId);
    if (r.deleted) { deletedScans += 1; deletedFiles += r.files; }
  }
  // Whatever else is under the organization's folder: matrices, training
  // memory, empty rack folders. The mirrors (org.json, site.json) are
  // rewritten on next use; they hold no scan data.
  const dir = path.join(DATA_ROOT, 'orgs', orgFolder(orgId));
  const sitesDir = path.join(dir, 'sites');
  if (fs.existsSync(sitesDir)) {
    for (const s of fs.readdirSync(sitesDir)) {
      for (const sub of ['matrix', 'racks', 'checks']) {
        const p = path.join(sitesDir, s, sub);
        deletedFiles += countFiles(p);
        fs.rmSync(p, { recursive: true, force: true });
      }
    }
  }
  const training = path.join(dir, 'training');
  deletedFiles += countFiles(training);
  fs.rmSync(training, { recursive: true, force: true });
  return { deletedScans, deletedFiles };
}

/** Remove cache files older than maxAgeMs. Returns how many went. */
function cleanCache(kind, maxAgeMs) {
  const dir = cachePath(kind);
  const cutoff = Date.now() - maxAgeMs;
  let n = 0;
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    try {
      const st = fs.statSync(p);
      if (st.mtimeMs < cutoff) { fs.rmSync(p, { recursive: true, force: true }); n += 1; }
    } catch { /* gone */ }
  }
  return n;
}

module.exports = {
  DATA_ROOT,
  NotFoundError,
  init,
  slugify,
  uniqueSlug,
  orgFolder,
  siteFolder,
  orgOfSite,
  orgDir,
  siteDir,
  rackDir,
  checksDir,
  trainingDir,
  cachePath,
  writeOrgMirror,
  writeSiteMirror,
  newScan,
  has,
  scanDir,
  scanInfo,
  file,
  paths,
  readResult,
  writeResult,
  readMeta,
  writeMeta,
  appendCorrection,
  readCorrections,
  renameRack,
  renameRackOfScan,
  moveScanToRack,
  moveScanToSite,
  listScans,
  deleteScan,
  deleteOrgData,
  cleanCache,
  countFiles,
  writeJsonAtomic,
  writeFileAtomic,
  readJson,
};
