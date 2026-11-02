/**
 * Rack identity routes: which of the customer's racks a scan is.
 *
 *   GET  /api/scan/:rackId/identity           the answer, with the evidence
 *   POST /api/scan/:rackId/identity/confirm   a person says which rack it is
 *
 * A factory, not a bare router, because three things it needs live in app.js
 * and must not be loaded twice: auth.requireAuth, the audit log (which opens
 * the real auth.db at a fixed path, so a test hands in a stand-in), and the one
 * function that builds the physical layer report, shared with
 * GET /api/scan/:rackId/physical-layer so python is never started twice for
 * one rack.
 *
 * Scoped the way the other /api/scan routes are: lib/rack_access decides who
 * may touch the rack (the owner, an admin of the rack's organisation, a member
 * of the Site that holds it) and everybody else is told 404, never 403. The
 * guard runs after requireAuth on each route, not as router.param, because a
 * param runs before the route's own middleware and would see no user; and
 * requireAuth is not mounted on the /api/scan prefix, because that prefix also
 * serves soft-auth routes for scans made with no session.
 *
 * GET writes nothing. It reads the cached physical layer report, building it
 * only when there is none. It states a rack ('matched') only from the record
 * or from a label that equals one rack; every inference comes back as
 * 'suggested' with no rack. POST is the one write, and what turns a
 * suggestion into a rack. Only these may make it: an admin of the Site (owner,
 * its organisation's admin, its site manager) or the technician who scanned
 * this rack. It answers with the rack the scan is now bound to.
 */
const express = require('express');
const estate = require('../lib/estate');
const tenantLib = require('../lib/tenant');
const rackIdentity = require('../lib/rack_identity');
const { rackOwnershipParam } = require('../lib/rack_access');
const { logger } = require('../lib/observability');
const store = require('../lib/store');
const rackNaming = require('../lib/rack_naming');

function readMetaOf(rackId) {
  try { return store.readMeta(String(rackId)) || {}; } catch { return {}; }
}

module.exports = function rackIdentityRouter({ requireAuth, audit = null, physicalLayer = null } = {}) {
  if (typeof requireAuth !== 'function') throw new Error('rack_identity routes need requireAuth');
  const router = express.Router();
  const guard = rackOwnershipParam({ tenant: tenantLib, logger });
  const guardRack = (req, res, next) => guard(req, res, next, req.params.rackId);

  /** Run a handler; an IdentityError or EstateError becomes its status, anything else 500. */
  const wrap = (fn) => async (req, res) => {
    try {
      await fn(req, res);
    } catch (err) {
      if (err instanceof rackIdentity.IdentityError || err instanceof estate.EstateError) {
        return res.status(err.status).json({ error: err.message, ...(err.extra || {}) });
      }
      logger.error({ event: 'rack_identity.error', err: err.message, route: req.originalUrl }, 'rack identity route failed');
      return res.status(500).json({ error: 'Rack identity request failed' });
    }
  };

  /** The Site this caller sees the scan under; 404 when there is none. */
  function tenantOf(req) {
    const asked = req.query.tenantId ?? req.body?.tenantId ?? null;
    if (asked != null && asked !== '' && !/^\d+$/.test(String(asked))) {
      throw new rackIdentity.IdentityError(400, 'Invalid site id');
    }
    const tenantId = rackIdentity.tenantForRack(req.user, req.params.rackId, asked);
    if (!tenantId) throw new rackIdentity.IdentityError(404, 'Rack not found');
    return tenantId;
  }

  /** The report, from the cache or built through app.js's one builder. Null when it cannot be had. */
  const reportFor = async (rackId) => {
    const cached = rackIdentity.io.readPhysicalLayer(rackId);
    if (cached || !physicalLayer) return cached;
    const built = await physicalLayer(rackId);
    return built && built.status === 200 ? built.body : null;
  };

  router.get('/api/scan/:rackId/identity', requireAuth, guardRack, wrap(async (req, res) => {
    const { rackId } = req.params;
    const tenantId = tenantOf(req);
    let spaceId;
    if (req.query.spaceId !== undefined && req.query.spaceId !== '') {
      if (!/^\d+$/.test(String(req.query.spaceId))) throw new rackIdentity.IdentityError(400, 'Invalid space id');
      spaceId = Number(req.query.spaceId);
    }
    let result = await rackIdentity.identify(rackId, { tenantId, spaceId, physicalLayer: reportFor });
    // A rack whose own label was read is assigned, not asked (the owner,
    // 25 Sep 2026): the match is written down as source 'label' so every
    // screen sees the same rack, and the Overview offers to change it. This is
    // the one write on the GET, and only for that case.
    if (result.decision === 'matched' && result.rule === 'label' && result.rack && result.rack.id != null && !rackIdentity.confirmedRack(tenantId, rackId)) {
      try {
        await rackIdentity.confirm(rackId, { tenantId, userId: req.user.id, knownRackId: result.rack.id, source: 'label' });
        audit?.log({ req, action: 'rack.identity.label', status: 'ok', targetType: 'rack', targetId: rackId, payload: { tenantId, knownRackId: result.rack.id } });
        // The answer keeps saying how the rack was found (its label); what
        // changed is that it is now on record, so it is confirmed.
        result = { ...result, confidence: 'confirmed', recorded: 'label' };
      } catch (err) {
        logger.warn({ event: 'rack_identity.label_bind_failed', rackId, err: err.message }, 'label match not written');
      }
    }
    res.setHeader('Cache-Control', 'no-store');
    // Where the rack is, for the header of the results screen: the Site the
    // scan belongs to, and the room by the name the customer gave it. Added
    // here rather than in identify(): that answer is compared whole in tests
    // and by callers, and these are only labels. The owner asked on 22 Sep
    // 2026 for the header to say the Site - the room is chosen nowhere any
    // more, since the Space picker came off the scan screen - so the site name
    // is what the screen reads and the room stays for callers that want it.
    let spaceName = null;
    try {
      const space = result.spaceId != null ? estate.getSpace(result.spaceId) : null;
      spaceName = space && space.name ? String(space.name) : null;
    } catch { spaceName = null; }
    let siteName = null;
    try {
      const site = tenantId != null ? estate.getTenant(tenantId) : null;
      siteName = site && site.name ? String(site.name) : null;
    } catch { siteName = null; }
    // How the rack was named after the scan (lib/rack_naming.js), and how
    // tall it was decided to be (lib/rack_height.js): the results header says
    // the first quietly, and asks "Which rack is this?" only when naming.ask.
    const meta = readMetaOf(rackId);
    let naming = meta.rackNaming || null;
    if (result.rack && (!naming || !naming.name || rackIdentity.keyOf(naming.name) !== rackIdentity.keyOf(result.rack.name || result.rack.facilityId || ''))) {
      const bound = rackIdentity.confirmedRack(tenantId, rackId);
      naming = { name: result.rack.name || result.rack.facilityId, method: rackNaming.METHOD_OF[bound?.source] || 'person', confidence: null, ask: false };
    }
    if (naming && naming.method) naming = { ...naming, words: rackNaming.METHOD_WORD[naming.method] || null };
    res.json({ ok: true, ...result, spaceName, siteName, naming, height: meta.rackHeight || null });
  }));

  router.post('/api/scan/:rackId/identity/confirm', requireAuth, guardRack, wrap(async (req, res) => {
    const { rackId } = req.params;
    const tenantId = tenantOf(req);
    const level = estate.accessLevel(req.user, tenantId);
    const scannedIt = !!req.user?.id && tenantLib.tenantUserRackIds(tenantId, req.user.id).has(rackId);
    if (level !== 'write' && !(level === 'read' && scannedIt)) {
      return res.status(403).json({ error: 'Only an admin of this Site or the technician who scanned this rack can confirm it' });
    }
    const body = req.body || {};
    let bound;
    try {
      bound = await rackIdentity.confirm(rackId, {
        tenantId,
        userId: req.user.id,
        knownRackId: body.knownRackId,
        name: body.name,
      });
    } catch (err) {
      audit?.log({ req, action: 'rack.identity.confirm', status: 'fail', targetType: 'rack', targetId: rackId,
        error: err.message, payload: { tenantId } });
      throw err;
    }
    audit?.log({ req, action: 'rack.identity.confirm', status: 'ok', targetType: 'rack', targetId: rackId,
      payload: {
        tenantId, knownRackId: bound.knownRackId,
        name: bound.name, created: bound.created, source: bound.source,
        by: level === 'write' ? 'admin' : 'technician',
      } });
    // A person named it: that is how it was found from now on.
    try {
      const m = store.readMeta(rackId) || {};
      m.rackNaming = { name: bound.name || bound.facilityId, method: 'person', confidence: 1, reasons: [], tried: m.rackNaming?.tried || [], ask: false, why: null, at: new Date().toISOString() };
      store.writeMeta(rackId, m);
    } catch { /* bookkeeping: the rack_identity row is the answer */ }
    // What the ladder says now: rung 1, the record.
    const identity = await rackIdentity.identify(rackId, { tenantId });
    const naming = { ...(readMetaOf(rackId).rackNaming || { name: bound.name, method: 'person', ask: false }), words: rackNaming.METHOD_WORD.person };
    res.json({ ok: true, bound, identity: { ...identity, naming } });
  }));

  return router;
};
