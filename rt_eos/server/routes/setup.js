/**
 * Organisation setup: the estate tree behind the minimal setup.
 *
 * Mounted at /api/setup behind auth.requireAuth (app.js) - this file reads
 * req.user and never requires auth.js itself, so a test can seed a throwaway
 * database and mount it behind a stub.
 *
 * Who may do what is decided once, in the :tenantId param below, from
 * lib/estate.accessLevel:
 *   owner, org_admin of the Site's organisation, site_manager of the Site → write
 *   member of the Site                                                   → read
 *   anyone else                                                          → 404
 * 404 rather than 403 for the stranger, as everywhere else in this codebase: a
 * 403 would confirm the Site exists.
 *
 * /state comes first and is per-principal, not per-Site: it is what the app
 * gates on, and it never names a Site the caller cannot see.
 *
 * The organisation profile (/org/:orgId/profile) has its own gate, decided
 * in the :orgId param from lib/estate_profile.orgAccessLevel: the owner and
 * the organisation's admins read and write it; anyone else in the
 * organisation gets 403; strangers 404. The vendor catalogue is the same for
 * everyone who is signed in.
 */
const express = require('express');
const estate = require('../lib/estate');
const profile = require('../lib/estate_profile');
const { logger } = require('../lib/observability');

const router = express.Router();

/** Run a handler; turn an EstateError / ProfileError into its status, anything else into 500. */
function wrap(fn) {
  return (req, res) => {
    try {
      fn(req, res);
    } catch (err) {
      if (err instanceof estate.EstateError || err instanceof profile.ProfileError) {
        return res.status(err.status).json({ error: err.message });
      }
      logger.error({ event: 'setup.error', err: err.message, route: req.originalUrl }, 'setup route failed');
      return res.status(500).json({ error: 'Setup request failed' });
    }
  };
}

// ── Per-principal state ─────────────────────────────────────────────
router.get('/state', wrap((req, res) => {
  res.json({ ok: true, ...estate.stateFor(req.user) });
}));

// ── Catalogue ───────────────────────────────────────────────────────
router.get('/catalogue/vendors', wrap((req, res) => {
  const vendors = profile.vendorCatalogue();
  res.json({ ok: true, vendors, count: vendors.length });
}));

// ── Organisation profile ────────────────────────────────────────────
router.param('orgId', (req, res, next, raw) => {
  if (!/^\d+$/.test(String(raw))) return res.status(400).json({ error: 'Invalid organization id' });
  const orgId = Number(raw);
  const level = profile.orgAccessLevel(req.user, orgId);
  if (!level) {
    logger.warn({
      event: 'setup.org_access_denied', orgId,
      userId: req.user?.id, role: req.user?.role, route: req.path,
    }, `blocked from profile of organisation ${orgId}`);
    return res.status(404).json({ error: 'Organisation not found' });
  }
  if (level !== 'write') {
    return res.status(403).json({ error: 'Only an organization admin can see or change the organization profile' });
  }
  req.org = { orgId };
  next();
});

router.get('/org/:orgId/profile', wrap((req, res) => {
  res.json({ ok: true, profile: profile.orgProfile(req.org.orgId) });
}));

router.put('/org/:orgId/profile', wrap((req, res) => {
  const updated = profile.updateOrgProfile(req.org.orgId, req.body || {}, req.user.id);
  res.json({ ok: true, profile: updated });
}));

// ── Site resolution + access ────────────────────────────────────────
router.param('tenantId', (req, res, next, raw) => {
  if (!/^\d+$/.test(String(raw))) return res.status(400).json({ error: 'Invalid site id' });
  const tenantId = Number(raw);
  const level = estate.accessLevel(req.user, tenantId);
  if (!level) {
    logger.warn({
      event: 'setup.access_denied', tenantId,
      userId: req.user?.id, role: req.user?.role, route: req.path,
    }, `blocked from setup of site ${tenantId}`);
    return res.status(404).json({ error: 'Site not found' });
  }
  req.setup = { tenantId, level };
  next();
});

function requireWrite(req, res, next) {
  if (req.setup?.level === 'write') return next();
  return res.status(403).json({ error: 'Only a site manager or an organization admin can change setup' });
}

function spaceIdOf(req) {
  const raw = req.params.spaceId;
  if (!/^\d+$/.test(String(raw))) throw new estate.EstateError(400, 'Invalid space id');
  return Number(raw);
}

// ── Whole picture ───────────────────────────────────────────────────
router.get('/:tenantId', wrap((req, res) => {
  res.json({ ok: true, ...estate.snapshot(req.setup.tenantId) });
}));

// ── Datacentre fields ───────────────────────────────────────────────
router.put('/:tenantId/datacentre', requireWrite, wrap((req, res) => {
  const datacentre = estate.updateDatacentre(req.setup.tenantId, req.body || {}, req.user.id);
  res.json({ ok: true, datacentre, completeness: estate.completeness(req.setup.tenantId) });
}));

// ── Spaces ──────────────────────────────────────────────────────────
router.post('/:tenantId/spaces', requireWrite, wrap((req, res) => {
  const space = estate.createSpace(req.setup.tenantId, req.body || {}, req.user.id);
  res.status(201).json({ ok: true, space, completeness: estate.completeness(req.setup.tenantId) });
}));

router.put('/:tenantId/spaces/:spaceId', requireWrite, wrap((req, res) => {
  const space = estate.updateSpace(req.setup.tenantId, spaceIdOf(req), req.body || {}, req.user.id);
  res.json({ ok: true, space, completeness: estate.completeness(req.setup.tenantId) });
}));

router.delete('/:tenantId/spaces/:spaceId', requireWrite, wrap((req, res) => {
  estate.deleteSpace(req.setup.tenantId, spaceIdOf(req), req.user.id);
  res.json({ ok: true, completeness: estate.completeness(req.setup.tenantId) });
}));

// ── Racks known in a space ──────────────────────────────────────────
router.get('/:tenantId/candidates', wrap((req, res) => {
  const raw = req.query.spaceId;
  let space = null;
  if (raw !== undefined && raw !== '') {
    if (!/^\d+$/.test(String(raw))) throw new estate.EstateError(400, 'Invalid space id');
    const s = estate.getSpace(Number(raw));
    if (!s || Number(s.tenant_id) !== req.setup.tenantId) throw new estate.EstateError(404, 'Space not found');
    space = s;
  }
  const racks = estate.listRacks(req.setup.tenantId, space ? space.id : null);
  res.json({
    ok: true,
    space: space ? { id: space.id, name: space.name } : null,
    racks,
    count: racks.length,
    // What the admin said the space holds, so a client can show "3 of 12".
    expected: space ? (space.rack_count ?? null) : null,
  });
}));

router.post('/:tenantId/spaces/:spaceId/racks', requireWrite, wrap((req, res) => {
  const body = { ...(req.body || {}), space_id: spaceIdOf(req) };
  const { rack, created } = estate.upsertRack(req.setup.tenantId, body, req.user.id);
  res.status(created ? 201 : 200).json({ ok: true, rack, created, completeness: estate.completeness(req.setup.tenantId) });
}));

// ── The site contact (the SPOC) ─────────────────────────────────────
router.put('/:tenantId/approver', requireWrite, wrap((req, res) => {
  const approver = estate.setApprover(req.setup.tenantId, req.body || {}, req.user.id);
  res.json({ ok: true, approver, completeness: estate.completeness(req.setup.tenantId) });
}));

// ── Rules ───────────────────────────────────────────────────────────
router.put('/:tenantId/rules', requireWrite, wrap((req, res) => {
  const rules = estate.acceptRules(req.setup.tenantId, req.body || {}, req.user.id);
  res.json({ ok: true, rules, completeness: estate.completeness(req.setup.tenantId) });
}));

// ── Profile sections (contacts, vendors, conventions, facility) ─────
function sectionOf(req) {
  const s = String(req.params.section || '');
  if (!profile.SECTIONS.includes(s)) throw new profile.ProfileError(404, 'No such section');
  return s;
}

router.get('/:tenantId/profile', wrap((req, res) => {
  res.json({ ok: true, profile: profile.tenantProfile(req.setup.tenantId) });
}));

// The body IS the section (a list for contacts / vendors, an object for the
// rest), replaced whole. `?source=imported` says where it came from; the
// default is typed.
router.put('/:tenantId/profile/:section', requireWrite, wrap((req, res) => {
  const section = sectionOf(req);
  const { data, updated } = profile.putSection(req.setup.tenantId, section, req.body, req.user.id, req.query.source);
  res.json({ ok: true, section, data, updated, completeness: estate.completeness(req.setup.tenantId) });
}));

router.delete('/:tenantId/profile/:section', requireWrite, wrap((req, res) => {
  const section = sectionOf(req);
  const existed = profile.deleteSection(req.setup.tenantId, section, req.user.id);
  res.json({ ok: true, section, existed, completeness: estate.completeness(req.setup.tenantId) });
}));

// Live check of a naming pattern against an example. Read access is enough:
// nothing is stored, and a member filling in a form may want to try one.
router.post('/:tenantId/conventions/check', wrap((req, res) => {
  res.json(profile.checkPattern(req.body || {}));
}));

module.exports = router;
