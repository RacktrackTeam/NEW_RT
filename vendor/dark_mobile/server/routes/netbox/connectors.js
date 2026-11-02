/**
 * Connectors: configure targets, test them, and export a scan to any of them.
 *
 * The Export screen's NetBox path still exists; this is the general road that
 * NetBox, ServiceNow, and a generic REST endpoint all travel. A connector is
 * configured once, then a scan is planned (dry run) or pushed to it.
 */
const express = require('express');
const store = require('../../lib/netbox/store');
const unmanaged = require('../../lib/netbox/unmanaged');
const entered = require('../../lib/netbox/entered');
const connectorStore = require('../../lib/netbox/connectorStore');
const registry = require('../../lib/netbox/connectors');

const router = express.Router();

/** The snapshot a scan exports, with hand-declared unmanaged switches folded in. */
function snapshotOf(scanId) {
  const scan = store.getScan(scanId);
  if (!scan) return { error: 'No scan with that id.', status: 404 };
  const snap = (scan.payload && scan.payload.reconciled) || (scan.payload && scan.payload.snapshot);
  if (!snap) return { error: 'This scan has no detection result yet.', status: 409 };
  unmanaged.applyTo(snap, scan.rackId);
  entered.applyTo(snap, scan.rackId);
  return { snap };
}

// Literal paths first, so neither is ever read as an :id.
router.get('/types', (req, res) => res.json({ types: registry.types() }));

/**
 * What is at this address?
 *
 * Asked while somebody is still filling in the form, before any credentials
 * exist, so that they do not have to know whether the thing they run is called
 * NetBox or ServiceNow in order to connect to it. The answer is a suggestion:
 * the form fills the type in and the person can override it, and signing in is
 * what actually confirms it.
 *
 * An address we must not connect to at all is refused with its own status, not
 * folded into "could not detect", because the two need different answers from
 * whoever typed it.
 */
router.post('/detect', async (req, res) => {
  const address = String((req.body || {}).address || '').trim();
  if (!address) return res.status(400).json({ error: 'Enter the address of your system.' });
  try {
    const found = await registry.detect(address, {
      // RackTrack installed inside the customer's own network reaches their
      // NetBox on a private address, which is a normal deployment. A hosted
      // RackTrack cannot, and saying so early is kinder than a timeout.
      allowPrivate: process.env.RT_CONNECTORS_ALLOW_PRIVATE === '1',
      allowed: String(process.env.RT_CONNECTORS_ALLOWED_HOSTS || '')
        .split(',').map((h) => h.trim()).filter(Boolean),
    });
    const known = registry.get(found.type);
    return res.json({
      type: found.type,
      label: known ? known.label : found.type,
      url: found.url,
      why: found.why,
      fields: known ? known.fields : [],
    });
  } catch (err) {
    if (err && err.code === 'address_refused') {
      return res.status(400).json({ error: err.message, code: 'address_refused' });
    }
    if (err && err.code === 'no_answer') {
      return res.status(502).json({ error: err.message, code: 'no_answer' });
    }
    return res.status(502).json({ error: `That address could not be checked: ${err.message}` });
  }
});

router.get('/', (req, res) => res.json(connectorStore.list()));

router.post('/', (req, res) => {
  const r = connectorStore.add(req.body || {});
  if (r.error) return res.status(400).json({ error: r.error });
  res.status(201).json(r.record);
});

router.patch('/:id', (req, res) => {
  const r = connectorStore.update(req.params.id, req.body || {});
  if (r.error) return res.status(r.error.startsWith('No connector') ? 404 : 400).json({ error: r.error });
  res.json(r.record);
});

router.delete('/:id', (req, res) => {
  const r = connectorStore.remove(req.params.id);
  if (r.error) return res.status(404).json({ error: r.error });
  res.json({ ok: true });
});

router.post('/:id/test', async (req, res) => {
  const resolved = connectorStore.resolve(req.params.id);
  if (!resolved) return res.status(404).json({ error: 'No connector with that id.' });
  const mod = registry.get(resolved.type);
  try {
    const result = await mod.test(resolved.config);
    connectorStore.recordTest(req.params.id, { ok: result.ok, message: result.message });
    res.json(result);
  } catch (err) {
    connectorStore.recordTest(req.params.id, { ok: false, message: String(err.message) });
    res.status(502).json({ ok: false, message: String(err.message) });
  }
});

router.post('/:id/export/:scanId', async (req, res) => {
  const resolved = connectorStore.resolve(req.params.id);
  if (!resolved) return res.status(404).json({ error: 'No connector with that id.' });
  const got = snapshotOf(req.params.scanId);
  if (got.error) return res.status(got.status).json({ error: got.error });

  const mod = registry.get(resolved.type);
  try {
    const report = await mod.export(got.snap, resolved.config, { apply: Boolean(req.body?.apply) });
    res.json(report);
  } catch (err) {
    res.status(502).json({ error: String(err.message) });
  }
});

module.exports = router;
