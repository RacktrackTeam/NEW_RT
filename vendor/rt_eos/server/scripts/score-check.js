#!/usr/bin/env node
/**
 * Score an A-end / Z-end label check against a hand-checked answer file
 * (the pilot's E3 and E4: how many cable ends RackTrack placed right, and
 * how many labels it read).
 *
 *   node scripts/score-check.js --check <check id> --answers answers.csv
 *   node scripts/score-check.js --a <scan id> --z <scan id> --answers answers.csv
 *
 * --check      a check already run in the app (its <id>.json under the site's
 *              checks folder; every organization and site is searched)
 * --a / --z    two scans: the check is run now on them, against the site's
 *              active Excel dataset, and not saved
 * --answers    CSV, one row per cable, a header row optional:
 *                cable label, A device, A port, Z device, Z port, present
 *              present is yes or no: whether the cable is really there. The
 *              devices and ports are where a person found each end, by hand.
 * --json       print the result as JSON instead of the table
 * --verbose    list every cable end and how it scored
 *
 * Read only: it never writes to the data folder. Point it at a data folder
 * with RACKTRACK_AUTH_DB, as the server (lib/data_paths.js).
 *
 * How each cable end of the answer file is scored, against what the check
 * found at that end:
 *   matched      present, and the label was read on the device and port the
 *                hand check says
 *   mismatched   the label was read on another device or port; or read at an
 *                end of a cable the hand check says is not there
 *   missed       present, and no label was read at that end
 * False flags: a problem the check raised that the hand check does not bear
 * out - a wrong port or missing cable at an end that is really where the sheet
 * says, or a label "not in the sheet" that is not on any cable of the answers
 * (a misreading).
 * Label read rate: ends with a label read / ends really present.
 *
 * Only the ends in the two checked racks count (an end the check did not
 * cover is left out). A cable of the answers the check never met counts its
 * ends as missed.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const plain = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const portNo = (p) => { const m = String(p ?? '').match(/(\d+)(?!.*\d)/); return m ? Number(m[1]) : null; };

/** Split one CSV line, with quoted cells. */
function csvCells(line) {
  const out = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (q) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i += 1; } else if (ch === '"') q = false; else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ',') { out.push(cur); cur = ''; } else cur += ch;
  }
  out.push(cur);
  return out.map((c) => c.trim());
}

const YES = new Set(['yes', 'y', 'true', '1']);
const NO = new Set(['no', 'n', 'false', '0', 'absent', 'missing']);

/** The answer file: [{ label, a: { device, port }, z: { device, port }, present }]. */
function parseAnswers(text) {
  const rows = String(text || '').replace(/^\uFEFF/, '').split(/\r?\n/).filter((l) => l.trim());
  const out = [];
  rows.forEach((line, i) => {
    const c = csvCells(line);
    const present = String(c[5] || '').toLowerCase();
    if (i === 0 && !YES.has(present) && !NO.has(present)) return; // the header
    if (!c[0]) return;
    if (!YES.has(present) && !NO.has(present)) throw new Error(`Row ${i + 1}: present must be yes or no, not "${c[5] || ''}".`);
    out.push({ label: c[0], a: { device: c[1] || null, port: c[2] || null }, z: { device: c[3] || null, port: c[4] || null }, present: YES.has(present) });
  });
  return out;
}

const sameEnd = (x, y) => !!x && !!y && plain(x.device) === plain(y.device) && portNo(x.port) != null && portNo(x.port) === portNo(y.port);

/** Score a check (lib/checks.js run() shape) against the answers. Pure. */
function score(check, answers) {
  const byLabel = new Map((check.cables || []).map((c) => [plain(c.cableLabel), c]));
  const answerLabels = new Set(answers.map((a) => plain(a.label)));
  const t = { matched: 0, mismatched: 0, missed: 0, falseFlags: 0, endsPresent: 0, endsRead: 0 };
  const ends = [];
  for (const ans of answers) {
    const c = byLabel.get(plain(ans.label)) || null;
    for (const e of ['a', 'z']) {
      const truth = ans[e];
      // The check's end for this answer end: the one whose sheet device is the
      // hand-checked device, else the same letter.
      let end = null;
      if (c) {
        const other = e === 'a' ? 'z' : 'a';
        end = c[e] && c[e].expected && plain(c[e].expected.device) === plain(truth.device) ? c[e]
          : c[other] && c[other].expected && plain(c[other].expected.device) === plain(truth.device) ? c[other] : c[e];
        if (!end || end.status == null) continue; // not in the two racks checked
      }
      const found = end ? end.found : null;
      let how;
      if (ans.present) {
        t.endsPresent += 1;
        if (found) t.endsRead += 1;
        if (!found) how = 'missed';
        else how = sameEnd(found, truth) ? 'matched' : 'mismatched';
      } else {
        how = found ? 'mismatched' : null;
      }
      if (how) t[how] += 1;
      // A flag the hand check does not bear out.
      const expectedHere = end && end.expected && sameEnd(end.expected, truth);
      // The cable is really where the sheet says, yet the check said it was
      // missing, or on the wrong port.
      const falseFlag = !!(end && ans.present && expectedHere && (end.status === 'missing_cable' || end.status === 'wrong_port'));
      if (falseFlag) t.falseFlags += 1;
      ends.push({ label: ans.label, end: e === 'a' ? 'A' : 'Z', truth, found: found ? { device: found.device, port: found.port } : null, checkStatus: end ? end.status : 'not in check', score: how || 'absent, not read', falseFlag });
    }
  }
  // Labels read that no sheet cable carries, and no cable of the answers either: misreadings.
  for (const c of check.cables || []) {
    if (c.status !== 'not_in_sheet') continue;
    if (answerLabels.has(plain(c.cableLabel))) continue;
    t.falseFlags += 1;
    ends.push({ label: c.cableLabel, end: c.a && c.a.status ? 'A' : 'Z', truth: null, found: (c.a && c.a.found) || (c.z && c.z.found) || null, checkStatus: 'not_in_sheet', score: 'false flag', falseFlag: true });
  }
  return {
    ...t,
    readRate: t.endsPresent ? Math.round((t.endsRead / t.endsPresent) * 1000) / 1000 : null,
    placementRate: t.endsPresent ? Math.round((t.matched / t.endsPresent) * 1000) / 1000 : null,
    ends,
  };
}

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--json' || a === '--verbose') out[a.slice(2)] = true;
    else if (a.startsWith('--')) { out[a.slice(2)] = argv[i + 1]; i += 1; }
  }
  return out;
}

/** A saved check by id, from any organization and site. */
function findCheck(id) {
  const { DATA_ROOT } = require('../lib/data_paths');
  if (!/^[a-f0-9]{8,16}$/.test(String(id))) throw new Error('That is not a check id.');
  const orgs = path.join(DATA_ROOT, 'orgs');
  for (const o of fs.existsSync(orgs) ? fs.readdirSync(orgs) : []) {
    const sites = path.join(orgs, o, 'sites');
    for (const s of fs.existsSync(sites) ? fs.readdirSync(sites) : []) {
      const f = path.join(sites, s, 'checks', `${id}.json`);
      if (fs.existsSync(f)) return JSON.parse(fs.readFileSync(f, 'utf8'));
    }
  }
  throw new Error(`No check ${id} was found under ${orgs}.`);
}

function runOnScans(aId, zId) {
  const scanView = require('../lib/scan_view');
  const matrix = require('../lib/matrix');
  const checks = require('../lib/checks');
  const a = scanView.load(String(aId).toUpperCase());
  const z = scanView.load(String(zId).toUpperCase());
  if (!a || !z) throw new Error('One of the two scans was not found.');
  if (a.siteId == null || a.siteId !== z.siteId) throw new Error('Both scans must be at the same site.');
  return checks.run({ a, z, doc: matrix.active(a.orgId, a.siteId), by: 'score-check' });
}

const pct = (r) => (r == null ? 'n/a' : `${(r * 100).toFixed(1)}%`);

function main() {
  const o = args(process.argv.slice(2));
  if (!o.answers || (!o.check && !(o.a && o.z))) {
    console.error('Usage: node scripts/score-check.js (--check <id> | --a <scan id> --z <scan id>) --answers answers.csv [--json] [--verbose]');
    process.exit(2);
  }
  const answers = parseAnswers(fs.readFileSync(o.answers, 'utf8'));
  const check = o.check ? findCheck(o.check) : runOnScans(o.a, o.z);
  const r = score(check, answers);
  if (o.json) { console.log(JSON.stringify({ check: { id: check.id, aRack: check.aRack, zRack: check.zRack }, ...r }, null, 2)); return; }
  console.log(`Check ${check.id || '(run now)'}: ${check.aRack || 'A'} to ${check.zRack || 'Z'}, ${answers.length} cables in the answer file`);
  console.log('');
  console.log(`  Matched        ${String(r.matched).padStart(5)}   label read on the right device and port`);
  console.log(`  Mismatched     ${String(r.mismatched).padStart(5)}   read on another device or port, or read where no cable is`);
  console.log(`  Missed         ${String(r.missed).padStart(5)}   cable there, no label read`);
  console.log(`  False flags    ${String(r.falseFlags).padStart(5)}   problems raised that the hand check does not bear out`);
  console.log(`  Label read rate ${pct(r.readRate)} (${r.endsRead} of ${r.endsPresent} cable ends present)`);
  console.log(`  Placed right    ${pct(r.placementRate)}`);
  if (o.verbose) {
    console.log('');
    for (const e of r.ends) {
      const f = e.found ? `${e.found.device || '?'} port ${e.found.port ?? '?'}` : 'not read';
      const tr = e.truth ? `${e.truth.device || '?'} port ${e.truth.port ?? '?'}` : '-';
      console.log(`  ${String(e.label).padEnd(14)} ${e.end}  hand: ${tr.padEnd(22)} read: ${f.padEnd(22)} ${e.score}${e.falseFlag ? ', false flag' : ''}`);
    }
  }
}

if (require.main === module) {
  try { main(); } catch (err) { console.error(err.message); process.exit(1); }
}

module.exports = { parseAnswers, score, csvCells };
