/**
 * Smoke tests — exercise the most load-bearing routes without spinning the
 * full worker pool or hitting external services. Run with:
 *
 *   node --test test/smoke.test.js
 *
 * Required env:
 *   JWT_SECRET   — any non-empty string (overrides the generated signing key)
 *   NODE_ENV=test, PORT=0
 */

const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

// Background timers (the orphan collector, log rotation) keep the event loop
// alive past the last test. Force-exit once all tests
// have reported — equivalent to Node 22's --test-force-exit flag but works
// on the Node 20 baseline the repo currently targets.
after(() => { setImmediate(() => process.exit(0)); });

process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');

function listen() {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port });
    });
  });
}

function fetchPath(port, path) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body, headers: res.headers }));
    });
    req.on('error', reject);
  });
}

test('GET /healthz returns 200', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const res = await fetchPath(port, '/healthz');
  assert.equal(res.status, 200);
});

test('GET /metrics returns Prometheus exposition', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const res = await fetchPath(port, '/metrics');
  assert.equal(res.status, 200);
  assert.match(res.body, /^# HELP /m, 'metrics body should contain Prometheus HELP lines');
});

test('Protected API route rejects unauthenticated request', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  // /api/scans is auth-required; without a Bearer token it should reject
  // with 401 (or 403). 404 is deliberately NOT accepted: it would also pass if
  // the route were deleted, renamed or never mounted, which is exactly the
  // regression this assertion exists to catch.
  const res = await fetchPath(port, '/api/scans');
  assert.ok(
    res.status === 401 || res.status === 403,
    `expected 401/403 for unauthenticated /api/scans, got ${res.status}`,
  );
});

// ── Cookie sessions ──────────────────────────────────────────
// The web session is an httpOnly access cookie plus a rotating refresh cookie.
// Native keeps the Bearer flow, so both shapes are exercised here.

const bcrypt = require('bcryptjs');
const Database = require('better-sqlite3');

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

const cookieVal = (setCookie, name) => {
  const hit = (setCookie || []).find((c) => c.startsWith(`${name}=`));
  return hit ? hit.split(';')[0].split('=').slice(1).join('=') : null;
};

// A throwaway account, created directly so the suite passes on a fresh
// checkout with no seeded data.
function makeUser(t) {
  const db = new Database(require('../lib/data_paths').AUTH_DB);
  const username = `smoketest_${Date.now()}_${Math.floor(process.hrtime()[1] % 100000)}`;
  const password = 'Sm0keTest!23';
  const tenantId = db.prepare(`SELECT id FROM tenants WHERE slug = 'default'`).get().id;
  const userId = db.prepare(`
    INSERT INTO users (email, username, password_hash, email_verified, tenant_id, active)
    VALUES (?, ?, ?, 1, ?, 1)
  `).run(`${username}@example.test`, username, bcrypt.hashSync(password, 10), tenantId).lastInsertRowid;
  t.after(() => {
    db.prepare('DELETE FROM refresh_tokens WHERE user_id = ?').run(userId);
    db.prepare('DELETE FROM users WHERE id = ?').run(userId);
    db.close();
  });
  return { db, username, password, userId };
}

test('GET /api/auth/me rejects a request with no auth cookie', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  assert.equal((await fetchPath(port, '/api/auth/me')).status, 401);
});

test('POST /api/auth/refresh with a garbage cookie value rejects with 401', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const res = await postJson(port, '/api/auth/refresh', undefined, {
    Cookie: 'rt_refresh=not-a-real-token',
    Origin: 'http://127.0.0.1',
  });
  assert.equal(res.status, 401);
});

test('login sets httpOnly cookies and returns no token to a browser', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { username, password } = makeUser(t);

  const res = await postJson(port, '/api/auth/login', { username, password });
  assert.equal(res.status, 200);

  const body = JSON.parse(res.body);
  assert.equal('token' in body, false, 'a browser must not receive the token in JSON');
  assert.equal(body.user?.username, username);

  const setCookie = res.headers['set-cookie'] || [];
  const access = setCookie.find((c) => c.startsWith('rt_access='));
  const refresh = setCookie.find((c) => c.startsWith('rt_refresh='));
  assert.ok(access, 'expected a Set-Cookie for rt_access');
  assert.ok(refresh, 'expected a Set-Cookie for rt_refresh');
  assert.match(access, /HttpOnly/i);
  assert.match(refresh, /HttpOnly/i);
  // Scoped so the long-lived credential is not attached to every image request.
  assert.match(refresh, /Path=\/api\/auth/);
});

test('native still receives a Bearer token in the body', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { username, password } = makeUser(t);

  const res = await postJson(port, '/api/auth/login', { username, password },
    { 'X-Client-Platform': 'native' });
  assert.equal(res.status, 200);
  const body = JSON.parse(res.body);
  assert.ok(body.token, 'native has no cookie jar for its custom scheme and needs the token');
});

test('the access cookie authenticates, and refresh rotates it', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { username, password } = makeUser(t);

  const login = await postJson(port, '/api/auth/login', { username, password });
  const access = cookieVal(login.headers['set-cookie'], 'rt_access');
  const refresh = cookieVal(login.headers['set-cookie'], 'rt_refresh');

  const me = await new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/auth/me',
      headers: { Cookie: `rt_access=${access}` } }, (res) => {
      res.resume(); res.on('end', () => resolve(res.statusCode));
    });
    req.on('error', reject);
  });
  assert.equal(me, 200, 'the access cookie alone must authenticate');

  const rot = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${refresh}`, Origin: 'http://127.0.0.1' });
  assert.equal(rot.status, 200);
  const newRefresh = cookieVal(rot.headers['set-cookie'], 'rt_refresh');
  assert.notEqual(newRefresh, refresh, 'refresh must rotate, not be handed back unchanged');
});

test('a refresh token replayed after its grace window is treated as theft', async (t) => {
  process.env.REFRESH_REUSE_GRACE_MS = '1';   // read at module load; see note below
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { db, username, password, userId } = makeUser(t);

  const login = await postJson(port, '/api/auth/login', { username, password });
  const stolen = cookieVal(login.headers['set-cookie'], 'rt_refresh');

  // Legitimate rotation.
  const first = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${stolen}`, Origin: 'http://127.0.0.1' });
  assert.equal(first.status, 200);

  // Age the revocation past the grace window without sleeping.
  db.prepare(`UPDATE refresh_tokens SET revoked_at = ?
              WHERE user_id = ? AND revoked_at IS NOT NULL`)
    .run(Date.now() - 60_000, userId);

  const replay = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${stolen}`, Origin: 'http://127.0.0.1' });
  assert.equal(replay.status, 401);
  assert.match(replay.body, /reuse/i);

  // And the response must kill the whole family, not just this chain — the
  // point of detection is that the legitimate session is compromised too.
  const live = db.prepare('SELECT COUNT(*) c FROM refresh_tokens WHERE user_id = ? AND revoked_at IS NULL')
                 .get(userId).c;
  assert.equal(live, 0, 'reuse detection must revoke every live refresh token for the user');
});

test('replaying inside the grace window does not extend it indefinitely', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { db, username, password, userId } = makeUser(t);

  const login = await postJson(port, '/api/auth/login', { username, password });
  const original = cookieVal(login.headers['set-cookie'], 'rt_refresh');

  await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${original}`, Origin: 'http://127.0.0.1' });
  const rotatedAt = db.prepare(`SELECT revoked_at FROM refresh_tokens
                                WHERE user_id = ? AND replaced_by_token_id IS NOT NULL`)
                      .get(userId).revoked_at;

  // A second presentation inside the window is tolerated (two tabs racing)…
  const second = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${original}`, Origin: 'http://127.0.0.1' });
  assert.equal(second.status, 200);

  // …but it must NOT re-stamp revoked_at. Re-stamping would restart the grace
  // clock on every presentation, so a stolen token replayed every few seconds
  // would stay usable forever and never trip the reuse check.
  const after = db.prepare(`SELECT revoked_at FROM refresh_tokens
                            WHERE user_id = ? AND replaced_by_token_id IS NOT NULL
                            ORDER BY id ASC LIMIT 1`).get(userId).revoked_at;
  assert.equal(after, rotatedAt, 'grace-window replay must not restart the reuse clock');
});

test('logout revokes the refresh token even with no access token', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { username, password } = makeUser(t);

  const login = await postJson(port, '/api/auth/login', { username, password });
  const refresh = cookieVal(login.headers['set-cookie'], 'rt_refresh');

  // Deliberately no rt_access — a browser whose 15-minute token already
  // expired must still be able to end its session.
  const out = await postJson(port, '/api/auth/logout', undefined,
    { Cookie: `rt_refresh=${refresh}`, Origin: 'http://127.0.0.1' });
  assert.equal(out.status, 200);

  const after = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${refresh}`, Origin: 'http://127.0.0.1' });
  assert.equal(after.status, 401, 'a logged-out refresh token must not still rotate');
});

test('a deactivated account is refused on its very next request', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { db, username, password, userId } = makeUser(t);

  const login = await postJson(port, '/api/auth/login', { username, password });
  const access = cookieVal(login.headers['set-cookie'], 'rt_access');
  const refresh = cookieVal(login.headers['set-cookie'], 'rt_refresh');

  db.prepare('UPDATE users SET active = 0 WHERE id = ?').run(userId);

  const me = await new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/auth/me',
      headers: { Cookie: `rt_access=${access}` } }, (res) => {
      res.resume(); res.on('end', () => resolve(res.statusCode));
    });
    req.on('error', reject);
  });
  assert.ok(me === 401 || me === 403, `deactivated account should be refused, got ${me}`);

  // And it must not be able to mint a fresh pair either.
  const rot = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: `rt_refresh=${refresh}`, Origin: 'http://127.0.0.1' });
  assert.equal(rot.status, 401);
});

test('CSRF: a cookie-bearing POST with no Origin or Referer is refused', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const res = await postJson(port, '/api/auth/refresh', undefined,
    { Cookie: 'rt_refresh=anything' });   // no Origin, no Referer
  assert.equal(res.status, 403);
});

test('CSRF: a request carrying no auth cookie is left alone', async (t) => {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  // The native app and server-to-server callers authenticate by Bearer and
  // have no ambient credential to forge, so the check must not touch them.
  // 401 (rejected by auth) is the pass condition; 403 would mean CSRF fired.
  const res = await postJson(port, '/api/auth/logout-all', undefined,
    { Authorization: 'Bearer not-a-real-token' });
  assert.notEqual(res.status, 403, 'CSRF must not engage without an auth cookie');
});
