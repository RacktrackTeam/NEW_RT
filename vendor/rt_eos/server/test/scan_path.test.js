/**
 * End-to-end coverage of the scan workflow — upload an image, get a rack back.
 *
 * This is the product's primary flow and it had no automated test at all,
 * because the worker pool was a boolean toggle: you could switch the Python
 * pipeline off (which made this route 500) but you could not replace it, so
 * any test either shelled out to real Python and model weights or could not
 * run. RACKTRACK_POOL_MODULE is the seam that fixes that; this suite is what
 * it was added for.
 *
 * A one-line scoping change once broke every scan in production and nothing
 * caught it. These tests are the "anything" that was missing from that path.
 */

const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

after(() => { setImmediate(() => process.exit(0)); });

process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-for-scan-path';
// The seam under test: a fake pipeline instead of Python.
process.env.RACKTRACK_POOL_MODULE = path.join(__dirname, 'fixtures', 'fake_pool.js');
delete process.env.RACKTRACK_SKIP_WORKER_POOL;

const { app } = require('../app');

function listen() {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

// ── Authentication ───────────────────────────────────────────
// /api/analyze is behind requireAuth, so every request here needs a session.
// These tests used to send none at all and assert 200, which cannot have
// passed since the route was protected — they were failing long before the
// cookie migration and simply went unnoticed.
//
// The session is the same shape a browser gets: an httpOnly rt_access cookie
// plus an Origin header, because a cookie-bearing POST with no Origin is
// refused by csrfOriginCheck. That means these also cover cookie auth and the
// CSRF gate on a multipart route, which nothing else exercises.
const bcrypt = require('bcryptjs');
const Database = require('better-sqlite3');

let authHeaders = {};      // Cookie + Origin, filled in by the setup test
let testUser = null;       // { db, userId } — torn down in after()

function postJson(port, urlPath, jsonBody, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const payload = jsonBody === undefined ? undefined : JSON.stringify(jsonBody);
    const req = http.request({
      host: '127.0.0.1', port, path: urlPath, method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
        ...extraHeaders,
      },
    }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

/** Minimal multipart POST — avoids adding a test-only HTTP dependency. */
function postImage(port, filePath, field = 'image') {
  const boundary = '----racktracktest' + Date.now();
  const file = fs.readFileSync(filePath);
  const head = Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="${field}"; ` +
    `filename="${path.basename(filePath)}"\r\nContent-Type: image/jpeg\r\n\r\n`);
  const tail = Buffer.from(`\r\n--${boundary}--\r\n`);
  const body = Buffer.concat([head, file, tail]);

  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path: '/api/analyze', method: 'POST',
      headers: {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': body.length,
        ...authHeaders,
        // Origin has to name the port too — csrfOriginCheck compares the
        // origin's host against req.headers.host, which includes it.
        Origin: `http://127.0.0.1:${port}`,
      },
    }, (res) => {
      let out = '';
      res.on('data', (c) => { out += c; });
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(out); } catch { /* non-JSON body */ }
        resolve({ status: res.statusCode, body: out, json });
      });
    });
    req.on('error', reject);
    req.end(body);
  });
}

// A real JPEG, so image normalisation does genuine work rather than being
// bypassed by a fixture the decoder rejects immediately. Its pixels are seeded
// from the pid so concurrent runs do not collide on one content-addressed rack
// id, and so a re-run starts from a genuinely cold rack rather than inheriting
// the last run's directory — the difference matters, because a cached rack
// takes a different path through the quality gates.
let imagePath;
const createdRacks = new Set();

test('setup: make a real test image', async () => {
  const sharp = require('sharp');
  imagePath = path.join(os.tmpdir(), `racktrack-test-${process.pid}.jpg`);
  await sharp({
    create: {
      width: 640, height: 480, channels: 3,
      background: { r: 40, g: (process.pid % 200) + 20, b: 52 },
    },
  }).jpeg().toFile(imagePath);
  assert.ok(fs.existsSync(imagePath));
});

// Created directly in the DB so the suite still passes on a fresh checkout
// with no seeded data. No organization_id, so orgBlocked() lets it straight
// through — this is a pipeline test, not an org-permissions test.
test('setup: sign in so /api/analyze accepts these requests', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));

  const db = new Database(require('../lib/data_paths').AUTH_DB);
  const username = `scanpath_${process.pid}_${Date.now()}`;
  const password = 'Sc4nPath!23';
  const tenantId = db.prepare(`SELECT id FROM tenants WHERE slug = 'default'`).get().id;
  const userId = db.prepare(`
    INSERT INTO users (email, username, password_hash, email_verified, tenant_id, active)
    VALUES (?, ?, ?, 1, ?, 1)
  `).run(`${username}@example.test`, username, bcrypt.hashSync(password, 10), tenantId).lastInsertRowid;
  testUser = { db, userId };

  const res = await postJson(port, '/api/auth/login', { username, password },
    { Origin: `http://127.0.0.1:${port}` });
  assert.equal(res.status, 200, `login failed: ${res.body.slice(0, 200)}`);

  const setCookie = res.headers['set-cookie'] || [];
  const access = setCookie.find((c) => c.startsWith('rt_access='));
  assert.ok(access, 'login must set an rt_access cookie');
  authHeaders = { Cookie: access.split(';')[0] };
});

after(() => {
  try { fs.rmSync(imagePath, { force: true }); } catch { /* best effort */ }
  for (const id of createdRacks) {
    try { require('../lib/store').deleteScan(id); }
    catch { /* best effort */ }
  }
  if (testUser) {
    try {
      // Order matters: scanning writes rack_owners rows that reference this
      // user, so deleting the user first fails the foreign key. That failure
      // used to be swallowed by the catch below, which quietly leaked a
      // throwaway account into auth.db on every run.
      testUser.db.prepare('DELETE FROM rack_owners WHERE created_by = ?').run(testUser.userId);
      testUser.db.prepare('DELETE FROM refresh_tokens WHERE user_id = ?').run(testUser.userId);
      testUser.db.prepare('DELETE FROM users WHERE id = ?').run(testUser.userId);
      testUser.db.close();
    } catch (err) {
      // Still best-effort — a leaked test row must not fail the suite — but
      // say so, rather than leaving the leak invisible.
      console.error(`[scan_path] test user ${testUser.userId} not cleaned up: ${err.message}`);
    }
  }
});

function rememberRack(json) {
  const id = json?.scanId ?? json?.rackId;
  if (id) createdRacks.add(id);
  return id;
}

test('a scan returns a rack id and the detected devices', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));

  process.env.FAKE_POOL_MODE = 'ok';
  const res = await postImage(port, imagePath);

  assert.equal(res.status, 200, `expected 200, got ${res.status}: ${res.body.slice(0, 300)}`);
  assert.ok(res.json, 'response must be JSON');
  // The contract the client actually depends on.
  assert.match(rememberRack(res.json) || '', /^RK-[A-Za-z0-9]+$/,
    'a scan must come back with a rack id');
  assert.ok(Array.isArray(res.json.devices), 'devices must be an array');
  assert.ok(res.json.devices.length > 0, 'the fake pipeline detected two devices; none survived');
});

test('the same image scanned twice yields the same rack id', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));

  process.env.FAKE_POOL_MODE = 'ok';
  const a = await postImage(port, imagePath);
  const b = await postImage(port, imagePath);

  assert.equal(a.status, 200);
  assert.equal(b.status, 200);
  // Rack ids are content-addressed, which is what makes a re-scan a cache hit
  // rather than a duplicate rack.
  assert.equal(rememberRack(a.json), rememberRack(b.json));
});

test('a request with no file is rejected as a client error', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));

  const res = await postJson(port, '/api/analyze', {},
    { ...authHeaders, Origin: `http://127.0.0.1:${port}` });
  assert.equal(res.status, 400, `expected 400, got ${res.status}: ${res.body.slice(0, 200)}`);
});

test('a pipeline crash is reported as OUR failure, not a bad photo', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));

  // The regression this pins is the one that turned a total scan outage into a
  // support mystery: every exception — a crashed worker, a bug in our code —
  // came back as 400 "Please upload a clearer photo". The failures never
  // registered as server errors, so no error-rate alarm could fire while 100%
  // of scans were failing, and testers re-took clear photos over and over.
  // A rack id this run has not already created, so the crash path is reached
  // rather than short-circuited by a cache hit from an earlier test.
  const fresh = path.join(os.tmpdir(), `racktrack-test-crash-${process.pid}.jpg`);
  await require('sharp')({
    create: { width: 320, height: 240, channels: 3, background: { r: 90, g: 12, b: 200 } },
  }).jpeg().toFile(fresh);

  process.env.FAKE_POOL_MODE = 'throw';
  const res = await postImage(port, fresh);
  fs.rmSync(fresh, { force: true });
  process.env.FAKE_POOL_MODE = 'ok';

  assert.equal(res.status, 500, 'a worker crash must surface as 5xx so alerting can see it');
  assert.equal(res.json?.kind, 'server');
  assert.doesNotMatch(String(res.json?.error || ''), /clearer photo|blur/i,
    'must not blame the user\'s image for a server-side fault');
});
