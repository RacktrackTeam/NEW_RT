/**
 * CMDB ticket router.
 *
 * Wraps servicenow/cmdb_ticket.py to expose a small REST surface the UI
 * can hit without spawning Python from the browser.
 *
 *   GET  /api/cmdb/ticket/:rackId          → current local state file
 *                                            (null if no ticket exists)
 *   POST /api/cmdb/ticket/:rackId/refresh  → re-poll one rack's ticket
 *   POST /api/cmdb/ticket/:rackId/create   → compute diff + open ticket
 *                                            (?force=1 → recreate even
 *                                            if diff is empty / unchanged)
 *   POST /api/cmdb/ticket/:rackId/cancel   → drop the local state file
 *   POST /api/cmdb/ticket/poll             → sweep every open ticket
 *
 * Also exports `scheduleCmdbTicket(rackId)` — a debounced background runner
 * the scan flow calls so a fresh scan auto-opens / updates a ticket.
 *
 * Plus `startTicketPoller(intervalMs)` which app.js kicks off at startup
 * to run the 5-minute poll cycle.
 */
const express = require('express');
const path    = require('path');
const fs      = require('fs');
const { spawn } = require('child_process');
const { logger, recordEvent } = require('./lib/observability');
const auth    = require('./auth');
const profiles = require('./lib/connection_profiles');

const router = express.Router();
const tenant = require('./lib/tenant');
const { rackOwnershipParam } = require('./lib/rack_access');
const PROJECT_ROOT  = path.resolve(__dirname, '..');

// Tenant ownership guard for any route that takes :rackId on this router.
// app.param doesn't propagate from the parent app to mounted routers, so
// we register it here too. requireAuth already runs before this fires
// (router.use('/api/cmdb/ticket', auth.requireAuth) below), so req.user
// is populated.
// Shared with app.js and netdisco_proxy.js so the three cannot drift again.
// This copy used to `return next()` when tenant_id was null — failing OPEN, so
// any user row without a Site reached every CMDB route on the platform,
// including the one that opens a real ServiceNow request against a rack. It
// also ignored role, so the platform owner and org admins got "Rack not found"
// for racks they are entitled to see.
router.param('rackId', rackOwnershipParam({ tenant, logger }));
const TICKET_SCRIPT = path.join(PROJECT_ROOT, 'servicenow', 'cmdb_ticket.py');
const PYTHON_CMD    = process.env.PYTHON_CMD ||
                      (process.platform === 'win32' ? 'python' : 'python3');


// Python failures embed the customer's ServiceNow instance URL (every client
// builds https://<inst>.service-now.com/...) and full tracebacks. Neither may
// reach the API caller. We keep the ticket_url the UI needs but scrub any
// instance hostname out of free-text error strings and cap their length.
function scrubError(msg) {
  if (typeof msg !== 'string' || !msg) return 'CMDB ticket operation failed';
  return msg
    // Any *.service-now.com reference — full URL, or the bare host= form a
    // requests ConnectionError prints — becomes an opaque token.
    .replace(/(?:https?:\/\/)?[A-Za-z0-9._-]*\.service-now\.com[^\s'"]*/gi, '[servicenow]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 300) || 'CMDB ticket operation failed';
}

function runTicketCmd(args, { timeoutMs = 60_000, userId = null, orgId = null } = {}) {
  return new Promise((resolve) => {
    if (!fs.existsSync(TICKET_SCRIPT)) {
      return resolve({ ok: false, error: `script missing at ${TICKET_SCRIPT}` });
    }
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
    // Inject ServiceNow credentials — the ORG-wide profile the admin set takes
    // precedence, falling back to a personal one. No env/file fallback: with no
    // org/user or no active profile, SN_* stays unset and the Python script
    // fails fast. Data only flows from a configured connection.
    const resolved = (orgId ? profiles.resolveCredsForOrg(orgId, 'servicenow') : null)
      || (userId ? profiles.resolveCredsForType(userId, 'servicenow') : null);
    if (resolved && resolved.secret) {
      const s = resolved.secret;
      if (s.instance) env.SN_INSTANCE = s.instance;
      if (s.user)     env.SN_USER     = s.user;
      if (s.password) env.SN_PASSWORD = s.password;
    }
    const child = spawn(PYTHON_CMD, [TICKET_SCRIPT, ...args, '--json'], {
      cwd: PROJECT_ROOT, env,
    });
    let stdout = '', stderr = '';
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch (_) {}
      resolve({ ok: false, error: `cmdb_ticket.py timed out (${timeoutMs}ms)` });
    }, timeoutMs);
    child.stdout.on('data', d => { stdout += d.toString(); });
    child.stderr.on('data', d => { stderr += d.toString(); });
    child.on('close', () => {
      clearTimeout(timer);
      // The script emits a single JSON document at the end of stdout when
      // --json is set; pull it out of any preceding log lines.
      let parsed = null;
      const text = stdout.trim();
      if (text.startsWith('{') || text.startsWith('[')) {
        try { parsed = JSON.parse(text); } catch (_) {}
      }
      if (!parsed) {
        // Look for the last balanced JSON block in stdout.
        const last = text.lastIndexOf('\n{');
        if (last >= 0) {
          try { parsed = JSON.parse(text.slice(last + 1)); } catch (_) {}
        }
      }
      if (parsed && typeof parsed === 'object') {
        // The script's own error strings can also carry the instance URL
        // (e.g. a requests exception). Scrub that one field; leave the rest
        // (including the legitimate ticket_url the UI links to) intact.
        if (parsed.ok === false && typeof parsed.error === 'string') {
          logger.warn(`[cmdb-ticket] ${args.join(' ')} script error: ${parsed.error}`);
          parsed.error = scrubError(parsed.error);
        }
        return resolve(parsed);
      }
      // No JSON at all — a crash or non-JSON exit. Log the raw output
      // server-side for diagnosis; return a generic message to the caller.
      logger.error(
        `[cmdb-ticket] ${args.join(' ')} produced no JSON (exit) — ` +
        `stderr=${stderr.trim().slice(-2000)} stdout=${stdout.trim().slice(-500)}`
      );
      resolve({ ok: false, error: 'CMDB ticket operation failed' });
    });
    child.on('error', (e) => {
      clearTimeout(timer);
      resolve({ ok: false, error: `spawn failed: ${e.message}` });
    });
  });
}


function safeAsync(handler) {
  return async (req, res) => {
    try { await handler(req, res); }
    catch (err) {
      logger.error(`[cmdb-ticket] ${req.method} ${req.originalUrl} — ${err.message}`);
      res.status(500).json({ error: 'ticket request failed' });
    }
  };
}


// All routes require app auth like the rest of /api.
router.use('/api/cmdb/ticket', auth.requireAuth);

// GET — current local state for a rack
router.get('/api/cmdb/ticket/:rackId', safeAsync(async (req, res) => {
  const r = await runTicketCmd(
    ['status', '--rack-id', req.params.rackId],
    { userId: req.user?.id, orgId: req.user?.organization_id }
  );
  res.json(r);
}));

// POST refresh — re-poll one rack's ticket
router.post('/api/cmdb/ticket/:rackId/refresh', safeAsync(async (req, res) => {
  const r = await runTicketCmd(
    ['poll', '--rack-id', req.params.rackId],
    { userId: req.user?.id, orgId: req.user?.organization_id }
  );
  res.json(r);
}));

// POST create — compute diff and open / update the SR
router.post('/api/cmdb/ticket/:rackId/create', safeAsync(async (req, res) => {
  const args = ['create', '--rack-id', req.params.rackId];
  if (req.query.force === '1' || req.body?.force) args.push('--force');
  const r = await runTicketCmd(args, { timeoutMs: 120_000, userId: req.user?.id, orgId: req.user?.organization_id });
  res.json(r);
}));

// POST cancel — drop the local state file
router.post('/api/cmdb/ticket/:rackId/cancel', safeAsync(async (req, res) => {
  const r = await runTicketCmd(
    ['cancel', '--rack-id', req.params.rackId],
    { userId: req.user?.id, orgId: req.user?.organization_id }
  );
  res.json(r);
}));

// POST poll — sweep all. This iterates EVERY tenant's outputs/RK-* directory
// and returns their rack ids + ticket numbers, so it is owner-only.
router.post('/api/cmdb/ticket/poll', auth.requireRole('owner'), safeAsync(async (req, res) => {
  const r = await runTicketCmd(['poll'], { timeoutMs: 300_000, userId: req.user?.id, orgId: req.user?.organization_id });
  res.json(r);
}));

// POST dev-approve — DEMO ONLY: skips the ServiceNow approval step, applies the
// scan to the CMDB immediately and fabricates a DEV-<rackId> ticket if none
// exists. The product's compliance claim is that every CMDB write is gated
// behind an approved Service Request, so this must never be reachable by a
// normal user in production: it let anyone self-approve a write against a fake
// ticket. Enabled only when CMDB_DEV_APPROVE=1 is explicitly set, and only for
// owners. The genuine flow (/refresh, /cancel, the 5-min poller) is unaffected.
router.post('/api/cmdb/ticket/:rackId/dev-approve', auth.requireRole('owner'), safeAsync(async (req, res) => {
  if (process.env.CMDB_DEV_APPROVE !== '1') {
    return res.status(403).json({
      ok: false,
      error: 'Demo approval is disabled. Approve the Service Request in ServiceNow instead.',
    });
  }
  const r = await runTicketCmd(
    ['dev-approve', '--rack-id', req.params.rackId],
    { timeoutMs: 600_000, userId: req.user?.id, orgId: req.user?.organization_id }
  );
  res.json(r);
}));


// ── Scheduler hooks (called from app.js) ─────────────────────────────────
// Debounced auto-create after a scan completes. We give topology +
// netdisco a head start so the diff engine has the freshest data.
const _ticketSchedule = new Map();   // rackId → setTimeout handle
function scheduleCmdbTicket(rackId, delayMs = 4000) {
  if (!rackId) return;
  if (_ticketSchedule.has(rackId)) clearTimeout(_ticketSchedule.get(rackId));
  _ticketSchedule.set(rackId, setTimeout(async () => {
    _ticketSchedule.delete(rackId);
    try {
      const r = await runTicketCmd(['create', '--rack-id', rackId],
                                   { timeoutMs: 120_000 });
      if (r.ok) {
        const action = r.action || 'unknown';
        const num    = r.ticket?.number || '—';
        logger.info(`[cmdb-ticket] ${rackId} ${action} ${num !== '—' ? `(${num})` : ''}`);
      } else {
        logger.warn(`[cmdb-ticket] ${rackId} create failed: ${r.error}`);
      }
    } catch (err) {
      logger.warn(`[cmdb-ticket] ${rackId} threw: ${err.message}`);
    }
  }, delayMs));
}

// Single-shot poll cycle helper (also exposed so we can run it on demand).
async function runPollCycle() {
  try {
    const r = await runTicketCmd(['poll'], { timeoutMs: 300_000 });
    if (r.ok) {
      const swept   = r.swept || 0;
      const results = r.results || [];
      const acted   = results.filter(x =>
        ['applied', 'rejected', 'cancelled', 'apply_failed',
         'apply_failed_terminal'].includes(x.action)
      );
      if (swept > 0) {
        logger.info(`[cmdb-ticket] poll cycle — swept ${swept}, acted ${acted.length}`);
      }
      acted.forEach(x => {
        const t = x.ticket || {};
        logger.info(`[cmdb-ticket]   ${x.rack_id}: ${x.action}  ${t.number || ''}`);
      });
    } else {
      logger.warn(`[cmdb-ticket] poll cycle failed: ${r.error}`);
    }
    return r;
  } catch (err) {
    logger.warn(`[cmdb-ticket] poll cycle threw: ${err.message}`);
    return { ok: false, error: err.message };
  }
}

let _pollerHandle = null;
function startTicketPoller(intervalMs = 5 * 60 * 1000) {
  if (_pollerHandle) return;     // already running
  _pollerHandle = setInterval(runPollCycle, intervalMs);
  // Fire one cycle ~30s after boot so existing tickets get caught up
  // without blocking startup.
  setTimeout(runPollCycle, 30_000);
  logger.info(`[cmdb-ticket] poller started (every ${Math.round(intervalMs / 1000)}s)`);
}

router.scheduleCmdbTicket = scheduleCmdbTicket;
router.startTicketPoller  = startTicketPoller;
router.runPollCycle       = runPollCycle;

module.exports = router;
