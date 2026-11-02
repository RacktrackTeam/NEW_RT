/**
 * The platform owner's console routes: the log store (GET /api/logs, /api/logs/stats, /api/logs/:id, POST /api/logs/clear)
 * and the dashboard (GET /api/admin/dashboard). Moved out of app.js unchanged so the sign-in service
 * (auth_service.js) can serve the same console; app.js mounts them exactly where they were.
 */
'use strict';

module.exports = function registerOwnerConsole(app, { auth, audit, logger, feedbackTally }) {
  const logStore = require('../lib/log-store');

  function requireLogAdmin(req, res) {
    const adminUsers = String(process.env.AUDIT_ADMINS || '')
      .split(',').map(s => s.trim()).filter(Boolean);
    const allowed = req.user.role === 'owner' || adminUsers.includes(req.user.username);
    if (!allowed) { res.status(403).json({ error: 'Admin access required' }); return false; }
    return true;
  }

  // GET /api/logs?level=&q=&requestId=&since=&until=&limit=&offset=
  app.get('/api/logs', auth.requireAuth, (req, res) => {
    if (!requireLogAdmin(req, res)) return;
    try {
      const { rows, total } = logStore.queryLogs({
        level:     req.query.level     || undefined,
        q:         req.query.q         || undefined,
        requestId: req.query.requestId || undefined,
        since:     req.query.since     || undefined,
        until:     req.query.until     || undefined,
        limit:     req.query.limit     || 200,
        offset:    req.query.offset    || 0,
      });
      res.json({ ok: true, count: rows.length, total, logs: rows });
    } catch (err) {
      logger.error({ err: err.message }, '[logs] query failed');
      res.status(500).json({ ok: false, error: 'Log query failed' });
    }
  });

  // GET /api/logs/stats?since=  → level histogram + totals for the stat tiles.
  app.get('/api/logs/stats', auth.requireAuth, (req, res) => {
    if (!requireLogAdmin(req, res)) return;
    try {
      res.json({ ok: true, ...logStore.logStats(req.query.since || undefined) });
    } catch (err) {
      logger.error({ err: err.message }, '[logs] stats failed');
      res.status(500).json({ ok: false, error: 'Log stats failed' });
    }
  });

  // POST /api/logs/clear  → wipe every stored log line ("start fresh").
  // Same admin gate as reading them. Audited, and we log one line immediately
  // afterwards so the fresh log records who cleared it and when.
  app.post('/api/logs/clear', auth.requireAuth, (req, res) => {
    if (!requireLogAdmin(req, res)) return;
    try {
      const removed = logStore.clearAll();
      audit.log({ req, action: 'logs.clear', status: 'ok', payload: { removed } });
      logger.info({ event: 'logs.cleared', removed, by: req.user?.username },
        `logs cleared (${removed} entries removed)`);
      res.json({ ok: true, removed });
    } catch (err) {
      logger.error({ err: err.message }, '[logs] clear failed');
      res.status(500).json({ ok: false, error: 'Could not clear logs' });
    }
  });

  // GET /api/logs/:id  → one row with its full parsed JSON line (row detail).
  app.get('/api/logs/:id', auth.requireAuth, (req, res) => {
    if (!requireLogAdmin(req, res)) return;
    try {
      const row = logStore.getLog(parseInt(req.params.id, 10));
      if (!row) return res.status(404).json({ ok: false, error: 'Not found' });
      res.json({ ok: true, log: row });
    } catch (err) {
      logger.error({ err: err.message }, '[logs] detail failed');
      res.status(500).json({ ok: false, error: 'Log detail failed' });
    }
  });

  // ── Live operations dashboard ────────────────────────────────────────
  // GET /api/admin/dashboard → one JSON snapshot the DashboardPage polls every
  // few seconds. Aggregates the audit log (who did what, ok/fail, errors),
  // the user/org tables, and feedback.jsonl (right/wrong accuracy) — all cheap
  // indexed SQLite reads plus one small file scan. Owner-only, or anyone in the
  // AUDIT_ADMINS allow-list, so per-org data never leaks to ordinary members.
  app.get('/api/admin/dashboard', auth.requireAuth, (req, res) => {
    const adminUsers = String(process.env.AUDIT_ADMINS || '')
      .split(',').map(s => s.trim()).filter(Boolean);
    const allowed = req.user.role === 'owner' || adminUsers.includes(req.user.username);
    if (!allowed) return res.status(403).json({ error: 'Owner access required' });

    try {
      const adb  = audit._db;
      const one  = (sql, ...p) => adb.prepare(sql).get(...p);
      const many = (sql, ...p) => adb.prepare(sql).all(...p);

      // Correlated sub-select turns a tenant_id into its org name in one shot.
      const orgOf = `(SELECT o.name FROM tenants t JOIN organizations o
                        ON o.id = t.organization_id WHERE t.id = a.tenant_id)`;

      // Headline totals.
      const users   = one("SELECT COUNT(*) n FROM users").n;
      const orgs    = one("SELECT COUNT(*) n FROM organizations").n;
      const scansOk = one("SELECT COUNT(*) n FROM audit_log WHERE action='scan.create' AND status='ok'").n;
      const scansFail = one("SELECT COUNT(*) n FROM audit_log WHERE action LIKE 'scan.%' AND status='fail'").n;
      const scansToday = one("SELECT COUNT(*) n FROM audit_log WHERE action='scan.create' AND status='ok' AND ts >= datetime('now','start of day')").n;
      const activeToday = one("SELECT COUNT(DISTINCT user_id) n FROM audit_log WHERE ts >= datetime('now','start of day') AND user_id IS NOT NULL").n;
      const totalEvents = one("SELECT COUNT(*) n FROM audit_log").n;
      const totalFails  = one("SELECT COUNT(*) n FROM audit_log WHERE status='fail'").n;

      // Feedback accuracy (right vs wrong), from the memoised tally.
      const { right: fbRight, wrong: fbWrong } = feedbackTally();

      // Live activity feed — most recent events, org-resolved.
      // Events at the auth gate (sign-in / sign-up / reset) have no logged-in
      // session, so username is NULL. We fall back to the *attempted* identifier
      // captured in the payload and flag the row as a guest so the UI can label
      // it clearly instead of showing a bare "anonymous".
      const recent = many(`
        SELECT a.ts,
               COALESCE(NULLIF(a.username,''), json_extract(a.payload,'$.ident')) AS username,
               (SELECT public_id FROM users u WHERE u.id = a.user_id) AS actor_id,
               CASE WHEN COALESCE(a.username,'') = '' THEN 1 ELSE 0 END AS guest,
               a.action, a.target_id, a.status, a.error, ${orgOf} AS org
        FROM audit_log a ORDER BY a.id DESC LIMIT 80`);

      // Recent failures with their error text.
      const errors = many(`
        SELECT a.ts,
               COALESCE(NULLIF(a.username,''), json_extract(a.payload,'$.ident')) AS username,
               CASE WHEN COALESCE(a.username,'') = '' THEN 1 ELSE 0 END AS guest,
               a.action, a.error, ${orgOf} AS org
        FROM audit_log a
        WHERE a.status='fail' AND a.error IS NOT NULL AND a.error <> ''
        ORDER BY a.id DESC LIMIT 30`);

      // Busiest scanners. Resolve the real name from the user the event is
      // attributed to (a.user_id) — the denormalized a.username is often blank
      // for owner/admin scans, which is why the board showed "(anonymous)".
      const topUsers = many(`
        SELECT COALESCE(NULLIF(u.username,''), NULLIF(a.username,''), '(anonymous)') AS username,
               COUNT(*) AS scans
        FROM audit_log a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.action='scan.create' AND a.status='ok'
        GROUP BY COALESCE(a.user_id, a.username) ORDER BY scans DESC LIMIT 10`);

      // Scans per organization. Resolve the org via the Site first, then the
      // user's own organization (owners/admins have no tenant_id, which is why
      // their scans fell into "(no org)").
      const orgOfUser = `(SELECT o.name FROM users u JOIN organizations o
                            ON o.id = u.organization_id WHERE u.id = a.user_id)`;
      const byOrg = many(`
        SELECT COALESCE(${orgOf}, ${orgOfUser}, '(no org)') AS org, COUNT(*) AS scans
        FROM audit_log a WHERE a.action='scan.create' AND a.status='ok'
        GROUP BY org ORDER BY scans DESC LIMIT 10`);

      // Site (tenant) name straight off the event, for scan attribution.
      const siteOf = `(SELECT t.name FROM tenants t WHERE t.id = a.tenant_id)`;

      // Scans per site — the same board as byOrg, one level finer.
      const bySite = many(`
        SELECT COALESCE(${siteOf}, '(no site)') AS site, COUNT(*) AS scans
        FROM audit_log a WHERE a.action='scan.create' AND a.status='ok'
        GROUP BY site ORDER BY scans DESC LIMIT 10`);

      // The scans themselves — each recent scan with who made it and where
      // (site + org), so the dashboard can show the work, not just the counts.
      const recentScans = many(`
        SELECT a.ts, a.target_id AS rack,
               COALESCE(NULLIF(u.username,''), NULLIF(a.username,''), '(anonymous)') AS username,
               ${siteOf} AS site,
               COALESCE(${orgOf}, ${orgOfUser}) AS org
        FROM audit_log a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.action='scan.create' AND a.status='ok'
        ORDER BY a.id DESC LIMIT 100`);

      // Action mix — EVERY action type (ok vs fail), nothing capped.
      const actions = many(`
        SELECT action,
               SUM(CASE WHEN status='ok'   THEN 1 ELSE 0 END) AS ok,
               SUM(CASE WHEN status='fail' THEN 1 ELSE 0 END) AS fail
        FROM audit_log GROUP BY action ORDER BY (ok+fail) DESC`);

      // EVERY user with their full activity — role, org, scans, total events,
      // failures, and when they were last seen.
      const allUsers = many(`
        SELECT u.username, u.email, u.role, u.active, u.public_id,
               (SELECT o.name FROM organizations o WHERE o.id = u.organization_id) AS org,
               (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id AND a.action='scan.create' AND a.status='ok') AS scans,
               (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id) AS events,
               (SELECT COUNT(*) FROM audit_log a WHERE a.user_id = u.id AND a.status='fail') AS fails,
               (SELECT MAX(a.ts) FROM audit_log a WHERE a.user_id = u.id) AS last_active
        FROM users u ORDER BY events DESC, scans DESC`);

      // EVERY organization with members + scans + status.
      const allOrgs = many(`
        SELECT o.name, o.status,
               (SELECT COUNT(*) FROM users u WHERE u.organization_id = o.id) AS members,
               (SELECT COUNT(*) FROM audit_log a WHERE a.action='scan.create' AND a.status='ok'
                  AND a.tenant_id IN (SELECT t.id FROM tenants t WHERE t.organization_id = o.id)) AS scans
        FROM organizations o ORDER BY scans DESC`);

      // Auth activity.
      const auth_ = {
        logins_ok:   one("SELECT COUNT(*) n FROM audit_log WHERE action='auth.login' AND status='ok'").n,
        logins_fail: one("SELECT COUNT(*) n FROM audit_log WHERE action='auth.login' AND status='fail'").n,
        signups:     one("SELECT COUNT(*) n FROM audit_log WHERE action LIKE 'auth.signup%' AND status='ok'").n,
        resets:      one("SELECT COUNT(*) n FROM audit_log WHERE action LIKE 'auth.forgot_password%'").n,
        invites:     one("SELECT COUNT(*) n FROM audit_log WHERE action='invite.accept'").n,
      };

      res.json({
        ok: true,
        generatedAt: new Date().toISOString(),
        totals: {
          users, orgs, scansOk, scansFail, scansToday, activeToday,
          totalEvents, totalFails,
          successRate: totalEvents ? Math.round((1 - totalFails / totalEvents) * 100) : null,
        },
        feedback: {
          right: fbRight, wrong: fbWrong,
          accuracy: (fbRight + fbWrong) ? Math.round(fbRight / (fbRight + fbWrong) * 100) : null,
        },
        auth: auth_,
        recent, errors, topUsers, byOrg, bySite, recentScans, actions,
        allUsers, allOrgs,
      });
    } catch (err) {
      logger.error('[dashboard] query failed:', err);
      res.status(500).json({ ok: false, error: 'Dashboard query failed' });
    }
  });
};
