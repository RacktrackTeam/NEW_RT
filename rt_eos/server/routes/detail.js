/**
 * Read-only views for the result tabs, the two-rack map and the Switches
 * screen. Mounted at /api; every route carries requireAuth.
 *
 *   GET /scans/:scanId/detail   -> lib/scan_detail.build: units top to bottom,
 *                                  devices, ports (label read, sheet cable,
 *                                  status), cables seen, counts and flags
 *   GET /switches[?siteId=]     -> every switch on the newest scan of each
 *                                  rack of the site:
 *                                  { site, matrix, racks, switches: [ ... ] }
 *   GET /scans/:scanId/switches -> the same for one scan
 *   GET /scans/:scanId/flags    -> the sheet check's flags, each with a crop
 *
 * Who: a scan is opened by whoever may open its photo (lib/rack_access);
 * anyone else is told it is not there. The switch list is for the caller's
 * own site; the owner and an organisation admin may name another
 * (lib/site_scope). Nothing here writes.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const express = require('express');
const store = require('../lib/store');
const busy = require('../lib/busy');
const tenant = require('../lib/tenant');
const matrix = require('../lib/matrix');
const rackAccess = require('../lib/rack_access');
const scanView = require('../lib/scan_view');
const sheetCheck = require('../lib/sheet_check');
const scanDetail = require('../lib/scan_detail');
const deviceLookup = require('../lib/device_lookup');
const orgSettings = require('../lib/org_settings');
const sfpAdvice = require('../lib/sfp_advice');
const { ensureCrop } = require('../lib/crops');
const { siteOf, ScopeError } = require('../lib/site_scope');
const { logger } = require('../lib/observability');

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// The SFP slot of a switch, for RackTrack's SFP Procurement Advisor: the
// datasheet's port list and the ports the photo read say whether it has SFP
// or QSFP cages, and the model's own speed hints say which generation
// (lib/sfp_advice.js slotFromModel, from RackTrack's sfp_recommend.py). No
// cage from any of them: null, and the advisor says no SFP is needed.
function sfpOf(d, specs) {
  const cages = d.ports.filter((p) => p.kind === 'sfp' || /SFP|QSFP/i.test(String(p.type || '')));
  const fromSheet = (specs?.portTypes || []).map((t) => String(t.type || '').toUpperCase());
  const hint = sfpAdvice.slotFromModel(d.make, d.model);
  // The cage type read by the SFP type model (Models/sfp_cage_finder_*.pt: SFP, QSFP, OSFP)
  // comes first; the datasheet's port list and the model name refine the speed.
  const typeOf = (p) => String(p.sfpType || p.type || '').toUpperCase();
  const osfp = cages.some((p) => typeOf(p) === 'OSFP');
  const qsfp = cages.some((p) => /QSFP/.test(typeOf(p))) || fromSheet.includes('QSFP');
  const plain = cages.some((p) => typeOf(p) === 'SFP') || fromSheet.includes('SFP');
  const sfp = cages.length > 0 || fromSheet.includes('SFP');
  let slotType = null;
  if (osfp && !plain && !qsfp) slotType = 'OSFP';
  else if (qsfp && !plain) slotType = hint && /^QSFP/.test(hint) ? hint : 'QSFP+';
  else if (sfp) slotType = hint && !/^QSFP/.test(hint) ? hint : 'SFP';
  else if (hint) slotType = hint;
  return {
    slotType,
    total: cages.length,
    free: cages.filter((p) => p.state !== 'connected').length,
  };
}

// Whether the labels of this scan are still being read: the reading runs
// after the scan and writes engine/ocr_devices.json when it is done.
function labelsPending(scanId) {
  try { return !fs.existsSync(path.join(store.file(scanId, 'engine'), 'ocr_devices.json')); } catch { return false; }
}

module.exports = function detailRouter({ requireAuth } = {}) {
  if (typeof requireAuth !== 'function') throw new Error('detail routes need requireAuth');
  const router = express.Router();

  const wrap = (fn) => async (req, res) => {
    try {
      await fn(req, res);
    } catch (err) {
      if (err instanceof HttpError || err instanceof ScopeError) return res.status(err.status).json({ error: err.message });
      logger.error({ event: 'detail.error', err: err.message, route: req.originalUrl }, 'detail route failed');
      return res.status(500).json({ error: 'That could not be read. Try again.' });
    }
    return undefined;
  };

  router.get('/scans/:scanId/detail', requireAuth, wrap(async (req, res) => {
    const scanId = String(req.params.scanId || '').toUpperCase();
    if (!rackAccess.isValidRackId(scanId) || !rackAccess.canAccessRack(req.user, scanId, tenant) || !store.has(scanId)) {
      throw new HttpError(404, 'Scan not found.');
    }
    const view = scanView.load(scanId);
    if (!view) throw new HttpError(404, 'Scan not found.');
    const doc = view.siteId != null ? matrix.active(view.orgId, view.siteId) : null;
    res.setHeader('Cache-Control', 'no-store');
    // Still reading the text and the cable tags on the photo: a screen then
    // says so and looks again, rather than saying nothing was read.
    let settling = false;
    try { settling = (store.readMeta(scanId) || {}).settle?.state === 'running'; } catch { settling = false; }
    const labels = busy.is(busy.labelsKey(scanId)) || (settling && !fs.existsSync(path.join(store.file(scanId, 'engine'), 'labels-front.json')));
    res.json({ ...scanDetail.build(view, doc), reading: { labels } });
  }));

  // Small pictures of every device, cut from the rack photo, for the Devices
  // tab's rows: { crops: { "<deviceId>": url | null } }. Made once and kept in
  // the scan's crops folder like the device card's picture.
  router.get('/scans/:scanId/device-thumbs', requireAuth, wrap(async (req, res) => {
    const { scanId, view } = openScan(req);
    const photo = store.file(scanId, 'photo');
    const crops = {};
    for (const d of view.visible) {
      try { crops[d.deviceId] = await ensureCrop(scanId, `thumb-${d.deviceId}.jpg`, photo, d.box, { pad: 0.02, width: 360 }); } catch { crops[d.deviceId] = null; }
    }
    res.setHeader('Cache-Control', 'no-store');
    res.json({ scanId, crops });
  }));

  // The switches of one scan, in the shape the Switches screen draws. Switches
  // only: a router, firewall, gateway or patch panel is not listed here.
  async function switchesOf(scanId, view, doc) {
    if (doc) sheetCheck.resolve(view, doc);
    const network = view.visible.filter((d) => /switch/i.test(String(d.type || '')));
    if (!network.length) return [];
    const optics = sfpAdvice.advise(view, doc, (d) => d.sheetName || scanView.displayName(d)).items;
    const out = [];
    const pending = labelsPending(scanId);
    for (const d of network) {
      let cropUrl = null;
      try { cropUrl = await ensureCrop(scanId, `device-${d.deviceId}.jpg`, store.file(scanId, 'photo'), d.box, { pad: 0.04, width: 900 }); } catch { cropUrl = null; }
      const online = orgSettings.onlineLookups(view.orgId);
      const sp = deviceLookup.specs(d.make, d.model, { online });
      const byType = new Map();
      for (const p of d.ports) {
        const t = p.type || (p.kind === 'sfp' ? 'SFP' : 'Not read');
        byType.set(t, (byType.get(t) || 0) + 1);
      }
      out.push({
        scanId,
        rackLabel: view.rackLabel || null,
        takenAt: view.takenAt || null,
        deviceId: d.deviceId,
        type: d.type,
        name: d.sheetName || scanView.shortName(d),
        sheetName: d.sheetName || null,
        unitTop: d.unitTop,
        unitBottom: d.unitBottom,
        heightU: d.heightU,
        make: d.make,
        model: d.model,
        confidence: d.confidence,
        source: d.source,
        labelText: d.labelText,
        cropUrl,
        // Where the switch sits on the rack photo, so a tap on its picture
        // opens the whole photo zoomed on it (a sharp closer look).
        photoUrl: `/api/scans/${encodeURIComponent(scanId)}/files/photo.jpg`,
        box: Array.isArray(d.box) ? d.box : null,
        specs: sp.specs,
        specsStatus: sp.specsStatus,
        specsNote: sp.note || null,
        positionKey: d.positionKey || null,
        awaitingLabel: pending && !d.make && !d.model,
        lookupsOff: !online,
        fields: d.fields || null,
        ports: {
          total: d.ports.length,
          connected: d.ports.filter((p) => p.state === 'connected').length,
          empty: d.ports.filter((p) => p.state === 'empty').length,
          unknown: d.ports.filter((p) => p.state !== 'connected' && p.state !== 'empty').length,
          byType: [...byType.entries()].map(([type, count]) => ({ type, count })),
        },
        sfp: sfpOf(d, sp.specs),
        optics: optics.filter((o) => o.deviceId === d.deviceId).map(({ portType, count, suggestion }) => ({ portType, count, suggestion })),
      });
    }
    return out;
  }

  // One scan the caller may open, with its view and the site's active sheet.
  function openScan(req) {
    const scanId = String(req.params.scanId || '').toUpperCase();
    if (!rackAccess.isValidRackId(scanId) || !rackAccess.canAccessRack(req.user, scanId, tenant) || !store.has(scanId)) {
      throw new HttpError(404, 'Scan not found.');
    }
    const view = scanView.load(scanId);
    if (!view) throw new HttpError(404, 'Scan not found.');
    const doc = view.siteId != null ? matrix.active(view.orgId, view.siteId) : null;
    return { scanId, view, doc };
  }

  router.get('/switches', requireAuth, wrap(async (req, res) => {
    const scope = siteOf(req);
    const doc = matrix.active(scope.orgId, scope.siteId);
    const seen = new Set();
    const racks = [];
    for (const s of store.listScans({ siteId: scope.siteId })) {
      if (seen.has(s.rackKey)) continue;
      seen.add(s.rackKey);
      if (!rackAccess.canAccessRack(req.user, s.scanId, tenant)) continue;
      racks.push(s);
    }
    const switches = [];
    for (const s of racks) {
      const view = scanView.load(s.scanId);
      if (!view) continue;
      switches.push(...await switchesOf(s.scanId, view, doc));
    }
    switches.sort((a, b) => String(a.rackLabel || '~').localeCompare(String(b.rackLabel || '~'), undefined, { numeric: true })
      || (b.unitTop ?? 0) - (a.unitTop ?? 0));
    res.setHeader('Cache-Control', 'no-store');
    res.json({
      site: { id: scope.siteId, name: scope.site?.name || null },
      matrix: !!doc,
      racks: racks.length,
      switches,
    });
  }));

  // The switches of one scan, top unit first.
  router.get('/scans/:scanId/switches', requireAuth, wrap(async (req, res) => {
    const { scanId, view, doc } = openScan(req);
    const switches = (await switchesOf(scanId, view, doc)).sort((a, b) => (b.unitTop ?? 0) - (a.unitTop ?? 0));
    res.setHeader('Cache-Control', 'no-store');
    res.json({ scanId, rackLabel: view.rackLabel || null, matrix: !!doc, switches });
  }));

  // What the sheet check flags on one scan, each with a picture: the port
  // ringed on the rack photo where its place is known, else the device. Also
  // a picture of every device with ports, for flags found elsewhere (the
  // A-end and Z-end label check) that name a device of this rack.
  router.get('/scans/:scanId/flags', requireAuth, wrap(async (req, res) => {
    const { scanId, view, doc } = openScan(req);
    const sheet = sheetCheck.check(view, doc);
    const photo = store.file(scanId, 'photo');
    const byId = new Map(view.visible.map((d) => [d.deviceId, d]));
    const deviceCrops = {};
    for (const d of view.visible) {
      if (!d.ports.length && !sheet.units.some((u) => u.deviceId === d.deviceId && u.status === 'wrong_unit')) continue;
      try { deviceCrops[d.deviceId] = await ensureCrop(scanId, `device-${d.deviceId}.jpg`, photo, d.box, { pad: 0.04, width: 900 }); } catch { deviceCrops[d.deviceId] = null; }
    }
    const portCrop = async (deviceId, portNo) => {
      const p = byId.get(deviceId)?.ports.find((x) => x.portNo === portNo);
      if (!p?.box) return deviceCrops[deviceId] || null;
      try { return (await ensureCrop(scanId, `port-${deviceId}-${portNo}.jpg`, photo, p.box, { pad: 1.2, mark: true })) || deviceCrops[deviceId] || null; } catch { return deviceCrops[deviceId] || null; }
    };
    const unitOf = (deviceId) => byId.get(deviceId)?.unitTop ?? null;
    const flags = [];
    for (const u of sheet.units.filter((x) => x.status === 'wrong_unit')) {
      flags.push({ kind: 'wrong_unit', deviceId: u.deviceId, device: u.name, unit: u.unitFound ?? null, portNo: null, unitExpected: u.unitExpected ?? null, cropUrl: deviceCrops[u.deviceId] || null });
    }
    for (const m of sheet.missing) {
      flags.push({ kind: 'missing_cable', deviceId: m.deviceId, device: m.deviceName, unit: unitOf(m.deviceId), portNo: m.portNo, cableLabel: m.cableLabel || null, otherEnd: m.otherEnd || null, cropUrl: await portCrop(m.deviceId, m.portNo) });
    }
    for (const m of sheet.unexpected) {
      flags.push({ kind: 'not_in_sheet', deviceId: m.deviceId, device: m.deviceName, unit: unitOf(m.deviceId), portNo: m.portNo, expectedBy: m.expectedBy || null, cropUrl: await portCrop(m.deviceId, m.portNo) });
    }
    for (const m of sheet.wrongType || []) {
      flags.push({ kind: 'wrong_type', deviceId: m.deviceId, device: m.deviceName, unit: unitOf(m.deviceId), portNo: m.portNo, expected: m.expected, found: m.found, cropUrl: await portCrop(m.deviceId, m.portNo) });
    }
    for (const m of sheet.uncapped) {
      flags.push({ kind: 'uncapped', deviceId: m.deviceId, device: m.deviceName, unit: unitOf(m.deviceId), portNo: m.portNo, markedBy: m.markedBy ?? null, cropUrl: await portCrop(m.deviceId, m.portNo) });
    }
    for (const m of sheet.opticNoCap || []) {
      flags.push({ kind: 'optic_no_cap', deviceId: m.deviceId, device: m.deviceName, unit: unitOf(m.deviceId), portNo: m.portNo, markedBy: m.markedBy ?? null, cropUrl: await portCrop(m.deviceId, m.portNo) });
    }
    res.setHeader('Cache-Control', 'no-store');
    res.json({ scanId, rackLabel: view.rackLabel || null, matrix: !!doc, flags, deviceCrops });
  }));

  return router;
};
