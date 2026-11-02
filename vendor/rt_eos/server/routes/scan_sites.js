/**
 * The Sites the scan screen offers.
 *
 * Mounted at /api/scan-sites behind auth.requireAuth (app.js), the same way
 * the setup router is - this file reads req.user and never requires auth.js
 * itself, so a test can seed a throwaway database and mount it behind a stub.
 *
 * One endpoint for every role; lib/scan_site scopes it. A technician gets
 * their one Site, preselected. An organisation admin gets every Site of the
 * organisation and has to choose. It reads and changes nothing.
 */
const express = require('express');
const scanSite = require('../lib/scan_site');
const { logger } = require('../lib/observability');

const router = express.Router();

router.get('/', (req, res) => {
  try {
    const { sites, preselect } = scanSite.listFor(req.user);
    return res.json({ ok: true, preselect, sites });
  } catch (err) {
    logger.error({ event: 'scan_sites.error', err: err.message, userId: req.user?.id }, 'site list failed');
    return res.status(500).json({ error: 'Could not list the sites' });
  }
});

module.exports = router;
