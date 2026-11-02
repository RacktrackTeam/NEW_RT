/**
 * How tall a rack is, in units, and how that was decided.
 *
 * Racks are made in standard heights (EIA-310, 1U = 44.45 mm). The heights
 * sold are the ones in STANDARD_HEIGHTS; 42U is the most common, and a 22U
 * rack is a real rack, not a misreading. A photograph shows the shelves it
 * shows, so the count of shelves found in it is the weakest of three answers:
 *
 *   1. rail_numbers   the unit numbers printed on the rails. When enough of
 *                     them were read and they run the right way (bigger
 *                     numbers higher up), the highest one read is the height
 *                     as the maker numbered it.
 *   2. dataset        the site's Excel dataset puts devices in this rack at
 *                     units; the largest unit used, rounded UP to the next
 *                     standard height (a rack using U40 is at least a 42U).
 *   3. detected       the shelves the photograph showed, snapped to the
 *                     nearest standard height only when it is within one
 *                     unit of it (41 or 43 read as 42). Anything else is
 *                     kept as counted and said not to be a standard size.
 *
 * Whatever is decided is never lower than the highest unit a device was
 * numbered on. The devices keep the numbers the pipeline gave them, with one
 * exception: when the rails were read and every device sits a whole number of
 * units off them (a frame rail counted as a row), the shift is recorded as
 * unitOffset and the screens show the rails' numbers (lib/scan_view.js). The
 * engine's files are never rewritten.
 */
'use strict';

const STANDARD_HEIGHTS = [4, 6, 9, 12, 15, 18, 20, 22, 24, 27, 32, 36, 42, 45, 47, 48, 52];
const MAX_RACK_UNITS = 58;
// A ladder read off the rails needs at least this many different numbers on it.
const MIN_RAIL_NUMBERS = 3;

const unitNum = (u) => {
  const m = String(u ?? '').match(/\d+/);
  return m ? Number(m[0]) : null;
};

/** The standard height at or above n, or null when n is above them all. */
function standardAtOrAbove(n) {
  return STANDARD_HEIGHTS.find((h) => h >= n) ?? null;
}

/**
 * The standard height within one unit of n, or null. On a tie (21 is one from
 * both 20 and 22) the taller one: a photo loses a unit at its edge far more
 * often than it invents one.
 */
function standardNear(n) {
  let best = null;
  for (const h of STANDARD_HEIGHTS) {
    const d = Math.abs(h - n);
    if (d <= 1 && (best == null || d <= Math.abs(best - n))) best = h;
  }
  return best;
}

/**
 * The highest unit number on the rails, when the readings make a ladder:
 * [{ n, yPct }] -> { top, count, pitch } or null.
 *
 * A rail reads as noise too: the tick marks between the numbers come back as
 * a stray 1, 4 or 7. So the readings are fitted to a straight ladder - every
 * pair of readings proposes one (so many percent of the photo per unit, bigger
 * numbers higher up), and the one that the most readings sit on, within a
 * third of a unit, is kept. At least MIN_RAIL_NUMBERS different numbers must
 * sit on it, and then its highest number is the rack's top unit.
 */
function railLadder(numbers) {
  const pts = [];
  for (const r of numbers || []) {
    const n = Number(r?.n);
    const y = Number(r?.yPct);
    if (!Number.isInteger(n) || n < 1 || n > MAX_RACK_UNITS || !Number.isFinite(y)) continue;
    pts.push({ n, y });
  }
  let best = null;
  for (let i = 0; i < pts.length; i += 1) {
    for (let j = i + 1; j < pts.length; j += 1) {
      const a = pts[i];
      const b = pts[j];
      if (a.n === b.n) continue;
      const pitch = (b.y - a.y) / (a.n - b.n); // photo height per unit, > 0 when bigger numbers sit higher
      if (!(pitch > 0)) continue;
      const on = pts.filter((p) => Math.abs(a.y + (a.n - p.n) * pitch - p.y) <= pitch / 3);
      const distinct = new Set(on.map((p) => p.n)).size;
      if (!best || distinct > best.distinct) best = { distinct, on, pitch };
    }
  }
  if (!best || best.distinct < MIN_RAIL_NUMBERS) return null;
  // The share of distinct numbers that sit on the ladder must be most of them,
  // or the "ladder" is three strays that happen to line up.
  const allDistinct = new Set(pts.map((p) => p.n)).size;
  if (best.distinct / allDistinct < 0.5) return null;
  // A point on the ladder, to say which rail unit any height in the photo is.
  const ref = best.on.reduce((a, p) => (p.n > a.n ? p : a), best.on[0]);
  return { top: Math.max(...best.on.map((p) => p.n)), count: best.distinct, pitch: Math.round(best.pitch * 100) / 100, ref: { n: ref.n, y: ref.y } };
}

/**
 * How far the photo's own unit numbers are from the rails' numbers, when the
 * rails were read: the pipeline counts rows up from the bottom of the photo,
 * and a frame rail read as a row shifts every number by one. Each device's
 * middle is put on the rail ladder; when they all agree on one whole shift
 * (within 0.6 of a unit), that shift is the offset. Otherwise 0: the numbers
 * are left as they were read.
 *   devices: [{ units: [n], yPct }]  (yPct: the middle of the device's box)
 */
function unitOffsetFrom(ladder, devices) {
  if (!ladder || !ladder.ref || !(ladder.pitch > 0)) return 0;
  const diffs = [];
  for (const d of devices || []) {
    const us = (d.units || []).filter((n) => Number.isFinite(n));
    if (!us.length || !Number.isFinite(d.yPct)) continue;
    const onRail = ladder.ref.n + (ladder.ref.y - d.yPct) / ladder.pitch;
    const mid = (Math.min(...us) + Math.max(...us)) / 2;
    diffs.push(onRail - mid);
  }
  if (diffs.length < 2) return 0;
  const sorted = diffs.slice().sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const offset = Math.round(median);
  if (!offset) return 0;
  const agree = diffs.filter((x) => Math.abs(x - offset) <= 0.6).length;
  return agree / diffs.length >= 0.75 ? offset : 0;
}

/**
 * decideHeight({ railNumbers, datasetMaxUnit, detectedCount, maxDeviceUnit })
 * -> { unitsTotal, source, standard, note }
 *
 * source is 'rail_numbers', 'dataset', 'detected' or null (nothing to go on).
 */
function decideHeight({ railNumbers = [], datasetMaxUnit = null, detectedCount = 0, maxDeviceUnit = null, devices = null } = {}) {
  let out = null;
  let unitOffset = 0;
  const rail = railLadder(railNumbers);
  if (rail) {
    out = {
      unitsTotal: rail.top, source: 'rail_numbers',
      note: `the rails are numbered up to U${rail.top} (${rail.count} numbers read)`,
    };
    // The devices' own numbers, put on the rails' numbering where they agree.
    if (devices) {
      unitOffset = unitOffsetFrom(rail, devices);
      const tops = devices.flatMap((d) => (d.units || []).map((n) => n + unitOffset)).filter((n) => n >= 1);
      maxDeviceUnit = tops.length ? Math.max(...tops) : null;
      if (unitOffset) out.note += `; the photo's unit numbers were ${unitOffset > 0 ? 'raised' : 'lowered'} by ${Math.abs(unitOffset)} to match the rails`;
    }
  }
  const ds = Number(datasetMaxUnit);
  if (!out && Number.isInteger(ds) && ds >= 1 && ds <= MAX_RACK_UNITS) {
    const std = standardAtOrAbove(ds);
    out = std
      ? { unitsTotal: std, source: 'dataset', note: `the Excel dataset uses units up to U${ds}, so this is a ${std}U rack` }
      : { unitsTotal: ds, source: 'dataset', note: `the Excel dataset uses units up to U${ds}` };
  }
  const n = Number(detectedCount) || 0;
  if (!out && n > 0) {
    const std = standardNear(n);
    out = std
      ? { unitsTotal: std, source: 'detected', note: std === n ? `${n} units were counted in the photo` : `${n} units were counted in the photo, which is within one unit of the standard ${std}U` }
      : { unitsTotal: n, source: 'detected', note: `${n} units were counted in the photo; that is not a standard rack size` };
  }
  if (!out) return { unitsTotal: null, source: null, standard: false, unitOffset: 0, note: 'nothing was read that says how tall this rack is' };
  const top = Number(maxDeviceUnit);
  if (Number.isInteger(top) && top > out.unitsTotal) {
    out = { ...out, unitsTotal: top, note: `${out.note}; a device was read at U${top}, so the rack is at least that tall` };
  }
  return { ...out, standard: STANDARD_HEIGHTS.includes(out.unitsTotal), unitOffset };
}

/** The largest unit the dataset uses in the rack named `rackName`, or null. */
function datasetMaxUnitOf(cables, rackName) {
  if (!rackName || !Array.isArray(cables)) return null;
  const key = (s) => String(s || '').toUpperCase().replace(/[\s\-_./\\:]+/g, '');
  const want = key(rackName);
  if (!want) return null;
  let max = null;
  for (const c of cables) {
    for (const e of ['a', 'z']) {
      if (key(c[`${e}_rack`]) !== want) continue;
      const u = unitNum(c[`${e}_unit`]);
      if (u != null && u >= 1 && u <= MAX_RACK_UNITS && (max == null || u > max)) max = u;
    }
  }
  return max;
}

module.exports = { decideHeight, railLadder, unitOffsetFrom, datasetMaxUnitOf, standardNear, standardAtOrAbove, STANDARD_HEIGHTS, MAX_RACK_UNITS };
