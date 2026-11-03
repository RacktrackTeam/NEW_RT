/**
 * An organization's data settings, and deleting its pilot data. Mounted at
 * /api; each route carries requireAuth. Owner and the organization's own admins only.
 *
 *   GET  /orgs/:orgId/data-settings        -> { neverTrain, retentionDays }
 *   PUT  /orgs/:orgId/data-settings        { neverTrain, retentionDays } -> the same
 *   POST /orgs/:orgId/delete-pilot-data    { confirm: "<exact organization name>" }
 *                                          -> { deletedScans, deletedFiles }
 *
 * neverTrain: corrections and crops of this organization are never written to
 * its training store. retentionDays: the daily job in app.js deletes its scans
 * older than that. Deleting pilot data removes every scan, matrix and file of
 * the organization through lib/store.js; the accounts, Sites and setup stay.
 */
'use strict';

const express = require('express');
const Database = require('better-sqlite3');
const store = require('../lib/store');
const matrix = require('../lib/matrix');
const orgSettings = require('../lib/org_settings');
const { AUTH_DB } = require('../lib/data_paths');
const { logger } = require('../lib/observability');

let _db = null;
function db() {
  if (!_db) { _db = new Database(AUTH_DB); _db.pragma('journal_mode = WAL'); }
  return _db;
}

function hasTable(name) {
  return !!db().prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(name);
}

module.exports = function orgDataRouter({ requireAuth, audit = null } = {}) {
  if (typeof requireAuth !== 'function') throw new Error('org data routes need requireAuth');
  const router = express.Router();

  // Who may: the owner, or an org admin of this organization. Somebody else
  // in the organization is told no; a stranger is told it is not there. A
  // middleware after requireAuth rather than router.param, because a param
  // runs before the route's own middleware and would see no user.
  const gate = (req, res, next) => {
    const raw = req.params.orgId;
    if (!/^\d+$/.test(String(raw))) return res.status(400).json({ error: 'That organization id is not valid.' });
    const orgId = Number(raw);
    const org = db().prepare('SELECT id, name FROM organizations WHERE id = ?').get(orgId);
    const u = req.user || {};
    const sameOrg = Number(u.organization_id ?? 0) === orgId;
    if (!org || (u.role !== 'owner' && !sameOrg)) return res.status(404).json({ error: 'Organization not found.' });
    if (u.role !== 'owner' && u.role !== 'org_owner' && u.role !== 'org_admin') {
      return res.status(403).json({ error: 'Only an organization admin can see or change the data settings.' });
    }
    req.org = org;
    return next();
  };

  router.get('/orgs/:orgId/data-settings', requireAuth, gate, (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.json(orgSettings.get(req.org.id));
  });

  router.put('/orgs/:orgId/data-settings', requireAuth, gate, (req, res) => {
    try {
      const before = orgSettings.get(req.org.id);
      const after = orgSettings.set(req.org.id, req.body || {});
      audit?.log({ req, action: 'org.data_settings', status: 'ok', targetType: 'organization',
        targetId: String(req.org.id), payload: { before, after } });
      return res.json(after);
    } catch (err) {
      if (err instanceof orgSettings.SettingsError) return res.status(err.status).json({ error: err.message });
      logger.error({ event: 'org.data_settings_failed', err: err.message }, 'data settings not saved');
      return res.status(500).json({ error: 'The data settings could not be saved.' });
    }
  });

  router.post('/orgs/:orgId/delete-pilot-data', requireAuth, gate, (req, res) => {
    const confirm = req.body?.confirm;
    if (typeof confirm !== 'string' || confirm !== req.org.name) {
      audit?.log({ req, action: 'org.delete_pilot_data', status: 'fail', targetType: 'organization',
        targetId: String(req.org.id), error: 'confirmation did not match' });
      return res.status(400).json({ error: 'Type the organization name exactly as it is written to confirm. Nothing was deleted.' });
    }
    try {
      const scanIds = store.listScans({ orgId: req.org.id }).map((s) => s.scanId);
      const out = store.deleteOrgData(req.org.id);
      matrix.forgetAll();
      // The scans are gone, so the rows that claimed them go too. Accounts,
      // Sites and the setup records stay.
      if (scanIds.length) {
        const d = db();
        const del = (table, col) => {
          if (!hasTable(table)) return;
          const st = d.prepare(`DELETE FROM ${table} WHERE ${col} = ?`);
          d.transaction(() => { for (const id of scanIds) st.run(id); })();
        };
        del('rack_owners', 'rack_id');
        del('rack_user_claims', 'rack_id');
        del('rack_identity', 'rack_id');
      }
      audit?.log({ req, action: 'org.delete_pilot_data', status: 'ok', targetType: 'organization',
        targetId: String(req.org.id), payload: out });
      logger.warn({ event: 'org.pilot_data_deleted', orgId: req.org.id, ...out }, 'pilot data deleted');
      return res.json(out);
    } catch (err) {
      logger.error({ event: 'org.delete_pilot_data_failed', err: err.message }, 'pilot data not deleted');
      return res.status(500).json({ error: 'The pilot data could not be deleted.' });
    }
  });

  return router;
};
