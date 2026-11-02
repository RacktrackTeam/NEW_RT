/**
 * lib/store.js: the one module that builds a path into the data tree.
 * Paths, slugs, the index, atomic writes, append-only corrections, moving a
 * rack folder when its label is confirmed, and deleting through the store.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-store-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = process.env.JWT_SECRET || 'store-test-secret';

const auth = require('../auth');   // creates the schema in the throwaway database
const store = require('../lib/store');
const dataPaths = require('../lib/data_paths');

const ids = {};
before(() => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('eos-x1', 'EOS Pilot', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.hall = Number(site.run('hall-a1', 'Hall A', ids.org).lastInsertRowid);
  ids.hall2 = Number(site.run('hall-a2', 'Hall A', ids.org).lastInsertRowid);   // same name, same org
  store.init();
});
after(() => { try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ } });

test('one setting decides the tree: db/ holds the databases, orgs/ and cache/ sit beside it', () => {
  assert.equal(dataPaths.DB_DIR, path.join(ROOT, 'db'));
  assert.equal(dataPaths.DATA_ROOT, ROOT);
  assert.equal(dataPaths.INDEX_DB, path.join(ROOT, 'db', 'index.db'));
  assert.ok(fs.existsSync(path.join(ROOT, 'orgs')));
  assert.equal(store.cachePath('uploads'), path.join(ROOT, 'cache', 'uploads'));
  assert.equal(store.cachePath('ocr'), path.join(ROOT, 'cache', 'ocr'));
  assert.throws(() => store.cachePath('../x'));
});

test('slugs: lower case, a-z 0-9 and hyphens, unique within the parent', () => {
  assert.equal(store.slugify('Rack Build Facility!'), 'rack-build-facility');
  assert.equal(store.slugify('  SP-HYB / RM01  '), 'sp-hyb-rm01');
  assert.equal(store.slugify(''), 'item');
  assert.equal(store.uniqueSlug('a', ['a', 'a-2']), 'a-3');
  assert.equal(store.orgFolder(ids.org), 'eos-pilot');
  assert.equal(store.siteFolder(ids.hall), 'hall-a');
  assert.equal(store.siteFolder(ids.hall2), 'hall-a-2', 'a second site with the same name gets -2');
  assert.equal(store.siteFolder(ids.hall), 'hall-a', 'and the slug is kept on the row');
  assert.equal(auth.db.prepare('SELECT folder_slug FROM tenants WHERE id = ?').get(ids.hall).folder_slug, 'hall-a');
});

test('newScan makes the folder under org, site and rack, and the index row', () => {
  const p = store.newScan({ scanId: 'RK-AB12CD34', siteId: ids.hall, at: '2026-09-28T10:11:12Z' });
  const expected = path.join(ROOT, 'orgs', 'eos-pilot', 'sites', 'hall-a', 'racks', 'RK-AB12CD34', 'scans', '2026-09-28_10-11-12_ab12cd34');
  assert.equal(p.dir, expected);
  assert.equal(store.scanDir('RK-AB12CD34'), expected);
  assert.ok(fs.statSync(p.crops).isDirectory() && fs.statSync(p.engine).isDirectory());
  assert.equal(p.photo, path.join(expected, 'photo.jpg'));
  assert.equal(store.file('RK-AB12CD34', 'result'), path.join(expected, 'result.json'));
  assert.equal(store.file('RK-AB12CD34', 'corrections'), path.join(expected, 'corrections.jsonl'));
  const org = JSON.parse(fs.readFileSync(path.join(ROOT, 'orgs', 'eos-pilot', 'org.json'), 'utf8'));
  assert.equal(org.name, 'EOS Pilot');
  const site = JSON.parse(fs.readFileSync(path.join(ROOT, 'orgs', 'eos-pilot', 'sites', 'hall-a', 'site.json'), 'utf8'));
  assert.equal(site.id, ids.hall);
  const rack = JSON.parse(fs.readFileSync(path.join(ROOT, 'orgs', 'eos-pilot', 'sites', 'hall-a', 'racks', 'RK-AB12CD34', 'rack.json'), 'utf8'));
  assert.deepEqual(rack.ids, ['RK-AB12CD34']);
  assert.equal(rack.latest_scan, 'RK-AB12CD34');
  const info = store.scanInfo('RK-AB12CD34');
  assert.equal(info.orgId, ids.org, 'the organization comes from the site row');
  assert.equal(info.siteId, ids.hall);
  // Asking again gives the same folder, not a second one.
  assert.equal(store.newScan({ scanId: 'RK-AB12CD34', siteId: ids.hall2 }).dir, expected);
});

test('an unknown scan id is not found, and names are checked', () => {
  assert.equal(store.has('RK-NOPE0001'), false);
  assert.throws(() => store.scanDir('RK-NOPE0001'), (e) => e instanceof store.NotFoundError && e.status === 404);
  assert.throws(() => store.file('RK-NOPE0001', 'photo'), store.NotFoundError);
  assert.throws(() => store.file('RK-AB12CD34', 'secrets'), /Unknown scan file/);
  assert.equal(store.readMeta('RK-NOPE0001'), null);
  assert.throws(() => store.newScan({ scanId: 'RK-BAD00001', rackKey: '../../etc' }), /rack key/);
});

test('result.json and meta.json are written atomically', () => {
  store.writeResult('RK-AB12CD34', { scan_id: 'RK-AB12CD34', n: 1 });
  store.writeResult('RK-AB12CD34', { scan_id: 'RK-AB12CD34', n: 2 });
  assert.equal(store.readResult('RK-AB12CD34').n, 2);
  store.writeMeta('RK-AB12CD34', { scan_id: 'RK-AB12CD34', user: 'mandar' });
  assert.equal(store.readMeta('RK-AB12CD34').user, 'mandar');
  const left = fs.readdirSync(store.scanDir('RK-AB12CD34')).filter((f) => f.endsWith('.tmp'));
  assert.deepEqual(left, [], 'no temporary file is left behind');
});

test('corrections are appended, never rewritten', () => {
  store.appendCorrection('RK-AB12CD34', { user: 'barry', kind: 'device', target: { device: 1 }, before: 'Switch', after: 'Router' });
  const first = fs.readFileSync(store.file('RK-AB12CD34', 'corrections'), 'utf8');
  store.appendCorrection('RK-AB12CD34', { user: 'barry', kind: 'port_count', before: 24, after: 48 });
  const both = fs.readFileSync(store.file('RK-AB12CD34', 'corrections'), 'utf8');
  assert.ok(both.startsWith(first));
  const rows = store.readCorrections('RK-AB12CD34');
  assert.equal(rows.length, 2);
  for (const k of ['at', 'user', 'kind', 'target', 'before', 'after']) assert.ok(k in rows[0], k);
});

test('confirming a label renames the rack folder; a second scan of the same rack joins it', () => {
  store.newScan({ scanId: 'RK-AB12CD35', siteId: ids.hall, at: '2026-09-28T11:00:00Z' });
  store.renameRackOfScan('RK-AB12CD34', 'SP-HYB-RM01-R01');
  store.renameRackOfScan('RK-AB12CD35', 'SP-HYB-RM01-R01');
  const rackDir = path.join(ROOT, 'orgs', 'eos-pilot', 'sites', 'hall-a', 'racks', 'sp-hyb-rm01-r01');
  assert.equal(path.dirname(path.dirname(store.scanDir('RK-AB12CD34'))), rackDir);
  assert.equal(path.dirname(path.dirname(store.scanDir('RK-AB12CD35'))), rackDir);
  assert.ok(!fs.existsSync(path.join(ROOT, 'orgs', 'eos-pilot', 'sites', 'hall-a', 'racks', 'RK-AB12CD34')), 'the old rack folder is gone');
  const rack = JSON.parse(fs.readFileSync(path.join(rackDir, 'rack.json'), 'utf8'));
  assert.equal(rack.label, 'SP-HYB-RM01-R01');
  assert.deepEqual(rack.ids.sort(), ['RK-AB12CD34', 'RK-AB12CD35']);
  assert.equal(rack.latest_scan, 'RK-AB12CD35');
  // The files came with the folder.
  assert.equal(store.readResult('RK-AB12CD34').n, 2);
  assert.equal(store.readCorrections('RK-AB12CD34').length, 2);
  // Confirmed as another rack later: only that scan moves.
  store.renameRackOfScan('RK-AB12CD35', 'R02');
  assert.equal(store.scanInfo('RK-AB12CD35').rackKey, 'r02');
  assert.equal(store.scanInfo('RK-AB12CD34').rackKey, 'sp-hyb-rm01-r01');
});

test('listScans: newest first, by site, rack or id set', () => {
  store.newScan({ scanId: 'RK-AB12CD36', siteId: ids.hall2, at: '2026-09-28T12:00:00Z' });
  assert.deepEqual(store.listScans({ orgId: ids.org }).map((s) => s.scanId), ['RK-AB12CD36', 'RK-AB12CD35', 'RK-AB12CD34']);
  assert.deepEqual(store.listScans({ siteId: ids.hall }).map((s) => s.scanId), ['RK-AB12CD35', 'RK-AB12CD34']);
  assert.deepEqual(store.listScans({ scanIds: new Set(['RK-AB12CD34']) }).map((s) => s.scanId), ['RK-AB12CD34']);
  assert.deepEqual(store.listScans({ siteIds: [] }), []);
  assert.equal(store.listScans({ limit: 1 }).length, 1);
  assert.deepEqual(store.listScans({ olderThan: '2026-09-28T11:30:00Z' }).map((s) => s.scanId), ['RK-AB12CD35', 'RK-AB12CD34']);
});

test('a scan moves with its Site', () => {
  store.moveScanToSite('RK-AB12CD36', ids.hall);
  assert.equal(store.scanInfo('RK-AB12CD36').siteId, ids.hall);
  assert.ok(store.scanDir('RK-AB12CD36').includes(`${path.sep}hall-a${path.sep}`));
  assert.ok(!fs.existsSync(path.join(ROOT, 'orgs', 'eos-pilot', 'sites', 'hall-a-2', 'racks', 'RK-AB12CD36')));
});

test('deleting goes through the store: the folder and the index row together', () => {
  const dir = store.scanDir('RK-AB12CD36');
  const r = store.deleteScan('RK-AB12CD36');
  assert.equal(r.deleted, true);
  assert.ok(!fs.existsSync(dir));
  assert.equal(store.has('RK-AB12CD36'), false);
  assert.deepEqual(store.deleteScan('RK-AB12CD36'), { deleted: false, files: 0 });

  fs.mkdirSync(path.join(store.siteDir(ids.org, ids.hall), 'matrix'), { recursive: true });
  fs.writeFileSync(path.join(store.siteDir(ids.org, ids.hall), 'matrix', 'cables.json'), '{}');
  const out = store.deleteOrgData(ids.org);
  assert.equal(out.deletedScans, 2);
  assert.ok(out.deletedFiles >= 3);
  assert.equal(store.listScans({ orgId: ids.org }).length, 0);
  assert.ok(!fs.existsSync(path.join(store.siteDir(ids.org, ids.hall), 'matrix')));
});
