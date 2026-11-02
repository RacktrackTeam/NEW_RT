/**
 * The worklist: the readings the engine is least sure of, for a person to
 * confirm or correct, least sure first.
 *
 * Items come from the newest scan of every rack the caller may see (the same
 * rule as scan history, lib/rack_access.visibleRackIds per user):
 *   device  a device whose kind was read below 65 per cent
 *   port    a port on a device with ports whose state the photo could not
 *           tell (at most 8 per device)
 *   label   a label on a zone close-up read below 60 per cent, or not read
 *
 * An item's id says where it is: <scanId>.d<deviceId>, <scanId>.p<deviceId>-<portNo>
 * or <scanId>.l<zoneId>-<label index>, so answering one needs no stored list.
 *
 * Answering writes to the scan's corrections.jsonl (lib/store.js):
 *   device  the same "device" row the results screen writes (app.js
 *           saveDeviceFeedback), so the correction is also learned from
 *   port    a "port_state" row; lib/scan_view reads it as the port's state
 *   label   a "zone_label" row; the zone file keeps the confirmed text
 *   skip    a "worklist_skip" row; the item stays away for that person only
 * An item is done once its row exists, so every screen agrees.
 */
'use strict';

const path = require('path');
const store = require('./store');
const tenant = require('./tenant');
const rackAccess = require('./rack_access');
const scanView = require('./scan_view');
const { ensureCrop } = require('./crops');

const DEVICE_CLASSES = ['Switch', 'Patch Panel', 'Firewall', 'Router', 'Server', 'Load Balancer', 'Modem',
  'Controller', 'Recorder', 'Amplifier', 'Gateway', 'PDU', 'PSU', 'UPS'];
const PORT_OPTIONS = [{ value: 'connected', label: 'Cable plugged in' }, { value: 'empty', label: 'Empty' }];
const LOW_DEVICE = 0.65;
const LOW_LABEL = 0.6;
const PORTS_PER_DEVICE = 8;
const MAX_ITEMS = 300;
const ID_RE = /^(RK-[A-Za-z0-9]{4,32})\.(?:d(\d{1,3})|p(\d{1,3})-(\d{1,4})|l([a-f0-9]{8,16})-(\d{1,3}))$/;

class WorklistError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function parseId(id) {
  const m = String(id || '').match(ID_RE);
  if (!m) return null;
  if (m[2]) return { scanId: m[1], kind: 'device', deviceId: Number(m[2]) };
  if (m[3]) return { scanId: m[1], kind: 'port', deviceId: Number(m[3]), portNo: Number(m[4]) };
  return { scanId: m[1], kind: 'label', zoneId: m[5], labelIndex: Number(m[6]) };
}

/** The newest scan of each rack the user may see (optionally one scan). */
function scansFor(user, scanId = null) {
  const allowed = rackAccess.visibleRackIds(user, tenant, { userId: user.id, perUser: true });
  if (scanId) {
    if (!rackAccess.canAccessRack(user, scanId, tenant) || !store.has(scanId)) return [];
    return [scanId];
  }
  const rows = store.listScans(allowed === null ? {} : { scanIds: allowed });
  const seen = new Set();
  const out = [];
  for (const r of rows) {
    const k = `${r.siteId}:${r.rackKey}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(r.scanId);
  }
  return out;
}

function itemsOfScan(scanId, user) {
  const view = scanView.load(scanId);
  if (!view) return [];
  const corrections = store.readCorrections(scanId);
  const reviewed = new Set(corrections.filter((c) => c.kind === 'device' && c.target).map((c) => Number(c.target.device_index)));
  const skipped = new Set(corrections.filter((c) => c.kind === 'worklist_skip' && c.user === (user?.username ?? null)).map((c) => c.target?.item));
  const items = [];
  const where = (d) => [d.unitTop ? `U${d.unitTop}` : null, view.rackLabel].filter(Boolean).join(', ');
  for (const d of view.visible) {
    const name = scanView.displayName(d);
    if (typeof d.confidence === 'number' && d.confidence < LOW_DEVICE && d.source !== 'corrected' && !reviewed.has(d.deviceId)) {
      items.push({
        id: `${scanId}.d${d.deviceId}`, scanId, kind: 'device',
        question: `Is this a ${d.type.toLowerCase()}?`,
        current: d.type, options: DEVICE_CLASSES, confidence: Math.round(d.confidence * 1000) / 1000,
        where: where(d), note: 'The kind of device was hard to read.',
        _crop: { src: 'photo', box: d.box, name: `wl-d${d.deviceId}.jpg` },
      });
    }
    if (!d.hasPorts) continue;
    let n = 0;
    for (const p of d.ports) {
      if (n >= PORTS_PER_DEVICE) break;
      if (p.state !== 'unknown') continue;
      n += 1;
      items.push({
        id: `${scanId}.p${d.deviceId}-${p.portNo}`, scanId, kind: 'port',
        question: `Is port ${p.portNo} on ${name} empty?`,
        current: 'Empty', options: PORT_OPTIONS,
        confidence: Math.round(0.5 * (p.confidence ?? 0.5) * 1000) / 1000,
        where: where(d), note: 'The photo could not tell if a cable is plugged in.',
        uncapped: !!p.uncapped, deviceId: d.deviceId, portNo: p.portNo,
        _crop: { src: 'photo', box: p.box, name: `wl-p${d.deviceId}-${p.portNo}-v2.jpg`, pad: 2.5, mark: true },
      });
    }
  }
  for (const z of view.zones) {
    const d = view.devices.find((x) => x.deviceId === Number(z.deviceId));
    (z.labels || []).forEach((l, i) => {
      if (l.confirmed) return;
      const conf = typeof l.confidence === 'number' ? l.confidence : 0;
      if (l.text && conf >= LOW_LABEL) return;
      const port = l.portNo != null ? ` on port ${l.portNo}` : '';
      items.push({
        id: `${scanId}.l${z.zoneId}-${i}`, scanId, kind: 'label',
        question: l.text ? `Does this label${port} read ${l.text}?` : `What does this label${port} read?`,
        current: l.text || null, confidence: Math.round(conf * 1000) / 1000,
        where: [d ? scanView.displayName(d) : null, view.rackLabel].filter(Boolean).join(', '),
        note: 'Read on a zone close-up.',
        uncapped: !!(d && l.portNo != null && d.ports.find((p) => p.portNo === l.portNo)?.uncapped),
        deviceId: d ? d.deviceId : null, portNo: l.portNo ?? null,
        _crop: Array.isArray(l.box) ? { src: 'zone', zoneId: z.zoneId, box: [l.box[0], l.box[1], l.box[0] + l.box[2], l.box[1] + l.box[3]], name: `wl-z${z.zoneId}-${i}.jpg`, pad: 0.6, mark: true } : null,
      });
    });
  }
  return items.filter((it) => !skipped.has(it.id));
}

function sortItems(items) {
  return items.sort((a, b) => (a.confidence ?? 2) - (b.confidence ?? 2) || String(a.id).localeCompare(String(b.id)));
}

/** Items without pictures (Home's count, and the answer check). */
function list(user, { scanId = null } = {}) {
  let items = [];
  for (const id of scansFor(user, scanId)) {
    items = items.concat(itemsOfScan(id, user));
    if (items.length > MAX_ITEMS * 2) break;
  }
  return sortItems(items).slice(0, MAX_ITEMS);
}

async function withCrop(item) {
  const { _crop, ...out } = item;
  out.cropUrl = null;
  if (_crop && _crop.box) {
    try {
      const src = _crop.src === 'zone'
        ? path.join(store.file(item.scanId, 'zones'), `${_crop.zoneId}.jpg`)
        : store.file(item.scanId, 'photo');
      out.cropUrl = await ensureCrop(item.scanId, _crop.name, src, _crop.box, { pad: _crop.pad ?? 0.15, mark: !!_crop.mark });
    } catch { out.cropUrl = null; }
  }
  return out;
}

/** GET /worklist */
async function listWithCrops(user, opts) {
  const items = list(user, opts);
  const out = [];
  for (const it of items) out.push(await withCrop(it));
  return out;
}

/**
 * POST /worklist/:id. deps.saveDeviceFeedback(req, body) writes the results
 * screen's device correction. Returns { ok, next }.
 */
async function answer(req, id, body, deps) {
  const user = req.user;
  const ref = parseId(id);
  if (!ref) throw new WorklistError(404, 'That item was not found.');
  if (!rackAccess.canAccessRack(user, ref.scanId, tenant) || !store.has(ref.scanId)) throw new WorklistError(404, 'That item was not found.');
  const action = String(body?.action || '');
  if (!['confirm', 'correct', 'skip'].includes(action)) throw new WorklistError(400, 'Choose confirm, correct or skip.');
  const item = itemsOfScan(ref.scanId, user).find((x) => x.id === id);
  if (!item) throw new WorklistError(409, 'That item has already been answered.');
  const value = body?.value == null ? '' : String(body.value).trim();
  if (action === 'correct' && !value) throw new WorklistError(400, 'Enter what it really says.');

  if (action === 'skip') {
    store.appendCorrection(ref.scanId, { user: user.username, kind: 'worklist_skip', target: { item: id } });
  } else if (item.kind === 'device') {
    let actual = null;
    if (action === 'correct') {
      actual = DEVICE_CLASSES.find((c) => c.toLowerCase() === value.toLowerCase());
      if (!actual) throw new WorklistError(400, `Choose one of: ${DEVICE_CLASSES.join(', ')}.`);
    }
    const r = await deps.saveDeviceFeedback(req, {
      scanId: ref.scanId, device_index: ref.deviceId, is_correct: action === 'confirm' || actual === item.current, actual_device_class: actual,
    });
    if (r && r.status && r.status >= 400) throw new WorklistError(r.status, r.body?.error || 'The correction could not be saved.');
  } else if (item.kind === 'port') {
    let state = 'empty';
    if (action === 'correct') {
      const v = value.toLowerCase();
      state = /^(connected|cable|yes|plugged)/.test(v) ? 'connected' : /^(empty|no)/.test(v) ? 'empty' : null;
      if (!state) throw new WorklistError(400, 'Choose Cable plugged in or Empty.');
    }
    store.appendCorrection(ref.scanId, {
      user: user.username, kind: 'port_state', is_correct: action === 'confirm' || state === 'empty',
      target: { device_index: ref.deviceId, port: ref.portNo }, before: { state: 'unknown' }, after: { state },
    });
  } else if (item.kind === 'label') {
    const zfile = path.join(store.file(ref.scanId, 'zones'), `${ref.zoneId}.json`);
    const zone = store.readJson(zfile, null);
    const label = zone?.labels?.[ref.labelIndex];
    if (!label) throw new WorklistError(404, 'That item was not found.');
    const before = label.text || null;
    if (action === 'correct') { label.text = value.slice(0, 80); label.confidence = 1; }
    label.confirmed = true;
    label.confirmedBy = user.username;
    store.writeJsonAtomic(zfile, zone);
    store.appendCorrection(ref.scanId, {
      user: user.username, kind: 'zone_label', is_correct: action === 'confirm' || value === before,
      target: { zone: ref.zoneId, label: ref.labelIndex, device_index: zone.deviceId, port: label.portNo ?? null },
      before: { text: before }, after: { text: label.text },
    });
  }

  if (action !== 'skip' && (item.kind === 'port' || item.kind === 'label') && typeof body?.uncapped === 'boolean'
      && item.deviceId && item.portNo != null && body.uncapped !== !!item.uncapped) {
    scanView.setUncapped(ref.scanId, item.deviceId, item.portNo, body.uncapped, user);
  }

  const rest = list(user).filter((x) => x.id !== id);
  const next = rest.length ? await withCrop(rest[0]) : null;
  return { ok: true, next };
}

module.exports = { list, listWithCrops, answer, parseId, WorklistError, DEVICE_CLASSES };
