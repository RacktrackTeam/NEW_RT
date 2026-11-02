/**
 * Read a rack's photograph again with the models the server has now.
 *
 * A rack id is a hash of its photograph, and an analysed rack is a cache hit
 * for ever: scanning the same photo again returns the stored result. That is
 * right for speed and wrong the day a model improves. The patch panel model,
 * the trained unit grid and the OCR all reached new photographs and never an
 * old one, so a rack scanned before an improvement kept the older reading.
 *
 * The hard part is not running the pipeline, it is what else in the rack's
 * folder is keyed by the old reading. A better unit grid numbers the rack
 * differently, and the OCR result and a person's own correction are both filed
 * under a U ("U12": D-Link, "U12": DGS-1024C). Left as they are, the D-Link
 * override would be applied to whatever box the new grid happens to put at
 * U12. So each old box is matched to its new box by where it sits on the photo,
 * which is the one thing a re-read of the same photograph cannot change, and
 * everything keyed by U or by device number is carried across under the new
 * one.
 *
 * Nothing is deleted. The engine folder, meta.json and corrections.jsonl are
 * copied aside first (into the scan's backups/ folder), and derived files that
 * rebuild themselves from the map (the physical layer, the scan's result.json)
 * are simply left to rebuild.
 */
const fs = require('fs');
const path = require('path');

/** Two boxes over the same pixels are the same device; this much overlap. */
const MIN_IOU = 0.5;

/** Placeholders the pipeline emits per empty slot. They are not devices. */
const NOT_A_DEVICE = new Set(['Empty', 'Unidentified', 'Closed Unit']);

/** Files that rebuild themselves from device_unit_map.json when missing. */
const DERIVED = ['physical_layer.json', 'selected_port_info.json'];
/** Folders of per-port crops, named by device number, rebuilt on the next tap. */
const DERIVED_DIRS = ['ports'];
/** What the pipeline writes and a re-read replaces. */
const REPLACED = ['device_unit_map.json', 'device_unit_report.txt'];
const REPLACED_DIRS = ['images'];

function iou(a, b) {
  const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
  const iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const inter = ix * iy;
  const union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
  return union > 0 ? inter / union : 0;
}

const hasBox = (d) => d && Array.isArray(d.box) && d.box.length === 4;
const firstU = (d) => {
  const units = Array.isArray(d && d.units) ? d.units : [];
  const nums = units.map((u) => parseInt(String(u).replace(/\D/g, ''), 10)).filter(Number.isFinite);
  return nums.length ? Math.min(...nums) : null;
};
const uKey = (n) => `U${String(n).padStart(2, '0')}`;
const readJson = (p, fallback = null) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return fallback; } };

/**
 * Which new device each old one became.
 *
 * Returns { index: Map(oldIndex -> newIndex), u: Map(oldU -> newU) }. One to
 * one, best overlap first, so two new boxes over one old box do not both claim
 * it. Placeholders take part in the index map, because device numbers count
 * them, but never in the U map, because no correction is ever filed against
 * an empty slot.
 */
function matchDevices(oldMap, newMap) {
  const olds = (oldMap && oldMap.devices) || [];
  const news = (newMap && newMap.devices) || [];
  const pairs = [];
  olds.forEach((o, i) => {
    if (!hasBox(o)) return;
    news.forEach((n, j) => {
      if (!hasBox(n)) return;
      const v = iou(o.box.map(Number), n.box.map(Number));
      if (v >= MIN_IOU) pairs.push({ i, j, v });
    });
  });
  pairs.sort((a, b) => b.v - a.v);
  const index = new Map();
  const usedNew = new Set();
  for (const p of pairs) {
    if (index.has(p.i) || usedNew.has(p.j)) continue;
    index.set(p.i, p.j);
    usedNew.add(p.j);
  }
  const u = new Map();
  for (const [i, j] of index) {
    const o = olds[i]; const n = news[j];
    if (NOT_A_DEVICE.has(o.class_name) || NOT_A_DEVICE.has(n.class_name)) continue;
    const a = firstU(o); const b = firstU(n);
    if (a !== null && b !== null) u.set(uKey(a), uKey(b));
  }
  return { index, u };
}

/** The OCR result, each row moved to the U its box now reads as. */
function migrateOcr(ocr, uMap) {
  if (!ocr || !Array.isArray(ocr.devices)) return { data: ocr, moved: 0, dropped: [] };
  let moved = 0;
  const dropped = [];
  const devices = [];
  for (const row of ocr.devices) {
    const to = uMap.get(String(row.position));
    if (to) {
      if (to !== row.position) moved += 1;
      devices.push({ ...row, position: to });
    } else if (row.make || row.model) {
      // A reading with something in it and no box to go to is not silently
      // re-filed under a guess. It stays in the backup, and is named.
      dropped.push(row.position);
    }
  }
  return { data: { ...ocr, devices }, moved, dropped };
}

/** A person's own make and model corrections, re-filed under the new U. */
function migrateOverrides(overrides, uMap) {
  const data = {};
  const dropped = [];
  let moved = 0;
  for (const [pos, value] of Object.entries(overrides || {})) {
    const to = uMap.get(pos);
    if (!to) { dropped.push(pos); continue; }
    if (to !== pos) moved += 1;
    data[to] = value;
  }
  return { data, moved, dropped };
}

/**
 * A JSON-lines log whose rows name a device by its number. The number is moved
 * to the device's new number; the old one is kept beside it on the row, so the
 * history still says what it said at the time.
 */
function migrateIndexLog(text, indexMap) {
  let moved = 0;
  const out = String(text || '').split('\n').map((line) => {
    if (!line.trim()) return line;
    let row;
    try { row = JSON.parse(line); } catch { return line; }
    const i = Number(row.device_index);
    if (!Number.isInteger(i) || !indexMap.has(i)) return line;
    const j = indexMap.get(i);
    if (j === i) return line;
    moved += 1;
    return JSON.stringify({ ...row, device_index: j, device_index_before_reanalysis: i });
  });
  return { text: out.join('\n'), moved };
}

function copyDir(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const e of fs.readdirSync(from, { withFileTypes: true })) {
    const a = path.join(from, e.name); const b = path.join(to, e.name);
    if (e.isDirectory()) copyDir(a, b); else fs.copyFileSync(a, b);
  }
}
const rm = (p) => { try { fs.rmSync(p, { recursive: true, force: true }); } catch { /* already gone */ } };

const running = new Set();

/**
 * Re-read one rack.
 *
 * The caller names every path (the server takes them from lib/store.js):
 *   rackDir          the scan's engine folder
 *   imagePath        the scan's photo
 *   metaPath         the scan's meta.json
 *   correctionsPath  the scan's corrections.jsonl
 *   backupsDir       where the copy made first is kept
 * `runPipeline(imagePath, outputDir)` is the server's own analyse call, passed
 * in so this can be tested without the models. Returns what changed, in words a
 * person can read, or throws with a reason.
 */
async function reanalyzeRack({ rackId, rackDir, imagePath, metaPath, correctionsPath, backupsDir, runPipeline, stamp }) {
  if (running.has(rackId)) {
    const err = new Error('This rack is already being read again.');
    err.status = 409;
    throw err;
  }
  const mapPath = path.join(rackDir, 'device_unit_map.json');
  if (!fs.existsSync(mapPath)) {
    const err = new Error('This rack has not been analysed, so there is nothing to read again.');
    err.status = 404;
    throw err;
  }
  if (!imagePath || !fs.existsSync(imagePath)) {
    const err = new Error('The photograph of this rack is not kept, so it cannot be read again.');
    err.status = 409;
    throw err;
  }

  running.add(rackId);
  const when = stamp || new Date().toISOString().replace(/[:.]/g, '-');
  const backup = path.join(backupsDir, `${rackId}-${when}`);
  const work = path.join(backupsDir, `${rackId}-${when}.work`);
  try {
    // 1. Everything, as it was, before anything is touched.
    copyDir(rackDir, path.join(backup, 'engine'));
    for (const [from, name] of [[metaPath, 'meta.json'], [correctionsPath, 'corrections.jsonl']]) {
      if (from && fs.existsSync(from)) fs.copyFileSync(from, path.join(backup, name));
    }

    // 2. The pipeline writes into a folder of its own, so a failure half way
    //    leaves the rack exactly as it was. The photo is only read.
    fs.mkdirSync(work, { recursive: true });
    await runPipeline(imagePath, work);
    const newMap = readJson(path.join(work, 'device_unit_map.json'));
    if (!newMap || !Array.isArray(newMap.devices)) {
      throw new Error('The analysis did not produce a result, so the rack was left as it was.');
    }
    const oldMap = readJson(mapPath);
    const { index, u } = matchDevices(oldMap, newMap);
    return swapIn({ rackDir, metaPath, correctionsPath, work, backup, oldMap, newMap, index, u, rackId });
  } finally {
    rm(work);
    running.delete(rackId);
  }
}

/**
 * Steps 3 to 5, on their own so that any failure part way puts the rack back
 * exactly as the backup holds it: a rack half re-keyed is worse than one not
 * touched at all.
 */
function swapIn({ rackDir, metaPath, correctionsPath, work, backup, oldMap, newMap, index, u, rackId }) {
  try {

    // 3. Carry across what is keyed by the old reading.
    const summary = { rackId, backup, before: 0, after: 0, renumbered: [], ocr: null, overrides: null, logs: {} };
    summary.before = (oldMap.devices || []).filter((d) => !NOT_A_DEVICE.has(d.class_name)).length;
    summary.after = newMap.devices.filter((d) => !NOT_A_DEVICE.has(d.class_name)).length;
    summary.renumbered = [...u].filter(([a, b]) => a !== b).map(([a, b]) => `${a} -> ${b}`);

    const ocrPath = path.join(rackDir, 'ocr_devices.json');
    if (fs.existsSync(ocrPath)) {
      const m = migrateOcr(readJson(ocrPath), u);
      fs.writeFileSync(ocrPath, JSON.stringify(m.data, null, 2));
      summary.ocr = { moved: m.moved, dropped: m.dropped };
    }
    const ovPath = path.join(rackDir, 'device_overrides.json');
    if (fs.existsSync(ovPath)) {
      const m = migrateOverrides(readJson(ovPath, {}), u);
      fs.writeFileSync(ovPath, JSON.stringify(m.data, null, 2));
      summary.overrides = { moved: m.moved, dropped: m.dropped };
    }
    // Rows keyed by device number move to the new number. The corrections
    // log is otherwise only ever appended to; this re-key is the one rewrite,
    // and the file as it was is in the backup.
    const logs = [['corrections.jsonl', correctionsPath], ['port_identifications.jsonl', path.join(rackDir, 'port_identifications.jsonl')]];
    for (const [name, p] of logs) {
      if (!p || !fs.existsSync(p)) continue;
      const m = migrateIndexLog(fs.readFileSync(p, 'utf8'), index);
      fs.writeFileSync(p, m.text);
      summary.logs[name] = m.moved;
    }

    // 4. The new reading in, the old derived files out (they rebuild).
    for (const f of REPLACED) {
      const from = path.join(work, f);
      if (fs.existsSync(from)) fs.copyFileSync(from, path.join(rackDir, f));
    }
    for (const d of REPLACED_DIRS) {
      const from = path.join(work, d);
      if (!fs.existsSync(from)) continue;
      rm(path.join(rackDir, d));
      copyDir(from, path.join(rackDir, d));
    }
    for (const f of DERIVED) rm(path.join(rackDir, f));
    for (const d of DERIVED_DIRS) rm(path.join(rackDir, d));

    // 5. Say on the rack when and why it was read again.
    const meta = readJson(metaPath, {}) || {};
    meta.reanalyses = [...(meta.reanalyses || []), {
      at: new Date().toISOString(), backup: path.basename(backup),
      devicesBefore: summary.before, devicesAfter: summary.after, renumbered: summary.renumbered.length,
    }];
    fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
    return summary;
  } catch (err) {
    rm(rackDir);
    copyDir(path.join(backup, 'engine'), rackDir);
    for (const [to, name] of [[metaPath, 'meta.json'], [correctionsPath, 'corrections.jsonl']]) {
      const from = path.join(backup, name);
      if (to && fs.existsSync(from)) fs.copyFileSync(from, to);
    }
    err.message = `${err.message} The rack was put back as it was.`;
    throw err;
  }
}

module.exports = {
  reanalyzeRack, matchDevices, migrateOcr, migrateOverrides, migrateIndexLog, MIN_IOU, DERIVED,
};
