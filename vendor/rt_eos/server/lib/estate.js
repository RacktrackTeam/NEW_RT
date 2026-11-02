/**
 * Estate: what a datacentre contains, and whether it has been set up.
 *
 * A tenant IS a datacentre. The console calls it a "Site"; the setup flow
 * calls it a datacentre; both are the same tenants row. There is deliberately
 * no separate datacentre table — one identity, one set of members, one set of
 * claimed racks.
 *
 * Under a tenant sit spaces (a hall, a floor, a room, a row — nested through
 * parent_id, named however the customer names them; nothing here assumes a
 * convention) and racks_known (the racks the admin typed or that the camera
 * has learned, keyed by the RK- scan id once one exists).
 *
 * Every stored row carries who created it, when, and a `source`:
 *   typed     an admin entered it in the console
 *   imported  it came from a file or a connector
 *   learned   the system recorded it from a scan
 *
 * Nothing in this module calls out. Coordinates are accepted as given; there
 * is no geocoding.
 *
 * Schema (added lazily on first use, all additive and idempotent):
 *   tenants + address, timezone, lat, lng, approver_user_id, approver_email,
 *             rules_accepted_at, rules_accepted_by, setup_completed_at
 *   spaces, racks_known, tenant_rules — see _prep() below.
 *   The optional profile sections and the organisation profile live in
 *   lib/estate_profile.js; completeness and the snapshot read them from there.
 *
 * Same database and env override as lib/tenant.js, for the same reason: tests
 * seed a throwaway file and point this module at it.
 */

const Database = require('better-sqlite3');
const { logger } = require('./observability');
const tenantLib = require('./tenant');
const profile = require('./estate_profile');
const { isValidRackId } = require('./rack_access');

// RACKTRACK_AUTH_DB, else data/db/auth.db: see lib/data_paths.js.
const dbPath = require('./data_paths').AUTH_DB;
const db = new Database(dbPath);
db.pragma('journal_mode = WAL');

const SOURCES = ['typed', 'imported', 'learned'];
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** A validation or state error the router turns into an HTTP status. */
class EstateError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ── Schema ───────────────────────────────────────────────────────────
function _hasColumn(table, col) {
  return db.prepare(`PRAGMA table_info(${table})`).all().some((c) => c.name === col);
}
function _ensureColumn(table, col, ddl) {
  if (!_hasColumn(table, col)) db.exec(`ALTER TABLE ${table} ADD COLUMN ${ddl}`);
}

let _ready = false;
function _prep() {
  if (_ready) return;
  _ensureColumn('tenants', 'address', 'address TEXT');
  _ensureColumn('tenants', 'timezone', 'timezone TEXT');
  _ensureColumn('tenants', 'lat', 'lat REAL');
  _ensureColumn('tenants', 'lng', 'lng REAL');
  _ensureColumn('tenants', 'approver_user_id', 'approver_user_id INTEGER REFERENCES users(id)');
  // For an approver who has not been made a user yet.
  _ensureColumn('tenants', 'approver_email', 'approver_email TEXT');
  _ensureColumn('tenants', 'rules_accepted_at', 'rules_accepted_at TEXT');
  _ensureColumn('tenants', 'rules_accepted_by', 'rules_accepted_by INTEGER REFERENCES users(id)');
  _ensureColumn('tenants', 'setup_completed_at', 'setup_completed_at TEXT');

  db.exec(`
    CREATE TABLE IF NOT EXISTS spaces (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
      parent_id   INTEGER REFERENCES spaces(id),
      name        TEXT    NOT NULL COLLATE NOCASE,
      facility_id TEXT,
      rack_count  INTEGER,
      source      TEXT    NOT NULL DEFAULT 'typed',
      created_by  INTEGER REFERENCES users(id),
      created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    -- SQLite treats two NULLs as distinct in a UNIQUE constraint, so a plain
    -- (tenant_id, parent_id, name) would allow two root spaces with one name.
    CREATE UNIQUE INDEX IF NOT EXISTS uq_spaces_tenant_parent_name
      ON spaces(tenant_id, COALESCE(parent_id, 0), name);
    CREATE INDEX IF NOT EXISTS idx_spaces_tenant ON spaces(tenant_id);

    CREATE TABLE IF NOT EXISTS racks_known (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
      space_id    INTEGER REFERENCES spaces(id),
      rack_id     TEXT,
      name        TEXT,
      facility_id TEXT,
      u_height    INTEGER DEFAULT 42,
      source      TEXT    NOT NULL DEFAULT 'typed',
      created_by  INTEGER REFERENCES users(id),
      created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uq_racks_known_tenant_rack
      ON racks_known(tenant_id, rack_id) WHERE rack_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_racks_known_space ON racks_known(space_id);

    -- One row per tenant. approve_before_write and never_delete are product
    -- rules, not settings: they are stored so the row says what was accepted,
    -- but nothing ever writes a 0 into them.
    CREATE TABLE IF NOT EXISTS tenant_rules (
      tenant_id            INTEGER PRIMARY KEY REFERENCES tenants(id),
      approve_before_write INTEGER NOT NULL DEFAULT 1,
      never_delete         INTEGER NOT NULL DEFAULT 1,
      photo_retention_days INTEGER NOT NULL DEFAULT 90,
      default_u_height     INTEGER NOT NULL DEFAULT 42,
      u_from_bottom        INTEGER NOT NULL DEFAULT 1,
      source               TEXT    NOT NULL DEFAULT 'typed',
      created_by           INTEGER REFERENCES users(id),
      created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
    );
  `);
  _ensureColumn('spaces', 'kind', 'kind TEXT');
  _ensureColumn('spaces', 'floor', 'floor TEXT');
  _ensureColumn('spaces', 'room', 'room TEXT');
  _ensureColumn('spaces', 'row', 'row TEXT');
  _ready = true;
  logger.info({ event: 'estate.schema_ready' }, 'estate schema ready');
}

// ── Small validators ────────────────────────────────────────────────
function _optString(v, field, max) {
  if (v === undefined) return undefined;
  if (v === null || v === '') return null;
  if (typeof v !== 'string') throw new EstateError(400, `${field} must be text`);
  const s = v.trim();
  if (s.length > max) throw new EstateError(400, `${field} is too long (max ${max})`);
  return s || null;
}
function _optInt(v, field, min, max) {
  if (v === undefined) return undefined;
  if (v === null || v === '') return null;
  const n = Number(v);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new EstateError(400, `${field} must be a whole number between ${min} and ${max}`);
  }
  return n;
}
function _optNumber(v, field, min, max) {
  if (v === undefined) return undefined;
  if (v === null || v === '') return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n < min || n > max) {
    throw new EstateError(400, `${field} must be a number between ${min} and ${max}`);
  }
  return n;
}
function _source(v) {
  if (v === undefined || v === null || v === '') return 'typed';
  if (!SOURCES.includes(v)) throw new EstateError(400, `source must be one of ${SOURCES.join(', ')}`);
  return v;
}
function _validTimezone(tz) {
  // Intl accepts aliases (Asia/Calcutta, US/Eastern) that the canonical list
  // from supportedValuesOf leaves out, and a datacentre's tz can be any of them.
  try { new Intl.DateTimeFormat('en', { timeZone: tz }); return true; } catch { return false; }
}

// ── Tenant (datacentre) ─────────────────────────────────────────────
function getTenant(tenantId) {
  if (!tenantId) return null;
  _prep();
  return db.prepare('SELECT * FROM tenants WHERE id = ?').get(Number(tenantId)) || null;
}

/**
 * What a principal may do with a tenant's setup: 'write', 'read' or null.
 *
 *   owner         → write, everywhere (the platform owner)
 *   org_admin     → write for Sites in their organisation, and for the Site
 *                   they themselves sit in (an org_admin stranded on the
 *                   default tenant still manages the Site they hold — the same
 *                   fall-through lib/rack_access applies)
 *   site_manager  → write for their own Site
 *   member        → read for their own Site
 *   anyone else   → null; the router answers 404 so existence is not leaked
 */
function accessLevel(user, tenantId) {
  if (!user) return null;
  const t = getTenant(tenantId);
  if (!t) return null;
  const role = user.role;
  const own = Number(user.tenant_id ?? user.tenantId ?? 0);
  const org = Number(user.organization_id ?? user.organizationId ?? 0);
  if (role === 'owner') return 'write';
  if (role === 'org_admin' && org && Number(t.organization_id) === org) return 'write';
  if (own !== Number(t.id)) return null;
  if (role === 'org_admin' || role === 'site_manager') return 'write';
  return 'read';
}

function datacentreOf(t) {
  return {
    address: t.address ?? null,
    timezone: t.timezone ?? null,
    lat: t.lat ?? null,
    lng: t.lng ?? null,
    setup_completed_at: t.setup_completed_at ?? null,
  };
}

function updateDatacentre(tenantId, body = {}, userId = null) {
  _prep();
  const t = getTenant(tenantId);
  if (!t) throw new EstateError(404, 'Site not found');
  const address = _optString(body.address, 'address', 500);
  const timezone = _optString(body.timezone, 'timezone', 64);
  if (timezone && !_validTimezone(timezone)) throw new EstateError(400, 'Unknown timezone');
  const lat = _optNumber(body.lat, 'lat', -90, 90);
  const lng = _optNumber(body.lng, 'lng', -180, 180);
  const sets = [];
  const args = [];
  for (const [col, val] of [['address', address], ['timezone', timezone], ['lat', lat], ['lng', lng]]) {
    if (val !== undefined) { sets.push(`${col} = ?`); args.push(val); }
  }
  if (!sets.length) throw new EstateError(400, 'Nothing to update');
  args.push(Number(tenantId));
  db.prepare(`UPDATE tenants SET ${sets.join(', ')} WHERE id = ?`).run(...args);
  _touchSetupCompleted(tenantId);
  logger.info({ event: 'estate.datacentre_updated', tenantId, userId, fields: sets.length }, 'datacentre updated');
  return datacentreOf(getTenant(tenantId));
}

// ── Spaces ──────────────────────────────────────────────────────────
function getSpace(spaceId) {
  if (!spaceId) return null;
  _prep();
  return db.prepare('SELECT * FROM spaces WHERE id = ?').get(Number(spaceId)) || null;
}

function listSpaces(tenantId) {
  _prep();
  return db.prepare('SELECT * FROM spaces WHERE tenant_id = ? ORDER BY name').all(Number(tenantId));
}

/** The spaces of a tenant as a tree: roots with nested children. */
function spaceTree(tenantId) {
  const rows = listSpaces(tenantId);
  const byId = new Map(rows.map((r) => [r.id, { ...r, children: [] }]));
  const roots = [];
  for (const node of byId.values()) {
    const parent = node.parent_id != null ? byId.get(node.parent_id) : null;
    if (parent) parent.children.push(node); else roots.push(node);
  }
  return roots;
}

function _spaceInTenant(tenantId, spaceId) {
  const s = getSpace(spaceId);
  if (!s || Number(s.tenant_id) !== Number(tenantId)) throw new EstateError(404, 'Space not found');
  return s;
}

/** Is `candidateId` the space `ancestorId` itself, or somewhere beneath it? */
function _isSelfOrDescendant(candidateId, ancestorId) {
  const seen = new Set();
  let cur = candidateId;
  while (cur != null) {
    if (Number(cur) === Number(ancestorId)) return true;
    if (seen.has(cur)) return true; // a cycle already exists; refuse rather than loop
    seen.add(cur);
    cur = db.prepare('SELECT parent_id FROM spaces WHERE id = ?').get(cur)?.parent_id ?? null;
  }
  return false;
}

function _duplicateName(tenantId, parentId, name, exceptId = null) {
  const row = db.prepare(
    `SELECT id FROM spaces WHERE tenant_id = ? AND COALESCE(parent_id, 0) = ? AND name = ?`)
    .get(Number(tenantId), parentId == null ? 0 : Number(parentId), name);
  return row && Number(row.id) !== Number(exceptId) ? row : null;
}

const SPACE_KINDS = ['hall', 'floor', 'room', 'row', 'cage', 'other'];

/** The optional detail on a space: what kind of space it is and where it sits. */
function _spaceDetail(body) {
  const kind = _optString(body.kind, 'kind', 20);
  if (kind && !SPACE_KINDS.includes(kind)) throw new EstateError(400, `kind must be one of ${SPACE_KINDS.join(', ')}`);
  return {
    kind,
    floor: _optString(body.floor, 'floor', 40),
    room: _optString(body.room, 'room', 80),
    row: _optString(body.row, 'row', 40),
  };
}

function createSpace(tenantId, body = {}, userId = null) {
  _prep();
  if (!getTenant(tenantId)) throw new EstateError(404, 'Site not found');
  const name = _optString(body.name, 'name', 120);
  if (!name) throw new EstateError(400, 'name is required');
  const parentId = _optInt(body.parent_id, 'parent_id', 1, Number.MAX_SAFE_INTEGER) ?? null;
  if (parentId != null) _spaceInTenant(tenantId, parentId);
  const facilityId = _optString(body.facility_id, 'facility_id', 120) ?? null;
  const rackCount = _optInt(body.rack_count, 'rack_count', 0, 100000) ?? null;
  const detail = _spaceDetail(body);
  const source = _source(body.source);
  if (_duplicateName(tenantId, parentId, name)) {
    throw new EstateError(409, 'A space with that name already exists at this level');
  }
  const r = db.prepare(
    `INSERT INTO spaces (tenant_id, parent_id, name, facility_id, rack_count, kind, floor, room, row, source, created_by)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(Number(tenantId), parentId, name, facilityId, rackCount,
      detail.kind ?? null, detail.floor ?? null, detail.room ?? null, detail.row ?? null, source, userId);
  _touchSetupCompleted(tenantId);
  logger.info({ event: 'estate.space_created', tenantId, spaceId: r.lastInsertRowid, userId, source }, `space "${name}" created`);
  return getSpace(r.lastInsertRowid);
}

function updateSpace(tenantId, spaceId, body = {}, userId = null) {
  _prep();
  const s = _spaceInTenant(tenantId, spaceId);
  const name = _optString(body.name, 'name', 120);
  if (name === null) throw new EstateError(400, 'name cannot be empty');
  const parentId = _optInt(body.parent_id, 'parent_id', 1, Number.MAX_SAFE_INTEGER);
  if (parentId !== undefined && parentId !== null) {
    _spaceInTenant(tenantId, parentId);
    if (_isSelfOrDescendant(parentId, s.id)) {
      throw new EstateError(400, 'A space cannot be moved beneath itself');
    }
  }
  const facilityId = _optString(body.facility_id, 'facility_id', 120);
  const rackCount = _optInt(body.rack_count, 'rack_count', 0, 100000);
  const detail = _spaceDetail(body);

  const nextName = name ?? s.name;
  const nextParent = parentId === undefined ? s.parent_id : parentId;
  if (_duplicateName(tenantId, nextParent, nextName, s.id)) {
    throw new EstateError(409, 'A space with that name already exists at this level');
  }
  const sets = ["updated_at = datetime('now')"];
  const args = [];
  for (const [col, val] of [['name', name], ['parent_id', parentId], ['facility_id', facilityId], ['rack_count', rackCount],
    ['kind', detail.kind], ['floor', detail.floor], ['room', detail.room], ['row', detail.row]]) {
    if (val !== undefined) { sets.push(`${col} = ?`); args.push(val); }
  }
  args.push(s.id);
  db.prepare(`UPDATE spaces SET ${sets.join(', ')} WHERE id = ?`).run(...args);
  _touchSetupCompleted(tenantId);
  logger.info({ event: 'estate.space_updated', tenantId, spaceId: s.id, userId }, 'space updated');
  return getSpace(s.id);
}

/**
 * Refused (409) while anything still points at the space: a known rack, or a
 * child space. Nothing is cascaded — the admin removes the leaves first, and
 * a rack the camera has learned is never silently detached.
 */
function deleteSpace(tenantId, spaceId, userId = null) {
  _prep();
  const s = _spaceInTenant(tenantId, spaceId);
  const racks = db.prepare('SELECT COUNT(*) AS c FROM racks_known WHERE space_id = ?').get(s.id).c;
  if (racks > 0) throw new EstateError(409, `${racks} rack(s) are recorded in this space; move them first`);
  const kids = db.prepare('SELECT COUNT(*) AS c FROM spaces WHERE parent_id = ?').get(s.id).c;
  if (kids > 0) throw new EstateError(409, `${kids} space(s) sit inside this one; remove them first`);
  db.prepare('DELETE FROM spaces WHERE id = ?').run(s.id);
  logger.info({ event: 'estate.space_deleted', tenantId, spaceId: s.id, userId }, 'space deleted');
  return true;
}

// ── Racks known ─────────────────────────────────────────────────────
function listRacks(tenantId, spaceId = null) {
  _prep();
  if (spaceId != null) {
    return db.prepare('SELECT * FROM racks_known WHERE tenant_id = ? AND space_id = ? ORDER BY name, rack_id')
      .all(Number(tenantId), Number(spaceId));
  }
  return db.prepare('SELECT * FROM racks_known WHERE tenant_id = ? ORDER BY name, rack_id')
    .all(Number(tenantId));
}

function getRackByRackId(tenantId, rackId) {
  _prep();
  return db.prepare('SELECT * FROM racks_known WHERE tenant_id = ? AND rack_id = ?')
    .get(Number(tenantId), String(rackId)) || null;
}

/**
 * The rack this scan id belongs to, whichever site it is at.
 *
 * A rack id is a hash minted for one rack, so it needs no site to be found.
 * The site-scoped lookup above is the one to use wherever the caller knows the
 * site; this one is for the readers that do not - a photograph's report knows
 * the rack it is of and nothing about the estate around it, and a report headed
 * "Unidentified rack" whose every device carries the rack's name is what sent
 * me looking (the owner, 23 September 2026).
 */
function findRackByRackId(rackId) {
  _prep();
  if (!rackId) return null;
  return db.prepare('SELECT * FROM racks_known WHERE rack_id = ? ORDER BY id LIMIT 1')
    .get(String(rackId)) || null;
}

/**
 * Upsert by rack_id. A rack the tenant already knows is updated in place —
 * only the fields given are changed, so a scan binding (which knows no name)
 * never erases a name the admin typed. Returns { rack, created }.
 */
function upsertRack(tenantId, body = {}, userId = null) {
  _prep();
  if (!getTenant(tenantId)) throw new EstateError(404, 'Site not found');
  const rackId = typeof body.rack_id === 'string' ? body.rack_id.trim() : body.rack_id;
  if (!rackId) throw new EstateError(400, 'rack_id is required');
  if (!isValidRackId(rackId)) throw new EstateError(400, 'Invalid rack id');
  const spaceId = _optInt(body.space_id, 'space_id', 1, Number.MAX_SAFE_INTEGER);
  if (spaceId !== undefined && spaceId !== null) _spaceInTenant(tenantId, spaceId);
  const name = _optString(body.name, 'name', 120);
  const facilityId = _optString(body.facility_id, 'facility_id', 120);
  const uHeight = _optInt(body.u_height, 'u_height', 1, 100);
  const source = _source(body.source);

  const existing = getRackByRackId(tenantId, rackId);
  if (existing) {
    const sets = ["updated_at = datetime('now')"];
    const args = [];
    for (const [col, val] of [['space_id', spaceId], ['name', name], ['facility_id', facilityId], ['u_height', uHeight]]) {
      if (val !== undefined && val !== null) { sets.push(`${col} = ?`); args.push(val); }
    }
    args.push(existing.id);
    db.prepare(`UPDATE racks_known SET ${sets.join(', ')} WHERE id = ?`).run(...args);
    return { rack: db.prepare('SELECT * FROM racks_known WHERE id = ?').get(existing.id), created: false };
  }
  const r = db.prepare(
    `INSERT INTO racks_known (tenant_id, space_id, rack_id, name, facility_id, u_height, source, created_by)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(Number(tenantId), spaceId ?? null, rackId, name ?? null, facilityId ?? null, uHeight ?? 42, source, userId);
  _touchSetupCompleted(tenantId);
  logger.info({ event: 'estate.rack_known', tenantId, rackId, spaceId: spaceId ?? null, userId, source }, `rack ${rackId} recorded`);
  return { rack: db.prepare('SELECT * FROM racks_known WHERE id = ?').get(r.lastInsertRowid), created: true };
}

// ── Approver ────────────────────────────────────────────────────────
function getApprover(tenantId) {
  const t = getTenant(tenantId);
  if (!t) return null;
  if (t.approver_user_id) {
    const u = db.prepare('SELECT id, username, email FROM users WHERE id = ?').get(t.approver_user_id);
    return { user_id: t.approver_user_id, email: u?.email ?? null, username: u?.username ?? null };
  }
  if (t.approver_email) return { user_id: null, email: t.approver_email, username: null };
  return null;
}

/**
 * { user_id } — must be a member of this Site, or an org_admin of its
 * organisation. { email } — anyone; for an approver who is not a user yet.
 * One or the other; setting one clears the other.
 */
function setApprover(tenantId, body = {}, userId = null) {
  _prep();
  const t = getTenant(tenantId);
  if (!t) throw new EstateError(404, 'Site not found');
  const hasUser = body.user_id !== undefined && body.user_id !== null && body.user_id !== '';
  const hasEmail = body.email !== undefined && body.email !== null && body.email !== '';
  if (hasUser === hasEmail) throw new EstateError(400, 'Give either user_id or email');
  if (hasUser) {
    const uid = _optInt(body.user_id, 'user_id', 1, Number.MAX_SAFE_INTEGER);
    const u = db.prepare('SELECT id, role, tenant_id, organization_id, active FROM users WHERE id = ?').get(uid);
    const inSite = u && Number(u.tenant_id) === Number(t.id);
    const orgAdmin = u && u.role === 'org_admin' && t.organization_id != null
      && Number(u.organization_id) === Number(t.organization_id);
    if (!u || u.active === 0 || !(inSite || orgAdmin)) {
      throw new EstateError(400, 'The approver must be a member of this Site or an admin of its organisation');
    }
    db.prepare('UPDATE tenants SET approver_user_id = ?, approver_email = NULL WHERE id = ?').run(u.id, t.id);
  } else {
    const email = String(body.email).trim().toLowerCase();
    if (!EMAIL_RE.test(email) || email.length > 254) throw new EstateError(400, 'Invalid email');
    db.prepare('UPDATE tenants SET approver_email = ?, approver_user_id = NULL WHERE id = ?').run(email, t.id);
  }
  _touchSetupCompleted(tenantId);
  logger.info({ event: 'estate.approver_set', tenantId, userId, byUser: hasUser }, 'approver set');
  return getApprover(tenantId);
}

// ── Rules ───────────────────────────────────────────────────────────
const RULE_DEFAULTS = {
  approve_before_write: true,
  never_delete: true,
  photo_retention_days: 90,
  default_u_height: 42,
  u_from_bottom: true,
};

function getRules(tenantId) {
  const t = getTenant(tenantId);
  if (!t) return null;
  const r = db.prepare('SELECT * FROM tenant_rules WHERE tenant_id = ?').get(t.id);
  return {
    ...RULE_DEFAULTS,
    ...(r ? {
      photo_retention_days: r.photo_retention_days,
      default_u_height: r.default_u_height,
      u_from_bottom: !!r.u_from_bottom,
    } : {}),
    // Product rules: reported as accepted, never as configurable.
    approve_before_write: true,
    never_delete: true,
    accepted_at: t.rules_accepted_at ?? null,
    accepted_by: t.rules_accepted_by ?? null,
  };
}

function acceptRules(tenantId, body = {}, userId = null) {
  _prep();
  const t = getTenant(tenantId);
  if (!t) throw new EstateError(404, 'Site not found');
  if (body.accepted !== true) throw new EstateError(400, 'The rules must be accepted (accepted: true)');
  const current = getRules(tenantId);
  const retention = _optInt(body.photo_retention_days, 'photo_retention_days', 1, 3650) ?? current.photo_retention_days;
  const uHeight = _optInt(body.default_u_height, 'default_u_height', 1, 100) ?? current.default_u_height;
  let uFromBottom = current.u_from_bottom;
  if (body.u_from_bottom !== undefined && body.u_from_bottom !== null) {
    if (![true, false, 0, 1, '0', '1'].includes(body.u_from_bottom)) {
      throw new EstateError(400, 'u_from_bottom must be true or false');
    }
    uFromBottom = body.u_from_bottom === true || body.u_from_bottom === 1 || body.u_from_bottom === '1';
  }
  const source = _source(body.source);
  db.transaction(() => {
    db.prepare(`
      INSERT INTO tenant_rules (tenant_id, photo_retention_days, default_u_height, u_from_bottom, source, created_by)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(tenant_id) DO UPDATE SET
        photo_retention_days = excluded.photo_retention_days,
        default_u_height = excluded.default_u_height,
        u_from_bottom = excluded.u_from_bottom,
        updated_at = datetime('now')
    `).run(t.id, retention, uHeight, uFromBottom ? 1 : 0, source, userId);
    db.prepare(`UPDATE tenants SET rules_accepted_at = datetime('now'), rules_accepted_by = ? WHERE id = ?`)
      .run(userId, t.id);
  })();
  _touchSetupCompleted(tenantId);
  logger.info({ event: 'estate.rules_accepted', tenantId, userId }, 'rules accepted');
  return getRules(tenantId);
}

// ── Completeness ────────────────────────────────────────────────────
function _mandatory(tenantId) {
  _prep();
  const t = getTenant(tenantId);
  if (!t) return null;
  const counts = {
    spaces: db.prepare('SELECT COUNT(*) AS c FROM spaces WHERE tenant_id = ?').get(t.id).c,
    racksTyped: db.prepare('SELECT COALESCE(SUM(rack_count), 0) AS c FROM spaces WHERE tenant_id = ?').get(t.id).c,
    racksKnown: db.prepare('SELECT COUNT(*) AS c FROM racks_known WHERE tenant_id = ?').get(t.id).c,
  };
  // Where the Site is. Setup asks for the Site's location and no longer for
  // its spaces (21 Sep 2026); a Site set up the older way, with a space that
  // holds racks or a rack already known, still counts as placed.
  const location = !!(t.address && String(t.address).trim())
    || counts.racksKnown > 0
    || !!db.prepare('SELECT 1 FROM spaces WHERE tenant_id = ? AND rack_count > 0 LIMIT 1').get(t.id);
  const approver = !!(t.approver_user_id || t.approver_email);
  const rules = !!t.rules_accepted_at;
  return { tenant: t, counts, mandatory: { location, approver, rules }, canScan: location && approver && rules };
}

/**
 * The three mandatory facts, whether the tenant may scan, and what else has
 * been filled in. `optional` reads what exists: conventions, vendors and
 * people from the profile sections (lib/estate_profile).
 */
function completeness(tenantId) {
  const m = _mandatory(tenantId);
  if (!m) return null;
  const f = profile.flags(m.tenant.id);
  return {
    mandatory: m.mandatory,
    canScan: m.canScan,
    optional: {
      conventions: f.conventions,
      vendors: f.vendors,
      people: f.people,
    },
    counts: m.counts,
  };
}

/** Stamp setup_completed_at the first time the three mandatory facts hold. */
function _touchSetupCompleted(tenantId) {
  const m = _mandatory(tenantId);
  if (!m || !m.canScan || m.tenant.setup_completed_at) return;
  db.prepare(`UPDATE tenants SET setup_completed_at = datetime('now') WHERE id = ? AND setup_completed_at IS NULL`)
    .run(m.tenant.id);
  logger.info({ event: 'estate.setup_completed', tenantId }, 'setup complete: the Site may scan');
}

/** Everything GET /api/setup/:tenantId returns. */
function snapshot(tenantId) {
  const t = getTenant(tenantId);
  if (!t) return null;
  return {
    tenant: { id: t.id, name: t.name, slug: t.slug ?? null, organization_id: t.organization_id ?? null },
    datacentre: datacentreOf(t),
    spaces: spaceTree(t.id),
    racks: listRacks(t.id),
    approver: getApprover(t.id),
    rules: getRules(t.id),
    // Every optional section, so one call fills the whole page.
    profile: profile.tenantProfile(t.id),
    completeness: completeness(t.id),
  };
}

// ── Per-principal state (what the app gates on) ───────
function _isAdmin(user) {
  return user && (user.role === 'owner' || user.role === 'org_admin');
}

/**
 * For an owner / org_admin: every Site they oversee, each with completeness
 * and whether its organisation's profile is filled, needsSetup when any of
 * them (or the absence of any) lacks the mandatory three, and `profile.org`
 * for the caller's own organisation (false for the owner, who has none). For
 * everyone else: the open gate and nothing about any Site.
 */
function stateFor(user) {
  _prep();
  if (_isAdmin(user)) {
    const ids = tenantLib.visibleTenantIds(user);
    const rows = ids === null
      ? db.prepare('SELECT id, name, organization_id FROM tenants ORDER BY name').all()
      : ids.length
        ? db.prepare(`SELECT id, name, organization_id FROM tenants WHERE id IN (${ids.map(() => '?').join(',')}) ORDER BY name`).all(...ids)
        : [];
    const tenants = rows.map((t) => {
      const c = completeness(t.id);
      return {
        id: t.id, name: t.name, completeness: c, canScan: !!c?.canScan,
        profile: { org: profile.orgProfileComplete(t.organization_id) },
      };
    });
    // The platform owner approves organisations; the organisation admin does
    // the setup. The owner sees every Site's completeness but is never sent
    // into setup, or the main account would be gated by every customer's gap.
    const owner = user?.role === 'owner';
    const ownOrg = user?.organization_id ?? user?.organizationId ?? null;
    return {
      needsSetup: !owner && (tenants.length === 0 || tenants.some((t) => !t.canScan)),
      blocked: false,
      profile: { org: profile.orgProfileComplete(ownOrg) },
      tenants,
    };
  }
  // A member is never gated. The organisation admin finishes setup when the
  // organisation is created and invites people afterwards, so a technician
  // who signs in walks straight into the app. `blocked` stays in the shape
  // for older clients and is always false.
  return { needsSetup: false, blocked: false, reason: null };
}

/** The two gate fields alone — cheap enough to ride on every whoami. */
function setupSummary(user) {
  _prep();
  if (_isAdmin(user)) {
    const ids = tenantLib.visibleTenantIds(user);
    const rows = ids === null
      ? db.prepare('SELECT id FROM tenants').all()
      : ids.length
        ? db.prepare(`SELECT id FROM tenants WHERE id IN (${ids.map(() => '?').join(',')})`).all(...ids)
        : [];
    const needsSetup = user?.role !== 'owner'
      && (rows.length === 0 || rows.some((t) => !_mandatory(t.id)?.canScan));
    return { needsSetup, blocked: false, reason: null };
  }
  const s = stateFor(user);
  return { needsSetup: s.needsSetup, blocked: s.blocked, reason: s.reason };
}

module.exports = {
  EstateError,
  SOURCES,
  getTenant,
  accessLevel,
  updateDatacentre,
  getSpace,
  listSpaces,
  spaceTree,
  createSpace,
  updateSpace,
  deleteSpace,
  listRacks,
  getRackByRackId,
  findRackByRackId,
  upsertRack,
  getApprover,
  setApprover,
  getRules,
  acceptRules,
  completeness,
  snapshot,
  stateFor,
  setupSummary,
};
