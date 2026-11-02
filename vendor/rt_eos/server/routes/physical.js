/**
 * The physical-layer work on a scan (contract "PART 2"). Mounted at /api;
 * every route carries requireAuth.
 *
 * Zone close-ups
 *   GET    /scans/:scanId/zones            -> { devices: [ { deviceId, name, unit, portCount, zones: [ { from, to, done, zoneId } ] } ], remaining }
 *   POST   /scans/:scanId/zones            multipart image + deviceId + from + to -> { zoneId, photoUrl, labels, ports }
 *   DELETE /scans/:scanId                   the scan, for its taker or a manager
 *   DELETE /scans/:scanId/zones/:zoneId
 * Against the sheet
 *   GET    /scans/:scanId/sheet-check      -> { rackLabel, matrixImportId, units, missing, unexpected, uncapped }
 *   POST   /scans/:scanId/ports/uncapped   { deviceId, portNo, uncapped }
 *   POST   /scans/:scanId/ports/cap        { deviceId, portNo, flag: "uncapped" | "optic_no_cap" | null }
 * A-end / Z-end label check
 *   POST   /checks { aScanId, zScanId }    GET /checks    GET /checks/:id
 *   GET    /checks/:id/export.csv          GET /checks/:id/report.pdf
 * Worklist
 *   GET    /worklist[?scanId=]             POST /worklist/:id { action, value?, uncapped? }
 * Devices
 *   GET    /scans/:scanId/devices/:deviceId
 *   GET    /devices/specs?make=&model=
 *   GET    /scans/:scanId/sfp-advice
 * Reports
 *   GET    /scans/:scanId/report.pdf       GET /scans/:scanId/report.csv   GET /scans/:scanId/report.json
 *
 * Who: anyone who may open a scan (lib/rack_access, the same rule as the
 * scan's photo) works on it; anyone else is told it is not there (404). A
 * check is read by the people of its site (lib/site_scope). Device ids are
 * the 1-based numbers the screens use ("d3" is accepted too).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const express = require('express');
const multer = require('multer');
const sharp = require('sharp');
const Database = require('better-sqlite3');
const store = require('../lib/store');
const tenant = require('../lib/tenant');
const estate = require('../lib/estate');
const matrix = require('../lib/matrix');
const rackAccess = require('../lib/rack_access');
const scanView = require('../lib/scan_view');
const sheetCheck = require('../lib/sheet_check');
const checks = require('../lib/checks');
const worklist = require('../lib/worklist');
const deviceLookup = require('../lib/device_lookup');
const orgSettings = require('../lib/org_settings');
const fieldSources = require('../lib/field_sources');
const sfpAdvice = require('../lib/sfp_advice');
const reports = require('../lib/reports');
const imageIntake = require('../lib/image_intake');
const { ensureCrop, fileUrl } = require('../lib/crops');
const { siteOf, ScopeError } = require('../lib/site_scope');
const { pythonCmd } = require('../lib/python');
const { AUTH_DB } = require('../lib/data_paths');
const { logger } = require('../lib/observability');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ZONE_ID_RE = /^[a-f0-9]{12}$/;
const MAX_ZONE_BYTES = 25 * 1024 * 1024;
const ZONE_LONG_SIDE = 3000;
const MAX_ZONE_PORTS = 48;

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

let _db = null;
function db() {
  if (!_db) { _db = new Database(AUTH_DB); _db.pragma('journal_mode = WAL'); }
  return _db;
}

const nameOf = (d) => d.sheetName || scanView.displayName(d);

/** Suggested zones: blocks of 12 ports (6 for a panel of 24 or fewer). */
function suggestedZones(portCount) {
  const size = portCount <= 24 ? 6 : 12;
  const out = [];
  for (let f = 1; f <= portCount; f += size) out.push({ from: f, to: Math.min(portCount, f + size - 1) });
  return out;
}

/**
 * How many ports to offer close-ups of: the ports the rack photo counted, or,
 * when it read none (a data-centre switch from across the room), the highest
 * port the Excel lists on that device in this rack, else a usual count for
 * its kind. A close-up then reads the ports themselves.
 */
const USUAL_PORTS = { Switch: 48, 'Patch Panel': 24, Router: 8, Firewall: 8, Gateway: 8 };
function portsToShoot(d, doc, rack) {
  if (d.portCount > 0) return d.portCount;
  let most = 0;
  if (doc && d.sheetName) {
    for (const c of doc.cables || []) {
      for (const e of ['a', 'z']) {
        if (scanView.norm(c[`${e}_device`]) !== scanView.norm(d.sheetName)) continue;
        if (c[`${e}_rack`] && rack && !sheetCheck.sameRack(c[`${e}_rack`], rack)) continue;
        const n = scanView.portNumber(c[`${e}_port`]);
        if (n != null && n <= 96 && !/^\s*\d{1,3}\s*[A-Za-z]\s*$/.test(String(c[`${e}_port`]))) most = Math.max(most, n);
      }
    }
  }
  return Math.max(most, USUAL_PORTS[d.type] || 0);
}

function siteName(siteId) {
  try { return estate.getTenant(siteId)?.name || null; } catch { return null; }
}

// The makers the faceplate reader knows (pipeline/switch_ocr/vendors.json), for the
// make picker when a person adds a make and model by hand.
let _vendors = null;
function vendorNames() {
  if (_vendors) return _vendors;
  try {
    const doc = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'pipeline', 'switch_ocr', 'vendors.json'), 'utf8'));
    _vendors = Object.keys(doc.vendors || {}).sort((a, b) => a.localeCompare(b));
  } catch { _vendors = []; }
  return _vendors;
}

module.exports = function physicalRouter({ requireAuth, audit = null, saveDeviceFeedback = null, readZone = null, refreshResult = null } = {}) {
  if (typeof requireAuth !== 'function') throw new Error('physical routes need requireAuth');
  const router = express.Router();

  const wrap = (fn) => async (req, res) => {
    try {
      await fn(req, res);
    } catch (err) {
      const known = err instanceof HttpError || err instanceof ScopeError || err instanceof checks.CheckError
        || err instanceof worklist.WorklistError || err instanceof matrix.MatrixError;
      if (known) return res.status(err.status).json({ error: err.message });
      logger.error({ event: 'physical.error', err: err.message, route: req.originalUrl }, 'physical-layer route failed');
      return res.status(500).json({ error: 'That did not work. Try again.' });
    }
    return undefined;
  };

  // The scan named in the path, when the caller may open it; else 404.
  function scanOf(req) {
    const scanId = String(req.params.scanId || '').toUpperCase();
    if (!rackAccess.isValidRackId(scanId)) throw new HttpError(404, 'Scan not found.');
    if (!rackAccess.canAccessRack(req.user, scanId, tenant) || !store.has(scanId)) throw new HttpError(404, 'Scan not found.');
    return scanId;
  }

  function viewOf(scanId, { resolveSheet = true } = {}) {
    const view = scanView.load(scanId);
    if (!view) throw new HttpError(404, 'Scan not found.');
    const doc = view.siteId != null ? matrix.active(view.orgId, view.siteId) : null;
    if (resolveSheet && doc) sheetCheck.resolve(view, doc);
    return { view, doc };
  }

  function deviceOf(view, raw) {
    const n = scanView.deviceIndexOf(raw);
    const d = n ? view.visible.find((x) => x.deviceId === n) : null;
    if (!d) throw new HttpError(404, 'That device is not on this scan.');
    return d;
  }

  // ── Zone close-ups ──────────────────────────────────────────────────
  function zonesAnswer(view, doc = null) {
    const devices = [];
    let remaining = 0;
    for (const d of view.visible) {
      const count = d.hasPorts ? portsToShoot(d, doc, view.rackLabel) : 0;
      if (!(count > 0)) continue;
      const taken = view.zones.filter((z) => Number(z.deviceId) === d.deviceId);
      const zones = suggestedZones(count).map((s) => {
        const hit = [...taken].reverse().find((z) => z.from <= s.from && z.to >= s.to);
        return { from: s.from, to: s.to, done: !!hit, zoneId: hit ? hit.zoneId : null };
      });
      for (const z of taken) {
        if (!zones.some((s) => s.zoneId === z.zoneId)) zones.push({ from: z.from, to: z.to, done: true, zoneId: z.zoneId });
      }
      zones.sort((a, b) => a.from - b.from || a.to - b.to);
      remaining += zones.filter((z) => !z.done).length;
      devices.push({ deviceId: d.deviceId, name: scanView.shortName(d), unit: d.unitTop != null ? `U${d.unitTop}` : null, portCount: count, type: d.type, zones,
        ...(d.portCount > 0 ? {} : { portsNotRead: true }) });
    }
    return { rackLabel: view.rackLabel, devices, remaining };
  }

  router.get('/scans/:scanId/zones', requireAuth, wrap(async (req, res) => {
    const { view, doc } = viewOf(scanOf(req));
    res.setHeader('Cache-Control', 'no-store');
    res.json(zonesAnswer(view, doc));
  }));

  const zoneUpload = multer({
    storage: multer.diskStorage({
      destination: (req, file, cb) => cb(null, store.cachePath('uploads')),
      filename: (req, file, cb) => cb(null, `tmp_zone_${crypto.randomBytes(8).toString('hex')}`),
    }),
    limits: { fileSize: MAX_ZONE_BYTES, files: 1 },
  });

  router.post('/scans/:scanId/zones', requireAuth, (req, res, next) => {
    zoneUpload.single('image')(req, res, (err) => {
      if (!err) return next();
      if (err.code === 'LIMIT_FILE_SIZE') return res.status(413).json({ error: 'The photo is larger than 25 MB.' });
      return res.status(400).json({ error: 'The photo could not be received. Try again.' });
    });
  }, wrap(async (req, res) => {
    const tmp = req.file?.path || null;
    const drop = (p) => { if (p) { try { fs.unlinkSync(p); } catch { /* gone */ } } };
    let scanId;
    try { scanId = scanOf(req); } catch (e) { drop(tmp); throw e; }
    if (!req.file) throw new HttpError(400, 'Take or choose a close-up photo (field "image").');
    const { view } = viewOf(scanId);
    let d;
    try { d = deviceOf(view, req.body?.deviceId); } catch (e) { drop(tmp); throw e; }
    const from = Number(req.body?.from);
    const to = Number(req.body?.to);
    const max = d.portCount > 0 ? d.portCount : 96;
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < from) {
      drop(tmp); throw new HttpError(400, 'Enter the first and last port, the first no bigger than the last.');
    }
    if (to > max) { drop(tmp); throw new HttpError(400, `This device has ${max} ports. Enter a last port of ${max} or less.`); }
    if (to - from + 1 > MAX_ZONE_PORTS) { drop(tmp); throw new HttpError(400, `One close-up covers at most ${MAX_ZONE_PORTS} ports.`); }

    let intake;
    try {
      intake = await imageIntake.normalizeForPipeline(tmp, { pythonCmd: pythonCmd(), projectRoot: REPO_ROOT });
    } catch (e) {
      drop(tmp);
      throw new HttpError(400, imageIntake.isUnreadable?.(e) ? 'That file is not a photo that can be read.' : 'That file is not a photo that can be read.');
    }
    if (intake.kind === 'video') { drop(tmp); drop(intake.path); throw new HttpError(400, 'Take a photo, not a video.'); }
    const zoneId = crypto.randomBytes(6).toString('hex');
    const dir = store.file(scanId, 'zones');
    fs.mkdirSync(dir, { recursive: true });
    const photo = path.join(dir, `${zoneId}.jpg`);
    try {
      await sharp(intake.path).rotate().resize({ width: ZONE_LONG_SIDE, height: ZONE_LONG_SIDE, fit: 'inside', withoutEnlargement: true })
        .flatten({ background: '#ffffff' }).jpeg({ quality: 90 }).toFile(photo);
    } catch {
      drop(tmp); drop(intake.path); drop(photo);
      throw new HttpError(400, 'That file is not a photo that can be read.');
    } finally {
      drop(tmp);
      if (intake.path !== tmp) drop(intake.path);
    }

    let read;
    try {
      read = await readZone({ imagePath: photo, from, to });
    } catch (e) {
      read = { ok: false, error: e.message };
    }
    if (!read || !read.ok) {
      drop(photo);
      logger.warn({ event: 'zone.read_failed', scanId, err: read?.error }, 'zone close-up not read');
      throw new HttpError(422, 'The close-up could not be read. Hold the phone 20 to 30 cm from the ports and try again.');
    }
    // Taking the same range again replaces the earlier close-up.
    for (const z of view.zones) {
      if (Number(z.deviceId) === d.deviceId && z.from === from && z.to === to) {
        drop(path.join(dir, `${z.zoneId}.jpg`));
        drop(path.join(dir, `${z.zoneId}.json`));
      }
    }
    const zone = {
      zoneId, deviceId: d.deviceId, from, to, at: new Date().toISOString(), by: req.user?.username ?? null,
      imageSize: read.image_size || null, portsFrom: read.portsFrom || null, extraPorts: read.extraPorts || 0,
      // A cable flag line carries its parsed end (device, port, position), pipeline/cable_flags.py.
      // A label left between two ports (pipeline/zone_read.py) names both.
      labels: (read.labels || []).map((l) => ({ text: l.text, confidence: l.confidence, portNo: l.portNo ?? null, box: l.box, ...(l.end ? { end: l.end } : {}),
        ...(Array.isArray(l.between) && l.between.length === 2 ? { between: l.between.map(Number) } : {}) })),
      ports: (read.ports || []).map((p) => ({ portNo: p.portNo, box: p.box, state: p.state, sure: p.sure !== false,
        ...(p.cap_state ? { cap_state: p.cap_state, cap_reason: p.cap_reason || null } : {}) })),
    };
    store.writeJsonAtomic(path.join(dir, `${zoneId}.json`), zone);
    audit?.log({ req, action: 'zone.add', status: 'ok', targetType: 'rack', targetId: scanId,
      payload: { zoneId, deviceId: d.deviceId, from, to, labels: zone.labels.length, ports: zone.ports.length } });
    res.json({
      zoneId,
      photoUrl: fileUrl(scanId, `zones/${zoneId}.jpg`),
      labels: zone.labels,
      ports: zone.ports.map(({ portNo, box, state, cap_state: capState, cap_reason: capReason }) => ({ portNo, box, state, ...(capState ? { capState, capReason: capReason || null } : {}) })),
      portsFrom: zone.portsFrom,
      note: zone.portsFrom === 'labels' ? 'No port was found in the close-up, so the labels were numbered in reading order.' : null,
    });
  }));

  // Delete one scan: its photo, readings, close-ups and the rows that claim
  // it. The person who took it may, and a site manager or organization admin.
  // The Excel dataset and the accounts are not touched.
  router.delete('/scans/:scanId', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const meta = store.readMeta(scanId) || {};
    const u = req.user || {};
    const mine = meta.userId != null && Number(meta.userId) === Number(u.id);
    const manager = ['owner', 'org_admin', 'site_manager'].includes(u.role);
    if (!mine && !manager) throw new HttpError(403, 'Only the person who took this scan, or a site manager, can delete it.');
    const out = store.deleteScan(scanId);
    const d = db();
    for (const [table, col] of [['rack_owners', 'rack_id'], ['rack_user_claims', 'rack_id'], ['rack_identity', 'rack_id']]) {
      const has = d.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?").get(table);
      if (has) d.prepare(`DELETE FROM ${table} WHERE ${col} = ?`).run(scanId);
    }
    audit?.log({ req, action: 'scan.delete', status: 'ok', targetType: 'rack', targetId: scanId, payload: { files: out.files } });
    res.json({ ok: true, scanId, files: out.files });
  }));

  router.delete('/scans/:scanId/zones/:zoneId', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const zoneId = String(req.params.zoneId || '');
    if (!ZONE_ID_RE.test(zoneId)) throw new HttpError(404, 'That close-up was not found.');
    const dir = store.file(scanId, 'zones');
    const json = path.join(dir, `${zoneId}.json`);
    if (!fs.existsSync(json)) throw new HttpError(404, 'That close-up was not found.');
    for (const f of [json, path.join(dir, `${zoneId}.jpg`)]) { try { fs.unlinkSync(f); } catch { /* gone */ } }
    audit?.log({ req, action: 'zone.remove', status: 'ok', targetType: 'rack', targetId: scanId, payload: { zoneId } });
    res.json({ ok: true });
  }));

  // ── Against the sheet ───────────────────────────────────────────────
  router.get('/scans/:scanId/sheet-check', requireAuth, wrap(async (req, res) => {
    const { view, doc } = viewOf(scanOf(req), { resolveSheet: false });
    res.setHeader('Cache-Control', 'no-store');
    res.json(sheetCheck.check(view, doc));
  }));

  router.post('/scans/:scanId/ports/uncapped', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const { view } = viewOf(scanId, { resolveSheet: false });
    const d = deviceOf(view, req.body?.deviceId);
    const portNo = Number(req.body?.portNo);
    if (!Number.isInteger(portNo) || portNo < 1 || portNo > 999) throw new HttpError(400, 'Enter a whole port number.');
    if (typeof req.body?.uncapped !== 'boolean') throw new HttpError(400, 'Say whether the port is uncapped (true or false).');
    const mark = scanView.setUncapped(scanId, d.deviceId, portNo, req.body.uncapped, req.user);
    audit?.log({ req, action: 'port.uncapped', status: 'ok', targetType: 'rack', targetId: scanId,
      payload: { deviceId: d.deviceId, portNo, uncapped: req.body.uncapped } });
    res.json({ ok: true, deviceId: d.deviceId, portNo, uncapped: mark });
  }));

  // The two dust-cap marks, one route: an empty port with no cap
  // ("uncapped"), an optic fitted with no cap ("optic_no_cap"), or neither.
  router.post('/scans/:scanId/ports/cap', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const { view } = viewOf(scanId, { resolveSheet: false });
    const d = deviceOf(view, req.body?.deviceId);
    const portNo = Number(req.body?.portNo);
    if (!Number.isInteger(portNo) || portNo < 1 || portNo > 999) throw new HttpError(400, 'Enter a whole port number.');
    const flag = req.body?.flag ?? null;
    if (flag !== null && !scanView.CAP_FLAGS.includes(flag)) throw new HttpError(400, 'Say uncapped, optic_no_cap or null.');
    const mark = scanView.setCapFlag(scanId, d.deviceId, portNo, flag, req.user);
    audit?.log({ req, action: 'port.cap_flag', status: 'ok', targetType: 'rack', targetId: scanId,
      payload: { deviceId: d.deviceId, portNo, flag } });
    res.json({ ok: true, deviceId: d.deviceId, portNo, flag, mark, ...scanView.capFields(mark) });
  }));

  // ── Checks ──────────────────────────────────────────────────────────
  router.post('/checks', requireAuth, wrap(async (req, res) => {
    const ids = ['aScanId', 'zScanId'].map((k) => String(req.body?.[k] || '').toUpperCase());
    if (!ids[0] || !ids[1]) throw new HttpError(400, 'Pick the rack at each end.');
    // One rack may hold both ends of its cables (switch to patch panel): the
    // same scan at A and Z checks every cable end to end inside that rack.
    for (const id of ids) {
      if (!rackAccess.isValidRackId(id) || !rackAccess.canAccessRack(req.user, id, tenant) || !store.has(id)) {
        throw new HttpError(404, 'One of the two scans was not found.');
      }
    }
    const a = scanView.load(ids[0]);
    const z = scanView.load(ids[1]);
    if (a.siteId == null || a.siteId !== z.siteId) throw new HttpError(400, 'Both racks must be at the same site: the check uses that site\'s patching matrix.');
    const doc = matrix.active(a.orgId, a.siteId);
    const check = checks.run({ a, z, doc, by: req.user?.username ?? null });
    checks.save(a.orgId, a.siteId, check);
    audit?.log({ req, action: 'check.run', status: 'ok', targetType: 'site', targetId: String(a.siteId),
      payload: { id: check.id, aScanId: ids[0], zScanId: ids[1], counts: check.counts } });
    res.json(check);
  }));

  router.get('/checks', requireAuth, wrap(async (req, res) => {
    const scope = siteOf(req);
    res.setHeader('Cache-Control', 'no-store');
    res.json(checks.list(scope.orgId, scope.siteId));
  }));

  // A check of the caller's site; the owner and an organisation admin also
  // find one of any site they may read.
  function findCheck(req) {
    const id = String(req.params.id || '');
    let scope = null;
    try { scope = siteOf(req); } catch (e) { if (!['owner', 'org_admin'].includes(req.user?.role)) throw e; }
    if (scope) {
      const c = checks.get(scope.orgId, scope.siteId, id);
      if (c) return c;
    }
    if (['owner', 'org_admin'].includes(req.user?.role)) {
      for (const t of db().prepare('SELECT id, organization_id FROM tenants').all()) {
        if (!estate.accessLevel(req.user, t.id)) continue;
        const c = checks.get(t.organization_id ?? null, t.id, id);
        if (c) return c;
      }
    }
    throw new HttpError(404, 'That check was not found.');
  }

  router.get('/checks/:id', requireAuth, wrap(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.json(findCheck(req));
  }));

  router.get('/checks/:id/export.csv', requireAuth, wrap(async (req, res) => {
    const c = findCheck(req);
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="label-check-${c.id}.csv"`);
    res.send(`\ufeff${checks.toCsv(c)}`);
  }));

  router.get('/checks/:id/report.json', requireAuth, wrap(async (req, res) => {
    const c = findCheck(req);
    res.setHeader('Content-Disposition', `attachment; filename="label-check-${c.id}.json"`);
    res.json(c);
  }));

  router.get('/checks/:id/report.pdf', requireAuth, wrap(async (req, res) => {
    const c = findCheck(req);
    const pdf = await reports.checkPdf(c, { siteName: siteName(c.siteId), printedBy: req.user?.username ?? null });
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="label-check-${c.id}.pdf"`);
    res.send(pdf);
  }));

  // ── Worklist ────────────────────────────────────────────────────────
  router.get('/worklist', requireAuth, wrap(async (req, res) => {
    let scanId = null;
    if (req.query?.scanId) {
      scanId = String(req.query.scanId).toUpperCase();
      if (!rackAccess.isValidRackId(scanId)) throw new HttpError(404, 'Scan not found.');
    }
    res.setHeader('Cache-Control', 'no-store');
    res.json({ items: await worklist.listWithCrops(req.user, { scanId }) });
  }));

  router.post('/worklist/:id', requireAuth, wrap(async (req, res) => {
    const out = await worklist.answer(req, String(req.params.id || ''), req.body || {}, { saveDeviceFeedback });
    audit?.log({ req, action: 'worklist.answer', status: 'ok', targetType: 'rack', targetId: String(req.params.id).split('.')[0],
      payload: { item: req.params.id, action: req.body?.action } });
    res.json(out);
  }));

  // ── Devices ─────────────────────────────────────────────────────────
  router.get('/scans/:scanId/devices/:deviceId', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const { view } = viewOf(scanId);
    const d = deviceOf(view, req.params.deviceId);
    let cropUrl = null;
    try { cropUrl = await ensureCrop(scanId, `device-${d.deviceId}.jpg`, store.file(scanId, 'photo'), d.box, { pad: 0.04, width: 900 }); } catch { cropUrl = null; }
    const online = orgSettings.onlineLookups(view.orgId);
    // Specifications for switching gear only; nothing is looked up for the rest.
    const gear = deviceLookup.isSwitchGear(d.type);
    const sp = gear ? deviceLookup.specs(d.make, d.model, { online }) : { specs: null, specsStatus: 'not_switch' };
    const fields = { ...(d.fields || {}) };
    // A height the photo did not give, from the maker's datasheet.
    if (!fields.height && sp.specs?.heightU) fields.height = fieldSources.field('datasheet');
    if (d.sheetName) fields.sheetName = fieldSources.field('excel', null, sheetCheck.nameHowWords(d));
    res.setHeader('Cache-Control', 'no-store');
    res.json({
      deviceId: d.deviceId,
      type: d.type,
      name: scanView.shortName(d),
      unitTop: d.unitTop,
      heightU: d.heightU,
      make: d.make,
      model: d.model,
      labelText: d.labelText,
      confidence: d.confidence,
      source: d.source,
      sheetName: d.sheetName || null,
      labelMatch: d.labelMatch || null,
      fields,
      lookupsOff: !online,
      cropUrl,
      specs: sp.specs,
      specsStatus: sp.specsStatus,
      switchGear: gear,
      ports: d.ports.map((p) => ({ portNo: p.portNo, type: p.type, state: p.state === 'unknown' ? 'unknown' : p.state, cable: p.cable, uncapped: !!p.uncapped, opticNoCap: !!p.opticNoCap })),
    });
  }));

  function makeModel(req) {
    const make = String(req.query?.make || '').trim().slice(0, 80);
    const model = String(req.query?.model || '').trim().slice(0, 80);
    return { make, model };
  }

  // A person adds or corrects the make and model of a device. Kept in the
  // scan's engine/device_overrides.json under the device's unit (the key the
  // scan result already reads), written to corrections.jsonl, and the scan
  // result is rebuilt so every screen and download shows it. For switching
  // gear the specifications of the new model are looked up.
  router.put('/scans/:scanId/devices/:deviceId/make-model', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const { view } = viewOf(scanId, { resolveSheet: false });
    const d = deviceOf(view, req.params.deviceId);
    const clean = (v) => (v == null ? '' : String(v).replace(/[\u0000-\u001f]/g, '').trim().slice(0, 80));
    const make = clean(req.body?.make);
    const model = clean(req.body?.model);
    if (!make && !model) throw new HttpError(400, 'Enter the make, the model or both.');
    if (!d.positionKey) throw new HttpError(409, 'This device has no unit, so its make and model cannot be kept yet.');
    const file = path.join(store.file(scanId, 'engine'), 'device_overrides.json');
    const all = store.readJson(file, {}) || {};
    const before = { make: d.make || null, model: d.model || null };
    all[d.positionKey] = { ...(all[d.positionKey] || {}), ...(make ? { make } : {}), ...(model ? { model } : {}),
      by: req.user?.username ?? null, updated_at: new Date().toISOString() };
    store.writeJsonAtomic(file, all);
    store.appendCorrection(scanId, { at: new Date().toISOString(), user: req.user?.username ?? null, kind: 'device_make_model',
      target: { device_index: d.deviceId, position: d.positionKey }, before, after: { make: make || before.make, model: model || before.model } });
    try { if (refreshResult) refreshResult(scanId); } catch (err) {
      logger.warn({ event: 'make_model.refresh_failed', scanId, err: err.message }, 'scan result not rebuilt after a make and model');
    }
    audit?.log({ req, action: 'device.make_model', status: 'ok', targetType: 'rack', targetId: scanId,
      payload: { deviceId: d.deviceId, before, after: { make, model } } });
    const after = scanView.load(scanId).visible.find((x) => x.deviceId === d.deviceId) || d;
    const online = orgSettings.onlineLookups(view.orgId);
    const gear = deviceLookup.isSwitchGear(after.type);
    // Start the lookup for the new model now, for switching gear only.
    if (gear) deviceLookup.specs(after.make, after.model, { online });
    res.json({ ok: true, deviceId: d.deviceId, make: after.make, model: after.model, fields: after.fields, switchGear: gear });
  }));

  // A person says which unit a device stands in (its top unit). Kept in the
  // scan's corrections; the scan view places the device there from then on.
  router.put('/scans/:scanId/devices/:deviceId/unit', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const { view } = viewOf(scanId, { resolveSheet: false });
    const d = deviceOf(view, req.params.deviceId);
    const n = Number(req.body?.unitTop);
    if (!Number.isInteger(n) || n < 1 || n > 58) throw new HttpError(400, 'Enter the top unit as a whole number from 1 to 58.');
    store.appendCorrection(scanId, { at: new Date().toISOString(), user: req.user?.username ?? null, kind: 'device_unit',
      target: { device_index: d.deviceId }, before: { unitTop: d.unitTop }, after: { unitTop: n } });
    audit?.log({ req, action: 'device.unit', status: 'ok', targetType: 'rack', targetId: scanId, payload: { deviceId: d.deviceId, before: d.unitTop, after: n } });
    const after = scanView.load(scanId).visible.find((x) => x.deviceId === d.deviceId) || d;
    res.json({ ok: true, deviceId: d.deviceId, units: after.units, unitTop: after.unitTop, fields: after.fields });
  }));

  // A person says whether a cable is plugged into a port: the same
  // port_state row the worklist writes, which the scan view reads first.
  router.put('/scans/:scanId/ports/state', requireAuth, wrap(async (req, res) => {
    const scanId = scanOf(req);
    const { view } = viewOf(scanId, { resolveSheet: false });
    const d = deviceOf(view, req.body?.deviceId);
    const portNo = Number(req.body?.portNo);
    if (!Number.isInteger(portNo) || portNo < 1 || portNo > 999) throw new HttpError(400, 'Enter a whole port number.');
    const state = req.body?.state;
    if (state !== 'connected' && state !== 'empty') throw new HttpError(400, 'Say connected or empty.');
    const before = d.ports.find((p) => p.portNo === portNo)?.state || 'unknown';
    store.appendCorrection(scanId, { at: new Date().toISOString(), user: req.user?.username ?? null, kind: 'port_state',
      is_correct: before === state, target: { device_index: d.deviceId, port: portNo }, before: { state: before }, after: { state } });
    audit?.log({ req, action: 'port.state', status: 'ok', targetType: 'rack', targetId: scanId, payload: { deviceId: d.deviceId, portNo, state } });
    res.json({ ok: true, deviceId: d.deviceId, portNo, state });
  }));

  router.get('/devices/vendors', requireAuth, wrap(async (req, res) => {
    res.setHeader('Cache-Control', 'private, max-age=3600');
    res.json({ vendors: vendorNames() });
  }));

  // The caller's organization decides whether a lookup may go online.
  const onlineFor = (req) => orgSettings.onlineLookups(req.user?.organization_id ?? req.user?.organizationId ?? null);

  router.get('/devices/specs', requireAuth, wrap(async (req, res) => {
    const { make, model } = makeModel(req);
    res.setHeader('Cache-Control', 'no-store');
    res.json(deviceLookup.specs(make, model, { online: onlineFor(req) }));
  }));

  router.get('/scans/:scanId/sfp-advice', requireAuth, wrap(async (req, res) => {
    const { view, doc } = viewOf(scanOf(req));
    res.setHeader('Cache-Control', 'no-store');
    res.json(sfpAdvice.advise(view, doc, nameOf));
  }));

  // ── Reports ─────────────────────────────────────────────────────────
  const reportName = (view) => `${String(view.rackLabel || view.scanId).replace(/[^A-Za-z0-9_-]+/g, '_')}-report`;

  router.get('/scans/:scanId/report.pdf', requireAuth, wrap(async (req, res) => {
    const { view, doc } = viewOf(scanOf(req), { resolveSheet: false });
    const sheet = sheetCheck.check(view, doc);
    const pdf = await reports.scanPdf(view, sheet, { siteName: siteName(view.siteId), printedBy: req.user?.username ?? null, doc });
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="${reportName(view)}.pdf"`);
    res.send(pdf);
  }));

  router.get('/scans/:scanId/report.csv', requireAuth, wrap(async (req, res) => {
    const { view, doc } = viewOf(scanOf(req), { resolveSheet: false });
    const sheet = sheetCheck.check(view, doc);
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${reportName(view)}.csv"`);
    res.send(`\ufeff${reports.scanCsv(view, sheet, doc)}`);
  }));

  // The same report as data: the rack, each device and its ports, and the flags.
  router.get('/scans/:scanId/report.json', requireAuth, wrap(async (req, res) => {
    const { view, doc } = viewOf(scanOf(req), { resolveSheet: false });
    const sheet = sheetCheck.check(view, doc);
    const isSwitch = (d) => /switch/i.test(String(d.type || ''));
    const out = {
      rack: view.rackLabel || null,
      site: siteName(view.siteId) || null,
      scanId: view.scanId,
      unitsTotal: view.unitsTotal ?? null,
      excelDataset: !!doc,
      devices: view.visible.map((d) => ({
        unitTop: d.unitTop ?? null,
        units: d.units || [],
        name: d.sheetName || d.labelText || null,
        type: d.type || null,
        ...(isSwitch(d) ? { make: d.make || null, model: d.model || null } : {}),
        ports: (d.ports || []).map((p) => ({ port: p.portNo, type: p.type || null, state: p.state || null })),
      })),
      flags: { missing: sheet.missing, unexpected: sheet.unexpected, wrongUnit: sheet.units.filter((u) => u.status === 'wrong_unit'), uncapped: sheet.uncapped, opticNoCap: sheet.opticNoCap, wrongType: sheet.wrongType || [] },
    };
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${reportName(view)}.json"`);
    res.send(JSON.stringify(out, null, 2));
  }));

  return router;
};

module.exports.suggestedZones = suggestedZones;
