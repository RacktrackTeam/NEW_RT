/**
 * The setup router is mounted at /api/setup behind the real login gate.
 *
 * The mount in app.js sits in a try/catch that logs and carries on, so a
 * router that failed to load would 404 while the server looked healthy. A 401
 * says the router is there and the gate is in front of it.
 *
 * Same harness as smoke.test.js: NODE_ENV=test, PORT=0, worker pool skipped.
 */

const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

after(() => { setImmediate(() => process.exit(0)); });

process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');

function listen() {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

function request(port, method, path, json) {
  return new Promise((resolve, reject) => {
    const data = json === undefined ? null : JSON.stringify(json);
    const req = http.request({
      host: '127.0.0.1', port, method, path,
      headers: data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {},
    }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

test('every setup route answers 401 to an anonymous caller (404 would mean the router did not load)', async () => {
  const { server, port } = await listen();
  try {
    for (const [method, path] of [
      ['GET', '/api/setup/state'],
      ['GET', '/api/setup/1'],
      ['PUT', '/api/setup/1/datacentre'],
      ['POST', '/api/setup/1/spaces'],
      ['PUT', '/api/setup/1/approver'],
      ['PUT', '/api/setup/1/rules'],
      ['GET', '/api/setup/1/candidates'],
      ['GET', '/api/setup/catalogue/vendors'],
      ['GET', '/api/setup/org/1/profile'],
      ['PUT', '/api/setup/org/1/profile'],
      ['GET', '/api/setup/1/profile'],
      ['PUT', '/api/setup/1/profile/contacts'],
      ['DELETE', '/api/setup/1/profile/facility'],
      ['POST', '/api/setup/1/conventions/check'],
    ]) {
      const r = await request(port, method, path);
      assert.equal(r.status, 401, `${method} ${path} → ${r.status}`);
    }
  } finally {
    server.close();
  }
});

test('a 250 KB body reaches the organisation profile route (the logo) and nowhere else', async () => {
  // app.js parses /api/setup/org with a larger JSON limit than the rest of
  // the API. Anonymous, so the answer past the parser is the gate's 401; the
  // same body on another setup path meets the default limit and is 413.
  const { server, port } = await listen();
  try {
    const big = { logo_data: 'x'.repeat(250 * 1024) };
    let r = await request(port, 'PUT', '/api/setup/org/1/profile', big);
    assert.equal(r.status, 401, `org profile → ${r.status} (413 would mean the larger limit is not mounted)`);
    r = await request(port, 'PUT', '/api/setup/1/profile/contacts', big);
    assert.equal(r.status, 413, `tenant profile → ${r.status} (the larger limit must not be global)`);
  } finally {
    server.close();
  }
});
