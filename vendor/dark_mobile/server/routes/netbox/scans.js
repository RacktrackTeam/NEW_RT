/**
 * Capture and Detect: upload a rack photo, run the CV engine, store the result.
 */
const express = require('express');
const fs = require('fs');
const multer = require('multer');
const path = require('path');

const cfg = require('../../lib/netbox/config');
const reader = require('../../lib/netbox/reader');
const cv = require('../../lib/netbox/cv');
const store = require('../../lib/netbox/store');
const rackNames = require('../../lib/netbox/rack_names');
const switches = require('../../lib/netbox/switches');
const reconcile = require('../../lib/netbox/reconcile');
const report = require('../../lib/netbox/report');
const rackMatch = require('../../lib/netbox/rack_match');
const identity = require('../../lib/netbox/identity');
const bindings = require('../../lib/netbox/bindings');
const find = require('../../lib/netbox/find');
const { clientForUser } = require('../../lib/netbox/client_for');
const { alignPortsToNetBox } = require('../../lib/netbox/align');
// RackTrack's own libraries: who may touch which rack, and where its scans live.
const tenant = require('../../lib/tenant');
const { rackOwnershipParam, canAccessRack } = require('../../lib/rack_access');
// Who may use each route. The mount only authenticates; a member (the
// technician at the rack) reaches adopt and nothing else on this router.
const gates = require('./gates');
const { logger } = require('../../lib/observability');
const { db: authDb } = require('../../auth');   // for the site's name, nothing else

const router = express.Router();

store.init();

const upload = multer({
  storage: multer.diskStorage({
    destination: (req, file, cb) => cb(null, store.UPLOADS_DIR),
    filename: (req, file, cb) => {
      const safe = file.originalname.replace(/[^A-Za-z0-9._-]/g, '_');
      cb(null, `${Date.now()}-${safe}`);
    },
  }),
  limits: { fileSize: 60 * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    const ok = /^image\/(jpeg|png|webp|heic|heif)$/.test(file.mimetype);
    cb(ok ? null : new Error(`unsupported file type: ${file.mimetype}`), ok);
  },
});

/**
 * The scan behind a :id route, or null when this caller may not have it.
 *
 * store.getScan reads the global index, and a role gate only says what KIND of
 * user the caller is, not whose racks they may touch. So every /:id route on this
 * router loads its scan through here, and a scan belonging to another
 * organisation's rack is "no such scan" - the same 404 lib/rack_access gives for
 * a rack you cannot see, and for the same reason: a 403 would confirm it exists.
 *
 * This matters more than it used to. A cross-tenant POST once dirtied one scan
 * payload that the next re-detect wiped; it now mints a binding in a file that
 * deliberately outlives the scan, and outranks every score for that rack in every
 * later photograph.
 */
function scanFor(req, res) {
  const scan = store.getScan(req.params.id);
  if (!scan) { res.status(404).json({ error: 'no such scan' }); return null; }
  if (!canAccessRack(req.user, scan.rackId, tenant)) {
    logger?.warn?.('netbox.scan.denied', { scanId: String(req.params.id), userId: req.user?.id ?? null });
    res.status(404).json({ error: 'no such scan' });
    return null;
  }
  return scan;
}

/** Is the CV engine installed and are all its weights present? */
router.get('/engine', gates.admin, (req, res) => res.json(cv.engineStatus()));

router.get('/', gates.admin, (req, res) => res.json(store.listScans()));

/**
 * The scan history for one rack, newest first, with a plain summary of what
 * changed between each scan and the one before it. This is how a rack accrues a
 * record over time rather than a pile of unrelated scans.
 *
 * A technician may read it. The Report screen is theirs - it is what they hand
 * to whoever was not at the rack - and this route is a read of a scan they are
 * already allowed to see (scanFor scopes it to their own racks). Gated to admins
 * it answered 403, and the screen told the person who had just photographed the
 * rack that their report "is for an admin".
 */
router.get('/:id/report', gates.technician, (req, res) => {
  const scan = scanFor(req, res);
  if (!scan) return undefined;
  const doc = report.build(scan);
  if (!doc) return res.status(409).json({ error: 'This rack has not been scanned yet.' });
  res.json(doc);
});

router.get('/rack/:rackId/history', gates.admin, (req, res) => {
  const scans = store.scansForRack(req.params.rackId)
    .filter((s) => s.stages && s.stages.detect && s.stages.detect.status === 'ok')
    .map((s) => {
      const full = store.getScan(s.id);
      return { id: s.id, createdAt: s.createdAt, rackName: s.rackName || s.rackId,
               snapshot: full && full.payload ? full.payload.snapshot : null };
    });
  const history = scans.map((s, i) => {
    const older = scans[i + 1];   // the index is already newest-first
    const sum = s.snapshot ? summarise(s.snapshot) : { devices: 0, ports: 0 };
    return {
      id: s.id, createdAt: s.createdAt, rackName: s.rackName,
      devices: sum.devices, ports: sum.ports,
      change: older && older.snapshot && s.snapshot
        ? changeSummary(older.snapshot, s.snapshot) : null,
    };
  });
  res.json({ rackId: req.params.rackId, scans: history });
});

/**
 * Upload a photo and run detection on it.
 *
 * Synchronous on purpose for now: one rack takes under a minute and a job
 * queue would be machinery without a problem to solve yet. When video or
 * multi-rack lands, this becomes a queued job and the UI polls.
 */
// ── Adopt a RackTrack rack ────────────────────────────────────────────────
//
// RackTrack has already photographed and detected the rack: the engine's
// output sits in outputs/<rackId>/ (device_unit_map.json, original_image.jpg).
// The NetBox side keeps its own scan records, so the Review and Export steps
// need one that points at that existing work rather than re-uploading and
// re-detecting the same photo. Adopt builds the NetBox-shaped snapshot from
// the engine output already on disk — the same converter the detect step
// below uses — and files it as a scan of this rack.
//
// Idempotent: a rack is adopted once and asking again returns the same record,
// so every step can call it freely. ?refresh=1 rebuilds the snapshot from the
// current detection (and drops any review done against the old one).
//
// Every :rackId on this router is checked against the caller with the same
// guard the rest of RackTrack uses — a rack you cannot see is a 404 here too.
const OUTPUTS_DIR = process.env.RT_OUTPUTS_DIR || path.resolve(__dirname, '..', '..', '..', 'outputs');

router.param('rackId', rackOwnershipParam({ tenant, logger }));

/**
 * A rack's name — read it, or change it.
 *
 * GET returns the person-given name and the id it falls back to, so a screen
 * can show "Comms Room A" while everything underneath still keys on the id.
 * PUT sets it; an empty name clears it and the id shows again. Guarded by the
 * same rack-ownership check as every other :rackId route, so you can only
 * rename a rack you can see.
 */
router.get('/rack/:rackId/name', gates.admin, (req, res) => {
  res.json({
    rackId: req.params.rackId,
    name: rackNames.get(req.params.rackId),
    display: rackNames.display(req.params.rackId),
  });
});

router.put('/rack/:rackId/name', gates.admin, (req, res) => {
  const name = rackNames.set(req.params.rackId, (req.body || {}).name);
  res.json({ rackId: req.params.rackId, name, display: rackNames.display(req.params.rackId) });
});

/**
 * Which rack this is, asked of lib/rack_identity - the six steps that identify a
 * rack, ending in a person. Asked with no NetBox client on purpose: the only two
 * steps that state a rack and hand out a key are the record and a label that
 * equals one rack in the space the scan was tied to, and both are answered from
 * what we already hold. So this costs no NetBox traffic and cannot be made slow
 * by a NetBox that is not answering. Everything else it can say is a suggestion,
 * which never becomes a key without a person.
 *
 * Required at the point of use and behind a catch: the identity module is not
 * this router's to depend on, and a scan it cannot answer for must leave the
 * adopt working exactly as it did before.
 */
async function identifiedRack(rackId, tenantId) {
  if (tenantId == null) return null;
  try {
    const answer = await require('../../lib/rack_identity').identify(rackId, { tenantId });
    return answer && answer.decision === 'matched' && answer.rackKey && answer.rack ? answer : null;
  } catch { return null; }
}

/**
 * Recognise the rack before its uids are minted.
 *
 * Preview and export read the snapshot exactly as it is stored, so the moment
 * it is built is the one moment the customer's rack can become the key its
 * NetBox uids are built on. Two things are asked, in this order:
 *
 *   1. the identity steps, which include a person's own confirmation. When they
 *      state a rack, that rack is the rack, and its key is the key. A person who
 *      answered "which rack is this" on the screen has to see that answer in the
 *      comparison, or the answer was theatre.
 *   2. the resolver (rack_match) otherwise, exactly as before.
 *
 * Neither states a rack it only suspects, so a scan nobody has identified keeps
 * its uids on the photo hash, as it always did. Nothing here can fail the
 * caller: no NetBox, an unreachable one or an unbound scan all mean "no key".
 */
/**
 * Give each port the NetBox record that already carries its number, once, as
 * the snapshot is made, so the plan, the approval and the write all name ports
 * the same way. See lib/netbox/align.js. Never fails the caller: no NetBox
 * means the camera's identities stand.
 */
async function alignPorts(req, snapshot) {
  let client = null;
  try { client = clientForUser(req.user); } catch { client = null; }
  if (!client) return snapshot;
  try { return (await alignPortsToNetBox(snapshot, client)).snapshot; } catch { return snapshot; }
}

async function recogniseRack(req, { tenantId, rackId, fallbackName, siteName = null }) {
  let client = null;
  try { client = clientForUser(req.user); } catch { client = null; }
  let found;
  try {
    found = await rackMatch.resolveRack(client, {
      tenantId, rackId, scanName: rackNames.get(rackId) || null, fallbackName, siteName,
    });
  } catch (err) {
    found = { name: fallbackName, rackKey: null, source: 'scan',
              why: `the rack could not be looked up: ${err.message}` };
  }

  const identified = await identifiedRack(rackId, tenantId);
  if (identified && identified.rackKey !== found.rackKey) {
    // The steps named a rack and the resolver did not, or named another one. The
    // steps win: they read the photo and they carry a person's confirmation,
    // which is the last word on this question. The name stays the record's own.
    found = {
      ...found,
      rackKey: identified.rackKey,
      knownRackId: identified.rack.id ?? null,
      source: identified.rule === 'record' ? 'identified' : `identified-${identified.rule}`,
      name: identified.rack.name || identified.rack.facilityId || found.name,
      why: identified.rule === 'record'
        ? 'this scan is tied to that rack in the record'
        : 'the label read off the rack names that rack in the record',
    };
  }

  return {
    rackKey: found.rackKey || null,
    rackKeySource: found.source || null,
    rackKeyWhy: found.why || null,
    tenantId: tenantId ?? null,
    // A keyed rack is named as the customer's record names it. The local alias
    // stands in only for a rack nobody has identified.
    rackName: found.rackKey ? String(found.name || fallbackName) : fallbackName,
    // What the customer's own record says this scan is, carried onto the snapshot
    // so the planner can propose a bind instead of planning a create against a
    // rack the record already holds. `by` is what decides whether it may be
    // written: their own rack id may, a name may not.
    recordMatch: {
      siteId: found.siteId ?? null,
      // Why there is no site id, when there is none. The writer refuses every
      // bind without one, and a refusal has to say what is missing rather than
      // leave an admin guessing which of the two names is the one to fix.
      siteWhy: found.siteWhy ?? null,
      rackNetboxId: found.netboxId ?? null,
      by: found.netboxBy ?? null,
      confidence: found.confidence ?? 'none',
      why: found.netboxWhy || found.why || null,
      candidates: found.candidates || null,
    },
  };
}

// Bump when cv.toSnapshot starts reading the same map differently, so every
// adopted rack is re-read under the new rules the next time it is opened.
//   2 — a Switch with fewer than ten ports is a Router
//   3 - uids keyed on the customer's rack (Part B Stage 1)
//   4 - the key may come from the identity steps, so a copy made before they
//       were asked is keyed on the photo hash while the app says which rack it
//       is. One re-adopt on the next open puts the two back in agreement.
//   5 - the snapshot carries the record binding a person made
const SNAPSHOT_RULES = 5;

/**
 * Which rows of the customer's own record a person said this rack and its boxes
 * are, and store anything the caller just said.
 *
 * It is kept in the binding store rather than in the scan payload, for the same
 * reason a confirmed switch is: store.setPayload rewrites a payload whole, so
 * an answer kept there is lost on the next re-adopt or re-detect, and a
 * person's answer must outlive the photograph they gave it about. The store is
 * keyed on the tenant and the scan's rack id, so a second photo of the same
 * rack finds the same answer without anybody saying it twice.
 *
 * Nothing here writes to NetBox. The writer still plans a visible rebind row
 * and an admin still approves it.
 */
async function recordBindingFor(req, {
  tenantId, rackId, said = null, by = null, rackKey = null, expect = null,
}) {
  const scope = bindings.scopeOf({ tenantId, rackId });
  // Answers held under an older spelling of the scope are carried forward here
  // too, exactly as the switch confirmations are. A scan whose tenant was not on
  // disk when it was first opened wrote to t0|<rackId>, and a person's answer
  // must not become invisible - or, worse, lose the protection that goes with it
  // - because the scope was spelled differently that day.
  const legacy = bindings.legacyScopesOf({ tenantId, rackKey, rackId });
  if (legacy.length) {
    try { bindings.migrate(scope, legacy); }
    catch (err) { logger?.warn?.('netbox.bindings.migrate', { scope, error: err.message }); }
  }

  if (said && typeof said === 'object') {
    // Checked against the record before it is stored, not only when it is used.
    // The id arrives as a bare integer, so a transposed digit is the expected
    // input: a rack at another site, a box in another rack or a row that has gone
    // is refused here, with the row named, while the person is still looking at
    // the screen they typed it on.
    let client = null;
    try { client = clientForUser(req.user); } catch { client = null; }
    const want = expect && typeof expect === 'object' ? expect : {};
    const rackShown = {};
    const deviceShown = {};
    let checked = true;

    const look = async (endpoint, id, scopeFor) => {
      const hit = await find.byId(client, endpoint, id, { expect: scopeFor });
      if (hit.none) return { error: hit.why };
      return { row: hit.row };
    };
    // What the person was reading when they answered. A rack and a box are
    // shown different things, because they ARE different things: a box sits on
    // a shelf inside a rack, and a rack sits at a site and has neither.
    const shownFromRow = (row, { inside = true } = {}) => {
      const shown = {
        name: row.name ?? null,
        facilityId: row.facility_id ?? null,
        serial: row.serial ?? null,
        assetTag: row.asset_tag ?? null,
        siteId: row.site && typeof row.site === 'object' ? row.site.id : row.site ?? null,
      };
      if (!inside) return shown;
      shown.position = row.position ?? null;
      shown.rackId = row.rack && typeof row.rack === 'object' ? row.rack.id : row.rack ?? null;
      return shown;
    };

    const askedRack = bindings.asNetboxId(said.rackNetboxId);
    if (client && askedRack !== null) {
      const hit = await look(find.RACKS, askedRack, { siteId: want.siteId ?? null });
      if (hit.error) {
        return { error: `Record ${askedRack} cannot be this rack: ${hit.error}` };
      }
      Object.assign(rackShown, shownFromRow(hit.row, { inside: false }));
    } else if (askedRack !== null) {
      checked = false;
    }

    const heldRack = (bindings.recordBinding(scope) || {}).rackNetboxId ?? null;
    const boundRack = askedRack ?? bindings.asNetboxId(heldRack) ?? bindings.asNetboxId(want.rackNetboxId);
    const asked = said.deviceNetboxIds && typeof said.deviceNetboxIds === 'object'
      ? said.deviceNetboxIds : {};
    for (const [uid, value] of Object.entries(asked)) {
      const id = bindings.asNetboxId(value);
      if (id === null) continue;   // bindRecord below refuses it with its own sentence
      if (!client) { checked = false; continue; }
      const hit = await look(find.DEVICES, id, { rackId: boundRack ?? null });
      if (hit.error) return { error: `Record ${id} cannot be the box ${uid}: ${hit.error}` };
      deviceShown[uid] = shownFromRow(hit.row);
    }

    const out = bindings.bindRecord(scope, {
      rackNetboxId: said.rackNetboxId,
      deviceNetboxIds: asked && Object.keys(asked).length ? asked : null,
      rackShown: Object.keys(rackShown).length ? rackShown : null,
      deviceShown: Object.keys(deviceShown).length ? deviceShown : null,
      by: said.by ?? by ?? null,
      at: said.at ?? null,
      why: said.why ?? (checked ? '' : 'stored without being checked against the record, because '
        + 'NetBox could not be reached'),
    });
    if (out.error) return { error: out.error };
  }
  return { binding: bindings.recordBinding(scope) };
}

router.post('/adopt/:rackId', gates.technician, async (req, res) => {
  const { rackId } = req.params;
  const dir = path.join(OUTPUTS_DIR, rackId);
  const mapFile = path.join(dir, 'device_unit_map.json');

  // An adopted snapshot is a copy of the detection result as it stood. A rack
  // re-scanned since — or a rule that now reads the same map differently — is
  // not served from that copy: when the map on disk is newer than the copy,
  // adopt again. ?refresh=1 still forces it.
  const existing = store.scansForRack(rackId).find((s) => s.source === 'adopted');
  // scansForRack reads the index, which carries no payload - the payload is a
  // file of its own. Everything below that asks what this scan already holds has
  // to read it, and reading `existing.payload` instead made every one of those
  // questions answer "nothing": the rules version below always read as 0, so a
  // rack was re-adopted on every single open however fresh its copy was.
  const heldPayload = existing ? (store.getScan(existing.id)?.payload || {}) : {};
  let stale = false;
  if (existing && fs.existsSync(mapFile)) {
    try { stale = fs.statSync(mapFile).mtimeMs > new Date(existing.createdAt).getTime(); } catch { stale = false; }
  }
  // The rules that turn a map into a snapshot change too — a small switch
  // became a router — and a copy made under the old rules is as stale as one
  // made from an old map. The version travels in the payload; older or
  // missing means adopt again.
  if (existing && (heldPayload.rulesVersion || 0) < SNAPSHOT_RULES) stale = true;
  // A person naming the customer's own records is a new fact about this rack,
  // so the snapshot is rebuilt to carry it rather than served from a copy made
  // before they said it.
  if (req.body && req.body.recordBinding) stale = true;
  // A person has since said which rack this is, and the copy was keyed on
  // something else: every uid in it names the wrong rack, so the compare that
  // reads it would go on comparing against the wrong record. That answer is what
  // the whole identity flow is for, so it makes the copy stale like any other
  // change. One indexed read of the local database, and only for a rack that has
  // been adopted before, so an open of a rack nobody has confirmed costs nothing.
  if (existing && heldPayload.tenantId != null) {
    const confirmedKey = rackMatch.confirmedKeyFor(heldPayload.tenantId, rackId);
    if (confirmedKey && heldPayload.rackKey !== confirmedKey) stale = true;
  }
  if (existing && !req.query.refresh && !stale) {
    return res.json({ id: existing.id, rackId, adopted: false, createdAt: existing.createdAt });
  }
  if (!fs.existsSync(mapFile)) {
    return res.status(409).json({ error: 'This rack has not been scanned yet.' });
  }

  let map;
  try { map = JSON.parse(fs.readFileSync(mapFile, 'utf8')); }
  catch (err) {
    logger?.warn?.('netbox.adopt.map_unreadable', { rackId, error: err.message });
    return res.status(500).json({ error: 'This rack\'s scan could not be read. Scan it again.' });
  }

  const image = ['original_image.jpg', 'original_image.jpeg', 'original_image.png']
    .map((f) => path.join(dir, f)).find((p) => fs.existsSync(p)) || null;
  let meta = {};
  try { meta = JSON.parse(fs.readFileSync(path.join(dir, 'scan_meta.json'), 'utf8')); } catch { /* optional */ }

  // Names are a person's to give, never invented. The caller may pass them;
  // failing that the site is the name someone gave the Site this rack was
  // scanned under, and the rack keeps its id.
  let siteName = String((req.body && req.body.siteName) || '').trim();
  if (!siteName && meta.tenantId) {
    try {
      siteName = authDb.prepare('SELECT name FROM tenants WHERE id = ?').get(Number(meta.tenantId))?.name || '';
    } catch { /* an older schema without a name column: fall through */ }
  }
  if (!siteName) siteName = 'RackTrack';
  // A name given now wins; a name saved earlier stands; otherwise the id.
  if (req.body && req.body.rackName != null) rackNames.set(rackId, req.body.rackName);
  const localName = String((req.body && req.body.rackName) || rackNames.get(rackId) || rackId).trim();
  const scannedAt = meta.timestamp || new Date().toISOString();

  // Recognise the rack with the scan's own tenant, written beside the
  // detection when the photo was taken, not the caller's, which for an owner
  // or an org admin can be a different one. The key is minted here because
  // the uids are frozen here: preview and export read this snapshot as stored.
  const tenantId = meta.tenantId == null || meta.tenantId === '' ? null : Number(meta.tenantId);
  const known = await recogniseRack(req, {
    tenantId: Number.isFinite(tenantId) ? tenantId : null, rackId, fallbackName: localName, siteName,
  });
  // A rack that was recognised once does not become unrecognised because NetBox
  // was unreachable for the two seconds this adopt ran in. recogniseRack fails
  // soft to a null key by design, and a null key re-mints every uid in the
  // snapshot on the photo hash - which is the duplicate rack in NetBox that Part
  // B exists to prevent. The key a scan already holds stands until a lookup that
  // actually answered says otherwise.
  if (!known.rackKey && heldPayload.rackKey) {
    known.rackKey = heldPayload.rackKey;
    known.rackKeySource = heldPayload.rackKeySource ?? null;
    known.rackKeyWhy = 'kept from the last time this rack was recognised, because the lookup '
      + 'did not answer this time';
    if (heldPayload.rackName) known.rackName = heldPayload.rackName;
  }
  const rackName = known.rackName;

  // The map may carry the photo's path from wherever the engine ran; point it
  // at the file that is actually here, or at nothing.
  map.image = image || '';

  // RackTrack's OCR pass keeps make and model in ocr_devices.json, keyed by
  // rack position, not in the unit map. Fold the rows that actually read
  // something in under the names the converter looks for (ocr_make,
  // ocr_model), so an adopted rack exports the model RackTrack already read
  // rather than "Unidentified". Rows marked failed or skipped carry nothing
  // trustworthy and are left out; the device then takes the honest
  // "Unidentified <class>" path.
  try {
    const ocr = JSON.parse(fs.readFileSync(path.join(dir, 'ocr_devices.json'), 'utf8'));
    const rows = (ocr.devices || []).filter((r) =>
      ['ocr_full', 'ocr_make_only'].includes(String(r.source || '')) || Number(r.match_conf) > 0);
    const posOf = (u) => `U${String(parseInt(String(u).replace(/\D/g, ''), 10)).padStart(2, '0')}`;
    for (const d of map.devices || []) {
      if (!Array.isArray(d.units) || !d.units.length) continue;
      const first = posOf(d.units[0]);
      const row = rows.find((r) => r.position === first && r.class_name === d.class_name)
        || rows.find((r) => r.position === first);
      if (!row) continue;
      if (row.make && !d.ocr_make) d.ocr_make = row.make;
      if (row.model && !d.ocr_model) d.ocr_model = row.model;
    }
  } catch { /* no OCR file is fine: the camera's classes stand on their own */ }

  // A person may name the customer's own records in the same call that adopts
  // the rack. It is stored first, then read back, so the snapshot and the store
  // always say the same thing.
  const said = await recordBindingFor(req, {
    tenantId: Number.isFinite(tenantId) ? tenantId : null, rackId,
    said: req.body && req.body.recordBinding, by: req.user?.email || req.user?.name || null,
    rackKey: known.rackKey || heldPayload.rackKey || null,
    expect: {
      siteId: known.recordMatch.siteId,
      rackNetboxId: known.recordMatch.rackNetboxId,
    },
  });
  if (said.error) return res.status(400).json({ error: said.error });

  let snapshot;
  try {
    snapshot = cv.toSnapshot(map, {
      rackId, rackKey: known.rackKey, siteName, rackName, uHeight: cfg.U_HEIGHT, scannedAt,
      recordBinding: said.binding, recordMatch: known.recordMatch,
      // The rack id is the photo's hash, so a rack adopted again is the same
      // photograph analysed again. Each box keeps the identity it had.
      previous: heldPayload.snapshot || null,
    });
  } catch (err) {
    logger?.warn?.('netbox.adopt.convert_failed', { rackId, error: err.message });
    return res.status(500).json({ error: 'This rack\'s scan could not be read. Scan it again.' });
  }
  snapshot = await alignPorts(req, snapshot);
  // `map` is kept alongside the snapshot, as the detect step keeps it: GET /:id
  // derives the detection boxes from payload.map, and without it the Review
  // page's pick-from-photo has nothing to draw and every adopted scan reports
  // "no detections" while plainly having them.
  const payload = {
    snapshot, map, siteName, rackName,
    // The key the uids were built on, and why, so a re-detect keeps it and the
    // plan reads the same tenant. A null key means the uids are on the hash.
    rackKey: known.rackKey, rackKeySource: known.rackKeySource, rackKeyWhy: known.rackKeyWhy,
    tenantId: known.tenantId,
    recordMatch: known.recordMatch,
    adoptedFrom: rackId, adoptedAt: new Date().toISOString(), engineOutput: dir,
    rulesVersion: SNAPSHOT_RULES,
  };

  let rec;
  if (existing) {
    // Carry the matching forward. setPayload rewrites the payload whole, so a
    // re-adopt used to drop every placement a person had made by hand, and the
    // screen only re-posts a matching that still has something in it - so for the
    // rack this whole design exists for, two identical switches with nothing to
    // tell them apart, the answer was dropped and nothing put it back. A
    // placement onto a box this detection no longer has is left behind.
    const boxes = new Set((snapshot.devices || []).map((d) => d.uid));
    const kept = {};
    for (const [swId, uid] of Object.entries(heldPayload.matches || {})) {
      if (uid === null || boxes.has(uid)) kept[swId] = uid ?? null;
    }
    if (Object.keys(kept).length) payload.matches = kept;
    // The Review work is carried forward too, and this is not a nicety. Every one
    // of the five screens after Review re-adopts on load, and a rules bump makes
    // every already-adopted rack stale, so a re-adopt that rebuilt the payload as
    // a fresh object silently threw away payload.reconciled - the SNMP-enriched
    // snapshot that every compare and export prefers. The rack then exported
    // camera-only data: no serials, no cables, and device types back to
    // "Unidentified".
    //
    // It is kept only while it is still about these boxes. A photograph that now
    // holds different boxes has made the earlier merge out of date, and serving a
    // stale merge is worse than asking for Review again.
    //
    // The answer a person gave about the customer's own records is stamped onto
    // it as it is carried. Every compare and every write PREFERS the reconciled
    // snapshot, and the answer lives on the snapshot, so a record bound after
    // Review had already run reached nothing at all: the person bound a record,
    // the screen said it was bound, and the plan that followed had never heard
    // of it. The merge is the SNMP work, not the answer, so the answer is put
    // back on top of it every time it is carried forward.
    if (heldPayload.reconciled && Array.isArray(heldPayload.reconciled.devices)) {
      const was = heldPayload.reconciled.devices.map((d) => d.uid).sort().join('|');
      const now = [...boxes].sort().join('|');
      if (was === now) {
        payload.reconciled = {
          ...heldPayload.reconciled,
          recordBinding: said.binding ?? null,
          recordMatch: known.recordMatch ?? null,
        };
      }
    }
    if (heldPayload.changeNote) payload.changeNote = heldPayload.changeNote;
    store.setPayload(existing.id, payload);
    rec = existing;
  } else {
    // imageHash is left null on purpose: RackTrack's scan_meta carries a SHA-256
    // of the file, the NetBox side's similarity search compares perceptual
    // hashes, and one would be mistaken for the other.
    rec = store.addScan({
      rackId, source: 'adopted', imagePath: image,
      imageHash: null, rackName: localName, siteName, payload,
    });
  }
  const devices = (map.devices || []).length;
  store.recordStage(rec.id, 'capture', 'ok', 'adopted from the RackTrack scan');
  store.recordStage(rec.id, 'detect', 'ok', `${devices} device${devices === 1 ? '' : 's'} from the existing detection`);
  res.status(existing ? 200 : 201).json({
    id: rec.id, rackId, adopted: true, devices,
    rackKey: known.rackKey, rackKeyWhy: known.rackKeyWhy,
    recordBinding: said.binding,
  });
});

router.post('/', gates.admin, upload.single('image'), async (req, res) => {
  const engine = cv.engineStatus();
  if (!engine.ready) {
    // Which models or which interpreter are an operator's problem, not the
    // technician's, so they go to the log and the screen gets the line that
    // tells the person in front of the rack what to do about it.
    logger?.warn?.('netbox.scan.engine_not_ready', {
      pythonPresent: engine.pythonPresent, python: engine.python,
      modelsPresent: engine.modelsPresent, modelsTotal: engine.modelsTotal,
    });
    return res.status(503).json({
      stage: 'detect',
      error: 'Scanning is not ready on this server.',
      detail: 'Ask your administrator to finish setting up the scanning service.',
      engine,
    });
  }
  if (!req.file) return res.status(400).json({ error: 'no image uploaded (field name: image)' });

  const siteName = (req.body.siteName || cfg.SITE_NAME || '').trim();
  if (!siteName) {
    return res.status(400).json({
      error: 'This scan needs a site name. Add the site, then scan again.',
    });
  }
  const rackId = (req.body.rackId || `RK-${Date.now().toString(36).toUpperCase()}`).trim();
  if (req.body.rackName != null) rackNames.set(rackId, req.body.rackName);
  const localName = (req.body.rackName || rackNames.get(rackId) || cfg.RACK_NAME || rackId).trim();
  // Recognise the rack the same way adopt does, so a capture never quietly
  // keys on the hash where an adopt would have keyed on the customer's record.
  // A fresh capture has no scan meta on disk yet, so the caller's tenant is
  // the scan's tenant; it is stored with the scan and read from there after.
  const known = await recogniseRack(req, {
    tenantId: req.user?.tenant_id ?? null, rackId, fallbackName: localName, siteName,
  });
  const rackName = known.rackName;
  const keyFields = {
    rackKey: known.rackKey, rackKeySource: known.rackKeySource,
    rackKeyWhy: known.rackKeyWhy, tenantId: known.tenantId,
    recordMatch: known.recordMatch,
  };

  // Rack height is not asked for and not guessed. The camera cannot see it,
  // the operator was being made to type a number they often do not know, and
  // NetBox has a sensible default of its own. Left null, the exporter omits
  // the field entirely rather than sending a height nobody stated. Set
  // RT_U_HEIGHT if a site genuinely needs one pinned.
  const rawU = cfg.U_HEIGHT;
  const parsedU = rawU === null || rawU === undefined || rawU === '' ? null : Number(rawU);
  const uHeight = Number.isInteger(parsedU) && parsedU >= 1 && parsedU <= 100 ? parsedU : null;

  // Fingerprint the photo before running the models. If it closely matches a
  // rack already scanned, the minute of vision work is worth pausing to ask the
  // operator whether anything actually changed.
  const imageHash = await cv.imageHash(req.file.path).catch(() => null);
  const force = String(req.body.force || '') === 'true';
  const SAME_RACK = 0.90;

  if (imageHash && !force) {
    const match = store.findSimilarScan(imageHash);
    if (match && match.similarity >= SAME_RACK) {
      // Keep the photo and file the scan, but hold off on detection until the
      // operator decides. Nothing is analysed and nothing is thrown away.
      const rec = store.addScan({
        rackId, source: 'capture', imagePath: req.file.path, imageHash, rackName: localName, siteName,
        payload: { siteName, rackName, uHeight, imageHash, ...keyFields, pending: true },
      });
      store.recordStage(rec.id, 'capture', 'ok', path.basename(req.file.path));
      return res.json({
        scanId: rec.id,
        pending: true,
        duplicate: {
          matchScanId: match.id,
          rackId: match.rackId,
          rackName: match.rackName || match.rackId,
          scannedAt: match.createdAt,
          similarity: Math.round(match.similarity * 100),
        },
      });
    }
  }

  const rec = store.addScan({
    rackId, source: 'capture', imagePath: req.file.path, imageHash, rackName: localName, siteName, payload: {},
  });
  const outputDir = path.join(store.SCANS_DIR, `${rec.id}-cv`);

  try {
    const { map, stderr } = await cv.runDetect(req.file.path, outputDir);
    // A rack somebody already bound to the customer's own records stays bound
    // when it is photographed again. The store is keyed on the rack, not on
    // the photograph.
    const held = await recordBindingFor(req, {
      tenantId: known.tenantId, rackId, rackKey: known.rackKey,
    });
    const snapshot = cv.toSnapshot(map, {
      rackId, rackKey: known.rackKey, siteName, rackName, uHeight, scannedAt: rec.createdAt,
      recordBinding: held.binding, recordMatch: known.recordMatch,
    });
    store.setPayload(rec.id, { map, snapshot, siteName, rackName, uHeight, imageHash, ...keyFields });
    store.recordStage(rec.id, 'capture', 'ok', path.basename(req.file.path));
    store.recordStage(rec.id, 'detect', 'ok',
      `${snapshot.devices.length} devices · ${snapshot.interfaces.length} ports`
      + (snapshot.conflicts.length
        ? ` · ${snapshot.conflicts.length} conflict${snapshot.conflicts.length === 1 ? '' : 's'}`
        : ''));
    res.json({ scanId: rec.id, rackId, summary: summarise(snapshot), warnings: tail(stderr) });
  } catch (err) {
    store.recordStage(rec.id, 'detect', 'failed', String(err.message).slice(0, 500));
    res.status(500).json({ stage: 'detect', scanId: rec.id, error: String(err.message) });
  }
});

/**
 * Run detection again on a scan that already has its photograph.
 *
 * Detection used to happen exactly once, inside the upload, so a scan that was
 * taken while the engine was half-installed — or before a model was retrained
 * — could only be fixed by photographing the rack a second time. The picture
 * was never the problem. This re-reads the stored image and replaces the
 * result; the scan keeps its id, its rack and its place in the history.
 */
router.post('/:id/detect', gates.admin, async (req, res) => {
  const scan = scanFor(req, res);
  if (!scan) return undefined;
  if (!scan.imagePath || !fs.existsSync(scan.imagePath)) {
    return res.status(409).json({
      stage: 'detect',
      error: 'This scan has no stored photograph to analyse again.',
    });
  }

  const engine = cv.engineStatus();
  if (!engine.ready) {
    // Which models or which interpreter are an operator's problem, not the
    // technician's, so they go to the log and the screen gets the line that
    // tells the person in front of the rack what to do about it.
    logger?.warn?.('netbox.scan.engine_not_ready', {
      pythonPresent: engine.pythonPresent, python: engine.python,
      modelsPresent: engine.modelsPresent, modelsTotal: engine.modelsTotal,
    });
    return res.status(503).json({
      stage: 'detect',
      error: 'Scanning is not ready on this server.',
      detail: 'Ask your administrator to finish setting up the scanning service.',
      engine,
    });
  }

  // Whatever the scan was filed under stays what it is filed under. Detection
  // reads a photograph; it does not get to rename a rack or move a site.
  const siteName = scan.payload.siteName || cfg.SITE_NAME || '';
  const rackName = scan.payload.rackName || scan.rackId;
  const uHeight = scan.payload.uHeight ?? null;
  // The key the scan was filed under stays too. A scan keyed on the customer's
  // rack must not slide back onto its photo hash because it was read again.
  const keyFields = {
    rackKey: scan.payload.rackKey || null,
    rackKeySource: scan.payload.rackKeySource ?? null,
    rackKeyWhy: scan.payload.rackKeyWhy ?? null,
    tenantId: scan.payload.tenantId ?? null,
    recordMatch: scan.payload.recordMatch ?? null,
  };
  const outputDir = path.join(store.SCANS_DIR, `${scan.id}-cv`);

  // Read again rather than carried in the payload: a record binding outlives the
  // photograph, and the payload is rewritten whole below.
  const said = await recordBindingFor(req, {
    tenantId: keyFields.tenantId, rackId: scan.rackId,
    said: req.body && req.body.recordBinding,
    by: req.user?.email || req.user?.name || null,
    rackKey: keyFields.rackKey,
    expect: {
      siteId: (keyFields.recordMatch || {}).siteId ?? null,
      rackNetboxId: (keyFields.recordMatch || {}).rackNetboxId ?? null,
    },
  });
  if (said.error) return res.status(400).json({ error: said.error });

  try {
    const { map, stderr } = await cv.runDetect(scan.imagePath, outputDir);
    let snapshot = cv.toSnapshot(map, {
      rackId: scan.rackId, rackKey: keyFields.rackKey, siteName, rackName, uHeight,
      scannedAt: scan.createdAt, recordBinding: said.binding,
      recordMatch: keyFields.recordMatch,
      // The same photograph, read again: each box keeps the identity it had,
      // so a unit grid that numbers the rack differently moves devices rather
      // than handing one device's record to another.
      previous: scan.payload.snapshot || null,
    });
    snapshot = await alignPorts(req, snapshot);
    // The operator's note on what changed since the last scan, kept with the
    // scan so the rack's history reads as a record, not just a pile of scans.
    const changeNote = String((req.body && req.body.note) || '').trim().slice(0, 500) || null;
    // A placement a person made by hand survives a re-detect, for the boxes this
    // detection still has. The payload is rewritten whole here, and that is what
    // used to drop it.
    const boxes = new Set((snapshot.devices || []).map((d) => d.uid));
    const kept = {};
    for (const [swId, uid] of Object.entries(scan.payload.matches || {})) {
      if (uid === null || boxes.has(uid)) kept[swId] = uid ?? null;
    }
    store.setPayload(scan.id, {
      map, snapshot, siteName, rackName, uHeight,
      imageHash: scan.payload.imageHash || null,
      ...keyFields,
      ...(Object.keys(kept).length ? { matches: kept } : {}),
      changeNote,
    });
    store.recordStage(scan.id, 'detect', 'ok',
      `${snapshot.devices.length} devices \u00b7 ${snapshot.interfaces.length} ports`
      + (snapshot.conflicts.length ? ` \u00b7 ${snapshot.conflicts.length} conflict(s)` : ''));
    res.json({ scanId: scan.id, rackId: scan.rackId, summary: summarise(snapshot), warnings: tail(stderr) });
  } catch (err) {
    store.recordStage(scan.id, 'detect', 'failed', String(err.message).slice(0, 500));
    res.status(500).json({ stage: 'detect', scanId: scan.id, error: String(err.message) });
  }
});

router.get('/:id', gates.admin, (req, res) => {
  const scan = scanFor(req, res);
  if (!scan) return undefined;
  const snapshot = scan.payload.snapshot;
  res.json({
    id: scan.id, rackId: scan.rackId, source: scan.source, createdAt: scan.createdAt,
    siteName: scan.payload.siteName, rackName: scan.payload.rackName,
    uHeight: scan.payload.uHeight ?? null,
    stages: scan.stages,
    detections: scan.payload.map ? detectionsView(scan.payload.map) : null,
    summary: snapshot ? summarise(snapshot) : null,
    devices: snapshot ? devicesView(snapshot) : [],
    conflicts: snapshot ? snapshot.conflicts : [],
    hasImage: Boolean(scan.imagePath && fs.existsSync(scan.imagePath)),
  });
});

/**
 * Delete a scan.
 *
 * Irreversible, and it takes the photograph with it — there is no trash and no
 * undo, because the thing being deleted is an image of a customer's
 * infrastructure and keeping a copy the operator believes is gone would be
 * worse than losing it. The UI asks before calling this.
 */
router.delete('/:id', gates.admin, (req, res) => {
  // Checked before it is deleted, not after: this takes the photograph with it.
  const scan = scanFor(req, res);
  if (!scan) return undefined;
  const rec = store.deleteScan(req.params.id);
  if (!rec) return res.status(404).json({ error: 'no such scan' });
  return res.json({ deleted: rec.id, rackId: rec.rackId });
});

/** The uploaded photo, for the UI to show beside what was detected. */
router.get('/:id/image', gates.admin, (req, res) => {
  const scan = store.getScan(req.params.id);
  if (!scan || !canAccessRack(req.user, scan.rackId, tenant)
      || !scan.imagePath || !fs.existsSync(scan.imagePath)) {
    return res.status(404).json({ error: 'no image for this scan' });
  }
  res.sendFile(path.resolve(scan.imagePath));
});

/**
 * The engine's own annotated renders.
 *
 * These live in <id>-cv/images/ under the engine's numbered names. This route
 * used to look for a device_unit_annotation.png that the engine has never
 * written, so it 404'd every time and the UI quietly fell back to the
 * unannotated photo -- "Show detections" showed no detections.
 */
const ANNOTATED_VIEWS = {
  units: '1_units_only.png',
  devices: '2_devices_only.png',
  both: '3_units_and_devices.png',
  ports: '7_rack_all_ports.png',
};

router.get('/:id/annotated', gates.admin, (req, res) => {
  const dir = path.join(store.SCANS_DIR, `${Number(req.params.id)}-cv`, 'images');
  const wanted = ANNOTATED_VIEWS[req.query.view] || ANNOTATED_VIEWS.both;
  const order = [wanted, ...Object.values(ANNOTATED_VIEWS).filter((f) => f !== wanted)];
  const found = order.map((f) => path.join(dir, f)).find((f) => fs.existsSync(f));
  if (!found) {
    return res.status(404).json({ error: 'no annotated image' });
  }
  res.sendFile(path.resolve(found));
});

// ── shaping for the UI ──────────────────────────────────────────────────────

/**
 * What changed between two scans of the same rack. Devices are keyed by their U
 * slot (or name when unplaced), and compared on model, port count and serial.
 * Deliberately coarse: this is a human-readable "what moved", not a NetBox diff.
 */
function deviceMap(snap) {
  const typeOf = (uid) => (snap.deviceTypes || []).find((t) => t.uid === uid);
  const m = new Map();
  for (const d of (snap.devices || [])) {
    const k = d.position != null ? `U${d.position}` : d.name;
    m.set(k, {
      name: d.name,
      model: typeOf(d.deviceTypeUid)?.model || '',
      ports: (snap.interfaces || []).filter((i) => i.deviceUid === d.uid).length,
      serial: d.serial || '',
    });
  }
  return m;
}

function changeSummary(prev, curr) {
  const P = deviceMap(prev);
  const C = deviceMap(curr);
  const added = [];
  const removed = [];
  const changed = [];
  for (const k of C.keys()) if (!P.has(k)) added.push(k);
  for (const k of P.keys()) if (!C.has(k)) removed.push(k);
  for (const [k, d] of C) {
    const p = P.get(k);
    if (!p) continue;
    const fields = [];
    if (p.model !== d.model) fields.push('model');
    if (p.ports !== d.ports) fields.push('ports');
    if (p.serial !== d.serial) fields.push('serial');
    if (fields.length) changed.push({ slot: k, fields });
  }
  return {
    added, removed, changed,
    unchanged: C.size - added.length - changed.length,
    same: added.length === 0 && removed.length === 0 && changed.length === 0,
  };
}

function summarise(snap) {
  const devices = snap.devices || [];
  const identified = devices.filter((d) => {
    const t = (snap.deviceTypes || []).find((x) => x.uid === d.deviceTypeUid);
    return t && !t.model.startsWith('Unidentified');
  }).length;
  return {
    devices: devices.length,
    placed: devices.filter((d) => d.position !== null).length,
    unplaced: devices.filter((d) => d.position === null).length,
    identified,
    ports: (snap.interfaces || []).length,
    withSerial: devices.filter((d) => d.serial).length,
    withAssetTag: devices.filter((d) => d.assetTag).length,
    cables: (snap.cables || []).length,
    conflicts: (snap.conflicts || []).length,
  };
}

/**
 * Raw detection geometry, for drawing boxes over the photograph.
 *
 * Two coordinate spaces come out of the engine and they are NOT the same:
 * device boxes are absolute image pixels, port boxes are relative to their
 * own device's box. Ports are translated to absolute here, once, so no
 * caller has to remember -- getting this wrong draws every port in the
 * top-left corner of the image.
 */
/**
 * The boxes, in the photograph's own pixel space.
 *
 * The engine keeps a device's ports in FOUR arrays — `ports` (RJ45), plus
 * `sfp_ports`, `console_ports` and `other_ports` — and this used to read only
 * the first. The snapshot counts all of them, so the table said a switch had
 * 28 ports and the picture drew 24: the four SFP cages were counted, exported
 * and never shown. They are all read now and each carries its category, so
 * the drawing can tell an SFP cage from an RJ45 socket instead of pretending
 * one of them does not exist.
 *
 * `portCount` is the engine's own number for a device, kept beside the boxes
 * rather than replaced by them. Where the two disagree the UI says so — an
 * engine that counts 24 and emits 22 boxes is a fact about the detection, and
 * hiding it behind whichever number is more convenient would be inventing
 * confidence we do not have.
 */
const PORT_ARRAYS = [
  ['ports', 'main'],
  ['sfp_ports', 'sfp'],
  ['console_ports', 'console'],
  ['other_ports', 'other'],
];

const NOT_DRAWN = new Set(['Empty', 'Unidentified']);

function detectionsView(map) {
  const devices = (map.devices || [])
    // Do not box a blank slot or a detection the classifier could not name.
    .filter((d) => !NOT_DRAWN.has(d.class_name || 'Unidentified'))
    .map((d, i) => {
      const [dx, dy] = d.box || [0, 0];
    // Same canonical, de-duplicated port list the snapshot's interfaces use, so
    // the boxes drawn and the table's count are always the same number.
    const ports = cv.extractPorts(d).map((p) => ({
      box: [p.box[0] + dx, p.box[1] + dy, p.box[2] + dx, p.box[3] + dy],
      status: p.status,
      cls: p.cls,
      index: p.index,
      category: p.category,
      // The engine marks ports it filled in to complete a row it could only
      // partly see. Drawn differently: this one was reasoned, not observed.
      synthesized: p.synthesized,
      confidence: p.confidence,
    }));
    const counted = ports.length;
    return {
      i,
      box: d.box,
      label: d.class_name || 'device',
      confidence: d.confidence ?? null,
      units: d.units || [],
      source: d.source || '',
      portCount: counted,
      ports,
    };
  });
  return {
    rackBounds: map.rack_bounds || null,
    unitSource: map.unit_source || '',
    unitsDetected: (map.units_detected || []).length,
    devices,
    ports: devices.reduce((n, d) => n + d.ports.length, 0),
    portsCounted: devices.reduce((n, d) => n + d.portCount, 0),
    portsSynthesized: devices.reduce(
      (n, d) => n + d.ports.filter((p) => p.synthesized).length, 0),
  };
}

function devicesView(snap) {
  const typeOf = (uid) => (snap.deviceTypes || []).find((t) => t.uid === uid);
  const roleOf = (uid) => (snap.deviceRoles || []).find((r) => r.uid === uid);
  return (snap.devices || [])
    .map((d) => ({
      uid: d.uid,
      name: d.name,
      position: d.position,
      role: roleOf(d.roleUid)?.name || '',
      model: typeOf(d.deviceTypeUid)?.model || '',
      identified: !(typeOf(d.deviceTypeUid)?.model || '').startsWith('Unidentified'),
      ports: (snap.interfaces || []).filter((i) => i.deviceUid === d.uid).length,
      serial: d.serial,
      assetTag: d.assetTag,
      evidence: d.evidence,
      connectedPorts: d.provenance?.connectedPorts ?? null,
    }))
    .sort((a, b) => (b.position ?? -1) - (a.position ?? -1));
}

const tail = (s, n = 6) =>
  String(s || '').trim().split('\n').filter(Boolean).slice(-n);

/**
 * Collect: ask every managed switch registered against this scan's rack about
 * itself.
 *
 * This is the same read the Network screen runs per switch, done for the whole
 * rack and stamped onto the scan. A scan is a moment in time, so what the
 * switches said today is evidence about today's photograph, and it belongs
 * with it rather than only on the switch record.
 *
 * A switch that fails does not fail the stage. Three switches that answered
 * are three switches' worth of evidence, and the fourth's error is reported
 * beside them rather than thrown over the top of them.
 */
router.post('/:id/collect', gates.admin, async (req, res) => {
  const scan = scanFor(req, res);
  if (!scan) return undefined;

  const targets = switches.list(scan.rackId);
  if (targets.length === 0) {
    store.recordStage(scan.id, 'collect', 'blocked', 'no switches registered for this rack');
    return res.status(428).json({
      error: 'No switches have been added for this rack.',
      hint: 'Add each managed switch on the Network screen, then read it.',
      rackId: scan.rackId,
    });
  }

  const results = [];
  for (const sw of targets) {
    const r = await reader.readSwitch(sw.id);
    results.push(r.ok
      ? {
        id: sw.id, label: sw.label, host: sw.host, ok: true,
        sysName: r.data.system.sysName, vendor: r.data.system.vendor,
        model: r.data.identity.model, serial: r.data.identity.serial,
        counts: r.data.counts, gaps: r.data.gaps,
      }
      : {
        id: sw.id, label: sw.label, host: sw.host, ok: false,
        error: r.error, hint: r.hint || '',
      });
  }

  const answered = results.filter((r) => r.ok);
  store.setPayload(scan.id, {
    ...scan.payload,
    network: {
      collectedAt: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
      switches: results,
    },
  });
  store.recordStage(scan.id, 'collect', answered.length ? 'ok' : 'failed',
    `${answered.length} of ${results.length} switches answered`);

  res.status(answered.length ? 200 : 502).json({
    collected: answered.length,
    total: results.length,
    switches: results,
  });
});

/**
 * Reconcile: join the camera scan with the switch readings.
 *
 * GET returns the current picture (camera devices, switches, and a proposed or
 * saved matching) so Review can show it. POST takes the human-confirmed
 * matches, merges, and stores the reconciled snapshot that Export then uses.
 */
/**
 * Where this scan's confirmed matches are kept.
 *
 * Keyed on the tenant and the scan's rack id, and on nothing that a lookup can
 * recompute. It used to key on the customer's rack key as well, and that key is
 * resolved fresh on every adopt through a NetBox call that fails soft to null: a
 * few seconds of an unreachable NetBox moved the whole rack to a different file
 * and every confirmation in it became invisible, with nothing said to anybody.
 *
 * Bindings written under the older spellings are carried forward the first time
 * the rack is opened, so nobody has to confirm anything twice.
 */
function scopeForScan(scan) {
  const tenantId = scan.payload?.tenantId ?? null;
  const scope = bindings.scopeOf({ tenantId, rackId: scan.rackId });
  const legacy = bindings.legacyScopesOf({
    tenantId, rackKey: scan.payload?.rackKey || null, rackId: scan.rackId,
  });
  if (legacy.length) {
    try { bindings.migrate(scope, legacy); }
    catch (err) { logger?.warn?.('netbox.bindings.migrate', { scope, error: err.message }); }
  }
  return scope;
}

/**
 * Check a posted matching before anything is stored, one row at a time.
 *
 * Four rules, each of which a real client has broken at least once:
 *   - the switch has to be one of this rack's switches;
 *   - the box has to be one of this scan's boxes;
 *   - the box cannot be a passive one. A patch panel has nothing to answer SNMP
 *     with, so no switch is ever a patch panel;
 *   - one box holds one switch;
 *   - and a box somebody confirmed at the rack is not quietly overwritten by a
 *     bulk save, which is somebody agreeing with a screen.
 *
 * Per row, not per body. One bad pick used to refuse the whole save and discard
 * every correct row with it, and both pickers could produce a bad pick, so the
 * one control that exists to correct the matcher could throw away the correction.
 *
 * Returns { matches } cleaned, plus { rejected } - a row each, in the words the
 * operator needs, for the screen to show beside the switch it belongs to.
 */
function checkMatches(posted, base, sws, held = new Map()) {
  const byId = new Map(sws.map((s) => [String(s.record.id), s]));
  const devByUid = new Map((base.devices || []).map((d) => [d.uid, d]));
  const labelOf = (id) => byId.get(String(id))?.record?.label || `switch ${id}`;
  const clean = {};
  const rejected = [];
  const seen = new Map();
  const refuse = (id, error) => rejected.push({ switchId: String(id), error });

  // Worked in a stable order so which of two rows wins a clash never depends on
  // the order a client happened to serialise its object in.
  const rows = Object.entries(posted || {}).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
  for (const [rawId, rawUid] of rows) {
    const id = String(rawId);
    if (!byId.has(id)) {
      refuse(id, 'That switch has not been added to this rack. Add it first.');
      continue;
    }
    const uid = rawUid === null || rawUid === undefined || rawUid === '' ? null : String(rawUid);
    if (uid === null) { clean[id] = null; continue; }
    const dev = devByUid.get(uid);
    if (!dev) {
      refuse(id, 'That box is not in this photo. Scan the rack again, then place the switch.');
      continue;
    }
    const cls = String(dev.provenance?.cvClass || '');
    if (reconcile.isPassive(cls)) {
      refuse(id, `${dev.name} is a ${cls.toLowerCase()}, so no switch can be it. Pick another box.`);
      continue;
    }
    if (seen.has(uid)) {
      refuse(id, `${labelOf(seen.get(uid))} and ${labelOf(id)} are both placed in ${dev.name}. `
        + 'One box holds one switch.');
      continue;
    }
    const confirmedAs = held.get(id) || null;
    if (confirmedAs && confirmedAs.deviceUid !== uid) {
      refuse(id, `${labelOf(id)} is confirmed as ${confirmedAs.name}. `
        + 'Confirm it against the new box instead.');
      continue;
    }
    seen.set(uid, id);
    clean[id] = uid;
  }
  return { matches: clean, rejected };
}

/**
 * The boxes a person has confirmed against THIS photograph, by switch id.
 *
 * Only the fresh ones: a confirmation made against an earlier photograph is a
 * recollection, and a person moving a box on the screen today outranks it.
 */
function confirmedHere(base, sws, scope, scanId) {
  const stamp = reconcile.snapshotStamp(base);
  const devName = new Map((base.devices || []).map((d) => [d.uid, d.name]));
  const out = new Map();
  for (const s of sws) {
    if (!s.reading) continue;
    const aliases = identity.aliasesOf({ ...s.reading, host: s.record.host });
    const hit = aliases.length ? bindings.find(scope, aliases, { switchId: s.record.id }) : null;
    if (!hit || hit.weak) continue;
    if (String(hit.binding.scanId ?? '') !== String(scanId) || hit.binding.snapshotStamp !== stamp) continue;
    if (!devName.has(hit.binding.deviceUid)) continue;
    out.set(String(s.record.id), {
      deviceUid: hit.binding.deviceUid,
      name: devName.get(hit.binding.deviceUid),
    });
  }
  return out;
}

// A read, and the Report screen asks for it beside the report itself, so a
// technician may have it too. Saving a matching or confirming a switch is the
// POST below, and that stays with admins.
/**
 * The rack as the customer's record holds it, for the rung that joins the two.
 *
 * Once the rack is identified, every row NetBox holds for it carries a shelf, a
 * serial and a management address - the same two identities a switch states
 * about itself. lib/netbox/switch_record.js does the join; this fetches the one
 * side of it, scoped to that rack and nothing wider.
 *
 * Never fails the caller. No NetBox, an unidentified rack or an instance that
 * will not answer all mean the same thing here: no record rows, and the
 * matching falls back to what it always did.
 */
async function recordRowsFor(req, scan) {
  try {
    /* The scan's own Site, the way scopeForScan reads it, falling back to the
       caller's. A rack is looked up inside one site and never across the
       instance (lib/netbox/find.js). */
    const tenantId = scan.payload?.tenantId ?? req.user?.tenant_id ?? null;
    if (tenantId == null) return [];
    const bound = require('../../lib/rack_identity').confirmedRack(tenantId, scan.rackId);
    const rackId = bound && bound.netboxRackId;
    if (!rackId) return [];
    const client = clientForUser(req.user);
    if (!client) return [];
    const answer = await client.get('/api/dcim/devices/', { rack_id: rackId, limit: 200 });
    return reconcile.recordsFrom(answer && answer.results);
  } catch { return []; }
}

router.get('/:id/reconcile', gates.technician, async (req, res) => {
  const scan = scanFor(req, res);
  if (!scan) return undefined;
  const base = scan.payload && scan.payload.snapshot;
  if (!base) return res.status(409).json({ error: 'this scan has no detection result yet' });
  const records = await recordRowsFor(req, scan);
  return res.json(reconcile.view(base, scan.rackId, scan.payload.matches || null,
    { scope: scopeForScan(scan), scanId: scan.id, records }));
});

/**
 * Save the matching, and confirm one switch at a time.
 *
 * A save stores the matching exactly as it always did, so the photo-to-record
 * link that works today is untouched. What it does NOT do is mint a confirmation:
 * a bulk save is somebody agreeing with a screen, and the screen's proposals are
 * inferred from port counts. Only `confirm: true` with one switch id says a
 * person stood at the rack and read the box, and only that writes a binding -
 * which outranks every score in this scan, and is remembered and shown, but not
 * restated as current, in every later photograph of the same rack (10.1).
 *
 * The switch's own facts - its model, its serial, its real ports, its cables -
 * are written onto a box only where that box is confirmed against the photograph
 * on the screen. Everything else is shown and reported, and written nowhere.
 */
/* Saying which box in the photograph a switch is, and confirming it.
 *
 * A technician's job, done at the rack with the switch in front of them - and
 * gated to admins, which answered "Insufficient permissions" to the person
 * who had just read that switch. The owner hit it on 22 Sep 2026 on the
 * Network screen ("Suggested. Choose it to keep it.") and again on Review.
 *
 * Safe to open: scanFor() above has already refused any scan this caller may
 * not touch, with a 404, so a member reaches only their own racks. */
router.post('/:id/reconcile', gates.technician, (req, res) => {
  const scan = scanFor(req, res);
  if (!scan) return undefined;
  const base = scan.payload && scan.payload.snapshot;
  if (!base) return res.status(409).json({ error: 'this scan has no detection result yet' });

  const sws = reconcile.gatherSwitches(scan.rackId);
  const scope = scopeForScan(scan);
  const isConfirm = Boolean(req.body && req.body.confirm === true);
  // A confirm is allowed to contradict an earlier confirm - that is how a person
  // corrects one. A plain save is not.
  const held = isConfirm ? new Map() : confirmedHere(base, sws, scope, scan.id);
  const posted = (req.body && req.body.matches) || {};
  const { matches, rejected } = checkMatches(posted, base, sws, held);
  // Every row refused and nothing left to store is a refusal, and it says which
  // row and why. A mixed save keeps the rows that were right.
  if (rejected.length && !Object.keys(matches).length) {
    return res.status(400).json({ error: rejected.map((r) => r.error).join(' '), rejected });
  }

  // ── the one explicit act that mints a binding ──────────────────────────────
  //
  // Stored first, confirmed second. A confirm that cannot be kept - a cheap
  // switch that answers SNMP but publishes neither a serial nor a chassis
  // address - used to refuse the whole request, so another switch's perfectly
  // valid placement was thrown away with it. Now the save stands and the
  // confirmation comes back as a note.
  let confirmed = null;
  let confirmNote = null;
  if (isConfirm) {
    const ids = Object.keys(matches);
    const swId = req.body.switchId !== undefined && req.body.switchId !== null
      ? String(req.body.switchId)
      : (ids.length === 1 ? ids[0] : null);
    if (!swId) {
      return res.status(400).json({
        error: 'Confirm one switch at a time.',
      });
    }
    if (!Object.prototype.hasOwnProperty.call(matches, swId)) {
      return res.status(400).json({ error: 'Place this switch first, then confirm it.' });
    }
    const sw = sws.find((s) => String(s.record.id) === swId);
    const aliases = sw.reading
      ? identity.aliasesOf({ ...sw.reading, host: sw.record.host })
      : [];
    const devUid = matches[swId];
    const who = req.user?.email || (req.user?.id != null ? `user ${req.user.id}` : 'a person at the rack');

    if (devUid === null) {
      // "It is not in this rack" is an answer too, and it has to be able to undo
      // a confirmation somebody made by mistake.
      const bound = aliases.length ? bindings.find(scope, aliases, { switchId: swId }) : null;
      const drop = bound && !bound.weak ? bound.binding.deviceUid : null;
      if (drop) bindings.forget(scope, drop);
      confirmed = { switchId: swId, deviceUid: null, forgot: drop };
    } else {
      const dev = (base.devices || []).find((d) => d.uid === devUid);
      const result = bindings.confirm(scope, {
        aliases,
        deviceUid: devUid,
        position: dev?.position ?? null,
        switchId: swId,
        // Placed by the app itself when its own proposal was clear - one box it
        // could be, no tie - and said so in the record rather than recorded as a
        // person standing at the rack (the owner, 25 September 2026: "it must
        // be automatic, only if it is unsure it should ask me").
        evidence: [identity.evidence('confirmed', req.body.auto === true
          ? `placed automatically for ${who}: ${sw.record.label || `switch ${swId}`} is the only box it could be (${dev?.name || devUid})`
          : `${who} confirmed ${sw.record.label || `switch ${swId}`} as ${dev?.name || devUid} at the rack`)],
        by: req.body.auto === true ? `${who} (automatic)` : who,
        why: String((req.body.why || '')).slice(0, 500),
        // Which photograph this was observed against, and what the box looked
        // like in it. Standard 10.1: a position is observed fresh in each scan,
        // so a later photograph recalls this rather than restating it.
        scanId: scan.id,
        snapshotStamp: reconcile.snapshotStamp(base),
        boxPrint: reconcile.printOf(reconcile.cameraDevices(base).find((d) => d.uid === devUid)),
        rackKey: scan.payload?.rackKey || null,
      });
      if (result.error) confirmNote = result.error;
      else confirmed = { switchId: swId, deviceUid: devUid, binding: result.binding, replaced: result.replaced };
    }
  }

  // What may be written is decided in one place, from the bindings, and never
  // from the matching alone: a match is where a switch is shown, a confirmation
  // is why its serial may be written onto that box.
  const auto = reconcile.suggest(base, sws, { scope, scanId: scan.id });
  const levels = reconcile.levelsFor(auto.reasons, matches);
  const { snapshot, summary } = reconcile.reconcile(base, sws, matches, { levels });

  store.setPayload(scan.id, { ...scan.payload, matches, reconciled: snapshot });
  store.recordStage(scan.id, 'reconcile', 'ok',
    `${summary.matched} matched, ${summary.serials} serials, ${summary.cables} cables`
    + (summary.withheld.length ? `, ${summary.withheld.length} shown only` : '')
    + (confirmed ? ', 1 confirmed' : ''));
  return res.json({ ok: true, summary, confirmed, confirmNote, rejected, levels });
});

module.exports = router;
// Which rack a scan's uids are keyed on, out on its own so a test can ask it
// directly: it is the one step where a person's answer either reaches the
// comparison or is lost, and that deserves proving without a photo and a NetBox.
module.exports.recogniseRack = recogniseRack;
