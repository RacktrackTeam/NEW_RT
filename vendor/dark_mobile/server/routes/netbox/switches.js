/**
 * The switch inventory, and the two things you can do with one: prove the
 * login works, and read the switch.
 *
 * Test and read are separate on purpose. A wrong password should come back in
 * a second from a single sysDescr read, not after a full walk of a 48 port
 * switch, and someone entering credentials for four switches wants to know
 * about the typo on the second one before starting the third.
 */
const express = require('express');
const { testSwitch } = require('../../lib/netbox/collect');
const { readSwitch, NO_CREDENTIALS } = require('../../lib/netbox/reader');
const { SnmpError } = require('../../lib/netbox/snmp');
const switches = require('../../lib/netbox/switches');
// RackTrack's drift store — the table the Drift view reads.
const { feedDrift } = require('../../lib/netbox/drift_feed');
const { logger } = require('../../lib/observability');
const gates = require('./gates');
const scope = require('./scope');

const router = express.Router();

/**
 * Who may reach which switch route.
 *
 * An admin reaches all of them. A technician is the person at the rack with
 * the phone: the phone reads the switch itself, and files that reading so
 * Drift can use it. That takes three routes - list this rack's switches, add
 * one to this rack, file a reading against one - and only for a rack their
 * Site can see. Editing, removing, server-side test and read, and the stored
 * credentials stay with the admin.
 */
const TECHNICIAN_ROUTES = [
  ['GET', /^\/$/],
  ['POST', /^\/$/],
  ['POST', /^\/\d+\/reading$/],
];
const NOT_FOR_TECHNICIANS = 'Setting up switches is for an admin. From the phone you can read a switch '
  + 'at a rack you are scanning, and the reading is saved for it.';

router.use((req, res, next) => {
  if (gates.isAdmin(req)) return next();
  // The whole inventory is not theirs: a list names its rack or is refused.
  const open = req.user?.role === 'member'
    && TECHNICIAN_ROUTES.some(([method, path]) => method === req.method && path.test(req.path))
    && !(req.method === 'GET' && !req.query.rackId);
  if (!open) return res.status(403).json({ error: NOT_FOR_TECHNICIANS });
  // The rack the request is about: named in the query or body, or the one
  // the switch was filed under. A switch that does not exist is a 404.
  return scope.requireRack((r) => {
    if (r.method === 'GET') return r.query.rackId;
    if (r.path === '/') return r.body?.rackId;
    const sw = switches.find(r.path.split('/')[1]);
    return sw ? sw.rackId : null;
  })(req, res, next);
});

/**
 * Every SNMP failure is somebody's job, and which one it is decides who gets
 * called. The status codes are chosen so the client can tell "the password is
 * wrong" from "that address is not answering" without reading prose.
 */
function fail(res, err) {
  if (!(err instanceof SnmpError)) {
    return res.status(500).json({ error: String(err.message || err) });
  }
  const status = { auth: 401, config: 400, timeout: 504, network: 504, protocol: 502 }[err.kind] || 500;
  return res.status(status).json({ error: err.message, hint: err.hint || '', kind: err.kind });
}

// Literal paths first, so neither is ever read as an :id.
router.get('/options', (req, res) => res.json(switches.options()));

router.post('/collect-all', async (req, res) => {
  const macTable = Boolean(req.body?.macTable);
  const all = switches.list(req.body?.rackId);
  if (all.length === 0) {
    return res.status(428).json({ error: 'There are no switches to read yet. Add one first.' });
  }

  // One at a time. Fanning out across four switches saves a couple of seconds
  // and costs a burst of simultaneous MIB walks on a customer's network, which
  // is not the first impression this tool should make.
  const results = [];
  for (const sw of all) {
    const r = await readSwitch(sw.id, { macTable });
    results.push({ id: sw.id, label: sw.label, host: sw.host, ok: r.ok, error: r.error || null });
  }
  res.json({ ok: results.filter((r) => r.ok).length, total: results.length, results });
});

router.get('/', (req, res) => res.json(switches.list(req.query.rackId)));

router.post('/', (req, res) => {
  const r = switches.add(req.body || {});
  if (r.error) return res.status(400).json({ error: r.error });
  res.status(201).json(r.record);
});

router.patch('/:id', (req, res) => {
  const r = switches.update(req.params.id, req.body || {});
  if (r.error) return res.status(r.error.startsWith('No switch') ? 404 : 400).json({ error: r.error });
  res.json(r.record);
});

router.delete('/:id', (req, res) => {
  const r = switches.remove(req.params.id);
  if (r.error) return res.status(404).json({ error: r.error });
  res.json({ ok: true });
});

router.post('/:id/test', async (req, res) => {
  const credentials = switches.credentials(req.params.id);
  if (!credentials) return res.status(428).json(NO_CREDENTIALS);
  try {
    const result = await testSwitch(credentials);
    switches.recordTest(req.params.id, { ok: true, sysName: result.sysName });
    res.json(result);
  } catch (err) {
    switches.recordTest(req.params.id, { ok: false, reason: String(err.message) });
    fail(res, err);
  }
});

router.post('/:id/collect', async (req, res) => {
  if (!switches.find(req.params.id)) return res.status(404).json({ error: 'No switch with that id.' });
  const r = await readSwitch(req.params.id, { macTable: Boolean(req.body?.macTable) });
  if (!r.ok) {
    const status = { credentials: 428, auth: 401, timeout: 504, network: 504, protocol: 502 }[r.kind] || 502;
    return res.status(status).json({ error: r.error, hint: r.hint || '' });
  }
  // A reading is a reading whichever machine took it: the server's goes into
  // Drift exactly as the phone's does, or the Drift view would only ever
  // move when a phone was in the room.
  try { feedDrift(switches.find(req.params.id), r.data, { tenantId: req.user?.tenant_id ?? null }); }
  catch (err) { logger.warn({ event: 'nb.drift_feed_failed', err: err.message }, 'read stored, drift not fed'); }
  res.json({ ok: true, data: r.data });
});

/** What the last read returned, without going back to the switch. */
router.get('/:id/data', (req, res) => {
  const data = switches.loadData(req.params.id);
  if (!data) return res.status(404).json({ error: 'This switch has not been read yet.' });
  res.json(data);
});

// ── The phone does the reading ──────────────────────────────────────────
//
// /test and /collect above have the SERVER talk to the switch, which works
// when the server sits on the customer's network and never otherwise: from
// demo.racktrack.ai a 10.x address is unreachable, full stop. The phone
// standing next to the rack is on the right network, so it takes the reading
// (client/src/utils/snmpClient.js) and posts it up. These two routes are that
// round trip. Credentials stay encrypted at rest here and are handed only to
// the signed-in caller, only when asked, and never cached.

/** The login for this switch, in the form the phone's SNMP client takes. */
router.get('/:id/credentials', (req, res) => {
  if (!switches.find(req.params.id)) return res.status(404).json({ error: 'No switch with that id.' });
  const c = switches.credentials(req.params.id);
  if (!c) return res.status(428).json(NO_CREDENTIALS);
  const out = { host: c.host, port: c.port, version: c.version };
  if (c.version === 'v3') {
    out.username = c.username;
    out.securityLevel = c.securityLevel;
    // authKey / privKey are deliberately NOT sent. Nothing on the phone speaks
    // authNoPriv or authPriv yet, and a secret that is not needed should not
    // travel. When the phone learns those levels, send them here — and only then.
  } else {
    out.community = c.community;
  }
  res.set('Cache-Control', 'no-store');
  res.json(out);
});

/** A reading the phone took, stored exactly as if the server had taken it. */
router.post('/:id/reading', (req, res) => {
  if (!switches.find(req.params.id)) return res.status(404).json({ error: 'No switch with that id.' });
  const data = req.body;
  if (!data || typeof data !== 'object' || !Array.isArray(data.interfaces)
      || !data.system || typeof data.system !== 'object') {
    return res.status(400).json({ error: 'That is not a switch reading.' });
  }
  const stored = {
    ...data,
    collectedAt: data.collectedAt || new Date().toISOString(),
    source: data.source || 'phone',
  };
  // Same three writes the server-side reader makes, so the inventory list and
  // the "last test" indicator cannot tell who took the reading.
  switches.saveData(req.params.id, stored);
  switches.recordCollected(req.params.id, stored);
  switches.recordTest(req.params.id, { ok: true, sysName: stored.system.sysName || null });
  // The reading is stored regardless; Drift is a second consumer, and a
  // failure there must not be reported as a failure to file the reading.
  let drift = false;
  try { feedDrift(switches.find(req.params.id), stored, { tenantId: req.user?.tenant_id ?? null }); drift = true; }
  catch (err) { logger.warn({ event: 'nb.drift_feed_failed', err: err.message }, 'reading stored, drift not fed'); }
  res.json({ ok: true, counts: stored.counts || null, collectedAt: stored.collectedAt, drift });
});

module.exports = router;
