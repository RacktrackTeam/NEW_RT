/**
 * The patching matrix routes (/api/matrix): the manager's workbook only
 * (lib/workbook.js): upload with its report, commit, summary, search, the
 * inventories, the template, replace, history, delete; who may change it and
 * who may read it. Runs the real Python reader (pipeline/matrix_read.py) on
 * the fixtures (test/fixtures/make_matrix_xlsx.py).
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-matrix-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'matrix-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

const FIX = path.join(__dirname, 'fixtures');
const ids = {};
const users = {};
let server;
let port;

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('mx-org', 'Matrix Org', 'active')").run().lastInsertRowid);
  ids.org2 = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('mx-org2', 'Other Org', 'active')").run().lastInsertRowid);
  const site = db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)');
  ids.site = Number(site.run('mx-site', 'Build Hall', ids.org).lastInsertRowid);
  ids.site2 = Number(site.run('mx-site2', 'Far Hall', ids.org2).lastInsertRowid);
  const add = (name, role, org, t) => {
    db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
                VALUES (?, ?, 'x', 1, ?, ?, ?, 1)`).run(`${name}@example.test`, name, role, org, t);
    users[name] = db.prepare('SELECT * FROM users WHERE username = ?').get(name);
  };
  add('mx_barry', 'site_manager', ids.org, ids.site);
  add('mx_mandar', 'member', ids.org, ids.site);
  add('mx_other', 'site_manager', ids.org2, ids.site2);
  add('mx_admin', 'org_admin', ids.org, null);
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { port = server.address().port; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

function call(method, p, { user, json, file } = {}) {
  let body = null;
  const headers = {};
  if (user) headers.Authorization = `Bearer ${auth.makeToken(users[user])}`;
  if (json !== undefined) {
    body = Buffer.from(JSON.stringify(json));
    headers['Content-Type'] = 'application/json';
  } else if (file) {
    const boundary = `----mx${Date.now()}`;
    body = Buffer.concat([
      Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${file.name}"\r\nContent-Type: application/octet-stream\r\n\r\n`),
      file.data,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ]);
    headers['Content-Type'] = `multipart/form-data; boundary=${boundary}`;
  }
  if (body) headers['Content-Length'] = body.length;
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: p, method, headers }, (res) => {
      let out = '';
      res.on('data', (c) => { out += c; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(out); } catch { /* not JSON */ }
        resolve({ status: res.statusCode, json: j, body: out });
      });
    });
    req.on('error', reject);
    req.end(body || undefined);
  });
}
const upload = (user, name) => call('POST', '/api/matrix/upload', { user, file: { name, data: fs.readFileSync(path.join(FIX, name)) } });

let firstImportId;

test('nothing imported yet', async () => {
  const r = await call('GET', '/api/matrix', { user: 'mx_mandar' });
  assert.equal(r.status, 200);
  assert.equal(r.json.import, null);
  assert.equal(r.json.counts.cables, 0);
  assert.deepEqual(r.json.problems, []);
});

test('upload the manager\'s workbook: every sheet read, problems before the import', async () => {
  const r = await upload('mx_barry', 'manager-sample.xlsx');
  assert.equal(r.status, 200, r.body);
  assert.match(r.json.uploadId, /^[a-f0-9]{16}$/);
  assert.deepEqual(r.json.sheets.map((s) => s.name), ['README', 'A_Z_CONNECTION_MATRIX', 'RACK_INVENTORY', 'DEVICE_INVENTORY', 'PORT_INVENTORY', 'LABEL_RULES', 'FIELD_DICTIONARY', 'LOOKUPS']);
  assert.deepEqual(r.json.sheets.filter((s) => !s.used).map((s) => s.name), ['README', 'FIELD_DICTIONARY']);
  assert.equal(r.json.counts.cables, 10, 'ten cables, the blank row skipped');
  assert.equal(r.json.counts.checked, 8, 'the Planned and the Spare cables are kept, not checked');
  assert.equal(r.json.counts.racks, 2);
  assert.equal(r.json.counts.devices, 6);
  assert.equal(r.json.counts.ports, 4);
  assert.equal(r.json.counts.errors, 2);
  assert.equal(r.json.counts.warnings, 3);
  const reasons = r.json.problems.map((p) => `${p.sheet} ${p.rowNo} ${p.level}: ${p.reason}`);
  assert.ok(reasons.some((x) => /^A_Z_CONNECTION_MATRIX 5 error: The A-end has no port/.test(x)), reasons.join(' | '));
  assert.ok(reasons.some((x) => /^A_Z_CONNECTION_MATRIX 9 error: Cable label FC-006 is also on row 8/.test(x)), reasons.join(' | '));
  assert.ok(reasons.some((x) => /Z_Rack_ID R03 is not in RACK_INVENTORY/.test(x)), reasons.join(' | '));
  assert.ok(reasons.some((x) => /^PORT_INVENTORY 4 warning: Port_Type "SFP-X"/.test(x)), reasons.join(' | '));
  assert.ok(reasons.some((x) => /Device_ID SW-GONE is not in DEVICE_INVENTORY/.test(x)), reasons.join(' | '));
  assert.deepEqual(r.json.revisions, ['Rev-1']);

  const c = await call('POST', '/api/matrix/commit', { user: 'mx_barry', json: { uploadId: r.json.uploadId } });
  assert.equal(c.status, 200, c.body);
  assert.equal(c.json.counts.cables, 10);
  assert.equal(c.json.counts.checked, 8);
  assert.equal(c.json.counts.devices, 6, 'every device named in the inventory or on a cable, once');
  assert.equal(c.json.counts.problems, 5);
  assert.equal(c.json.import.fileName, 'manager-sample.xlsx');
  assert.equal(c.json.import.format, 'manager');
  assert.equal(c.json.import.uploadedBy, 'mx_barry');
  assert.equal(c.json.import.cableCount, 10);
  assert.deepEqual(c.json.import.revisions, ['Rev-1']);
  firstImportId = c.json.import.id;

  // The file as uploaded and the record are in the site's matrix folder.
  const dir = path.join(store.siteDir(ids.org, ids.site), 'matrix');
  assert.ok(fs.readdirSync(dir).some((f) => f.endsWith('_manager-sample.xlsx')));
  const doc = JSON.parse(fs.readFileSync(path.join(dir, 'cables.json'), 'utf8'));
  assert.equal(doc.format, 'manager');
  const fc1 = doc.cables[0];
  assert.equal(fc1.cable_label, 'FC-001', 'snake_case on disk');
  assert.equal(fc1.label_a, 'FC-001-A');
  assert.equal(fc1.label_z, 'FC-001-Z');
  assert.equal(fc1.a_unit, '42');
  assert.equal(fc1.cable_type, 'MMF-OM4');
  assert.equal(fc1.a.port_type, 'SFP+');
  assert.equal(fc1.raw.Effective_Date, '2026-10-01');
  assert.equal(doc.cables[1].raw.Approval_Date, '2026-09-29', 'an Excel day number reads as a date');
  assert.equal(doc.devices.find((d) => d.device_id === 'SPARE-SW').ru_height, '2');
  assert.equal(doc.ports[1].expected_occupied, 'No', 'no reads as No');
  assert.equal(doc.racks[1].expected_label, 'R-02');
  // The pending upload is gone from the cache.
  assert.equal(fs.readdirSync(store.cachePath('uploads')).filter((f) => f.startsWith('matrix-')).length, 0);
});

test('the checks see only the cables that are checked', () => {
  const matrix = require('../lib/matrix');
  const doc = matrix.active(ids.org, ids.site);
  assert.equal(doc.cables.length, 8);
  assert.ok(!doc.cables.some((c) => c.cable_label === 'FC-009' || c.cable_label === 'FC-010'));
  assert.equal(doc.allCables.length, 10);
});

test('everyone at the site reads it; search, device filters and the inventories', async () => {
  const s = await call('GET', '/api/matrix', { user: 'mx_mandar' });
  assert.equal(s.status, 200);
  assert.equal(s.json.counts.cables, 10);
  const all = await call('GET', '/api/matrix/cables', { user: 'mx_mandar' });
  assert.equal(all.json.total, 10);
  assert.deepEqual(Object.keys(all.json.cables[0]).slice(0, 6), ['rowNo', 'cableLabel', 'aDevice', 'aPort', 'zDevice', 'zPort']);
  assert.equal(all.json.cables[0].rowNo, 2);
  assert.equal(all.json.cables[0].labelA, 'FC-001-A');
  assert.equal(all.json.cables[0].aEnd.portType, 'SFP+');
  assert.equal(all.json.cables.find((c) => c.cableLabel === 'FC-009').checked, false);
  const q = await call('GET', '/api/matrix/cables?q=gi1/0/2', { user: 'mx_mandar' });
  assert.equal(q.json.total, 2);
  const d = await call('GET', '/api/matrix/cables?device=fw-01', { user: 'mx_mandar' });
  assert.equal(d.json.total, 1);
  assert.equal(d.json.cables[0].notes, 'Uplink');
  const page = await call('GET', '/api/matrix/cables?limit=3&offset=8', { user: 'mx_mandar' });
  assert.equal(page.json.cables.length, 2);
  const racks = await call('GET', '/api/matrix/inventory?kind=racks', { user: 'mx_mandar' });
  assert.equal(racks.json.total, 2);
  assert.equal(racks.json.items[0].heightU, '42');
  const devs = await call('GET', '/api/matrix/inventory?kind=devices', { user: 'mx_mandar' });
  assert.equal(devs.json.items[0].expectedLabel, 'CORE01');
  assert.equal((await call('GET', '/api/matrix/inventory?kind=nothing', { user: 'mx_mandar' })).status, 400);
});

test('the template downloads for anyone signed in at the site', async () => {
  const r = await call('GET', '/api/matrix/template', { user: 'mx_mandar' });
  assert.equal(r.status, 200);
  assert.ok(r.body.length > 1000);
  assert.equal((await call('GET', '/api/matrix/template')).status, 401);
});

test('a member cannot change it', async () => {
  const r = await upload('mx_mandar', 'manager-sample.xlsx');
  assert.equal(r.status, 403);
  const c = await call('POST', '/api/matrix/commit', { user: 'mx_mandar', json: { uploadId: 'a'.repeat(16) } });
  assert.equal(c.status, 403);
  const d = await call('DELETE', `/api/matrix/${firstImportId}`, { user: 'mx_mandar' });
  assert.equal(d.status, 403);
});

test('a cable read on a tag is added to the dataset by a manager, once', async () => {
  const cable = { cable_label: 'nvs-sgkc-sis-sw05b Et 22', a_device: 'nvs-sgkc-sis-sw05b', a_port: 'Et 22', a_rack: 'B12' };
  assert.equal((await call('POST', '/api/matrix/cables', { user: 'mx_mandar', json: { cable } })).status, 403);
  assert.equal((await call('POST', '/api/matrix/cables', { user: 'mx_barry', json: { cable: { a_device: 'x' } } })).status, 400);
  const r = await call('POST', '/api/matrix/cables', { user: 'mx_barry', json: { cable } });
  assert.equal(r.status, 200);
  assert.equal(r.json.cableLabel, 'nvs-sgkc-sis-sw05b Et 22');
  assert.equal(r.json.aRack, 'B12');
  assert.equal(r.json.checked, true);
  assert.match(r.json.notes, /Added from the rack photo by mx_barry/);
  assert.equal((await call('POST', '/api/matrix/cables', { user: 'mx_barry', json: { cable } })).status, 409);
  const q = await call('GET', '/api/matrix/cables?q=Et%2022', { user: 'mx_mandar' });
  assert.equal(q.json.total, 1);
  assert.equal(q.json.cables[0].rowNo, r.json.rowNo);
  const matrix = require('../lib/matrix');
  assert.equal(matrix.active(ids.org, ids.site).cables.length, 9, 'the added cable is checked');
});

test('another organization cannot read it', async () => {
  const r = await call('GET', '/api/matrix', { user: 'mx_other' });
  assert.equal(r.status, 200);
  assert.equal(r.json.import, null, 'the other site sees only its own (empty) matrix');
  const named = await call('GET', `/api/matrix?siteId=${ids.site}`, { user: 'mx_other' });
  assert.equal(named.status, 404);
  const anon = await call('GET', '/api/matrix');
  assert.equal(anon.status, 401);
});

test('the organization admin reads it by naming the site', async () => {
  const r = await call('GET', `/api/matrix?siteId=${ids.site}`, { user: 'mx_admin' });
  assert.equal(r.status, 200);
  assert.equal(r.json.counts.cables, 11, 'the 10 imported and the one added from a tag');
});

test('any other file is refused plainly', async () => {
  const xls = await call('POST', '/api/matrix/upload', { user: 'mx_barry', file: { name: 'old.xls', data: Buffer.from('x') } });
  assert.equal(xls.status, 400);
  assert.match(xls.json.error, /Save the workbook as \.xlsx first/);
  const csv = await upload('mx_barry', 'matrix-sample.csv');
  assert.equal(csv.status, 400);
  assert.match(csv.json.error, /manager's workbook/);
  const other = await upload('mx_barry', 'matrix-sample.xlsx');
  assert.equal(other.status, 422);
  assert.match(other.json.error, /no A_Z_CONNECTION_MATRIX sheet/);
  assert.equal(fs.readdirSync(store.cachePath('uploads')).filter((f) => f.startsWith('matrix-')).length, 0, 'a refused upload is not kept');
  const gone = await call('POST', '/api/matrix/commit', { user: 'mx_barry', json: { uploadId: 'f'.repeat(16) } });
  assert.equal(gone.status, 404);
});

test('replace with a new revision: history keeps the first', async () => {
  const r = await upload('mx_barry', 'manager-sample-v2.xlsx');
  assert.equal(r.status, 200, r.body);
  const c = await call('POST', '/api/matrix/commit', { user: 'mx_barry', json: { uploadId: r.json.uploadId } });
  assert.equal(c.status, 200, c.body);
  assert.equal(c.json.counts.cables, 5);
  assert.equal(c.json.counts.errors, 0);
  assert.deepEqual(c.json.import.revisions, ['Rev-2']);
  const cab = await call('GET', '/api/matrix/cables?q=F-104', { user: 'mx_mandar' });
  assert.equal(cab.json.cables[0].zPort, '1', 'a whole number reads without .0');

  const h = await call('GET', '/api/matrix/history', { user: 'mx_mandar' });
  assert.equal(h.status, 200);
  assert.equal(h.json.length, 2);
  assert.equal(h.json[0].status, 'active');
  assert.equal(h.json[1].id, firstImportId);
  assert.equal(h.json[1].status, 'replaced');
});

test('delete: an earlier import, then the active one', async () => {
  const d1 = await call('DELETE', `/api/matrix/${firstImportId}`, { user: 'mx_barry' });
  assert.equal(d1.status, 200, d1.body);
  const dir = path.join(store.siteDir(ids.org, ids.site), 'matrix');
  assert.ok(!fs.readdirSync(dir).some((f) => f.endsWith('_manager-sample.xlsx')), 'its file is removed');
  const h = await call('GET', '/api/matrix/history', { user: 'mx_barry' });
  assert.equal(h.json.length, 1);
  const d2 = await call('DELETE', `/api/matrix/${h.json[0].id}`, { user: 'mx_barry' });
  assert.equal(d2.status, 200);
  const s = await call('GET', '/api/matrix', { user: 'mx_barry' });
  assert.equal(s.json.import, null);
  assert.equal((await call('DELETE', '/api/matrix/abcdef123456', { user: 'mx_barry' })).status, 404);
});
