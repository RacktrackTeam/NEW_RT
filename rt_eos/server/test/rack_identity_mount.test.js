/**
 * The rack identity router is mounted in app.js behind the real login gate.
 *
 * Same fact setup_mount.test.js checks for /api/setup, for the same reason:
 * the mount sits in a try/catch that logs and carries on, so a router that
 * failed to load would 404 while the server looked healthy. A 401 says the
 * router is there and the gate is in front of it. The physical layer route
 * shares its builder with this router since that builder was pulled out into
 * a function, so it is checked to still answer.
 *
 * Same harness as smoke.test.js: NODE_ENV=test, PORT=0, worker pool skipped.
 */

const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

after(() => { setImmediate(() => process.exit(0)); });

process.env.NODE_ENV = process.env.NODE_ENV || 'test';
process.env.PORT = process.env.PORT || '0';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';

const { app } = require('../app');

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

test('both rack identity routes answer 401 to an anonymous caller (404 would mean the router did not load)', async () => {
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const { port } = server.address();
  try {
    let r = await request(port, 'GET', '/api/scan/RK-ABCD1234/identity');
    assert.equal(r.status, 401, `GET identity -> ${r.status}`);
    r = await request(port, 'POST', '/api/scan/RK-ABCD1234/identity/confirm', { name: 'Rack 1' });
    assert.equal(r.status, 401, `POST confirm -> ${r.status}`);
    // The physical layer route still stands in front of its builder: the rack
    // guard answers an anonymous caller, and a bad id never reaches the builder.
    r = await request(port, 'GET', '/api/scan/RK-ABCD1234/physical-layer');
    assert.equal(r.status, 401, `GET physical-layer -> ${r.status}`);
    r = await request(port, 'GET', '/api/scan/not-a-rack/physical-layer');
    assert.equal(r.status, 400, `GET physical-layer with a bad id -> ${r.status}`);
  } finally {
    server.close();
  }
});

test('the physical layer report has one builder, and two callers at once share one build', async () => {
  const { physicalLayerReport, store } = require('../app')._internals;
  assert.equal((await physicalLayerReport('../etc')).status, 400);
  assert.equal((await physicalLayerReport('RK-NOSUCHRACK01')).status, 404);

  // A rack folder with no detection in it: the pipeline refuses, quickly, and
  // writes nothing. Whether python is installed here or not, both callers must
  // be waiting on the same build.
  const rackId = `RK-T${Date.now().toString(36).toUpperCase()}`;
  const dir = store.newScan({ scanId: rackId }).engine;
  try {
    const first = physicalLayerReport(rackId);
    const second = physicalLayerReport(rackId);
    assert.equal(first, second);
    const r = await first;
    assert.equal(r.status, 500);
    assert.equal(r.body.ok, false);
    assert.equal(fs.existsSync(path.join(dir, 'physical_layer.json')), false);
    // Once it has settled, the next caller gets a build of its own.
    const third = physicalLayerReport(rackId);
    assert.notEqual(third, first);
    await third;
    // A cached report is served as it is, with no build.
    fs.writeFileSync(path.join(dir, 'physical_layer.json'), JSON.stringify({ schema: 'racktrack-physical-layer/1', rack_id: rackId }));
    const cached = await physicalLayerReport(rackId);
    assert.equal(cached.status, 200);
    assert.equal(cached.body.rack_id, rackId);
  } finally {
    store.deleteScan(rackId);
  }
});
