/**
 * Contact-form attachments - /api/support/contact.
 *
 * The form posts multipart when the user attaches a screenshot and JSON when
 * they do not, and both shapes have to reach the same handler. These tests
 * cover the validation that runs BEFORE the mail transport, so they need no
 * SMTP or Graph credentials: a request that passes validation reaches the send
 * and comes back 502 ("could not send") in a test environment with no mail
 * configured, which is itself the proof that the multipart body was parsed and
 * accepted. A request that fails validation comes back 400 and never gets that
 * far.
 *
 *   node --test test/contact_attachments.test.js
 */

const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const Database = require('better-sqlite3');
const bcrypt = require('bcryptjs');

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

function request(port, method, pathname, { headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: pathname, method, headers }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(buf); } catch { /* not json */ }
        resolve({ status: res.statusCode, body: buf, json });
      });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

function makeUser(t) {
  const db = new Database(require('../lib/data_paths').AUTH_DB);
  const username = `contacttest_${Date.now()}_${Math.floor(process.hrtime()[1] % 100000)}`;
  const password = 'C0ntactTest!23';
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
  return { username, password, userId };
}

async function login(port, username, password) {
  const res = await request(port, 'POST', '/api/auth/login', {
    // Ask for the body token the native app receives: the alternative is
    // carrying the cookie jar plus the CSRF header through every request here,
    // and the handler under test does not care which of the two it arrived by.
    headers: { 'Content-Type': 'application/json', 'X-Client-Platform': 'native' },
    body: JSON.stringify({ username, password }),
  });
  return res.json?.token || null;
}

// Minimal multipart/form-data encoder - enough for text fields plus files, and
// preferable to pulling a dependency into the test suite for six lines.
function multipart(fields, files) {
  const boundary = '----racktracktest' + Math.floor(process.hrtime()[1]).toString(16);
  const chunks = [];
  for (const [name, value] of Object.entries(fields)) {
    chunks.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`,
    ));
  }
  for (const f of files) {
    chunks.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="attachments"; filename="${f.filename}"\r\n` +
      `Content-Type: ${f.contentType}\r\n\r\n`,
    ));
    chunks.push(f.content);
    chunks.push(Buffer.from('\r\n'));
  }
  chunks.push(Buffer.from(`--${boundary}--\r\n`));
  return { boundary, body: Buffer.concat(chunks) };
}

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

async function authedPort(t) {
  const { server, port } = await listen();
  t.after(() => new Promise((r) => server.close(r)));
  const { username, password } = makeUser(t);
  const token = await login(port, username, password);
  assert.ok(token, 'expected a bearer token for the test user');
  return { port, token };
}

// A validation failure must be answered as a 400 the form can show, never as a
// 500 - a user picking the wrong file is not a server fault, and these were the
// responses most likely to regress into an uncaught multer error.
test('an attachment over the per-file limit is refused as a 400', async (t) => {
  const { port, token } = await authedPort(t);
  const { boundary, body } = multipart(
    { message: 'The scan drew the wrong unit numbers.' },
    [{ filename: 'huge.png', contentType: 'image/png', content: Buffer.alloc(6 * 1024 * 1024, 1) }],
  );
  const res = await request(port, 'POST', '/api/support/contact', {
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Length': body.length,
    },
    body,
  });
  assert.equal(res.status, 400);
  assert.match(res.json?.error || '', /under 5 MB/i);
});

test('a disallowed attachment type is refused as a 400', async (t) => {
  const { port, token } = await authedPort(t);
  const { boundary, body } = multipart(
    { message: 'Here is the installer that crashed.' },
    [{ filename: 'payload.exe', contentType: 'application/x-msdownload', content: Buffer.from('MZ') }],
  );
  const res = await request(port, 'POST', '/api/support/contact', {
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Length': body.length,
    },
    body,
  });
  assert.equal(res.status, 400);
  assert.match(res.json?.error || '', /image, PDF, or text/i);
});

test('too many attachments are refused as a 400', async (t) => {
  const { port, token } = await authedPort(t);
  const files = Array.from({ length: 6 }, (_, i) => ({
    filename: `shot${i}.png`, contentType: 'image/png', content: PNG,
  }));
  const { boundary, body } = multipart({ message: 'Several screenshots of the fault.' }, files);
  const res = await request(port, 'POST', '/api/support/contact', {
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Length': body.length,
    },
    body,
  });
  assert.equal(res.status, 400);
  assert.match(res.json?.error || '', /at most 5 files/i);
});

// The message body is still validated on the multipart path - the check must
// not be skipped just because the request arrived as form-data.
test('a multipart request with too short a message is still refused', async (t) => {
  const { port, token } = await authedPort(t);
  const { boundary, body } = multipart(
    { message: 'hi' },
    [{ filename: 'shot.png', contentType: 'image/png', content: PNG }],
  );
  const res = await request(port, 'POST', '/api/support/contact', {
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Length': body.length,
    },
    body,
  });
  assert.equal(res.status, 400);
  assert.match(res.json?.error || '', /describe the problem/i);
});

// The positive case. With no mail transport configured in test, a request that
// passes every check reaches the send and fails there - 502, not 400. That
// distinguishes "the multipart body was parsed and accepted" from "rejected",
// which is what these tests exist to pin.
test('a valid image attachment passes validation and reaches the mail transport', async (t) => {
  const { port, token } = await authedPort(t);
  const { boundary, body } = multipart(
    { message: 'The unit grid is off by one - screenshot attached.', subject: 'Wrong unit numbers' },
    [{ filename: 'rack.png', contentType: 'image/png', content: PNG }],
  );
  const res = await request(port, 'POST', '/api/support/contact', {
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': `multipart/form-data; boundary=${boundary}`,
      'Content-Length': body.length,
    },
    body,
  });
  assert.notEqual(res.status, 400, `validation rejected a valid attachment: ${res.body}`);
  assert.ok([200, 502].includes(res.status), `unexpected status ${res.status}: ${res.body}`);
});

// Older builds of the app post JSON and attach nothing. That path must keep
// working unchanged now that multer sits in front of the handler.
test('the JSON path still works when there are no attachments', async (t) => {
  const { port, token } = await authedPort(t);
  const res = await request(port, 'POST', '/api/support/contact', {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: 'No attachment on this one, just the description.' }),
  });
  assert.notEqual(res.status, 400, `the JSON path regressed: ${res.body}`);
  assert.ok([200, 502].includes(res.status), `unexpected status ${res.status}: ${res.body}`);
});
