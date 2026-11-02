/**
 * The logical layer of a rack: the EVE-NG twins of its NetBox devices, the
 * emulated cabling against the recorded one, and start and stop on a node.
 *
 *   GET  /api/nb/logical/status                        is EVE-NG connected and answering
 *   GET  /api/nb/logical/source                        the EVE-NG address a phone should read
 *   GET  /api/nb/logical/rack/:rackId                  the Network tab's logical side, one rack
 *   POST /api/nb/logical/rack/:rackId/reading          a phone files what it read from EVE-NG
 *   POST /api/nb/logical/rack/:rackId/nodes/:id/:action   start | stop, admins only, live only
 *
 * A technician reads; an admin starts and stops. The console is a WebSocket
 * on /ws/eve-console, attached in app.js (lib/eve/console_ws.js), and gated
 * to admins the same way.
 *
 * A request is scoped to its rack by lib/eve/service.js: the rack has to be
 * confirmed and bound to a NetBox rack, and only that rack's lab group is
 * ever read or acted on.
 */
const express = require('express');
const gates = require('./gates');
const scope = require('./scope');
const service = require('../../lib/eve/service');
const { EveError } = require('../../lib/eve/client');
const { logger } = require('../../lib/observability');

const router = express.Router();

const wrap = (fn) => async (req, res) => {
  try { res.json(await fn(req, res)); } catch (e) {
    if (e instanceof service.LogicalError) return res.status(e.status).json({ error: e.message, code: e.code });
    if (e instanceof EveError) return res.status(e.code === 503 ? 503 : 502).json({ error: e.message, code: 'eve' });
    logger.error({ event: 'eve.logical.error', err: e.message, route: req.originalUrl }, 'logical layer request failed');
    return res.status(500).json({ error: 'The logical layer could not be read.' });
  }
};

const byParam = scope.requireRack((r) => r.params.rackId);

router.get('/status', gates.technician, wrap((req) => service.status(req.user)));
// Where a phone inside the lab's network should read from: address and lab, never the login.
router.get('/source', gates.technician, wrap((req) => service.eveSource(req.user)));
// A phone filed what it read from EVE-NG. Bigger than a switch reading: a lab
// with a few hundred nodes and its cabling.
router.post('/rack/:rackId/reading', gates.technician, byParam, express.json({ limit: '4mb' }), wrap((req) => service.fileReading(req.user, req.rackId, req.body)));
router.get('/rack/:rackId', gates.technician, byParam, wrap((req) => service.rackLogical(req.user, req.rackId)));
router.post('/rack/:rackId/nodes/:nodeId/:action', gates.admin, byParam, wrap((req) => {
  if (!/^\d+$/.test(req.params.nodeId)) throw new service.LogicalError(400, 'node', 'Bad node id.');
  return service.nodeAction(req.user, req.rackId, Number(req.params.nodeId), req.params.action);
}));

module.exports = router;
