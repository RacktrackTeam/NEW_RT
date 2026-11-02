/**
 * The pilot plan's gaps, through the routes:
 *   - the two dust-cap marks (Uncapped; Optic fitted without cap) kept apart
 *     in the sheet check, the detail, the flags and both downloads
 *   - every hardware detail with its source and its real confidence (E8)
 *   - online lookups switched off: nothing is looked up on the web
 *   - the light settle status the result polls after the first answer
 * Everything runs on a throwaway data folder; the web lookups are stubs.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-gaps-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = 'pilot-gaps-test-secret';
process.env.RACKTRACK_SKIP_WORKER_POOL = '1';
process.env.NODE_ENV = 'test';

const { app } = require('../app');
const auth = require('../auth');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const orgSettings = require('../lib/org_settings');
const deviceLookup = require('../lib/device_lookup');

const A = 'RK-GAPS0A01';
const ids = {};
const users = {};
const calls = { specs: 0, specsLocal: 0 };
let server;
let base;

after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
  setImmediate(() => process.exit(0));
});

function ports(n, states) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const x = 20 + i * 30;
    out.push({ box: [x, 10, x + 24, 34], status: states[i + 1] || 'empty', class_name: 'SFP', confidence: 0.7 + i / 100, index: i + 1,
      ...(states[i + 1] === 'connected' ? { cable_connector: 'LC', cable_color: 'Aqua', cable_confidence: 0.91 } : {}) });
  }
  return out;
}

before(async () => {
  const db = auth.db;
  ids.org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('gp-org', 'Gaps Org', 'active')").run().lastInsertRowid);
  ids.site = Number(db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)').run('gp-site', 'Gaps Site', ids.org).lastInsertRowid);
  db.prepare(`INSERT INTO users (email, username, password_hash, email_verified, role, organization_id, tenant_id, active)
              VALUES ('gp_tech@example.test', 'gp_tech', 'x', 1, 'member', ?, ?, 1)`).run(ids.org, ids.site);
  users.gp_tech = db.prepare('SELECT * FROM users WHERE username = ?').get('gp_tech');

  const p = store.newScan({ scanId: A, siteId: ids.site, at: '2026-09-29T08:00:00Z' });
  await sharp({ create: { width: 1000, height: 800, channels: 3, background: '#b0b0b0' } }).jpeg().toFile(p.photo);
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({
    units_detected: [],
    devices: [{ class_name: 'Switch', confidence: 0.88, box: [100, 50, 900, 130], units: ['u20'], port_count: 6,
      ports: ports(6, { 1: 'connected', 2: 'empty', 3: 'empty', 4: 'empty', 5: 'empty', 6: 'empty' }) }],
  }));
  fs.writeFileSync(path.join(p.engine, 'ocr_devices.json'), JSON.stringify({
    ok: true, devices: [{ position: 'U20', class_name: 'Switch', make: 'D-Link', model: 'DGS-1210-10', raw_text: 'D-Link DGS-1210-10', ocr_conf: 0.97, match_conf: 0.82, source: 'ocr' }],
  }));
  store.writeResult(A, { api: { devices: [{ position: 'U20', make: 'D-Link', model: 'DGS-1210-10' }] } });
  store.writeMeta(A, { scan_id: A, timestamp: '2026-09-29T08:00:00Z', username: 'gp_tech',
    rackNaming: { name: 'R07', method: 'device_labels', confidence: 0.74, reasons: [], tried: [], ask: false } });
  tenant.claimRack(ids.site, A, users.gp_tech.id);
  store.renameRackOfScan(A, 'R07');

  deviceLookup.setRunner('specs', async () => { calls.specs += 1; return { ok: true, specs: { heightU: 1, portCount: 10, portTypes: [], power: null, datasheetUrl: null } }; });
  deviceLookup.setRunner('specsLocal', async () => { calls.specsLocal += 1; return { ok: false, note: 'not in the local base' }; });

  await new Promise((r) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; r(); }); });
});
after(() => new Promise((r) => (server ? server.close(r) : r())));

async function call(method, p, body) {
  const res = await fetch(base + p, {
    method,
    headers: { Authorization: `Bearer ${auth.makeToken(users.gp_tech)}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const buf = Buffer.from(await res.arrayBuffer());
  let json = null;
  try { json = JSON.parse(buf.toString('utf8')); } catch { /* not JSON */ }
  return { status: res.status, json, text: buf.toString('utf8') };
}

test('the two dust-cap marks are kept apart everywhere', async () => {
  assert.equal((await call('POST', `/api/scans/${A}/ports/cap`, { deviceId: 1, portNo: 2, flag: 'loose' })).status, 400);
  const u = await call('POST', `/api/scans/${A}/ports/cap`, { deviceId: 1, portNo: 2, flag: 'uncapped' });
  assert.equal(u.status, 200, u.text);
  assert.equal(u.json.uncapped.markedBy, 'gp_tech');
  assert.equal(u.json.opticNoCap, null);
  const o = await call('POST', `/api/scans/${A}/ports/cap`, { deviceId: 1, portNo: 3, flag: 'optic_no_cap' });
  assert.equal(o.json.opticNoCap.markedBy, 'gp_tech');

  const sc = (await call('GET', `/api/scans/${A}/sheet-check`)).json;
  assert.deepEqual(sc.uncapped.map((m) => m.portNo), [2]);
  assert.deepEqual(sc.opticNoCap.map((m) => m.portNo), [3]);

  const d = (await call('GET', `/api/scans/${A}/detail`)).json;
  const pt = (n) => d.devices[0].ports.find((x) => x.portNo === n);
  assert.equal(pt(2).status, 'uncapped');
  assert.equal(pt(3).status, 'optic_no_cap');
  assert.ok(pt(3).opticNoCap && !pt(3).uncapped);
  assert.deepEqual([d.flags.uncapped, d.flags.opticNoCap], [1, 1]);

  const flags = (await call('GET', `/api/scans/${A}/flags`)).json.flags;
  assert.deepEqual(flags.map((f) => [f.kind, f.portNo]), [['uncapped', 2], ['optic_no_cap', 3]]);

  const csv = (await call('GET', `/api/scans/${A}/report.csv`)).text.split(/\r\n/);
  assert.match(csv[0], /Uncapped,Optic fitted without cap,Sheet port type,Port type check,Sheet occupied,Sheet dust cap$/);
  assert.ok(csv.some((l) => /,3,SFP,Empty,.*"Optic fitted, cap off",+"yes, gp_tech",,,,$/.test(l)), csv.join('\n'));
  assert.ok(csv.some((l) => /,2,SFP,Empty,.*Uncapped,+"yes, gp_tech",,,,,$/.test(l)), csv.join('\n'));
  assert.equal((await call('GET', `/api/scans/${A}/report.pdf`)).status, 200);

  // One mark at most: the optic mark replaces the uncapped one on the same port.
  await call('POST', `/api/scans/${A}/ports/cap`, { deviceId: 1, portNo: 2, flag: 'optic_no_cap' });
  const again = (await call('GET', `/api/scans/${A}/sheet-check`)).json;
  assert.deepEqual([again.uncapped.length, again.opticNoCap.map((m) => m.portNo)], [0, [2, 3]]);
  // The old route unmarking "uncapped" leaves an optic mark alone.
  await call('POST', `/api/scans/${A}/ports/uncapped`, { deviceId: 1, portNo: 2, uncapped: false });
  assert.equal((await call('GET', `/api/scans/${A}/sheet-check`)).json.opticNoCap.length, 2);
  await call('POST', `/api/scans/${A}/ports/cap`, { deviceId: 1, portNo: 2, flag: null });
  assert.equal((await call('GET', `/api/scans/${A}/sheet-check`)).json.opticNoCap.length, 1);
});

test('every hardware detail has its source and its real confidence', async () => {
  const card = (await call('GET', `/api/scans/${A}/devices/1`)).json;
  assert.deepEqual(card.fields.make, { source: 'photo_text', confidence: 0.82, note: 'how well the text read matched the maker' });
  assert.equal(card.fields.model.source, 'photo_text');
  assert.equal(card.fields.model.confidence, 0.82);
  assert.equal(card.fields.type.source, 'photo');
  assert.equal(card.fields.type.confidence, 0.88);
  assert.equal(card.fields.unit.source, 'photo');
  assert.equal(card.fields.height.source, 'photo');

  const d = (await call('GET', `/api/scans/${A}/detail`)).json;
  const p1 = d.devices[0].ports.find((x) => x.portNo === 1);
  assert.deepEqual(p1.fields.type, { source: 'photo', confidence: 0.7, note: 'detection score of the port' });
  assert.equal(p1.fields.state.source, 'photo');
  assert.equal(p1.fields.cable.confidence, 0.91);
  assert.deepEqual(d.rackNameField, { source: 'excel', confidence: 0.74, note: 'the labels read on the devices, looked up in the Excel dataset' });

  // A person's make and model: the source says so, with no made-up score.
  fs.writeFileSync(path.join(store.file(A, 'engine'), 'device_overrides.json'), JSON.stringify({ U20: { make: 'D-Link', model: 'DGS-1210-10P' } }));
  store.writeResult(A, { api: { devices: [{ position: 'U20', make: 'D-Link', model: 'DGS-1210-10P' }] } });
  const fixed = (await call('GET', `/api/scans/${A}/devices/1`)).json;
  assert.deepEqual(fixed.fields.model, { source: 'person', confidence: null, note: 'entered by a person' });
});

test('online lookups off: no web lookup starts, and the card says so', async () => {
  orgSettings.set(ids.org, { onlineLookups: false });
  const before = { ...calls };
  const card = (await call('GET', `/api/scans/${A}/devices/1`)).json;
  assert.equal(card.lookupsOff, true);
  // A model never looked up before: specifications come from the local
  // knowledge base only.
  assert.equal((await call('GET', '/api/devices/specs?make=Acme&model=NX-900')).json.specsStatus, 'looking');
  await deviceLookup.settle('specsLocal', 'Acme', 'NX-900');
  const sp = (await call('GET', '/api/devices/specs?make=Acme&model=NX-900')).json;
  assert.equal(sp.specsStatus, 'not_found');
  assert.equal(sp.lookupsOff, true);
  assert.equal(calls.specs, before.specs, 'the web specification lookup was not run');
  assert.ok(calls.specsLocal > before.specsLocal, 'only the local knowledge base was asked');
  // Lookups on again: the same model is now asked on the web.
  orgSettings.set(ids.org, { onlineLookups: true });
  const on = (await call('GET', '/api/devices/specs?make=Acme&model=NX-900')).json;
  assert.equal(on.specsStatus, 'looking');
  assert.equal(on.lookupsOff, undefined);
  await deviceLookup.settle('specs', 'Acme', 'NX-900');
  assert.ok(calls.specs > before.specs, 'the web specification lookup ran');
});

test('the settle status: done, with the rack naming and its words', async () => {
  const s = await call('GET', `/api/scan/${A}/settle`);
  assert.equal(s.status, 200, s.text);
  assert.equal(s.json.state, 'done');
  assert.equal(s.json.rackNaming.name, 'R07');
  assert.equal(s.json.rackNaming.words, 'matched from the device labels and the Excel dataset');
  assert.equal(s.json.makeModelRead, true);
});

test('small pictures of each device for the Devices tab', async () => {
  const r = await call('GET', `/api/scans/${A}/device-thumbs`);
  assert.equal(r.status, 200, r.text);
  assert.match(r.json.crops['1'], /crops\/thumb-1\.jpg$/);
});

test('a make and model added by hand are kept, rebuild the result, and read as a person', async () => {
  assert.equal((await call('PUT', `/api/scans/${A}/devices/1/make-model`, {})).status, 400);
  const r = await call('PUT', `/api/scans/${A}/devices/1/make-model`, { make: 'Cisco', model: 'C9300-48P' });
  assert.equal(r.status, 200, r.text);
  assert.equal(r.json.make, 'Cisco');
  assert.equal(r.json.model, 'C9300-48P');
  assert.equal(r.json.fields.make.source, 'person');
  assert.equal(r.json.switchGear, true);
  // It survives a reload: the device card, the detail, the stored result and the CSV say it.
  const card = (await call('GET', `/api/scans/${A}/devices/1`)).json;
  assert.deepEqual([card.make, card.model, card.fields.model.source], ['Cisco', 'C9300-48P', 'person']);
  const d = (await call('GET', `/api/scans/${A}/detail`)).json;
  assert.deepEqual([d.devices[0].make, d.devices[0].model], ['Cisco', 'C9300-48P']);
  const stored = store.readResult(A);
  assert.equal(stored.api.devices[0].model, 'C9300-48P');
  assert.match((await call('GET', `/api/scans/${A}/report.csv`)).text, /,Cisco,C9300-48P,/);
  assert.ok(store.readCorrections(A).some((c) => c.kind === 'device_make_model' && c.after.model === 'C9300-48P'));
  const v = (await call('GET', '/api/devices/vendors')).json.vendors;
  assert.ok(v.includes('Cisco') && v.includes('D-Link'));
});

test('a person sets the unit a device stands in; it moves there and says so', async () => {
  assert.equal((await call('PUT', `/api/scans/${A}/devices/1/unit`, { unitTop: 99 })).status, 400);
  const r = await call('PUT', `/api/scans/${A}/devices/1/unit`, { unitTop: 24 });
  assert.equal(r.status, 200, r.text);
  assert.deepEqual([r.json.unitTop, r.json.units], [24, [24]]);
  assert.equal(r.json.fields.unit.source, 'person');
  const d = (await call('GET', `/api/scans/${A}/detail`)).json;
  assert.equal(d.devices[0].unitTop, 24);
});

test('a person says a port is plugged in or empty', async () => {
  assert.equal((await call('PUT', `/api/scans/${A}/ports/state`, { deviceId: 1, portNo: 4, state: 'maybe' })).status, 400);
  assert.equal((await call('PUT', `/api/scans/${A}/ports/state`, { deviceId: 1, portNo: 4, state: 'connected' })).status, 200);
  const d = (await call('GET', `/api/scans/${A}/detail`)).json;
  const p4 = d.devices[0].ports.find((x) => x.portNo === 4);
  assert.equal(p4.state, 'connected');
  assert.equal(p4.fields.state.source, 'person');
});
