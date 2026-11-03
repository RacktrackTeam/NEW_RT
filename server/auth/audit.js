/**
 * Audit log - append-only event trail for every authenticated/sensitive action.
 *
 * Storage: shares data/db/auth.db (a single SQLite file keeps backup +
 * restore trivial). Table is created on first require.
 *
 * Usage:
 *   const audit = require('./audit');
 *   audit.log({ req, action: 'auth.login', status: 'ok', targetType: 'user', targetId: user.id });
 *   audit.log({ req, action: 'scan.share', status: 'fail', targetType: 'rack', targetId: rackId,
 *               error: err.message, payload: { channel: 'email' } });
 *
 * Reads:
 *   audit.query({ userId, action, sinceTs, untilTs, limit, offset })
 *
 * Design notes:
 *   - All log() calls are best-effort: a DB error is caught + logged, never thrown.
 *     Audit logging must never break the request path.
 *   - We snapshot username at write time so deleting a user later does not
 *     erase their trail.
 *   - payload is JSON-stringified; we cap it at 8 KiB to bound row size.
 *   - `req` is optional - pass it when available so we can capture IP + UA.
 */

const fs       = require('fs');
const Database = require('better-sqlite3');

// RACKTRACK_AUTH_DB, else data/db/auth.db: see lib/data_paths.js.
const { AUTH_DB: dbPath, DATA_DIR: dataDir } = require('./lib/data_paths');
if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

const db = new Database(dbPath);
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL DEFAULT (datetime('now')),
    user_id      INTEGER,
    username     TEXT,
    action       TEXT    NOT NULL,
    target_type  TEXT,
    target_id    TEXT,
    status       TEXT    NOT NULL CHECK (status IN ('ok','fail')),
    ip           TEXT,
    user_agent   TEXT,
    payload      TEXT,
    error        TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_audit_user_ts   ON audit_log(user_id, ts DESC);
  CREATE INDEX IF NOT EXISTS idx_audit_action_ts ON audit_log(action, ts DESC);
  CREATE INDEX IF NOT EXISTS idx_audit_target    ON audit_log(target_type, target_id);
  -- The owner console polls /api/admin/dashboard every 5s, and its queries
  -- filter on status and ts. Without these, "failures" and "active today" were
  -- full table scans of audit_log on every poll, forever.
  CREATE INDEX IF NOT EXISTS idx_audit_status_id ON audit_log(status, id DESC);
  CREATE INDEX IF NOT EXISTS idx_audit_ts        ON audit_log(ts);
`);

// tenant_id is added to audit_log by auth.js's tenant migration - which runs
// AFTER this module is required (auth.js does `require('./audit')` at the top,
// THEN runs migrateTenants()). So at load time the column is typically absent.
//
// We must NOT freeze that observation for the life of the process. Doing so
// pinned the INSERT to the no-tenant shape forever: every audit row written
// after boot silently landed with tenant_id NULL, which the migration then
// re-stamped to the default tenant on the next restart. Instead we re-detect
// until the column shows up, then latch the positive result (a column is only
// ever added during a run, never dropped) and prepare the matching INSERT
// lazily on first use.
let _tenantColReady = false;
function _hasTenantColumn() {
  if (_tenantColReady) return true;
  const has = db.prepare('PRAGMA table_info(audit_log)').all().some(c => c.name === 'tenant_id');
  if (has) _tenantColReady = true;
  return has;
}

let _insWithTenant = null;
let _insNoTenant = null;
function _insertStmt(hasTenant) {
  if (hasTenant) {
    if (!_insWithTenant) {
      _insWithTenant = db.prepare(`
        INSERT INTO audit_log
          (user_id, username, tenant_id, action, target_type, target_id,
           status, ip, user_agent, payload, error)
        VALUES
          (@user_id, @username, @tenant_id, @action, @target_type, @target_id,
           @status, @ip, @user_agent, @payload, @error)
      `);
    }
    return _insWithTenant;
  }
  if (!_insNoTenant) {
    _insNoTenant = db.prepare(`
      INSERT INTO audit_log
        (user_id, username, action, target_type, target_id, status, ip, user_agent, payload, error)
      VALUES
        (@user_id, @username, @action, @target_type, @target_id, @status, @ip, @user_agent, @payload, @error)
    `);
  }
  return _insNoTenant;
}

const PAYLOAD_LIMIT = 8 * 1024;

function clientIp(req) {
  if (!req) return null;
  // Trust the first non-internal X-Forwarded-For hop if Express was set up
  // with `app.set('trust proxy', ...)`; otherwise fall back to req.ip.
  const xff = req.headers?.['x-forwarded-for'];
  if (typeof xff === 'string' && xff.length) {
    return xff.split(',')[0].trim();
  }
  return req.ip || req.socket?.remoteAddress || null;
}

function safeStringify(payload) {
  if (payload == null) return null;
  try {
    const s = typeof payload === 'string' ? payload : JSON.stringify(payload);
    if (s.length <= PAYLOAD_LIMIT) return s;
    return s.slice(0, PAYLOAD_LIMIT - 3) + '...';
  } catch {
    return null;
  }
}

// Lazy-load observability so audit.js can still be required from contexts
// where the observability module isn't bootstrapped (e.g. one-off scripts).
// Falls back to console.* in that case so logs aren't lost.
function _o11y() {
  try { return require('./lib/observability'); }
  catch { return null; }
}

function log(opts = {}) {
  const {
    req,
    user,                  // optional explicit user object {id, username}
    action,
    targetType = null,
    targetId   = null,
    status     = 'ok',
    error      = null,
    payload    = null,
  } = opts;

  const o = _o11y();
  const fallbackWarn = (msg) => o ? o.logger.warn({ kind: 'audit', drop: true }, msg) : console.warn(msg);

  if (!action || typeof action !== 'string') {
    fallbackWarn('[audit] dropped event: missing action');
    return;
  }
  if (status !== 'ok' && status !== 'fail') {
    fallbackWarn(`[audit] dropped event "${action}": invalid status "${status}"`);
    return;
  }

  // Resolve user from req.user (set by requireAuth) if not passed explicitly.
  const u = user || req?.user || null;
  const targetIdStr = targetId == null ? null : String(targetId);
  const ip = clientIp(req);
  const userAgent = req?.headers?.['user-agent']?.slice(0, 512) ?? null;
  // Tenant snapshot at write time so the audit row stays correct even
  // if the user later moves tenants (rare but possible via admin tools).
  const tenantId = u?.tenant_id ?? u?.tenant?.id ?? null;

  try {
    const hasTenant = _hasTenantColumn();
    const params = {
      user_id:     u?.id ?? null,
      username:    u?.username ?? null,
      action,
      target_type: targetType,
      target_id:   targetIdStr,
      status,
      ip,
      user_agent:  userAgent,
      payload:     safeStringify(payload),
      error:       error ? String(error).slice(0, 1024) : null,
    };
    if (hasTenant) params.tenant_id = tenantId;
    _insertStmt(hasTenant).run(params);
  } catch (err) {
    // Never let audit failures break the caller. Surface to log for ops.
    if (o) {
      o.logger.error({
        kind: 'audit', subkind: 'persist_failed',
        action, targetId: targetIdStr, err: err.message,
      }, 'audit DB write failed');
    } else {
      console.error('[audit] failed to record event', { action, targetId: targetIdStr, err: err.message });
    }
  }

  // Mirror to structured log stream + bump audit-event counter so the same
  // trail is queryable in logs/aggregators, not just SQLite. Best-effort -
  // a failure here must not affect the caller.
  if (o) {
    try {
      o.emitAudit({
        action, status, targetType, targetId: targetIdStr,
        userId: u?.id ?? null, username: u?.username ?? null,
        tenantId,
        ip, userAgent,
        requestId: req?.id ?? null,
        error: error ? String(error).slice(0, 1024) : null,
      });
    } catch { /* swallow */ }
  }
}

/**
 * Query the audit log. All filters are optional. Newest-first.
 * Returns an array of rows.
 */
function query({
  userId   = undefined,
  tenantId = undefined,   // restrict to one tenant (multi-tenancy guard)
  action   = undefined,
  targetType = undefined,
  targetId = undefined,
  status   = undefined,
  sinceTs  = undefined,  // ISO string or SQLite-friendly 'YYYY-MM-DD HH:MM:SS'
  untilTs  = undefined,
  limit    = 100,
  offset   = 0,
} = {}) {
  const where = [];
  const params = {};

  if (userId !== undefined && userId !== null && userId !== '') {
    where.push('user_id = @userId');
    params.userId = Number(userId);
  }
  if (tenantId !== undefined && tenantId !== null && tenantId !== '' && _hasTenantColumn()) {
    where.push('tenant_id = @tenantId');
    params.tenantId = Number(tenantId);
  }
  if (action) {
    // Allow wildcard suffix like 'scan.*'
    if (action.endsWith('.*')) {
      where.push('action LIKE @actionPrefix');
      params.actionPrefix = action.slice(0, -1) + '%';
    } else {
      where.push('action = @action');
      params.action = action;
    }
  }
  if (targetType) { where.push('target_type = @targetType'); params.targetType = targetType; }
  if (targetId)   { where.push('target_id   = @targetId');   params.targetId   = String(targetId); }
  if (status)     { where.push('status      = @status');     params.status     = status; }
  if (sinceTs)    { where.push('ts >= @sinceTs');            params.sinceTs    = sinceTs; }
  if (untilTs)    { where.push('ts <= @untilTs');            params.untilTs    = untilTs; }

  const sql = `
    SELECT id, ts, user_id, username, action, target_type, target_id, status, ip, user_agent, payload, error
    FROM audit_log
    ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
    ORDER BY id DESC
    LIMIT @limit OFFSET @offset
  `;
  params.limit  = Math.min(Math.max(1, Number(limit) || 100), 1000);
  params.offset = Math.max(0, Number(offset) || 0);

  const rows = db.prepare(sql).all(params);
  // Parse payload back to JSON for callers; keep raw string on parse failure.
  return rows.map(r => {
    if (r.payload) {
      try { r.payload = JSON.parse(r.payload); } catch { /* leave as string */ }
    }
    return r;
  });
}

module.exports = { log, query, _db: db };
