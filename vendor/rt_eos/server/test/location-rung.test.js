/**
 * The rack ladder's location rung: which Site was the photo taken at?
 *
 * GPS indoors cannot tell one rack from the next, so it never picks one. It
 * can tell Hyderabad from Bengaluru, and every other rung looks for the rack
 * inside the Site the scan is filed under. So a photo taken at a different
 * Site turns a match that rests on a label back into a suggestion - "Rack 1"
 * is a rack in both cities - while a person's own confirmation stands.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Database = require('better-sqlite3');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'rt-location-'));
process.env.RACKTRACK_AUTH_DB = path.join(tmp, 'auth.db');
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-location';

// Two Sites of one organisation, 500 km apart, and one far from both.
const HYD = { lat: 17.4474, lng: 78.3762 };
const BLR = { lat: 12.9716, lng: 77.5946 };
{
  const db = new Database(process.env.RACKTRACK_AUTH_DB);
  db.exec(`CREATE TABLE IF NOT EXISTS tenants (id INTEGER PRIMARY KEY, name TEXT, slug TEXT,
    lat REAL, lng REAL, organization_id INTEGER)`);
  db.prepare('INSERT INTO tenants (id, name, slug, lat, lng, organization_id) VALUES (?,?,?,?,?,?)')
    .run(1, 'Hyderabad DC1', 'hyd', HYD.lat, HYD.lng, 7);
  db.prepare('INSERT INTO tenants (id, name, slug, lat, lng, organization_id) VALUES (?,?,?,?,?,?)')
    .run(2, 'Bengaluru DC1', 'blr', BLR.lat, BLR.lng, 7);
  db.prepare('INSERT INTO tenants (id, name, slug, lat, lng, organization_id) VALUES (?,?,?,?,?,?)')
    .run(3, 'No Address DC', 'none', null, null, 7);
  db.close();
}

const location = require('../lib/location');
const { applyLocation } = require('../lib/rack_identity');

const near = (p, metres) => ({ lat: p.lat + metres / 111320, lng: p.lng, accuracyM: 30 });

// -- the judgement ----------------------------------------------------------

test('a photo taken at the Site is judged to be here, with how far from its address', () => {
  const v = location.judge(near(HYD, 120), { id: 1, name: 'Hyderabad DC1', ...HYD }, []);
  assert.equal(v.verdict, 'here');
  assert.match(v.note, /^Taken at Hyderabad DC1, 120 m from its address\.$/);
});

test('a photo taken at another Site of the organisation says which', () => {
  const v = location.judge(near(BLR, 50), { id: 1, name: 'Hyderabad DC1', ...HYD },
    [{ id: 2, name: 'Bengaluru DC1', ...BLR }]);
  assert.equal(v.verdict, 'elsewhere');
  assert.equal(v.nearest.name, 'Bengaluru DC1');
  assert.match(v.note, /Taken at Bengaluru DC1, not Hyderabad DC1, which is \d+ km away/);
});

test('a photo taken far from every Site says so', () => {
  const v = location.judge({ lat: 28.61, lng: 77.2, accuracyM: 20 }, { id: 1, name: 'Hyderabad DC1', ...HYD },
    [{ id: 2, name: 'Bengaluru DC1', ...BLR }]);
  assert.equal(v.verdict, 'away');
});

test('nothing to go on is said plainly and decides nothing', () => {
  assert.equal(location.judge(null, { name: 'x', ...HYD }).verdict, 'unknown');
  assert.equal(location.judge({ lat: 'x', lng: 1 }, { name: 'x', ...HYD }).verdict, 'unknown');
  assert.equal(location.judge(near(HYD, 0), { name: 'No Address DC', lat: null, lng: null }).verdict, 'unknown');
  const rough = location.judge({ ...HYD, accuracyM: 5000 }, { name: 'x', ...HYD });
  assert.equal(rough.verdict, 'unknown', 'a reading good only to 5 km cannot say which building');
});

test('a rough reading widens the circle rather than calling the Site wrong', () => {
  // 600 m out, but the phone said it was only good to 400 m: that is "here".
  const v = location.judge({ ...near(HYD, 600), accuracyM: 400 }, { name: 'Hyderabad DC1', ...HYD });
  assert.equal(v.verdict, 'here');
});

// -- what it does to the ladder's answer ------------------------------------

const labelMatch = () => ({
  decision: 'matched', confidence: 'probable', rule: 'label',
  rack: { source: 'known', id: 5, name: 'Rack 1' },
  candidates: [], evidence: { labels: [], pattern: {}, deviceHints: [], notes: [] },
});

test('a label match at another Site is turned back into a suggestion', () => {
  const out = applyLocation(labelMatch(), { tenantId: 1, capture: near(BLR, 40) });
  assert.equal(out.decision, 'suggested');
  assert.equal(out.rack, null, 'no rack is stated');
  assert.equal(out.evidence.location.verdict, 'elsewhere');
  assert.ok(out.evidence.notes.some((n) => /Taken at Bengaluru DC1/.test(n)));
});

test('a person\'s own confirmation is not overruled by the phone', () => {
  const confirmed = { ...labelMatch(), rule: 'record', confidence: 'confirmed' };
  const out = applyLocation(confirmed, { tenantId: 1, capture: near(BLR, 40) });
  assert.equal(out.decision, 'matched', 'they were standing there');
  assert.ok(out.evidence.notes.some((n) => /Taken at Bengaluru DC1/.test(n)), 'but it is still said');
});

test('a match at the right Site stands, and says where it was taken', () => {
  const out = applyLocation(labelMatch(), { tenantId: 1, capture: near(HYD, 80) });
  assert.equal(out.decision, 'matched');
  assert.equal(out.rack.id, 5);
  assert.equal(out.evidence.location.verdict, 'here');
});

test('no location at all leaves the answer exactly as it was', () => {
  const out = applyLocation(labelMatch(), { tenantId: 1, capture: null });
  assert.equal(out.decision, 'matched');
  assert.equal(out.evidence.location.verdict, 'unknown');
});

test('the reading itself travels with the verdict, so a person can see where they were', () => {
  const v = location.judge({ ...near(HYD, 120), accuracyM: 12 }, { id: 1, name: 'Hyderabad DC1', ...HYD }, []);
  assert.equal(v.verdict, 'here');
  assert.ok(v.at, 'the position is carried');
  assert.equal(Math.round(v.at.lng * 1000), Math.round(HYD.lng * 1000));
  assert.equal(v.at.accuracyM, 12);
  // Nothing to go on means nothing is invented.
  assert.equal(location.judge(null, { name: 'x', ...HYD }).at, undefined);
});
