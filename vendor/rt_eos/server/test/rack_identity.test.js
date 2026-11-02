/**
 * Rack identity: which of the customer's racks a scan is.
 *
 * Against the real estate, profile and tenant modules, the real router, and a
 * seeded throwaway database, the way setup.test.js runs. One thing is stood
 * in at the module boundary: the physical layer report (io.readPhysicalLayer
 * answers from a map, so no file is read and python is never started).
 *
 * What is proved:
 *   - each rung of the ladder speaks when it should, and names its rule;
 *   - only the record and a label that equals one rack state a rack; the only
 *     rack in the space only ever suggests, with no rack;
 *   - a tie is never broken: two racks that fit stay two racks;
 *   - text is put in one shape first (case, separators), and a letter becomes a
 *     digit only where the organisation's rack pattern says digits;
 *   - a new rack's name is only ever a label that was read;
 *   - one organisation never sees another's racks, by lookup or by confirm;
 *   - GET writes nothing, and confirm is the one write.
 */

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const Database = require('better-sqlite3');

const DB_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-rack-identity-'));
const DB_PATH = path.join(DB_DIR, 'auth.db');
process.env.RACKTRACK_AUTH_DB = DB_PATH;

const ORG_A = 10, ORG_B = 20;
const SITE_A = 11, SITE_A2 = 12, SITE_B = 21;
const OWNER = 1, ADMIN_A = 2, TECH_A = 3, OTHER_A = 4, ADMIN_B = 5, TECH_B = 6, MGR_A = 7;

before(() => {
  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.exec(`
    CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active');
    CREATE TABLE tenants (id INTEGER PRIMARY KEY, slug TEXT, name TEXT, organization_id INTEGER);
    CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, role TEXT,
                        tenant_id INTEGER, organization_id INTEGER, active INTEGER DEFAULT 1);
    CREATE TABLE rack_owners (rack_id TEXT, tenant_id INTEGER, created_by INTEGER,
                              created_at TEXT DEFAULT (datetime('now')),
                              PRIMARY KEY (tenant_id, rack_id));
  `);
  db.prepare('INSERT INTO organizations (id,name) VALUES (?,?)').run(ORG_A, 'Org A');
  db.prepare('INSERT INTO organizations (id,name) VALUES (?,?)').run(ORG_B, 'Org B');
  const t = db.prepare('INSERT INTO tenants (id,slug,name,organization_id) VALUES (?,?,?,?)');
  t.run(SITE_A, 'north-dc', 'North DC', ORG_A);
  t.run(SITE_A2, 'south-dc', 'South DC', ORG_A);
  t.run(SITE_B, 'b-dc', 'B DC', ORG_B);
  const u = db.prepare('INSERT INTO users (id,username,email,role,tenant_id,organization_id) VALUES (?,?,?,?,?,?)');
  u.run(OWNER, 'owner', 'owner@example.test', 'owner', null, null);
  u.run(ADMIN_A, 'admin_a', 'admin_a@example.test', 'org_admin', null, ORG_A);
  u.run(TECH_A, 'tech_a', 'tech_a@example.test', 'member', SITE_A, ORG_A);
  u.run(OTHER_A, 'other_a', 'other_a@example.test', 'member', SITE_A, ORG_A);
  u.run(ADMIN_B, 'admin_b', 'admin_b@example.test', 'org_admin', SITE_B, ORG_B);
  u.run(TECH_B, 'tech_b', 'tech_b@example.test', 'member', SITE_B, ORG_B);
  u.run(MGR_A, 'mgr_a', 'mgr_a@example.test', 'site_manager', SITE_A, ORG_A);
  db.close();
});

after(() => { try { fs.rmSync(DB_DIR, { recursive: true, force: true }); } catch { /* best effort */ } });

// -- Harness -----------------------------------------------------------
const estate = require('../lib/estate');
const profile = require('../lib/estate_profile');
const tenantLib = require('../lib/tenant');
const rackIdentity = require('../lib/rack_identity');
const express = require('express');

// The physical layer report, answered from a map instead of the scan folder.
const reports = new Map();
rackIdentity.io.readPhysicalLayer = (rackId) => reports.get(rackId) || null;

/** A report as pipeline/physical_layer.py writes it, cut down to what is read here. */
function report({ labels = [], devices = [] } = {}) {
  return {
    schema: 'racktrack-physical-layer/1',
    rack: { candidates: labels.map(([text, source = 'rail chip', conf = 0.9]) => ({ text, conf, source })) },
    units: devices.map((name, i) => ({ unit: `u${i + 1}`, devices: [{ device_type: 'Switch', label: { text: name } }] })),
  };
}

let seq = 0;
const nextId = (prefix) => `RK-${prefix}${String(++seq).padStart(4, '0')}`;
const mkSpace = (tenantId, rackCount, parentId) => estate.createSpace(tenantId,
  { name: `Space ${++seq}`, rack_count: rackCount, ...(parentId ? { parent_id: parentId } : {}) }, ADMIN_A);
const mkRack = (tenantId, spaceId, name, facilityId) => estate.upsertRack(tenantId,
  { rack_id: nextId('TYPED'), space_id: spaceId, name, ...(facilityId ? { facility_id: facilityId } : {}) }, ADMIN_A).rack;
/** A scan as /api/analyze leaves it: claimed for the Site, and learned into the space. */
function mkScan(tenantId, spaceId, userId, rep) {
  const rackId = nextId('SCAN');
  tenantLib.claimRack(tenantId, rackId, userId);
  if (spaceId != null) estate.upsertRack(tenantId, { rack_id: rackId, space_id: spaceId, source: 'learned' }, userId);
  if (rep) reports.set(rackId, rep);
  return rackId;
}

// -- 1. record ---------------------------------------------------------
test('record: a scan whose own row was typed is that rack, and the photo is not read', async () => {
  const space = mkSpace(SITE_A, 4);
  const rackId = nextId('SCAN');
  tenantLib.claimRack(SITE_A, rackId, TECH_A);
  const row = estate.upsertRack(SITE_A, { rack_id: rackId, space_id: space.id, name: 'A01', facility_id: 'F-A01' }, ADMIN_A).rack;
  rackIdentity.io.readPhysicalLayer = () => { throw new Error('the record decides; the photo is not read'); };
  let r;
  try {
    r = await rackIdentity.identify(rackId, { tenantId: SITE_A });
  } finally {
    rackIdentity.io.readPhysicalLayer = (id) => reports.get(id) || null;
  }
  assert.equal(r.decision, 'matched');
  assert.equal(r.confidence, 'confirmed');
  assert.equal(r.rule, 'record');
  // The rack is the row that was typed, and that is settled here.
  assert.deepEqual(r.rack, { source: 'known', id: row.id, name: 'A01', facilityId: 'F-A01' });
  assert.equal(r.spaceId, space.id);
});

// -- 2. label ----------------------------------------------------------
test('label: a label that equals exactly one rack in the scan\'s space is that rack, by name or by facility id', async () => {
  const space = mkSpace(SITE_A, 6);
  const r07 = mkRack(SITE_A, space.id, 'RK-07', 'FAC-0007');
  const r08 = mkRack(SITE_A, space.id, 'RK-08', 'FAC-0008');
  const byName = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RK-07']] })), { tenantId: SITE_A });
  assert.equal(byName.decision, 'matched');
  assert.equal(byName.confidence, 'probable');
  assert.equal(byName.rule, 'label');
  assert.deepEqual(byName.rack, { source: 'known', id: r07.id, name: 'RK-07', facilityId: 'FAC-0007' });
  assert.equal(byName.candidates[0].score, 0.9);
  assert.match(byName.candidates[0].reasons[0], /"RK-07" \(rail chip, confidence 0\.9\) equals the name/);
  assert.deepEqual(byName.evidence.labels[0], { text: 'RK-07', normalized: 'RK-07', where: 'rail chip', confidence: 0.9, repaired: false });

  const byFacility = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['fac 0008', 'front label', 0.8]] })), { tenantId: SITE_A });
  assert.equal(byFacility.rule, 'label');
  assert.equal(byFacility.rack.name, 'RK-08');
  assert.match(byFacility.candidates[0].reasons[0], /facility id/);
});

test('label: a reading one character out names the rack and asks, and two racks that close ask nothing', async () => {
  // What the rails actually gave on the office rack: seventeen characters of
  // eighteen, with an R read as an A.
  const space = mkSpace(SITE_A, 6);
  const rack = mkRack(SITE_A, space.id, 'RACK-1', 'SP-HYB-RM01-R01-R1');
  const read = await rackIdentity.identify(
    mkScan(SITE_A, space.id, TECH_A, report({ labels: [['SP-HYB-AM01-R01-R1']] })), { tenantId: SITE_A });
  assert.equal(read.decision, 'suggested', 'read, not proved: a person still says yes');
  assert.equal(read.rack, null, 'and no rack is stated');
  assert.equal(read.candidates.length, 1);
  assert.equal(read.candidates[0].name, 'RACK-1', 'but the right rack is named');
  assert.match(read.candidates[0].reasons[0], /is one character different from the facility id/);
  assert.match(read.candidates.concat(read).map((c) => (c.reasons || []).join(' ')).join(' '),
    /one character was read differently/);

  // Two racks a character away from one reading is a coin toss, and it is
  // never tossed: the near tier stands aside entirely.
  mkRack(SITE_A, space.id, 'RACK-2', 'SP-HYB-RM01-R01-R2');
  const tie = await rackIdentity.identify(
    mkScan(SITE_A, space.id, TECH_A, report({ labels: [['SP-HYB-RM01-R01-RX']] })), { tenantId: SITE_A });
  assert.notEqual(tie.decision, 'matched');
  assert.deepEqual(tie.candidates.filter((c) => c.near), []);
});

test('label: a reading that equals a rack exactly still states it, near or not', async () => {
  const space = mkSpace(SITE_A, 6);
  const rack = mkRack(SITE_A, space.id, 'RACK-9', 'SP-HYB-RM01-R01-R9');
  const r = await rackIdentity.identify(
    mkScan(SITE_A, space.id, TECH_A, report({ labels: [['SP-HYB-RM01-R01-R9']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rack.id, rack.id);
});

test('label: a rack in a space beneath the scan\'s space is in this space', async () => {
  const hall = mkSpace(SITE_A, null);
  const row = mkSpace(SITE_A, 4, hall.id);
  const rack = mkRack(SITE_A, row.id, 'H2-R03');
  const r = await rackIdentity.identify(mkScan(SITE_A, hall.id, TECH_A, report({ labels: [['H2-R03']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rule, 'label');
  assert.equal(r.rack.id, rack.id);
});

test('label: with no space chosen the whole Site is searched, and one exact hit states the rack (the owner, 25 Sep 2026)', async () => {
  const space = estate.createSpace(SITE_A2, { name: 'South Hall', rack_count: 4 }, ADMIN_A);
  const rack = estate.upsertRack(SITE_A2, { rack_id: nextId('TYPED'), space_id: space.id, name: 'S-14' }, ADMIN_A).rack;
  const r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ labels: [['S-14']] })), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'matched');
  assert.equal(r.confidence, 'probable');
  assert.equal(r.rule, 'label');
  assert.equal(r.rack.id, rack.id);
  assert.equal(r.candidates.length, 1);
  assert.equal(r.candidates[0].id, rack.id);
  assert.match(r.candidates[0].reasons.join(' '), /across the whole Site/);
});

test('device labels that name the rack by number state it when two agree, suggest when one, say nothing when they disagree', async () => {
  const space = estate.createSpace(SITE_A2, { name: 'Office', rack_count: 4 }, ADMIN_A);
  const racks = {};
  for (const n of [1, 2, 3, 4]) {
    racks[n] = estate.upsertRack(SITE_A2, { rack_id: nextId('TYPED'), space_id: space.id, name: `SP-HYD-RM01-R01-R${n}`, facility_id: `SP-HYD-RM01-R01-R${n}` }, ADMIN_A).rack;
  }
  // R3's photograph: no rack label, two device labels.
  let r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ devices: ['SP-R3-U13-SW02', 'SP-R3-U10-SW01'] })), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rule, 'label');
  assert.equal(r.rack.id, racks[3].id);
  assert.match(r.candidates[0].reasons.join(' '), /name rack R3/);
  // One device label read by the front reader (a certain read): stated.
  r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ devices: ['SP-R4-U11-SW01'] })), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rule, 'label');
  assert.equal(r.rack.id, racks[4].id);
  // Labels from two racks: nobody is chosen from them.
  r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ devices: ['SP-R3-U13-SW02', 'SP-R4-U11-SW01'] })), { tenantId: SITE_A2 });
  assert.notEqual(r.decision, 'matched');
  assert.notEqual(r.rule, 'label');
  // A rail reading that is a device label (the sticker sits on the rail) counts the same way.
  r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ labels: [['SP-R3-U13-SW02']], devices: ['SP-R3-U10-SW01'] })), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rack.id, racks[3].id);
});

test('what the rail reader saw on R3: one clean device label on the rail states the rack, a faint one only suggests', async () => {
  const space = estate.createSpace(SITE_A2, { name: 'Office 2', rack_count: 4 }, ADMIN_A);
  const racks = {};
  for (const n of [1, 2, 3, 4]) {
    racks[n] = estate.upsertRack(SITE_A2, { rack_id: nextId('TYPED'), space_id: space.id, name: `SP-HYD-RM02-R01-R${n}`, facility_id: `SP-HYD-RM02-R01-R${n}` }, ADMIN_A).rack;
  }
  // The physical layer of RK-028F3F92 on 25 Sep 2026: rail text, no rack candidate, no front labels.
  const railed = (rail) => { const pl = report({}); pl.labels = { front: [], rail, pattern: null, sources: { front: false, rail: true, ocr: false } }; pl.rack = { candidates: [] }; return pl; };
  let r = await rackIdentity.identify(mkScan(SITE_A2, space.id, TECH_A, railed([
    { text: 'NOCTUA', side: 'right', conf: 0.918 }, { text: 'CAT6', side: 'right', conf: 0.99 },
    { text: 'SP-R3-U13-SWO2', side: 'right', conf: 0.977 }, { text: 'SWO1', side: 'right', conf: 0.838 },
  ])), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rule, 'label');
  assert.equal(r.rack.id, racks[3].id);
  assert.match(r.candidates[0].reasons.join(' '), /"SP-R3-U13-SWO2" names rack R3/);
  // The same sticker read faintly is a suggestion, not a statement.
  r = await rackIdentity.identify(mkScan(SITE_A2, space.id, TECH_A, railed([{ text: 'SP-R3-U13-SWO2', side: 'right', conf: 0.61 }])), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'suggested');
  assert.equal(r.candidates[0].id, racks[3].id);
  // A rack-shaped reading with one R-token left in it is not a device label: it suggests, never states.
  r = await rackIdentity.identify(mkScan(SITE_A2, space.id, TECH_A, railed([{ text: 'SP-HYD-RM02-R01-RX', side: 'left', conf: 0.99 }])), { tenantId: SITE_A2 });
  assert.notEqual(r.decision, 'matched');
});

test('label: with no space, a reading one character off still only suggests', async () => {
  const space = estate.createSpace(SITE_A2, { name: 'North Hall', rack_count: 4 }, ADMIN_A);
  const rack = estate.upsertRack(SITE_A2, { rack_id: nextId('TYPED'), space_id: space.id, name: 'SP-HYB-RM01-R01-R7' }, ADMIN_A).rack;
  const r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ labels: [['SP-HYB-AM01-R01-R7']] })), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'suggested');
  assert.equal(r.rule, 'label');
  assert.equal(r.rack, null);
  assert.equal(r.candidates[0].id, rack.id);
});

// -- Normalisation and the rack pattern --------------------------------
test('pattern: RK 07 matches RK-07 by separators alone; rk-o7 matches only when the pattern says digits', async () => {
  const space = mkSpace(SITE_A, 6);
  const rack = mkRack(SITE_A, space.id, 'RK-07');
  const spaced = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RK 07']] }));
  const misread = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['rk-o7']] }));

  // No pattern: separators and case are set aside, but nobody says the O is a zero.
  let r = await rackIdentity.identify(spaced, { tenantId: SITE_A });
  assert.equal(r.rule, 'label');
  assert.equal(r.rack.id, rack.id);
  assert.equal(r.candidates[0].score, 0.86); // 0.95 for separators x 0.9 read confidence
  assert.deepEqual(r.evidence.pattern, { rackPattern: null, matches: false });
  r = await rackIdentity.identify(misread, { tenantId: SITE_A });
  assert.notEqual(r.decision, 'matched');
  assert.notEqual(r.decision, 'suggested');
  assert.equal(r.rack, null);
  assert.deepEqual(r.candidates, []);
  assert.equal(r.evidence.labels[0].repaired, false);

  // The organisation says racks are RK-##: now the O sits where a digit belongs.
  profile.putSection(SITE_A, 'conventions', { rack_pattern: 'RK-##' }, ADMIN_A);
  try {
    r = await rackIdentity.identify(misread, { tenantId: SITE_A });
    assert.equal(r.decision, 'matched');
    assert.equal(r.rule, 'label');
    assert.equal(r.rack.id, rack.id);
    assert.deepEqual(r.evidence.labels[0], { text: 'rk-o7', normalized: 'RK-07', where: 'rail chip', confidence: 0.9, repaired: true });
    assert.deepEqual(r.evidence.pattern, { rackPattern: 'RK-##', matches: true });
    assert.match(r.candidates[0].reasons[0], /repaired to "RK-07" because the rack pattern says digits there/);
    assert.equal(r.candidates[0].score, 0.77); // 0.85 for a repair x 0.9

    r = await rackIdentity.identify(spaced, { tenantId: SITE_A });
    assert.equal(r.rule, 'label');
    assert.equal(r.evidence.labels[0].normalized, 'RK-07');
    assert.equal(r.evidence.labels[0].repaired, false);

    // The same through a regular expression instead of a template.
    profile.putSection(SITE_A, 'conventions', { rack_pattern: '/RK-\\d{2}/' }, ADMIN_A);
    r = await rackIdentity.identify(misread, { tenantId: SITE_A });
    assert.equal(r.rule, 'label');
    assert.equal(r.evidence.labels[0].normalized, 'RK-07');
    assert.equal(r.evidence.labels[0].repaired, true);
  } finally {
    profile.deleteSection(SITE_A, 'conventions', ADMIN_A);
  }
});

test('pattern: only a letter where a digit belongs is repaired, and a guess between two repairs is refused', () => {
  assert.deepEqual(rackIdentity.fitToPattern('rk-o7', 'RK-##'), { text: 'RK-07', repaired: true });
  assert.deepEqual(rackIdentity.fitToPattern('RK 07', 'RK-##'), { text: 'RK-07', repaired: false });
  assert.deepEqual(rackIdentity.fitToPattern('RKO7', 'RK-##'), { text: 'RK-07', repaired: true });
  // A is not something OCR makes out of a digit; this is another label.
  assert.equal(rackIdentity.fitToPattern('RK-A7', 'RK-##'), null);
  // Letters where the pattern says letters are left alone.
  assert.deepEqual(rackIdentity.fitToPattern('RO-07', 'AA-##'), { text: 'RO-07', repaired: false });
  // A digit where a letter belongs is never turned back into one.
  assert.equal(rackIdentity.fitToPattern('R0-07', 'AA-##'), null);
  assert.equal(rackIdentity.fitToPattern('RK-007', 'RK-##'), null);
  // A literal digit in the template is repaired the same way.
  assert.deepEqual(rackIdentity.fitToPattern('DCI-RO7', 'DC1-R##'), { text: 'DC1-R07', repaired: true });
  // A regular expression that two different repairs satisfy: no repair is taken.
  assert.equal(rackIdentity.fitToPattern('SO', '/S0|5O/'), null);
  assert.deepEqual(rackIdentity.fitToPattern('RK-O7', '/RK-\\d{2}/'), { text: 'RK-07', repaired: true });
  assert.equal(rackIdentity.keyOf('rk 07'), rackIdentity.keyOf('RK-07'));
  assert.notEqual(rackIdentity.keyOf('RK-O7'), rackIdentity.keyOf('RK-07'));
});

// -- Ties --------------------------------------------------------------
test('tie: a label that fits two racks chooses neither, and a clean read is not dragged into it', async () => {
  const space = mkSpace(SITE_A, 6);
  const a = mkRack(SITE_A, space.id, 'R1-01');
  const b = mkRack(SITE_A, space.id, 'R10-1');
  // The hyphen was lost: R101 is R1-01 and R10-1 alike.
  let r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['R101']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'ambiguous');
  assert.equal(r.confidence, 'unidentified');
  assert.equal(r.rack, null);
  assert.equal(r.rule, null);
  assert.deepEqual(r.candidates.map((c) => c.id).sort(), [a.id, b.id].sort());
  for (const c of r.candidates) { assert.ok(c.score > 0); assert.ok(c.reasons.length > 0); }

  // Read with its hyphen, it equals one rack exactly and the looser match is not considered.
  r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['R1-01']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rack.id, a.id);

  // Two labels in the frame naming two racks: a disagreement, not a choice, whatever their confidence.
  r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A,
    report({ labels: [['R1-01', 'rail chip', 0.99], ['R10-1', 'front label', 0.4]] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'ambiguous');
  assert.equal(r.candidates.length, 2);
  assert.equal(r.candidates[0].id, a.id); // listed best first, but not chosen

  // Two racks typed with one name: neither is taken.
  const twins = mkSpace(SITE_A, 6);
  mkRack(SITE_A, twins.id, 'TW-01');
  mkRack(SITE_A, twins.id, 'TW-01');
  r = await rackIdentity.identify(mkScan(SITE_A, twins.id, TECH_A, report({ labels: [['TW-01']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'ambiguous');
});

// -- 3. only-rack ------------------------------------------------------
test('only-rack: a suggestion, and only in a space said to hold one rack with no other scan unaccounted for', async () => {
  const space = mkSpace(SITE_A, 1);
  const only = mkRack(SITE_A, space.id, 'Comms Rack');
  const first = mkScan(SITE_A, space.id, TECH_A, report());
  let r = await rackIdentity.identify(first, { tenantId: SITE_A });
  assert.equal(r.decision, 'suggested');
  assert.equal(r.confidence, 'possible');
  assert.equal(r.rule, 'only-rack');
  assert.equal(r.rack, null);
  assert.equal(r.candidates.length, 1);
  assert.equal(r.candidates[0].id, only.id);
  assert.match(r.candidates[0].reasons[0], /the only rack set up in this space/);

  // A second scan nobody has tied to a rack: it may be another photo of the same
  // rack, or the first of eleven racks the count forgot. The space stops speaking.
  const second = mkScan(SITE_A, space.id, TECH_A, report());
  for (const id of [first, second]) {
    r = await rackIdentity.identify(id, { tenantId: SITE_A });
    assert.equal(r.decision, 'new'); // room for one rack, none tied to a scan yet
    assert.equal(r.rule, null);
    assert.equal(r.rack, null);
    assert.equal(r.candidates.length, 1);
    assert.equal(r.candidates[0].id, only.id);
    assert.match(r.candidates[0].reasons.join(' '), /not tied to a rack yet/);
  }

  // Once a person has said the first scan is that rack, the second is alone again.
  const bound = await rackIdentity.confirm(first, { tenantId: SITE_A, userId: TECH_A, knownRackId: only.id });
  r = await rackIdentity.identify(first, { tenantId: SITE_A });
  assert.equal(r.decision, 'matched');
  assert.equal(r.rule, 'record');
  r = await rackIdentity.identify(second, { tenantId: SITE_A });
  assert.equal(r.decision, 'suggested');
  assert.equal(r.rule, 'only-rack');

  // One rack set up in a space said to hold twelve: the space says nothing at all.
  const big = mkSpace(SITE_A, 12);
  mkRack(SITE_A, big.id, 'The One Typed');
  r = await rackIdentity.identify(mkScan(SITE_A, big.id, TECH_A, report()), { tenantId: SITE_A });
  assert.equal(r.decision, 'new');
  assert.deepEqual(r.candidates, []);
});

test('only-rack: a rack label that says otherwise stops the space from speaking; other print does not', async () => {
  const space = mkSpace(SITE_A, 1);
  const only = mkRack(SITE_A, space.id, 'RK-07');
  const r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RK-09']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'new');
  assert.equal(r.rack, null);
  assert.deepEqual(r.proposal, { name: 'RK-09', where: 'rail chip', confidence: 0.9 });
  assert.equal(r.candidates[0].id, only.id);
  assert.match(r.candidates[0].reasons.join(' '), /"RK-09", which is not this rack/);

  // Print that is not a rack identifier (the word ROUTER off a front panel) does not.
  const quiet = mkSpace(SITE_A, 1);
  mkRack(SITE_A, quiet.id, 'Solo');
  const noisy = await rackIdentity.identify(mkScan(SITE_A, quiet.id, TECH_A, report({ labels: [['ROUTER', 'front label', 0.99]] })), { tenantId: SITE_A });
  assert.equal(noisy.decision, 'suggested');
  assert.equal(noisy.rule, 'only-rack');
});

// -- new and unknown ---------------------------------------------------
test('new: room left in the space and nothing matched; the name offered is the label read, or none', async () => {
  const space = mkSpace(SITE_A, 3);
  mkRack(SITE_A, space.id, 'RACK 1');
  let r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RACK 12', 'front label', 0.72]] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'new');
  assert.equal(r.confidence, 'unidentified');
  assert.equal(r.rack, null);
  assert.equal(r.rule, null);
  assert.deepEqual(r.proposal, { name: 'RACK 12', where: 'front label', confidence: 0.72 });

  // Nothing read: still a new rack by the count, and no name is made up for it.
  r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report()), { tenantId: SITE_A });
  assert.equal(r.decision, 'new');
  assert.equal(r.proposal, null);

  // Print that is not a rack identifier is never offered as a name.
  r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['ROUTER', 'front label', 0.99]] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'new');
  assert.equal(r.proposal, null);

  // Two different rack labels in the frame: neither is picked.
  r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RACK 12'], ['RACK 13']] })), { tenantId: SITE_A });
  assert.equal(r.proposal, null);
  assert.equal(r.evidence.labels.length, 2);

  // With a rack pattern, only a label that fits it is offered, in the pattern's own form.
  profile.putSection(SITE_A, 'conventions', { rack_pattern: 'RACK-##' }, ADMIN_A);
  try {
    r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['rack 1z'], ['R9']] })), { tenantId: SITE_A });
    assert.equal(r.decision, 'new');
    assert.equal(r.proposal.name, 'RACK-12');
  } finally {
    profile.deleteSection(SITE_A, 'conventions', ADMIN_A);
  }
});

test('unknown: no room left in the space, or nothing to go on at all', async () => {
  // Both racks the space holds have a scan tied to them already.
  const space = mkSpace(SITE_A, 2);
  const a = mkRack(SITE_A, space.id, 'U-1');
  const b = mkRack(SITE_A, space.id, 'U-2');
  await rackIdentity.confirm(mkScan(SITE_A, space.id, TECH_A), { tenantId: SITE_A, userId: TECH_A, knownRackId: a.id });
  await rackIdentity.confirm(mkScan(SITE_A, space.id, TECH_A), { tenantId: SITE_A, userId: TECH_A, knownRackId: b.id });
  let r = await rackIdentity.identify(mkScan(SITE_A, space.id, TECH_A, report({ labels: [['U-9']] })), { tenantId: SITE_A });
  assert.equal(r.decision, 'unknown');
  assert.equal(r.proposal, null);
  assert.equal(r.evidence.labels[0].text, 'U-9'); // what was read is still shown, verbatim

  // No report, no space, nothing the label could equal: nothing at all.
  r = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A), { tenantId: SITE_A2 });
  assert.equal(r.decision, 'unknown');
  assert.equal(r.spaceId, null);
  assert.deepEqual(r.candidates, []);
  assert.deepEqual(r.evidence.labels, []);
  assert.ok(r.evidence.notes.some((n) => /no physical layer report/.test(n)));

  r = await rackIdentity.identify('RK-NOBODY01', {});
  assert.equal(r.decision, 'unknown');
});

// -- Organisation isolation --------------------------------------------
test('isolation: another organisation\'s racks are never candidates, and a foreign space is not found', async () => {
  const spaceB = estate.createSpace(SITE_B, { name: 'B Hall', rack_count: 4 }, ADMIN_B);
  estate.upsertRack(SITE_B, { rack_id: nextId('TYPED'), space_id: spaceB.id, name: 'ISO-01' }, ADMIN_B);
  const spaceA = mkSpace(SITE_A, null);
  const rackId = mkScan(SITE_A, spaceA.id, TECH_A, report({ labels: [['ISO-01']] }));

  const r = await rackIdentity.identify(rackId, { tenantId: SITE_A });
  assert.equal(r.decision, 'unknown');
  assert.deepEqual(r.candidates, []);
  // Not tied to a space, the whole Site is searched, and still only this Site.
  const loose = await rackIdentity.identify(mkScan(SITE_A2, null, TECH_A, report({ labels: [['ISO-01']] })), { tenantId: SITE_A2 });
  assert.deepEqual(loose.candidates, []);

  await assert.rejects(rackIdentity.identify(rackId, { tenantId: SITE_A, spaceId: spaceB.id }),
    (err) => err instanceof rackIdentity.IdentityError && err.status === 404);
});

// -- The routes --------------------------------------------------------
const auditLog = [];
const built = [];
const app = express();
app.use(express.json());
app.use(require('../routes/rack_identity')({
  // Stands in for auth.requireAuth: the seeded user row named by x-user, else 401.
  requireAuth: (req, res, next) => {
    const id = Number(req.headers['x-user'] || 0);
    if (!id) return res.status(401).json({ error: 'Authentication required' });
    const db = new Database(DB_PATH, { readonly: true });
    req.user = db.prepare('SELECT * FROM users WHERE id = ?').get(id);
    db.close();
    if (!req.user) return res.status(401).json({ error: 'User no longer exists' });
    next();
  },
  audit: { log: (entry) => auditLog.push(entry) },
  // Stands in for app.js's one builder of the physical layer report.
  physicalLayer: async (rackId) => {
    built.push(rackId);
    return { status: 200, body: report({ labels: [['BUILT-01']] }) };
  },
}));

let server, port;
before(() => new Promise((resolve) => {
  server = app.listen(0, '127.0.0.1', () => { port = server.address().port; resolve(); });
}));
after(() => server && server.close());

function call(method, urlPath, { user, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const req = http.request({
      host: '127.0.0.1', port, method, path: urlPath,
      headers: {
        ...(user ? { 'x-user': String(user) } : {}),
        ...(data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(raw); } catch { /* not JSON */ }
        resolve({ status: res.statusCode, json, raw });
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

/** Everything a GET could have written to, as one comparable value: every row of every table, and the name file. */
function everythingStored() {
  const db = new Database(DB_PATH, { readonly: true });
  try {
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all().map((t) => t.name);
    const rows = {};
    for (const t of tables) rows[t] = db.prepare(`SELECT * FROM ${t}`).all();
    return JSON.stringify({ tables, rows });
  } finally { db.close(); }
}

test('GET: signed in, scoped to the rack\'s organisation and Site; a label match is written once, everything else reads', async () => {
  const space = mkSpace(SITE_A, 6);
  const rack = mkRack(SITE_A, space.id, 'GET-01');
  const rackId = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['GET 01']], devices: ['GG-1', 'GG-2', 'GG-3'] }));
  const unmatched = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['GET 99']], devices: ['GG-1', 'GG-2', 'GG-3'] }));
  const bare = mkScan(SITE_A, mkSpace(SITE_A, 1).id, TECH_A);
  const url = `/api/scan/${rackId}/identity`;

  assert.equal((await call('GET', url)).status, 401);
  assert.equal((await call('GET', '/api/scan/not-a-rack/identity', { user: TECH_A })).status, 400);
  // Another organisation's technician and admin are told the rack is not there.
  assert.equal((await call('GET', url, { user: TECH_B })).status, 404);
  assert.equal((await call('GET', url, { user: ADMIN_B })).status, 404);

  profile.tenantProfile(SITE_A); // the profile tables are made on first read; that is schema, not this route
  const stored = everythingStored();
  const audited = auditLog.length;
  // The first look states the rack from its label and writes that down, once
  // (the owner, 25 Sep 2026: a rack whose label was read is assigned, not
  // asked). The answer still says how it was found.
  const first = await call('GET', url, { user: TECH_A });
  assert.equal(first.status, 200);
  assert.equal(first.json.decision, 'matched');
  assert.equal(first.json.rule, 'label');
  assert.equal(first.json.recorded, 'label');
  assert.equal(first.json.confidence, 'confirmed');
  assert.deepEqual(first.json.rack, { source: 'known', id: rack.id, name: 'GET-01', facilityId: null });
  assert.deepEqual(Object.keys(first.json).sort(), ['candidates', 'confidence', 'decision', 'evidence', 'height', 'naming', 'ok', 'proposal',
    'rack', 'rackId', 'recorded', 'rule', 'siteName', 'spaceId', 'spaceName']);
  // How it was named, for the quiet line on the results header.
  assert.equal(first.json.naming.name, 'GET-01');
  assert.equal(first.json.naming.method, 'rail_label');
  assert.equal(first.json.naming.words, 'read from the rail label');
  assert.deepEqual(Object.keys(first.json.evidence).sort(), ['deviceHints', 'labels', 'location', 'notes', 'pattern']);
  assert.notEqual(everythingStored(), stored, 'the label match is on record');
  assert.equal(auditLog.length, audited + 1);
  assert.equal(auditLog[auditLog.length - 1].action, 'rack.identity.label');
  const storedAfter = everythingStored();
  const auditedAfter = auditLog.length;
  // From then on it is the record that says so, for everyone who may see the rack, and nothing is written again.
  for (const user of [TECH_A, OTHER_A, MGR_A, ADMIN_A, OWNER]) {
    const r = await call('GET', url, { user });
    assert.equal(r.status, 200, `user ${user}`);
    assert.equal(r.json.ok, true);
    assert.equal(r.json.rackId, rackId);
    assert.equal(r.json.spaceId, space.id);
    assert.equal(r.json.decision, 'matched');
    assert.equal(r.json.rule, 'record');
    assert.equal(r.json.rack.id, rack.id);
    assert.equal(r.json.rack.name, 'GET-01');
    assert.deepEqual(Object.keys(r.json).sort(), ['candidates', 'confidence', 'decision', 'evidence', 'height', 'naming', 'ok', 'proposal',
      'rack', 'rackId', 'rule', 'siteName', 'spaceId', 'spaceName']);   // siteName and spaceName: labels for the results header; naming and height: how it was named and how tall
    assert.deepEqual(Object.keys(r.json.evidence).sort(), ['deviceHints', 'labels', 'location', 'notes', 'pattern']);
  }
  // Every other outcome reads only, too: a label that matches nothing, a scan with no report, a refusal.
  let r = await call('GET', `/api/scan/${unmatched}/identity`, { user: TECH_A });
  assert.notEqual(r.json.decision, 'matched');
  built.length = 0;
  r = await call('GET', `/api/scan/${bare}/identity`, { user: TECH_A });
  assert.equal(r.json.decision, 'new');
  assert.deepEqual(built, [bare]);
  assert.equal((await call('GET', `${url}?spaceId=999999`, { user: TECH_A })).status, 404);
  assert.equal((await call('GET', `${url}?spaceId=abc`, { user: TECH_A })).status, 400);
  assert.equal((await call('GET', `${url}?tenantId=${SITE_B}`, { user: ADMIN_A })).status, 404);
  assert.equal(everythingStored(), storedAfter);
  assert.equal(auditLog.length, auditedAfter);
});

test('GET: the report is built once through the shared builder only when there is none and it is needed', async () => {
  const space = mkSpace(SITE_A, 6);
  const rack = mkRack(SITE_A, space.id, 'BUILT-01');
  const noReport = mkScan(SITE_A, space.id, TECH_A);
  const cached = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['BUILT-01']] }));
  built.length = 0;

  let r = await call('GET', `/api/scan/${noReport}/identity`, { user: TECH_A });
  assert.equal(r.json.rule, 'label');
  assert.equal(r.json.rack.id, rack.id);
  assert.deepEqual(built, [noReport]);

  r = await call('GET', `/api/scan/${cached}/identity`, { user: TECH_A });
  assert.equal(r.json.rule, 'label');
  assert.deepEqual(built, [noReport]); // the cached report was enough

  // A scan the record already names never needs the photo.
  await rackIdentity.confirm(noReport, { tenantId: SITE_A, userId: TECH_A, knownRackId: rack.id });
  r = await call('GET', `/api/scan/${noReport}/identity`, { user: TECH_A });
  assert.equal(r.json.rule, 'record');
  assert.deepEqual(built, [noReport]);
});

test('confirm: the technician who scanned it or an admin, never another member, never across organisations', async () => {
  const space = mkSpace(SITE_A, 6);
  const rack = mkRack(SITE_A, space.id, 'CF-01', 'F-CF-01');
  const rackId = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['CF-01'], ['CF-02']] }));
  const other = mkRack(SITE_A, space.id, 'CF-02');
  const url = `/api/scan/${rackId}/identity/confirm`;
  const spaceB = estate.createSpace(SITE_B, { name: 'B Row', rack_count: 2 }, ADMIN_B);
  const foreign = estate.upsertRack(SITE_B, { rack_id: nextId('TYPED'), space_id: spaceB.id, name: 'CF-01' }, ADMIN_B).rack;

  let r = await call('GET', `/api/scan/${rackId}/identity`, { user: TECH_A });
  assert.equal(r.json.decision, 'ambiguous');

  assert.equal((await call('POST', url, { body: { knownRackId: rack.id } })).status, 401);
  assert.equal((await call('POST', url, { user: TECH_B, body: { knownRackId: rack.id } })).status, 404);
  // A member of the Site who did not scan this rack may read it but not confirm it.
  r = await call('POST', url, { user: OTHER_A, body: { knownRackId: rack.id } });
  assert.equal(r.status, 403);
  // Exactly one of the three; and a rack of another organisation is not there.
  assert.equal((await call('POST', url, { user: TECH_A, body: {} })).status, 400);
  assert.equal((await call('POST', url, { user: TECH_A, body: { knownRackId: rack.id, name: 'X' } })).status, 400);
  assert.equal((await call('POST', url, { user: ADMIN_A, body: { knownRackId: foreign.id } })).status, 404);
  assert.equal((await call('POST', url, { user: OWNER, body: { knownRackId: foreign.id } })).status, 404);
  assert.equal(rackIdentity.confirmedRack(SITE_A, rackId), null);

  const audited = auditLog.length;
  r = await call('POST', url, { user: TECH_A, body: { knownRackId: rack.id } });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.bound, {
    rackId, knownRackId: rack.id, name: 'CF-01', facilityId: 'F-CF-01', created: false, source: 'confirmed',
  });
  assert.equal(r.json.identity.decision, 'matched');
  assert.equal(r.json.identity.confidence, 'confirmed');
  assert.equal(r.json.identity.rule, 'record');
  assert.equal(r.json.identity.rack.id, rack.id);

  const entry = auditLog[auditLog.length - 1];
  assert.equal(auditLog.length, audited + 1);
  assert.equal(entry.action, 'rack.identity.confirm');
  assert.equal(entry.status, 'ok');
  assert.equal(entry.targetType, 'rack');
  assert.equal(entry.targetId, rackId);
  assert.equal(entry.payload.knownRackId, rack.id);
  assert.equal(entry.payload.by, 'technician');

  const db = new Database(DB_PATH, { readonly: true });
  const row = db.prepare('SELECT * FROM rack_identity WHERE tenant_id = ? AND rack_id = ?').get(SITE_A, rackId);
  db.close();
  assert.equal(row.source, 'confirmed');
  assert.equal(row.known_rack_id, rack.id);
  assert.equal(row.confirmed_by, TECH_A);

  // The ladder was ambiguous before; the record now decides for everyone who may see it.
  r = await call('GET', `/api/scan/${rackId}/identity`, { user: ADMIN_A });
  assert.equal(r.json.rule, 'record');
  assert.match(r.json.candidates[0].reasons[0], /a person confirmed/);
  // An admin may correct it, and the key follows the rack.
  r = await call('POST', url, { user: MGR_A, body: { knownRackId: other.id } });
  assert.equal(r.status, 200);
  assert.equal(r.json.identity.rack.id, other.id);
  assert.equal(auditLog[auditLog.length - 1].payload.by, 'admin');
});

test('confirm: a new rack takes the confirmed name on the scan\'s own row, created when there is none', async () => {
  const space = mkSpace(SITE_A, 4);
  const existing = mkRack(SITE_A, space.id, 'NEW-01');
  const learned = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RACK 7']] }));
  const url = `/api/scan/${learned}/identity/confirm`;
  let r = await call('GET', `/api/scan/${learned}/identity`, { user: TECH_A });
  assert.equal(r.json.decision, 'new');
  assert.equal(r.json.proposal.name, 'RACK 7');

  // A name that is already a rack here is pointed at, not duplicated.
  r = await call('POST', url, { user: TECH_A, body: { name: 'new 01' } });
  assert.equal(r.status, 409);
  assert.equal(r.json.knownRackId, existing.id);
  assert.equal((await call('POST', url, { user: TECH_A, body: { name: '   ' } })).status, 400);
  assert.equal((await call('POST', url, { user: TECH_A, body: { name: 'x'.repeat(121) } })).status, 400);

  r = await call('POST', url, { user: TECH_A, body: { name: 'RACK 7' } });
  assert.equal(r.status, 200);
  assert.equal(r.json.bound.name, 'RACK 7');
  assert.equal(r.json.bound.created, false); // the learned row was named in place
  const own = estate.getRackByRackId(SITE_A, learned);
  assert.equal(own.id, r.json.bound.knownRackId);
  assert.equal(own.name, 'RACK 7');
  assert.equal(own.space_id, space.id);
  assert.equal(r.json.identity.rule, 'record');

  // The next photo of that rack is recognised by its label.
  const again = mkScan(SITE_A, space.id, TECH_A, report({ labels: [['RACK 7']] }));
  r = await call('GET', `/api/scan/${again}/identity`, { user: TECH_A });
  assert.equal(r.json.decision, 'matched');
  assert.equal(r.json.rule, 'label');
  assert.equal(r.json.rack.id, own.id);

  // A scan that was never tied to a space has no row yet: one is created.
  const unbound = mkScan(SITE_A2, null, TECH_A);
  r = await call('POST', `/api/scan/${unbound}/identity/confirm`, { user: ADMIN_A, body: { name: 'South 1' } });
  assert.equal(r.status, 200);
  assert.equal(r.json.bound.created, true);
  assert.equal(estate.getRackByRackId(SITE_A2, unbound).name, 'South 1');
});
