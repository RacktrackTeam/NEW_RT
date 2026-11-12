/**
 * The patching matrix of a Site: the manager's source-of-truth workbook
 * (lib/workbook.js reads it: the A-to-Z connection matrix, the rack, device
 * and port inventories, the label rules and the allowed values). In RT-EOS
 * the imported workbook IS the record for a Site. No other Excel layout is
 * imported; the template is served at GET /api/matrix/template.
 *
 * Storage (lib/store.js builds every path; nothing here does):
 *   <site>/matrix/<uploaded_at>_<file name>   the file as uploaded
 *   <site>/matrix/cables.json                 the active import: { format,
 *                                             import, cables, racks, devices,
 *                                             ports, label_rules, lookups,
 *                                             problems }
 *   <site>/matrix/history.json                earlier imports' summaries (no
 *                                             rows), newest first
 *   cache/uploads/matrix-<upload id>.<ext>    a pending upload, at most an hour
 *   cache/uploads/matrix-<upload id>.json     who uploaded it, for which Site
 *
 * On disk the fields are snake_case (cable_label, a_device ...); the API
 * speaks camelCase (cableLabel, aDevice ...), as the contract says.
 *
 * active() gives the checks only the cables they check: Record_Status Active
 * and Expected_State Connected (lib/workbook.js `checked`). A cable added
 * from a rack photo's tag, and every cable of an import made before the
 * manager's workbook, has no `checked` and is checked.
 *
 * The file is read by pipeline/matrix_read.py on the repo's Python. Reads of
 * cables.json are cached in memory per Site and dropped on commit or delete.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');
const store = require('./store');
const matrixFind = require('./matrix_find');
const workbook = require('./workbook');
const { pythonCmd } = require('./python');

// the RT-EOS readers (package pipeline) live in engine/readers; the matrix template beside this service
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', 'engine', 'readers');
const TEMPLATE_FILE = path.join(__dirname, '..', 'reference', 'RackTrack_A_Z_Source_of_Truth_Upload_Matrix.xlsx');
const PENDING_TTL_MS = 60 * 60 * 1000;
const PROBLEMS_SHOWN = 50;
const READ_TIMEOUT_MS = 60_000;
const EXTENSIONS = ['.xlsx', '.xlsm'];

const FIELDS = [
  'cableLabel', 'aDevice', 'aPort', 'zDevice', 'zPort',
  'aRack', 'aUnit', 'zRack', 'zUnit', 'cableType', 'connector', 'cableColor', 'notes',
];
const LABELS = {
  cableLabel: 'Cable label', aDevice: 'A device', aPort: 'A port', zDevice: 'Z device', zPort: 'Z port',
  aRack: 'A rack', aUnit: 'A unit', zRack: 'Z rack', zUnit: 'Z unit', cableType: 'Cable type', connector: 'Connector', cableColor: 'Cable colour', notes: 'Notes',
};
const SNAKE = {
  cableLabel: 'cable_label', aDevice: 'a_device', aPort: 'a_port', zDevice: 'z_device', zPort: 'z_port',
  aRack: 'a_rack', aUnit: 'a_unit', zRack: 'z_rack', zUnit: 'z_unit', cableType: 'cable_type', connector: 'connector', cableColor: 'cable_color', notes: 'notes',
};

class MatrixError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ── Paths (all through the store) ─────────────────────────────────────
function matrixDir(orgId, siteId) {
  const d = path.join(store.siteDir(orgId, siteId), 'matrix');
  fs.mkdirSync(d, { recursive: true });
  return d;
}
function pendingBase(uploadId) {
  if (!/^[a-f0-9]{16}$/.test(String(uploadId || ''))) return null;
  return path.join(store.cachePath('uploads'), `matrix-${uploadId}`);
}

// ── The reader ────────────────────────────────────────────────────────
function runReader(file, { headerRow = null, maxRows = null, sheet = null } = {}) {
  return new Promise((resolve, reject) => {
    const args = ['-m', 'pipeline.matrix_read', file];
    if (headerRow != null) args.push('--header-row', String(headerRow));
    if (maxRows != null) args.push('--max-rows', String(maxRows));
    if (sheet != null) args.push('--sheet', String(sheet));
    const child = spawn(pythonCmd(), args, {
      cwd: REPO_ROOT,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    let out = '';
    let err = '';
    const t = setTimeout(() => { try { child.kill('SIGKILL'); } catch { /* gone */ } }, READ_TIMEOUT_MS);
    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', (d) => { err += d.toString(); });
    child.on('error', (e) => { clearTimeout(t); reject(e); });
    child.on('close', () => {
      clearTimeout(t);
      try { resolve(JSON.parse(out)); } catch {
        reject(new MatrixError(422, `The file could not be read. ${err.trim().split('\n').pop() || ''}`.trim()));
      }
    });
  });
}

/** Read and parse a workbook file: the parsed workbook, or a MatrixError. */
async function readWorkbook(file) {
  const read = await runReader(file);
  if (read.error) throw new MatrixError(422, read.error);
  const parsed = workbook.parse(read);
  if (!parsed.ok) throw new MatrixError(422, parsed.error);
  return parsed;
}

// ── Helpers ───────────────────────────────────────────────────────────
function safeFileName(name) {
  const base = path.basename(String(name || 'matrix')).replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^\.+/, '');
  return base.slice(-120) || 'matrix';
}
function stampNow(d = new Date()) {
  return d.toISOString().replace(/[:.]/g, '-');
}

const camel = (s) => s.replace(/_([a-z0-9])/g, (_, ch) => ch.toUpperCase());
/** A stored record as the API speaks it: camelCase keys, one level deep. */
function camelRecord(o) {
  const out = {};
  for (const [k, v] of Object.entries(o || {})) {
    if (k === 'raw') continue;
    out[camel(k)] = v;
  }
  return out;
}

function toApiImport(i) {
  if (!i) return null;
  return {
    id: i.id,
    fileName: i.file_name,
    format: i.format || 'columns',
    sheetName: i.sheet_name,
    headerRow: i.header_row,
    uploadedBy: i.uploaded_by,
    uploadedAt: i.uploaded_at,
    rowCount: i.row_count,
    cableCount: i.cable_count,
    checkedCount: i.checked_count ?? i.cable_count,
    rackCount: i.rack_count ?? 0,
    deviceCount: i.device_count ?? 0,
    portCount: i.port_count ?? 0,
    problemCount: i.problem_count,
    revisions: i.revisions || [],
    status: i.status || 'active',
  };
}
function toApiCable(c) {
  const out = { rowNo: c.row_no };
  for (const f of FIELDS) out[f] = c[SNAKE[f]] ?? '';
  out.labelA = c.label_a || '';
  out.labelZ = c.label_z || '';
  out.labelAlias = c.label_alias || '';
  out.checked = c.checked !== false;
  for (const f of ['record_status', 'expected_state', 'connection_id', 'cable_id', 'circuit_id', 'media_type', 'fiber_mode', 'polarity', 'length_m', 'source_revision', 'change_ticket']) {
    if (c[f] != null) out[camel(f)] = c[f];
  }
  if (c.a) out.aEnd = camelRecord(c.a);
  if (c.z) out.zEnd = camelRecord(c.z);
  if (c.added) out.added = c.added;
  return out;
}
function toApiProblem(p) {
  return { rowNo: p.row_no, reason: p.reason, level: p.level || 'error', sheet: p.sheet || null };
}

// ── Cache of cables.json per Site ─────────────────────────────────────
const _cache = new Map();
function cacheKey(orgId, siteId) { return `${orgId ?? ''}:${siteId}`; }
function readActive(orgId, siteId) {
  const k = cacheKey(orgId, siteId);
  if (_cache.has(k)) return _cache.get(k).doc;
  const doc = store.readJson(path.join(matrixDir(orgId, siteId), 'cables.json'), null);
  _cache.set(k, { doc, checkedView: null });
  return doc;
}
function forget(orgId, siteId) { _cache.delete(cacheKey(orgId, siteId)); }
function readHistory(orgId, siteId) {
  const h = store.readJson(path.join(matrixDir(orgId, siteId), 'history.json'), []);
  return Array.isArray(h) ? h : [];
}

// ── Upload ────────────────────────────────────────────────────────────
/** What an upload (or the active import) holds, for the screen that shows it before the import. */
function uploadReport(parsed) {
  return {
    sheets: parsed.sheets,
    counts: parsed.counts,
    problems: parsed.problems.slice(0, PROBLEMS_SHOWN).map(toApiProblem),
    revisions: [...new Set(parsed.cables.map((c) => c.source_revision).filter(Boolean))],
  };
}

/**
 * Keep an uploaded file as a pending upload and read the whole workbook:
 * what it holds and every problem found, before anything is imported.
 */
async function createUpload({ tmpPath, originalName, siteId, orgId, user }) {
  const ext = path.extname(String(originalName || '')).toLowerCase();
  if (!EXTENSIONS.includes(ext)) {
    try { fs.unlinkSync(tmpPath); } catch { /* gone */ }
    throw new MatrixError(400, ext === '.xls'
      ? 'Save the workbook as .xlsx first. Old .xls files cannot be read.'
      : 'Upload the manager\'s workbook (.xlsx). Download the template from the Excel page.');
  }
  const uploadId = crypto.randomBytes(8).toString('hex');
  const base = pendingBase(uploadId);
  const file = `${base}${ext}`;
  fs.renameSync(tmpPath, file);
  fs.writeFileSync(`${base}.json`, JSON.stringify({
    uploadId, ext, siteId, orgId, userId: user?.id ?? null,
    fileName: safeFileName(originalName), at: new Date().toISOString(),
  }));
  let parsed;
  try {
    parsed = await readWorkbook(file);
  } catch (err) {
    discardUpload(uploadId);
    throw err;
  }
  return { uploadId, fileName: safeFileName(originalName), ...uploadReport(parsed) };
}

function pendingOf(uploadId) {
  const base = pendingBase(uploadId);
  if (!base) return null;
  const meta = store.readJson(`${base}.json`, null);
  if (!meta) return null;
  const file = `${base}${meta.ext}`;
  if (!fs.existsSync(file)) return null;
  if (Date.now() - Date.parse(meta.at) > PENDING_TTL_MS) { discardUpload(uploadId); return null; }
  return { ...meta, file };
}

function discardUpload(uploadId) {
  const base = pendingBase(uploadId);
  if (!base) return;
  for (const e of ['.xlsx', '.xlsm', '.csv', '.json']) {
    try { fs.unlinkSync(`${base}${e}`); } catch { /* gone */ }
  }
}

/** Remove pending uploads older than maxAgeMs (0: all of them). Boot calls it with 0. */
function cleanPendingUploads(maxAgeMs = PENDING_TTL_MS) {
  let n = 0;
  const dir = store.cachePath('uploads');
  for (const name of fs.readdirSync(dir)) {
    if (!name.startsWith('matrix-')) continue;
    const p = path.join(dir, name);
    try {
      if (Date.now() - fs.statSync(p).mtimeMs >= maxAgeMs) { fs.unlinkSync(p); n += 1; }
    } catch { /* gone */ }
  }
  return n;
}

// ── Commit ────────────────────────────────────────────────────────────
async function commit({ uploadId, siteId, orgId, user }) {
  const pending = pendingOf(uploadId);
  if (!pending || Number(pending.siteId) !== Number(siteId)) {
    throw new MatrixError(404, 'That upload has expired or was for another site. Upload the file again.');
  }
  const parsed = await readWorkbook(pending.file);
  if (!parsed.cables.length) throw new MatrixError(422, `The ${workbook.MAIN} sheet has no cables.`);

  const now = new Date();
  const dir = matrixDir(orgId, siteId);
  const storedFile = `${stampNow(now)}_${pending.fileName}`;
  fs.copyFileSync(pending.file, path.join(dir, storedFile));
  discardUpload(uploadId);

  const imp = {
    id: crypto.randomBytes(6).toString('hex'),
    format: 'manager',
    file_name: pending.fileName,
    stored_file: storedFile,
    sheet_name: workbook.MAIN,
    header_row: 0,
    uploaded_by: user?.username ?? null,
    uploaded_by_id: user?.id ?? null,
    uploaded_at: now.toISOString(),
    row_count: parsed.cables.length,
    cable_count: parsed.cables.length,
    checked_count: parsed.counts.checked,
    rack_count: parsed.counts.racks,
    device_count: parsed.counts.devices,
    port_count: parsed.counts.ports,
    problem_count: parsed.counts.problems,
    revisions: [...new Set(parsed.cables.map((c) => c.source_revision).filter(Boolean))],
  };
  const previous = readActive(orgId, siteId);
  if (previous && previous.import) {
    const history = readHistory(orgId, siteId);
    history.unshift({ ...previous.import, status: 'replaced', replaced_at: now.toISOString() });
    store.writeJsonAtomic(path.join(dir, 'history.json'), history);
  }
  store.writeJsonAtomic(path.join(dir, 'cables.json'), {
    format: 'manager',
    import: imp,
    cables: parsed.cables,
    racks: parsed.racks,
    devices: parsed.devices,
    ports: parsed.ports,
    label_rules: parsed.labelRules,
    lookups: parsed.lookups,
    problems: parsed.problems,
  });
  forget(orgId, siteId);
  return { summary: summary(orgId, siteId), replaced: previous?.import?.id ?? null, importId: imp.id };
}

// ── Reads ─────────────────────────────────────────────────────────────
function deviceCount(doc) {
  const set = new Set();
  for (const d of doc.devices || []) if (d.device_name) set.add(d.device_name.toLowerCase());
  for (const c of doc.cables || []) {
    if (c.a_device) set.add(c.a_device.toLowerCase());
    if (c.z_device) set.add(c.z_device.toLowerCase());
  }
  return set.size;
}

/** { import, counts, problems: [first 50] } */
function summary(orgId, siteId) {
  const doc = readActive(orgId, siteId);
  if (!doc || !doc.import) {
    return { import: null, counts: { cables: 0, checked: 0, racks: 0, devices: 0, ports: 0, labelRules: 0, problems: 0, errors: 0, warnings: 0 }, problems: [] };
  }
  const problems = doc.problems || [];
  const errors = problems.filter((p) => (p.level || 'error') === 'error').length;
  const checked = (doc.cables || []).filter((c) => c.checked !== false).length;
  return {
    import: toApiImport(doc.import),
    counts: {
      cables: doc.cables.length, checked,
      racks: (doc.racks || []).length, devices: deviceCount(doc), ports: (doc.ports || []).length,
      labelRules: (doc.label_rules || []).length,
      problems: doc.import.problem_count ?? problems.length, errors, warnings: problems.length - errors,
    },
    problems: problems.slice(0, PROBLEMS_SHOWN).map(toApiProblem),
  };
}

/** Cables of the active import. q: label, devices, ports contain it; device: either end is it. */
function cables(orgId, siteId, { q, device, limit, offset } = {}) {
  const doc = readActive(orgId, siteId);
  let list = doc?.cables || [];
  // Plain text over the labels, devices and ports, and the site's shorthand
  // (sw1 p7, pp1 10a, r1/u13/sw1/7; lib/matrix_find.js).
  list = matrixFind.find(list, q);
  const dev = String(device || '').trim().toLowerCase();
  if (dev) list = list.filter((c) => String(c.a_device || '').toLowerCase() === dev || String(c.z_device || '').toLowerCase() === dev);
  const lim = Math.min(Math.max(parseInt(limit, 10) || 100, 1), 1000);
  const off = Math.max(parseInt(offset, 10) || 0, 0);
  return { total: list.length, cables: list.slice(off, off + lim).map(toApiCable) };
}

/** The racks, devices or ports of the active import, for the Excel page. */
function inventory(orgId, siteId, kind) {
  const doc = readActive(orgId, siteId);
  const field = { racks: 'racks', devices: 'devices', ports: 'ports', labelRules: 'label_rules' }[kind];
  if (!field) throw new MatrixError(400, 'Ask for racks, devices, ports or labelRules.');
  const list = doc?.[field] || [];
  return { total: list.length, items: list.map((x) => ({ rowNo: x.row_no, ...camelRecord(x) })) };
}

/** Every import for the site, newest first: the active one, then the replaced ones. */
function history(orgId, siteId) {
  const doc = readActive(orgId, siteId);
  const out = [];
  if (doc && doc.import) out.push(toApiImport({ ...doc.import, status: 'active' }));
  for (const h of readHistory(orgId, siteId)) out.push(toApiImport(h));
  return out;
}

/** Remove one import: its rows (when active) and its file. */
function remove(orgId, siteId, importId) {
  const dir = matrixDir(orgId, siteId);
  const doc = readActive(orgId, siteId);
  const rmFile = (name) => {
    if (!name) return;
    const p = path.join(dir, path.basename(name));
    try { fs.unlinkSync(p); } catch { /* gone */ }
  };
  if (doc && doc.import && doc.import.id === importId) {
    rmFile(doc.import.stored_file);
    try { fs.unlinkSync(path.join(dir, 'cables.json')); } catch { /* gone */ }
    forget(orgId, siteId);
    return { removed: toApiImport(doc.import) };
  }
  const hist = readHistory(orgId, siteId);
  const i = hist.findIndex((h) => h.id === importId);
  if (i < 0) throw new MatrixError(404, 'That import was not found.');
  const [gone] = hist.splice(i, 1);
  rmFile(gone.stored_file);
  store.writeJsonAtomic(path.join(dir, 'history.json'), hist);
  return { removed: toApiImport(gone) };
}

/** The Home card: { cableCount, importedAt, importedBy } or null. */
function homeCard(orgId, siteId) {
  const doc = readActive(orgId, siteId);
  if (!doc || !doc.import) return null;
  return { cableCount: doc.cables.length, importedAt: doc.import.uploaded_at, importedBy: doc.import.uploaded_by };
}

/**
 * The active import as stored (snake_case rows), for the checks against the
 * photos (lib/sheet_check.js, lib/checks.js), holding only the cables that
 * are checked. null when nothing is imported.
 */
function active(orgId, siteId) {
  const doc = readActive(orgId, siteId);
  if (!doc || !doc.import) return null;
  const entry = _cache.get(cacheKey(orgId, siteId));
  if (entry && entry.checkedView) return entry.checkedView;
  const all = doc.cables || [];
  const view = all.every((c) => c.checked !== false) ? doc : { ...doc, cables: all.filter((c) => c.checked !== false), allCables: all };
  if (entry) entry.checkedView = view;
  return view;
}

/**
 * Add one cable to the active Excel dataset, as read on a rack photo's cable
 * tag: the next row number, a note saying where it came from. The file the
 * site imported is not changed; the row lives in the dataset RackTrack keeps.
 */
function addCable(orgId, siteId, input, user) {
  const doc = readActive(orgId, siteId);
  if (!doc || !doc.import) throw new MatrixError(409, 'Import the Excel dataset for this site first.');
  const clean = (v) => String(v ?? '').trim().slice(0, 200);
  const c = {};
  for (const f of Object.values(SNAKE)) c[f] = clean(input?.[f] ?? input?.[Object.keys(SNAKE).find((k) => SNAKE[k] === f)]);
  if (!c.cable_label) throw new MatrixError(400, 'A cable needs its label.');
  if (!c.a_device && !c.z_device) throw new MatrixError(400, 'A cable needs the device of at least one end.');
  const key = (s) => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if ((doc.cables || []).some((x) => key(x.cable_label) === key(c.cable_label))) throw new MatrixError(409, 'That cable is already in the Excel dataset.');
  const rowNo = (doc.cables || []).reduce((m, x) => Math.max(m, Number(x.row_no) || 0), 0) + 1;
  const at = new Date().toISOString().slice(0, 10);
  const row = {
    row_no: rowNo, ...c,
    notes: c.notes || `Added from the rack photo by ${user?.username || 'a person'} on ${at}`,
    record_status: 'Active', expected_state: 'Connected', checked: true,
    added: { by: user?.username || null, at: new Date().toISOString() },
  };
  const next = { ...doc, cables: [...(doc.cables || []), row], import: { ...doc.import, cable_count: (doc.cables || []).length + 1, checked_count: (doc.import.checked_count ?? (doc.cables || []).length) + 1 } };
  store.writeJsonAtomic(path.join(matrixDir(orgId, siteId), 'cables.json'), next);
  forget(orgId, siteId);
  return toApiCable(row);
}

/** The manager's blank template, to download from the Excel page. */
function templatePath() {
  if (!fs.existsSync(TEMPLATE_FILE)) throw new MatrixError(404, 'The template is not on this server.');
  return TEMPLATE_FILE;
}

/** Drop every cached read (the pilot data of an organization was deleted). */
function forgetAll() { _cache.clear(); }

module.exports = {
  MatrixError, createUpload, commit, summary, cables, inventory, history, remove, homeCard, active, addCable,
  templatePath, cleanPendingUploads, discardUpload, forgetAll, runReader, EXTENSIONS, LABELS, FIELDS,
};
