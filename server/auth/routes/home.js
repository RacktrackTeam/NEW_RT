/**
 * Home and the Site overview. Mounted at /api; each route carries requireAuth.
 *
 *   GET /home -> {
 *     role, site: { id, name } | null,
 *     recent:   [ { scanId, rackLabel, takenAt, thumbUrl, deviceCount } ],   8 newest the caller may see
 *     worklist: { count },                                                   items waiting for this person
 *     matrix:   { cableCount, importedAt, importedBy } | null,               site managers and above
 *     checks:   { count, last } | null                                       site managers and above
 *   }
 *   GET /sites/:siteId/overview -> { site, racks: [ { rackLabel, scanId, thumbUrl, takenAt } ],
 *                                    people: [ { id, name, username, role } ] }
 *
 * "Recent" follows the same rule as scan history (lib/rack_access
 * visibleRackIds, per user): the owner sees every scan, an organisation admin
 * the organisation's, a site manager the Site's, a technician his own. The
 * overview is for anyone who may read the Site (lib/estate.accessLevel); a Site
 * the caller cannot see is not found.
 *
 * worklist.count is the number of readings waiting for this person
 * (lib/worklist.js); checks are the site's A-end / Z-end label checks
 * (lib/checks.js).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const express = require('express');
const Database = require('better-sqlite3');
const store = require('../lib/store');
const estate = require('../lib/estate');
const tenant = require('../lib/tenant');
const matrix = require('../lib/matrix');
const checks = require('../lib/checks');
const worklist = require('../lib/worklist');
const rackAccess = require('../lib/rack_access');
const { rackNameFor } = require('../lib/rack_name');
const { AUTH_DB } = require('../lib/data_paths');
const { logger } = require('../lib/observability');

const MANAGERS = new Set(['owner', 'org_owner', 'org_admin', 'site_manager']);
const RECENT = 8;

let _db = null;
function db() {
  if (!_db) { _db = new Database(AUTH_DB); _db.pragma('journal_mode = WAL'); }
  return _db;
}

const fileUrl = (scanId, name) => `/api/scans/${encodeURIComponent(scanId)}/files/${name}`;

function hasPhoto(scanId) {
  try { return fs.existsSync(store.file(scanId, 'photo')); } catch { return false; }
}

function deviceCountOf(scanId) {
  try {
    const map = store.readJson(path.join(store.file(scanId, 'engine'), 'device_unit_map.json'), null);
    return Array.isArray(map?.devices) ? map.devices.length : 0;
  } catch { return 0; }
}

function takenAtOf(scan) {
  try { return store.readMeta(scan.scanId)?.timestamp || scan.createdAt; } catch { return scan.createdAt; }
}

// The rack's label: the one confirmed for its folder, else what a person
// called the rack elsewhere (lib/rack_name), else null.
function rackLabelOf(scan) {
  try {
    const rj = store.readJson(path.join(path.dirname(path.dirname(scan.dir)), 'rack.json'), null);
    if (rj && rj.label) return String(rj.label);
  } catch { /* fall through */ }
  try { return rackNameFor(scan.scanId, { tenantId: scan.siteId ?? null }); } catch { return null; }
}

module.exports = function homeRouter({ requireAuth } = {}) {
  if (typeof requireAuth !== 'function') throw new Error('home routes need requireAuth');
  const router = express.Router();

  router.get('/home', requireAuth, (req, res) => {
    try {
      const user = req.user;
      const role = user.role;
      const own = Number(user.tenant_id ?? 0) || null;
      const t = own ? estate.getTenant(own) : null;
      const site = t ? { id: t.id, name: t.name } : null;

      const allowed = rackAccess.visibleRackIds(user, tenant, { userId: user.id, perUser: true });
      const scans = store.listScans(allowed === null ? { limit: RECENT } : { scanIds: allowed, limit: RECENT });
      const recent = scans.map((s) => ({
        scanId: s.scanId,
        rackLabel: rackLabelOf(s),
        takenAt: takenAtOf(s),
        thumbUrl: hasPhoto(s.scanId) ? fileUrl(s.scanId, 'thumb.jpg') : null,
        deviceCount: deviceCountOf(s.scanId),
      }));

      const manager = MANAGERS.has(role);
      let matrixCard = null;
      let checksCard = manager ? { count: 0, last: null } : null;
      if (manager && t) {
        try { matrixCard = matrix.homeCard(t.organization_id ?? null, t.id); } catch { matrixCard = null; }
        try { checksCard = checks.homeCard(t.organization_id ?? null, t.id); } catch { /* keep the empty card */ }
      }
      let waiting = 0;
      try { waiting = worklist.list(user).length; } catch { waiting = 0; }
      res.setHeader('Cache-Control', 'no-store');
      res.json({
        role,
        site,
        recent,
        worklist: { count: waiting },
        matrix: matrixCard,
        checks: checksCard,
      });
    } catch (err) {
      logger.error({ event: 'home.failed', err: err.message }, 'home failed');
      res.status(500).json({ error: 'Home could not be loaded.' });
    }
  });

  router.get('/sites/:siteId/overview', requireAuth, (req, res) => {
    try {
      if (!/^\d+$/.test(String(req.params.siteId))) return res.status(400).json({ error: 'That site id is not valid.' });
      const siteId = Number(req.params.siteId);
      if (!estate.accessLevel(req.user, siteId)) return res.status(404).json({ error: 'Site not found.' });
      const t = estate.getTenant(siteId);
      if (!t) return res.status(404).json({ error: 'Site not found.' });

      // One row per rack: its newest scan.
      const seen = new Set();
      const racks = [];
      for (const s of store.listScans({ siteId })) {
        if (seen.has(s.rackKey)) continue;
        seen.add(s.rackKey);
        racks.push({
          rackLabel: rackLabelOf(s),
          scanId: s.scanId,
          thumbUrl: hasPhoto(s.scanId) ? fileUrl(s.scanId, 'thumb.jpg') : null,
          takenAt: takenAtOf(s),
        });
      }

      const people = db().prepare(
        `SELECT id, username, role FROM users WHERE tenant_id = ? AND COALESCE(active, 1) = 1 ORDER BY username`)
        .all(siteId)
        .map((u) => ({ id: u.id, name: u.username, username: u.username, role: u.role }));

      res.setHeader('Cache-Control', 'no-store');
      res.json({
        site: {
          id: t.id, name: t.name,
          address: t.address ?? null,
          organizationId: t.organization_id ?? null,
        },
        racks,
        people,
      });
    } catch (err) {
      logger.error({ event: 'site_overview.failed', err: err.message }, 'site overview failed');
      res.status(500).json({ error: 'The site could not be loaded.' });
    }
  });

  return router;
};
