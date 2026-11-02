/**
 * Organization data settings, kept on the organizations row.
 *
 *   never_train     1: this organization's corrections and crops are never
 *                   written to its training store (the correction memory the
 *                   engine learns from). Corrections still land on their scan.
 *   retention_days  when set, a daily job deletes this organization's scans
 *                   older than that many days (through lib/store.js).
 *   online_lookups  1 (the default): the specifications of a model are
 *                   looked up on the maker's public pages and by web search.
 *                   0: no request leaves the server for them; only what the
 *                   server already holds is shown.
 *
 * A new organization starts with the pilot's safe defaults (pilotDefaults()):
 * never train ON and scans kept 90 days (RT_DEFAULT_RETENTION_DAYS changes
 * the number), online lookups ON. The admin can change all three later.
 *
 * The columns are added lazily and idempotently, the way lib/estate.js adds
 * its columns. Same database as every other module: lib/data_paths.js.
 */
'use strict';

const Database = require('better-sqlite3');
const { AUTH_DB } = require('./data_paths');

const MAX_RETENTION_DAYS = 3650;
const DEFAULT_RETENTION_DAYS = 90;

/** What a new organization starts with. */
function pilotDefaults() {
  const n = Number(process.env.RT_DEFAULT_RETENTION_DAYS);
  const days = Number.isInteger(n) && n >= 1 && n <= MAX_RETENTION_DAYS ? n : DEFAULT_RETENTION_DAYS;
  return { neverTrain: true, retentionDays: days, onlineLookups: true };
}

let _db = null;
function db() {
  if (_db) return _db;
  _db = new Database(AUTH_DB);
  _db.pragma('journal_mode = WAL');
  return _db;
}

/** Add the settings columns on a database handle (idempotent). False when there is no organizations table yet. */
function ensureColumns(d) {
  if (!d.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organizations'").get()) return false;
  const has = (col) => d.prepare('PRAGMA table_info(organizations)').all().some((c) => c.name === col);
  if (!has('never_train')) d.exec('ALTER TABLE organizations ADD COLUMN never_train INTEGER NOT NULL DEFAULT 0');
  if (!has('retention_days')) d.exec('ALTER TABLE organizations ADD COLUMN retention_days INTEGER');
  if (!has('online_lookups')) d.exec('ALTER TABLE organizations ADD COLUMN online_lookups INTEGER NOT NULL DEFAULT 1');
  return true;
}

/**
 * Give a just-created organization the pilot defaults, on the caller's own
 * database handle (so it works inside the caller's transaction).
 */
function applyPilotDefaults(d, orgId) {
  if (!ensureColumns(d)) return null;
  const def = pilotDefaults();
  d.prepare('UPDATE organizations SET never_train = ?, retention_days = ?, online_lookups = ? WHERE id = ?')
    .run(def.neverTrain ? 1 : 0, def.retentionDays, def.onlineLookups ? 1 : 0, Number(orgId));
  return def;
}

let _ready = false;
function prep() {
  if (_ready) return;
  if (!ensureColumns(db())) return;
  _ready = true;
}

function orgRow(orgId) {
  prep();
  if (!_ready || orgId == null) return null;
  return db().prepare('SELECT * FROM organizations WHERE id = ?').get(Number(orgId)) || null;
}

/** { neverTrain, retentionDays } for one organization, or null when there is no such organization. */
function get(orgId) {
  const row = orgRow(orgId);
  if (!row) return null;
  return {
    neverTrain: Number(row.never_train) === 1,
    retentionDays: row.retention_days == null ? null : Number(row.retention_days),
    onlineLookups: row.online_lookups == null ? true : Number(row.online_lookups) === 1,
  };
}

class SettingsError extends Error {
  constructor(message) { super(message); this.status = 400; }
}

/**
 * Check a body and turn it into the two values. Only the fields present are
 * changed; a missing field keeps what is stored.
 */
function validate(body, current) {
  const b = body || {};
  const out = { ...current };
  if (b.neverTrain !== undefined) {
    if (typeof b.neverTrain !== 'boolean') throw new SettingsError('neverTrain must be true or false.');
    out.neverTrain = b.neverTrain;
  }
  if (b.onlineLookups !== undefined) {
    if (typeof b.onlineLookups !== 'boolean') throw new SettingsError('onlineLookups must be true or false.');
    out.onlineLookups = b.onlineLookups;
  }
  if (b.retentionDays !== undefined) {
    if (b.retentionDays === null || b.retentionDays === '') {
      out.retentionDays = null;
    } else {
      const n = Number(b.retentionDays);
      if (!Number.isInteger(n) || n < 1 || n > MAX_RETENTION_DAYS) {
        throw new SettingsError(`retentionDays must be a whole number of days from 1 to ${MAX_RETENTION_DAYS}, or empty to keep scans for ever.`);
      }
      out.retentionDays = n;
    }
  }
  return out;
}

function set(orgId, body) {
  const current = get(orgId);
  if (!current) return null;
  const next = validate(body, current);
  db().prepare('UPDATE organizations SET never_train = ?, retention_days = ?, online_lookups = ? WHERE id = ?')
    .run(next.neverTrain ? 1 : 0, next.retentionDays, next.onlineLookups ? 1 : 0, Number(orgId));
  return get(orgId);
}

/**
 * True when this organization lets the server look models up online. An
 * organization that cannot be read, or no organization at all, is treated as
 * the default (on), which is how the lookups behaved before the setting.
 */
function onlineLookups(orgId) {
  if (orgId == null) return true;
  try { const g = get(orgId); return g ? g.onlineLookups !== false : true; } catch { return true; }
}

/** True when this organization chose that nothing of it is ever trained on. */
function neverTrain(orgId) {
  try { return !!get(orgId)?.neverTrain; } catch { return false; }
}

/** Every organization with a retention period: [{ id, name, retentionDays }]. */
function withRetention() {
  prep();
  if (!_ready) return [];
  return db().prepare('SELECT id, name, retention_days FROM organizations WHERE retention_days IS NOT NULL')
    .all().map((r) => ({ id: r.id, name: r.name, retentionDays: Number(r.retention_days) }));
}

module.exports = {
  get, set, neverTrain, onlineLookups, withRetention, pilotDefaults, applyPilotDefaults, ensureColumns,
  SettingsError, MAX_RETENTION_DAYS, DEFAULT_RETENTION_DAYS,
};
