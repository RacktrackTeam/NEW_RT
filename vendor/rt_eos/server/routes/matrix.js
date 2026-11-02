/**
 * The patching matrix of a Site. Mounted at /api/matrix behind requireAuth.
 *
 * Only the manager's source-of-truth workbook is imported (lib/workbook.js).
 *
 *   POST   /upload          multipart "file" (.xlsx .xlsm, 10 MB): the whole workbook is read
 *                           -> { uploadId, fileName, sheets: [ { name, used, rows } ], counts, problems, revisions }
 *   POST   /commit          { uploadId } -> summary (as GET /)
 *   GET    /                -> { import | null, counts: { cables, checked, racks, devices, ports, labelRules, problems, errors, warnings },
 *                                problems: [ { rowNo, reason, level, sheet } ] }
 *   GET    /cables          ?q=&device=&limit=&offset= -> { total, cables: [...] }
 *   GET    /inventory       ?kind=racks|devices|ports|labelRules -> { total, items: [...] }
 *   GET    /template        the manager's blank workbook, to fill in
 *   POST   /drift-check     { device, port, status, connector, cableColor, cableType, label, otherEnd }
 *                           -> one port's drift check against the Excel (lib/drift.js), for testing
 *   GET    /history         -> [ import summary ], newest first
 *   DELETE /:importId       removes that import's rows and file
 *
 * The Site is the caller's own; the owner and an organisation admin may name
 * one with ?siteId= (lib/site_scope.js). Everyone at the Site reads; the site
 * manager and above change it. Every change goes to the audit log.
 */
'use strict';

const express = require('express');
const multer = require('multer');
const path = require('path');
const crypto = require('crypto');
const store = require('../lib/store');
const matrix = require('../lib/matrix');
const drift = require('../lib/drift');
const { siteOf, ScopeError } = require('../lib/site_scope');
const { logger } = require('../lib/observability');

const MAX_BYTES = 10 * 1024 * 1024;

module.exports = function matrixRouter({ audit = null } = {}) {
  const router = express.Router();

  const upload = multer({
    storage: multer.diskStorage({
      destination: (req, file, cb) => cb(null, store.cachePath('uploads')),
      filename: (req, file, cb) => cb(null, `tmp_matrix_${crypto.randomBytes(8).toString('hex')}`),
    }),
    limits: { fileSize: MAX_BYTES, files: 1 },
    fileFilter: (req, file, cb) => {
      const ext = path.extname(String(file.originalname || '')).toLowerCase();
      if (matrix.EXTENSIONS.includes(ext)) return cb(null, true);
      const err = new Error(ext === '.xls'
        ? 'Save the workbook as .xlsx first. Old .xls files cannot be read.'
        : 'Upload the manager\'s workbook (.xlsx). Download the template from the Excel page.');
      err.status = 400;
      return cb(err, false);
    },
  });

  const wrap = (fn) => async (req, res) => {
    try {
      await fn(req, res);
    } catch (err) {
      if (err instanceof matrix.MatrixError || err instanceof ScopeError) {
        return res.status(err.status).json({ error: err.message });
      }
      logger.error({ event: 'matrix.error', err: err.message, route: req.originalUrl }, 'matrix route failed');
      return res.status(500).json({ error: 'The patching matrix request failed.' });
    }
  };

  const mayChange = (scope, res) => {
    if (scope.level === 'write') return true;
    res.status(403).json({ error: 'Only the site manager or an admin can change the patching matrix.' });
    return false;
  };

  router.post('/upload', (req, res, next) => {
    upload.single('file')(req, res, (err) => {
      if (!err) return next();
      if (err.code === 'LIMIT_FILE_SIZE') return res.status(413).json({ error: 'The file is larger than 10 MB.' });
      return res.status(err.status || 400).json({ error: err.message || 'The upload failed.' });
    });
  }, wrap(async (req, res) => {
    const drop = () => { if (req.file) { try { require('fs').unlinkSync(req.file.path); } catch { /* gone */ } } };
    let scope;
    try { scope = siteOf(req); } catch (err) { drop(); throw err; }
    if (!mayChange(scope, res)) { drop(); return undefined; }
    if (!req.file) return res.status(400).json({ error: 'Choose a file to upload (field "file").' });
    const out = await matrix.createUpload({
      tmpPath: req.file.path, originalName: req.file.originalname,
      siteId: scope.siteId, orgId: scope.orgId, user: req.user,
    });
    audit?.log({ req, action: 'matrix.upload', status: 'ok', targetType: 'site', targetId: String(scope.siteId),
      payload: { fileName: req.file.originalname, sheets: out.sheets.length, cables: out.counts.cables, problems: out.counts.problems } });
    return res.json(out);
  }));

  router.post('/commit', wrap(async (req, res) => {
    const scope = siteOf(req);
    if (!mayChange(scope, res)) return undefined;
    const b = req.body || {};
    const out = await matrix.commit({
      uploadId: b.uploadId,
      siteId: scope.siteId, orgId: scope.orgId, user: req.user,
    });
    audit?.log({ req, action: 'matrix.commit', status: 'ok', targetType: 'site', targetId: String(scope.siteId),
      payload: { importId: out.importId, replaced: out.replaced, cables: out.summary.counts.cables, problems: out.summary.counts.problems } });
    return res.json(out.summary);
  }));

  router.get('/', wrap(async (req, res) => {
    const scope = siteOf(req);
    res.setHeader('Cache-Control', 'no-store');
    return res.json(matrix.summary(scope.orgId, scope.siteId));
  }));

  router.get('/cables', wrap(async (req, res) => {
    const scope = siteOf(req);
    res.setHeader('Cache-Control', 'no-store');
    return res.json(matrix.cables(scope.orgId, scope.siteId, req.query || {}));
  }));

  router.get('/inventory', wrap(async (req, res) => {
    const scope = siteOf(req);
    res.setHeader('Cache-Control', 'no-store');
    return res.json(matrix.inventory(scope.orgId, scope.siteId, String(req.query?.kind || '')));
  }));

  // The manager's template, blank but for its two sample rows, to fill in.
  router.get('/template', wrap(async (req, res) => {
    siteOf(req);
    return res.download(matrix.templatePath(), 'RackTrack_A_Z_Source_of_Truth_Upload_Matrix.xlsx');
  }));

  // The drift check of one port from structured data sent by hand, for
  // testing without a photo. In the app the check runs by itself on every
  // port of a scan (lib/scan_detail.js); this reads nothing from a scan.
  router.post('/drift-check', wrap(async (req, res) => {
    const scope = siteOf(req);
    const b = req.body || {};
    const text = (v, max = 120) => (v == null || v === '' ? null : String(v).slice(0, max));
    const other = b.otherEnd && typeof b.otherEnd === 'object' ? { device: text(b.otherEnd.device), port: text(b.otherEnd.port, 40) } : null;
    const actual = {
      device: text(b.device),
      port: text(b.port, 40),
      rack: text(b.rack),
      status: String(b.status || '').toLowerCase() === 'connected' ? 'connected' : String(b.status || '').toLowerCase() === 'empty' ? 'empty' : 'unknown',
      connector: text(b.connector, 40),
      cableColor: text(b.cableColor, 40),
      cableType: text(b.cableType, 80),
      label: text(b.label),
      otherEnd: other && other.device ? other : null,
    };
    res.setHeader('Cache-Control', 'no-store');
    return res.json(drift.checkAgainst(matrix.active(scope.orgId, scope.siteId), actual));
  }));

  // One cable read on a rack photo's tag, added to the dataset by a manager.
  router.post('/cables', wrap(async (req, res) => {
    const scope = siteOf(req);
    if (!mayChange(scope, res)) return undefined;
    const out = matrix.addCable(scope.orgId, scope.siteId, req.body?.cable || {}, req.user);
    audit?.log({ req, action: 'matrix.add_cable', status: 'ok', targetType: 'site', targetId: String(scope.siteId),
      payload: { cableLabel: out.cableLabel, rowNo: out.rowNo } });
    return res.json(out);
  }));

  router.get('/history', wrap(async (req, res) => {
    const scope = siteOf(req);
    res.setHeader('Cache-Control', 'no-store');
    return res.json(matrix.history(scope.orgId, scope.siteId));
  }));

  router.delete('/:importId', wrap(async (req, res) => {
    const scope = siteOf(req);
    if (!mayChange(scope, res)) return undefined;
    if (!/^[a-f0-9]{6,32}$/.test(String(req.params.importId))) return res.status(404).json({ error: 'That import was not found.' });
    const out = matrix.remove(scope.orgId, scope.siteId, req.params.importId);
    audit?.log({ req, action: 'matrix.delete', status: 'ok', targetType: 'site', targetId: String(scope.siteId),
      payload: { importId: req.params.importId } });
    return res.json({ ok: true, ...out });
  }));

  return router;
};
