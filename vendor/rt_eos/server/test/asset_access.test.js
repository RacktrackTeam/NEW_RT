/**
 * A scan's files (GET /api/scans/:scanId/files/...) are readable only by
 * someone who may open the scan.
 *
 * This route replaced the /outputs and /uploads static mounts. A rack id used
 * to be a permanent bearer credential for its photos, and ids travel through
 * SPA URLs, browser history and proxy logs. These tests pin the legitimate ways
 * in (a session, an asset token for <img> tags) and the ways that must fail,
 * and what is served: images only, never a scan's JSON, never a path outside
 * the scan's folder.
 */

const { test, after, before } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const jwt = require('jsonwebtoken');
const sharp = require('sharp');

after(() => { setImmediate(() => process.exit(0)); });

process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.JWT_SECRET = 'test-secret-for-asset-access';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const SECRET = process.env.JWT_SECRET;

const RACK = 'RK-ASSET001';
const ids = {};

function seedUser(username, role, tenantId) {
  const db = auth.db;
  const existing = db.prepare('SELECT * FROM users WHERE username = ?').get(username);
  if (existing) return existing;
  db.prepare(`INSERT INTO users (email, username, password_hash, role, tenant_id, active)
              VALUES (?, ?, ?, ?, ?, 1)`)
    .run(`${username}@example.com`, username, 'x', role, tenantId);
  return db.prepare('SELECT * FROM users WHERE username = ?').get(username);
}

before(async () => {
  const db = auth.db;
  ids.default = db.prepare(`SELECT id FROM tenants WHERE slug = 'default'`).get().id;
  ids.other = Number(db.prepare('INSERT INTO tenants (slug, name) VALUES (?, ?)')
    .run(`asset-other-${Date.now()}`, 'Other Site').lastInsertRowid);
  ids.owner = seedUser('asset-test-owner', 'owner', ids.default);
  ids.member = seedUser('asset-test-member', 'member', ids.default);
  ids.stranger = seedUser('asset-test-stranger', 'member', ids.other);
  tenant.claimRack(ids.default, RACK, ids.member.id);

  const p = store.newScan({ scanId: RACK, siteId: ids.default });
  await sharp({ create: { width: 800, height: 600, channels: 3, background: { r: 90, g: 90, b: 90 } } })
    .jpeg().toFile(p.photo);
  fs.mkdirSync(path.join(p.engine, 'images'), { recursive: true });
  await sharp({ create: { width: 800, height: 600, channels: 3, background: { r: 0, g: 120, b: 0 } } })
    .png().toFile(path.join(p.engine, 'images', '2_devices_only.png'));
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), '{"devices":[]}');
  await sharp({ create: { width: 40, height: 20, channels: 3, background: { r: 200, g: 0, b: 0 } } })
    .png().toFile(path.join(p.crops, 'dev1_device.png'));
  store.writeMeta(RACK, { scan_id: RACK, timestamp: new Date().toISOString() });
  store.writeResult(RACK, { scan_id: RACK, api: { rackId: RACK } });
});

after(() => { try { store.deleteScan(RACK); } catch { /* best effort */ } });

function listen() {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

function get(port, p, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: p, headers }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, type: res.headers['content-type'] || '', body: Buffer.concat(chunks) }));
    });
    req.on('error', reject);
  });
}

const url = (name) => `/api/scans/${RACK}/files/${name}`;
const assetToken = (role, tenantId) => jwt.sign({ scope: 'asset', role, tenantId }, SECRET, { expiresIn: 3600 });
const bearer = (user) => ({ Authorization: `Bearer ${auth.makeToken(user)}` });

test('an anonymous request with only the scan id is refused', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  assert.equal((await get(port, url('photo.jpg'))).status, 401);
});

test('an asset token is honoured, but only for scans its holder may open', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const r = await get(port, `${url('photo.jpg')}?t=${assetToken('owner', ids.default)}`);
  assert.equal(r.status, 200);
  assert.match(r.type, /image\/jpeg/);
  // A member of an unrelated Site holds a valid token and still gets nothing.
  assert.equal((await get(port, `${url('photo.jpg')}?t=${assetToken('member', ids.other)}`)).status, 404);
});

test('token scopes are not interchangeable', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  // A session JWT in the query string is not an asset capability.
  const session = auth.makeToken(ids.owner);
  assert.equal((await get(port, `${url('photo.jpg')}?t=${session}`)).status, 401);
  // And an asset token is not a session.
  assert.equal((await get(port, `/api/scan/${RACK}/result?t=${assetToken('owner', ids.default)}`)).status, 401);
});

test('a session works: the Site member who scanned it, and not a stranger', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  assert.equal((await get(port, url('photo.jpg'), bearer(ids.member))).status, 200);
  assert.equal((await get(port, url('photo.jpg'), bearer(ids.stranger))).status, 404);
});

test('thumb.jpg is made on first request, 360 px wide, and kept', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const thumbPath = store.file(RACK, 'thumb');
  try { fs.rmSync(thumbPath); } catch { /* not there */ }
  const r = await get(port, url('thumb.jpg'), bearer(ids.owner));
  assert.equal(r.status, 200);
  assert.equal((await sharp(r.body).metadata()).width, 360);
  assert.ok(fs.existsSync(thumbPath), 'the thumbnail is kept in the scan folder');
});

test('annotated.jpg comes from the engine drawing, and crops are served', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const a = await get(port, url('annotated.jpg'), bearer(ids.owner));
  assert.equal(a.status, 200);
  assert.equal((await sharp(a.body).metadata()).format, 'jpeg');
  assert.equal((await get(port, url('crops/dev1_device.png'), bearer(ids.owner))).status, 200);
  assert.equal((await get(port, url('engine/images/2_devices_only.png'), bearer(ids.owner))).status, 200);
  // The scan id is not case sensitive, as the store's ids are upper case.
  assert.equal((await get(port, `/api/scans/${RACK.toLowerCase()}/files/photo.jpg`, bearer(ids.owner))).status, 200);
});

test('only images, only inside the scan folder, only for known scans', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const h = bearer(ids.owner);
  assert.equal((await get(port, url('result.json'), h)).status, 404);
  assert.equal((await get(port, url('meta.json'), h)).status, 404);
  assert.equal((await get(port, url('engine/device_unit_map.json'), h)).status, 404);
  assert.equal((await get(port, url('..%2f..%2f..%2fdb%2fauth.db'), h)).status, 404);
  assert.equal((await get(port, url('crops/..%2f..%2fphoto.jpg'), h)).status, 404);
  assert.equal((await get(port, url('other.jpg'), h)).status, 404);
  assert.equal((await get(port, '/api/scans/RK-NOSUCH01/files/photo.jpg', h)).status, 404);
  assert.equal((await get(port, '/api/scans/not-a-scan/files/photo.jpg', h)).status, 404);
});

test('the old static mounts are gone', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const h = bearer(ids.owner);
  assert.equal((await get(port, `/outputs/${RACK}/photo.jpg`, h)).status, 404);
  assert.equal((await get(port, '/uploads/anything.jpg', h)).status, 404);
});

test('every image URL a scan returns uses the files route', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const r = await get(port, `/api/scan/${RACK}`, bearer(ids.owner));
  assert.equal(r.status, 200, r.body.toString().slice(0, 200));
  const body = JSON.parse(r.body.toString());
  assert.equal(body.originalImageUrl, url('photo.jpg'));
  assert.equal(body.imageUrl, url('annotated.jpg'));
  assert.equal(body.thumbUrl, url('thumb.jpg'));
  assert.ok(!JSON.stringify(body).includes('/outputs/'));
});
