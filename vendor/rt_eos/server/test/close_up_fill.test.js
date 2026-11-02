/**
 * A data-centre switch photographed from across the room: no port read, so
 * the scan asks for one close-up of it (scan_detail closeUp), the close-ups
 * screen offers it (by the Excel's ports), and the close-up then fills its
 * ports, dust caps, a missing cable and a wrong port, end to end through the
 * routes (routes/physical.js zones, routes/detail.js, sheet-check).
 *
 * The zone reader is stubbed; everything else is the shipped code on a
 * throwaway data folder.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-closeup-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'closeup-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app, _internals } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');

const S = 'RK-CLUP0A01';
const users = {};
let server;
let base;

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

before(async () => {
  const db = auth.db;
  const org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('cu-org', 'Close Org', 'active')").run().lastInsertRowid);
  const site = Number(db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)').run('cu-site', 'DC Site', org).lastInsertRowid);
  db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
              VALUES ('cu_tech@example.test', 'cu_tech', 'x', 1, 'member', ?, ?, 1)`).run(org, site);
  users.cu_tech = db.prepare('SELECT * FROM users WHERE username = ?').get('cu_tech');

  // One switch, its name read on it, not one port read at that distance.
  const p = store.newScan({ scanId: S, siteId: site, at: '2026-10-01T08:00:00Z' });
  await sharp({ create: { width: 1000, height: 800, channels: 3, background: '#b0b0b0' } }).jpeg().toFile(p.photo);
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({ devices: [
    { class_name: 'Switch', confidence: 0.9, box: [100, 50, 900, 130], units: ['u20'], ports: [] },
  ], units_detected: [] }));
  fs.writeFileSync(path.join(p.engine, 'labels-front.json'), JSON.stringify({ labels: [{ text: 'DC-SW-01', conf: 0.95, bbox: { x: 150, y: 60, w: 80, h: 16 } }], flagLines: [] }));
  store.writeMeta(S, { scan_id: S, timestamp: '2026-10-01T08:00:00Z', username: 'cu_tech' });
  tenant.claimRack(site, S, users.cu_tech.id);
  store.renameRackOfScan(S, 'D01');

  const cable = (n, label, port) => ({ row_no: n, cable_label: label, a_device: 'DC-SW-01', a_port: port, a_rack: 'D01', a_unit: '20',
    z_device: 'DC-PP-09', z_port: String(n), z_rack: 'D02', z_unit: '9', cable_type: 'LC-LC OM4', notes: '' });
  const dir = path.join(store.siteDir(org, site), 'matrix');
  fs.mkdirSync(dir, { recursive: true });
  store.writeJsonAtomic(path.join(dir, 'cables.json'), {
    import: { id: 'cu0001cu0001', file_name: 'm.xlsx', sheet_name: 'S', header_row: 0, uploaded_by: 'cu_tech', uploaded_at: '2026-10-01T07:00:00Z', row_count: 3, cable_count: 3, problem_count: 0 },
    mapping: {},
    cables: [cable(2, 'FC-101', 'Eth1/1'), cable(3, 'FC-102', 'Eth1/2'), cable(4, 'FC-104', 'Eth1/4')],
    problems: [],
  });

  // The close-up of ports 1 to 6: FC-101 on port 1, FC-102 read on port 3
  // (the Excel says 2), port 4 empty (FC-104 belongs there), port 5 empty
  // with its dust cap off, port 6 empty and capped.
  _internals.setZoneReader(async ({ from }) => ({
    ok: true, image_size: { w: 400, h: 300 }, portsFrom: 'model', extraPorts: 0,
    labels: [
      { text: 'FC-101', confidence: 0.95, portNo: from, box: [10, 60, 60, 14] },
      { text: 'FC-102', confidence: 0.93, portNo: from + 2, box: [110, 60, 60, 14] },
    ],
    ports: [
      { portNo: from, box: [10, 10, 40, 40], state: 'connected', sure: true },
      { portNo: from + 1, box: [60, 10, 40, 40], state: 'connected', sure: true },
      { portNo: from + 2, box: [110, 10, 40, 40], state: 'connected', sure: true },
      { portNo: from + 3, box: [160, 10, 40, 40], state: 'empty', sure: true, cap_state: 'capped', cap_reason: 'a cap is in the port' },
      { portNo: from + 4, box: [210, 10, 40, 40], state: 'empty', sure: true, cap_state: 'empty', cap_reason: 'the port is open' },
      { portNo: from + 5, box: [260, 10, 40, 40], state: 'empty', sure: true, cap_state: 'capped', cap_reason: 'a cap is in the port' },
    ],
  }));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

async function call(method, p, { form } = {}) {
  const res = await fetch(base + p, { method, headers: { Authorization: `Bearer ${auth.makeToken(users.cu_tech)}` }, body: form });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return { status: res.status, json };
}

test('no port read: the device asks for one close-up, and the close-ups screen offers its ports', async () => {
  const d = await call('GET', `/api/scans/${S}/detail`);
  assert.equal(d.status, 200);
  const sw = d.json.devices[0];
  assert.equal(sw.name, 'DC-SW-01');
  assert.equal(sw.ports.length, 0);
  assert.deepEqual(sw.closeUp, { why: 'no_ports', text: 'Take a close-up of DC-SW-01 to read its ports and caps', notClear: 0, capsNotClear: 0 });
  assert.equal(d.json.counts.closeUps, 1);

  const z = await call('GET', `/api/scans/${S}/zones`);
  const zd = z.json.devices.find((x) => x.deviceId === 1);
  assert.ok(zd, 'a switch with no port read is still offered for close-ups');
  assert.equal(zd.portsNotRead, true);
  assert.equal(zd.portCount, 48, 'a switch: the usual 48');
  assert.deepEqual(zd.zones[0], { from: 1, to: 12, done: false, zoneId: null });
});

test('the close-up fills its ports, caps, the missing cable and the wrong port', async () => {
  const img = await sharp({ create: { width: 400, height: 300, channels: 3, background: '#808080' } }).jpeg().toBuffer();
  const f = new FormData();
  f.append('image', new Blob([img], { type: 'image/jpeg' }), 'close.jpg');
  f.append('deviceId', '1');
  f.append('from', '1');
  f.append('to', '6');
  const r = await call('POST', `/api/scans/${S}/zones`, { form: f });
  assert.equal(r.status, 200, JSON.stringify(r.json));

  const d = await call('GET', `/api/scans/${S}/detail`);
  const sw = d.json.devices[0];
  const at = (n) => sw.ports.find((p) => p.portNo === n);
  assert.deepEqual(sw.ports.map((p) => p.portNo), [1, 2, 3, 4, 5, 6]);
  assert.ok(sw.ports.every((p) => p.stateSource === 'close-up'));
  assert.equal(sw.closeUp, null, 'the close-up read it: nothing more to ask');
  assert.equal(at(1).status, 'matched');
  assert.equal(at(3).status, 'wrong_port', 'FC-102 read on port 3, the Excel says port 2');
  assert.equal(at(3).labelBelongsTo.cableLabel, 'FC-102');
  assert.equal(at(4).status, 'missing_cable', 'FC-104 belongs on port 4, which is empty');
  assert.equal(at(5).capState, 'empty');
  assert.equal(at(6).capState, 'capped');

  const sc = await call('GET', `/api/scans/${S}/sheet-check`);
  assert.deepEqual(sc.json.missing.map((m) => [m.portNo, m.cableLabel]), [[4, 'FC-104']]);
  assert.deepEqual(sc.json.uncapped.map((m) => [m.portNo, m.markedBy]), [[5, 'photo']], 'the cap off on port 5, told by the close-up');
  assert.deepEqual(sc.json.capUnsure, []);
});
