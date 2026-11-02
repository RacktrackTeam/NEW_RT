/**
 * The photo's dust-cap read on the server side: cap_state and cap_reason on
 * an engine port (and on a close-up port) become capState and capReason in
 * lib/scan_view.js; lib/sheet_check.js raises an empty port the photo read
 * as open under uncapped (markedBy "photo") and lists the ports it could not
 * settle under capUnsure; lib/scan_detail.js exposes both. A person's mark,
 * or a mark a person took off, always wins over the photo.
 *
 * Runs on the libraries alone, on a throwaway data folder.
 */
'use strict';

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-capcheck-'));
process.env.RACKTRACK_AUTH_DB = path.join(ROOT, 'db', 'auth.db');
process.env.JWT_SECRET = process.env.JWT_SECRET || 'cap-check-test-secret';

const auth = require('../auth'); // creates the schema in the throwaway database
const store = require('../lib/store');
const scanView = require('../lib/scan_view');
const sheetCheck = require('../lib/sheet_check');
const scanDetail = require('../lib/scan_detail');

const A = 'RK-CAPC0A01';
const B = 'RK-CAPC0B02'; // a copper switch: open RJ45 ports, no dust caps to flag
const user = { username: 'cc_mandar' };

after(() => { try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ } });

function port(i, status, cap, extra = {}) {
  const x = 20 + i * 60;
  // Read clearly (0.8): a cap off is told only on a port read reliably, as a
  // missing cable is. SFP cages: a dust cap is for optics, not copper ports.
  return { box: [x, 10, x + 50, 60], status, class_name: 'SFP', confidence: 0.8, index: i + 1,
    ...(cap ? { cap_state: cap[0], cap_reason: cap[1] } : {}), ...extra };
}

before(() => {
  const db = auth.db;
  const org = Number(db.prepare("INSERT INTO organizations (slug, name, status) VALUES ('cc-org', 'Cap Org', 'active')").run().lastInsertRowid);
  const site = Number(db.prepare('INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)').run('cc-site', 'Cap Site', org).lastInsertRowid);
  store.init();
  const p = store.newScan({ scanId: A, siteId: site, at: '2026-09-30T08:00:00Z' });
  fs.writeFileSync(path.join(p.engine, 'device_unit_map.json'), JSON.stringify({
    units_detected: [],
    devices: [{
      class_name: 'Switch', confidence: 0.9, box: [100, 50, 900, 130], units: ['u20'], port_count: 8,
      ports: [
        port(0, 'connected', ['connected', 'cable in port'], { confidence: 0.9 }),
        port(1, 'empty', ['empty', 'gold pins found']),            // the photo: open, no cap
        port(2, 'empty', ['capped', 'red cap']),                    // the photo: a cap
        port(3, 'empty', ['uncertain', 'no clear difference in row 1']),
        port(4, 'empty', ['too_small', 'port under 40 px']),
        port(5, 'empty', null),                                     // the check did not run
        port(6, 'empty', ['empty', 'dark hole with edges inside']), // a person will take this off
        port(7, 'empty', ['uncertain', 'too few ports in row 1 to compare']), // a person marks it
      ],
    }],
  }));
  store.writeMeta(A, { scan_id: A, timestamp: '2026-09-30T08:00:00Z', username: 'cc_mandar' });
  const q = store.newScan({ scanId: B, siteId: site, at: '2026-09-30T09:00:00Z' });
  fs.writeFileSync(path.join(q.engine, 'device_unit_map.json'), JSON.stringify({
    units_detected: [],
    devices: [{
      class_name: 'Switch', confidence: 0.9, box: [100, 50, 900, 130], units: ['u21'], port_count: 2,
      ports: [
        port(0, 'empty', ['empty', 'gold pins found'], { class_name: 'RJ45' }),
        port(1, 'empty', ['uncertain', 'no clear difference in row 1'], { class_name: 'RJ45' }),
      ],
    }],
  }));
  store.writeMeta(B, { scan_id: B, timestamp: '2026-09-30T09:00:00Z', username: 'cc_mandar' });
  // A close-up of ports 3 and 4 that could see the caps: it wins over the rack photo.
  const zones = store.file(A, 'zones');
  fs.mkdirSync(zones, { recursive: true });
  fs.writeFileSync(path.join(zones, 'z1.json'), JSON.stringify({
    zoneId: 'z1', deviceId: 1, from: 3, to: 4, at: '2026-09-30T08:05:00Z', by: 'cc_mandar', labels: [],
    ports: [
      { portNo: 3, box: [10, 10, 40, 40], state: 'empty', sure: true, cap_state: 'empty', cap_reason: 'gold pins found' },
      { portNo: 4, box: [60, 10, 40, 40], state: 'empty', sure: true, cap_state: 'capped', cap_reason: 'blue cap' },
    ],
  }));
});

test('capState and capReason come through the view, the close-up first', () => {
  const view = scanView.load(A);
  const ports = view.visible[0].ports;
  const by = Object.fromEntries(ports.map((p) => [p.portNo, [p.capState, p.capReason]]));
  assert.deepEqual(by[1], ['connected', 'cable in port']);
  assert.deepEqual(by[2], ['empty', 'gold pins found']);
  assert.deepEqual(by[3], ['empty', 'gold pins found'], 'the close-up read wins over the rack photo');
  assert.deepEqual(by[4], ['capped', 'blue cap'], 'the close-up read wins over the rack photo');
  assert.deepEqual(by[5], ['too_small', 'port under 40 px']);
  assert.deepEqual(by[6], [null, null], 'no read, no claim');
  assert.equal(ports.find((p) => p.portNo === 3).stateSource, 'close-up');
  for (const p of ports) assert.equal(p.capByPerson, null);
});

test('the photo raises an open empty port under uncapped, and lists what it could not settle', () => {
  const view = scanView.load(A);
  const sheet = sheetCheck.check(view, null);
  assert.deepEqual(sheet.uncapped.map((u) => [u.portNo, u.markedBy, u.reason]),
    [[2, 'photo', 'gold pins found'], [3, 'photo', 'gold pins found'], [7, 'photo', 'dark hole with edges inside']]);
  assert.deepEqual(sheet.capUnsure.map((u) => [u.deviceId, u.portNo, u.markedBy, u.reason]),
    [[1, 8, 'photo', 'too few ports in row 1 to compare']]);
  assert.deepEqual(sheet.opticNoCap, []);
});

test('a person always wins: a mark, an optic mark, and a mark taken off', () => {
  // A person marks port 8 uncapped: theirs, not the photo's, and no longer unsure.
  scanView.setUncapped(A, 1, 8, true, user);
  // A person marks port 2 as an optic fitted without a cap: the photo's open-port finding steps aside.
  scanView.setCapFlag(A, 1, 2, 'optic_no_cap', user);
  // A person takes the mark off port 7: the cap is on, and the photo does not raise it again.
  scanView.setUncapped(A, 1, 7, true, user);
  assert.equal(scanView.setUncapped(A, 1, 7, false, user), null);
  const view = scanView.load(A);
  const p7 = view.visible[0].ports.find((p) => p.portNo === 7);
  assert.equal(p7.capByPerson, 'capped');
  assert.equal(p7.uncapped, null);
  assert.equal(p7.capState, 'empty', 'the photo read itself is still shown');
  const sheet = sheetCheck.check(view, null);
  assert.deepEqual(sheet.uncapped.map((u) => [u.portNo, u.markedBy]), [[3, 'photo'], [8, 'cc_mandar']]);
  assert.deepEqual(sheet.opticNoCap.map((u) => [u.portNo, u.markedBy]), [[2, 'cc_mandar']]);
  assert.deepEqual(sheet.capUnsure, []);
  // Taking the optic mark off with flag null is kept the same way.
  assert.equal(scanView.setCapFlag(A, 1, 2, null, user), null);
  assert.equal(scanView.readUncapped(A)['1:2'].kind, 'capped');
  assert.deepEqual(scanView.capFields(scanView.readUncapped(A)['1:2']), { uncapped: null, opticNoCap: null });
  assert.equal(sheetCheck.check(scanView.load(A), null).uncapped.some((u) => u.portNo === 2), false);
});

test('the detail exposes capState and capReason on each port, and the counts', () => {
  const view = scanView.load(A);
  const detail = scanDetail.build(view, null);
  const ports = detail.devices[0].ports;
  assert.deepEqual(ports.map((p) => p.capState), ['connected', 'empty', 'empty', 'capped', 'too_small', null, 'empty', 'uncertain']);
  assert.equal(ports[3].capReason, 'blue cap');
  assert.equal(detail.counts.capUnsure, 1, 'the port the photo could not settle, whatever a person then said');
  assert.equal(detail.flags.capUnsure, 0, 'a person marked that port, so nothing is left to settle');
  assert.equal(detail.flags.uncapped, 2);
  assert.equal(detail.flags.opticNoCap, 0);
  // A port the check did not run on claims nothing.
  assert.deepEqual([ports[5].capState, ports[5].capReason], [null, null]);
});

test('an open copper (RJ45) port is not a dust-cap flag: the photo flags caps on optical ports only', () => {
  const sheet = sheetCheck.check(scanView.load(B), null);
  assert.deepEqual(sheet.uncapped, []);
  assert.deepEqual(sheet.capUnsure, []);
});
