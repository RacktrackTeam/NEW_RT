/**
 * The E3 / E4 scoring script: a label check against a hand-checked answer
 * file gives matched, mismatched, missed, false flags and the label read rate.
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseAnswers, score } = require('../scripts/score-check');

const ANSWERS = `cable label,A device,A port,Z device,Z port,present
CAB-0101,SW-01,Gi1/0/1,PP-B,1,yes
CAB-0102,SW-01,2,PP-B,2,yes
CAB-0103,SW-01,3,PP-B,3,yes
CAB-0104,SW-01,4,PP-B,4,no
"CAB-0105",SW-01,5,PP-B,5,Yes
`;

const end = (status, expected, found) => ({ status, expected, found });
const CHECK = {
  id: 'abc12345', aRack: 'R01', zRack: 'R02',
  cables: [
    // Both ends read where they are: matched twice.
    { cableLabel: 'CAB-0101', status: 'matched', a: end('matched', { device: 'SW-01', port: '1' }, { device: 'SW-01', port: '1' }), z: end('matched', { device: 'PP-B', port: '1' }, { device: 'PP-B', port: '1' }) },
    // A-end read on port 7 though it is on 2 (a misplaced reading), and the
    // check called it a wrong port: mismatched, and a false flag since the
    // cable is where the sheet says. Z-end not read: missed.
    { cableLabel: 'CAB-0102', status: 'wrong_port', a: end('wrong_port', { device: 'SW-01', port: '2' }, { device: 'SW-01', port: '7' }), z: end('not_found', { device: 'PP-B', port: '2' }, null) },
    // Flagged missing at the A-end though a person found it there: missed and a false flag.
    { cableLabel: 'CAB-0103', status: 'missing_cable', a: end('missing_cable', { device: 'SW-01', port: '3' }, null), z: end('matched', { device: 'PP-B', port: '3' }, { device: 'PP-B', port: '3' }) },
    // Not really there, and not read: nothing to count.
    { cableLabel: 'CAB-0104', status: 'missing_cable', a: end('missing_cable', { device: 'SW-01', port: '4' }, null), z: end('missing_cable', { device: 'PP-B', port: '4' }, null) },
    // A label read that is on no cable at all: a misreading, a false flag.
    { cableLabel: 'CAB-9999', status: 'not_in_sheet', a: end('not_in_sheet', null, { device: 'SW-01', port: '9' }), z: { status: null } },
  ],
};

test('the answer file is read with or without a header; present must be yes or no', () => {
  const a = parseAnswers(ANSWERS);
  assert.equal(a.length, 5);
  assert.deepEqual(a[0], { label: 'CAB-0101', a: { device: 'SW-01', port: 'Gi1/0/1' }, z: { device: 'PP-B', port: '1' }, present: true });
  assert.equal(a[3].present, false);
  assert.equal(parseAnswers('X-1,A,1,B,1,no').length, 1);
  assert.throws(() => parseAnswers('X-1,A,1,B,1,yes\nX-2,A,2,B,2,maybe'), /Row 2: present must be yes or no/);
});

test('matched, mismatched, missed, false flags and the read rate', () => {
  const r = score(CHECK, parseAnswers(ANSWERS));
  // Present ends: 0101 x2, 0102 x2, 0103 x2, 0105 x2 (never met by the check) = 8.
  assert.equal(r.endsPresent, 8);
  assert.equal(r.matched, 3);
  assert.equal(r.mismatched, 1);
  assert.equal(r.missed, 4);
  assert.equal(r.falseFlags, 3);
  assert.equal(r.endsRead, 4);
  assert.equal(r.readRate, 0.5);
  assert.equal(r.placementRate, 0.375);
});
