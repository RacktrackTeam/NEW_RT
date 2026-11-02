/**
 * Profile: what else is known about an organisation and a datacentre.
 *
 * lib/estate.js holds the estate tree and the three mandatory facts. This
 * module holds the rest of first-run onboarding — every part optional, every
 * part editable later under organisation settings:
 *
 *   organisation profile   columns on `organizations`: short_code, timezone,
 *                          country, website, phone, industry, logo_data, and
 *                          who last touched them
 *   tenant profile         one row per (tenant, section) in `tenant_profile`:
 *                          contacts, vendors, conventions, facility. Each
 *                          section is a JSON document replaced whole by its
 *                          PUT, and each row says who wrote it, when, and
 *                          from what source, like every other row in the
 *                          estate.
 *
 * Nothing is invented: a field the admin did not give is stored as null, a
 * default that exists (the short code) is derived from what the row already
 * says and can be changed.
 *
 * Same database and env override as lib/estate.js and lib/tenant.js, for the
 * same reason: tests seed a throwaway file and point every module at it.
 */

const path = require('path');
const fs = require('fs');
const vm = require('vm');
const Database = require('better-sqlite3');
const { logger } = require('./observability');

// RACKTRACK_AUTH_DB, else data/db/auth.db: see lib/data_paths.js.
const dbPath = require('./data_paths').AUTH_DB;
const db = new Database(dbPath);
db.pragma('journal_mode = WAL');

// The faceplate reader's maker list sits in pipeline/switch_ocr/.
const VENDORS_FILE = path.join(__dirname, '..', '..', 'pipeline', 'switch_ocr', 'vendors.json');

const SOURCES = ['typed', 'imported', 'learned'];
const SECTIONS = ['contacts', 'vendors', 'conventions', 'facility'];
// spoc: the Site's single point of contact, the one contact setup asks for.
// approver: the person who signs off on work at the Site.
const CONTACT_ROLES = ['spoc', 'approver', 'on_site', 'escalation', 'facilities', 'security', 'vendor'];
const FACES = ['front', 'rear', 'both'];
const PATTERN_FIELDS = ['rack_pattern', 'device_pattern', 'asset_pattern', 'port_pattern'];
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const LOGO_MAX_BYTES = 200 * 1024;
const PATTERN_MAX = 200;
const EXAMPLE_MAX = 200;
const CHECK_BUDGET_MS = 100;

/** A validation or state error the router turns into an HTTP status. */
class ProfileError extends Error {
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
  _ensureColumn('organizations', 'short_code', 'short_code TEXT');
  _ensureColumn('organizations', 'timezone', 'timezone TEXT');
  _ensureColumn('organizations', 'country', 'country TEXT');
  _ensureColumn('organizations', 'website', 'website TEXT');
  _ensureColumn('organizations', 'primary_contact_name', 'primary_contact_name TEXT');
  _ensureColumn('organizations', 'primary_contact_email', 'primary_contact_email TEXT');
  _ensureColumn('organizations', 'phone', 'phone TEXT');
  _ensureColumn('organizations', 'industry', 'industry TEXT');
  _ensureColumn('organizations', 'logo_data', 'logo_data TEXT');
  _ensureColumn('organizations', 'profile_updated_at', 'profile_updated_at TEXT');
  _ensureColumn('organizations', 'profile_updated_by', 'profile_updated_by INTEGER REFERENCES users(id)');
  db.exec(`
    CREATE UNIQUE INDEX IF NOT EXISTS uq_organizations_short_code
      ON organizations(short_code) WHERE short_code IS NOT NULL;

    -- One row per (tenant, section). data is the section's JSON document as
    -- validated.
    CREATE TABLE IF NOT EXISTS tenant_profile (
      tenant_id  INTEGER NOT NULL REFERENCES tenants(id),
      section    TEXT    NOT NULL,
      data       TEXT    NOT NULL,
      source     TEXT    NOT NULL DEFAULT 'typed',
      created_by INTEGER REFERENCES users(id),
      created_at TEXT    NOT NULL DEFAULT (datetime('now')),
      updated_by INTEGER REFERENCES users(id),
      updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (tenant_id, section)
    );
  `);
  _backfillShortCodes();
  _ready = true;
  logger.info({ event: 'profile.schema_ready' }, 'profile schema ready');
}

// ── Small validators ────────────────────────────────────────────────
function _optString(v, field, max) {
  if (v === undefined) return undefined;
  if (v === null || v === '') return null;
  if (typeof v !== 'string') throw new ProfileError(400, `${field} must be text`);
  const s = v.trim();
  if (s.length > max) throw new ProfileError(400, `${field} is too long (max ${max})`);
  return s || null;
}
function _reqString(v, field, max) {
  const s = _optString(v, field, max);
  if (!s) throw new ProfileError(400, `${field} is required`);
  return s;
}
function _oneOf(v, field, allowed, { lower = false } = {}) {
  if (v === undefined || v === null || v === '') return null;
  const s = lower ? String(v).toLowerCase() : String(v);
  if (!allowed.includes(s)) throw new ProfileError(400, `${field} must be one of ${allowed.join(', ')}`);
  return s;
}
function _optBool(v, field) {
  if (v === undefined || v === null || v === '') return null;
  if (![true, false, 0, 1, '0', '1'].includes(v)) throw new ProfileError(400, `${field} must be true or false`);
  return v === true || v === 1 || v === '1';
}
function _email(v, field) {
  const s = _optString(v, field, 254);
  if (!s) return null;
  const e = s.toLowerCase();
  if (!EMAIL_RE.test(e)) throw new ProfileError(400, `${field} is not a valid email address`);
  return e;
}
function _phone(v, field) {
  const s = _optString(v, field, 40);
  if (s && !/^\+?[0-9][0-9 ()./-]{2,}$/.test(s)) throw new ProfileError(400, `${field} is not a valid phone number`);
  return s ?? null;
}
function _stringList(v, field, maxItems, maxLen) {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v)) throw new ProfileError(400, `${field} must be a list`);
  if (v.length > maxItems) throw new ProfileError(400, `${field} has too many entries (max ${maxItems})`);
  const out = [];
  v.forEach((item, i) => {
    const s = _optString(item, `${field}[${i}]`, maxLen);
    if (s) out.push(s);
  });
  return out;
}
function _object(v, field) {
  if (v === undefined || v === null) return {};
  if (typeof v !== 'object' || Array.isArray(v)) throw new ProfileError(400, `${field} must be an object`);
  return v;
}
function _list(v, field, maxItems) {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v)) throw new ProfileError(400, `${field} must be a list`);
  if (v.length > maxItems) throw new ProfileError(400, `${field} has too many entries (max ${maxItems})`);
  return v.map((item, i) => _object(item, `${field}[${i}]`));
}
function _source(v) {
  if (v === undefined || v === null || v === '') return 'typed';
  if (!SOURCES.includes(v)) throw new ProfileError(400, `source must be one of ${SOURCES.join(', ')}`);
  return v;
}
function _validTimezone(tz) {
  // Same rule as lib/estate.js: Intl accepts aliases the canonical list leaves out.
  try { new Intl.DateTimeFormat('en', { timeZone: tz }); return true; } catch { return false; }
}
function _validWebsite(s) {
  const withScheme = /^https?:\/\//i.test(s) ? s : `https://${s}`;
  try {
    const u = new URL(withScheme);
    return /^https?:$/.test(u.protocol) && /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(u.hostname);
  } catch { return false; }
}

// ── Organisation profile ────────────────────────────────────────────
/**
 * The default short code: the slug without the 4-hex suffix auth._slug
 * appends for uniqueness, upper-cased, letters and digits only, 8 chars.
 * "acme-datacentres-3f2a" → "ACMEDATA". Falls back to the name, then ORG<id>.
 */
function _deriveShortCode(org) {
  const base = String(org.slug || org.name || '').replace(/-[0-9a-f]{4}$/i, '');
  const code = base.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
  return code || `ORG${org.id}`;
}
function _shortCodeTaken(code, orgId) {
  return !!db.prepare('SELECT 1 FROM organizations WHERE short_code = ? AND id != ?').get(code, Number(orgId));
}
function _freeShortCode(code, orgId) {
  const candidates = [code, `${code}-${orgId}`, `ORG${orgId}`];
  for (const c of candidates) if (!_shortCodeTaken(c, orgId)) return c;
  for (let n = 2; ; n += 1) {
    const c = `ORG${orgId}-${n}`;
    if (!_shortCodeTaken(c, orgId)) return c;
  }
}
function _backfillShortCodes() {
  const rows = db.prepare('SELECT * FROM organizations WHERE short_code IS NULL').all();
  for (const org of rows) {
    db.prepare('UPDATE organizations SET short_code = ? WHERE id = ? AND short_code IS NULL')
      .run(_freeShortCode(_deriveShortCode(org), org.id), org.id);
  }
  if (rows.length) logger.info({ event: 'profile.short_codes_derived', count: rows.length }, 'short codes derived from slugs');
}

function getOrganization(orgId) {
  if (!orgId) return null;
  _prep();
  const o = db.prepare('SELECT * FROM organizations WHERE id = ?').get(Number(orgId)) || null;
  if (o && !o.short_code) {
    // Created after the backfill ran (auth.createOrganization does not know
    // about short codes): derive it now, the first time anyone looks.
    db.prepare('UPDATE organizations SET short_code = ? WHERE id = ? AND short_code IS NULL')
      .run(_freeShortCode(_deriveShortCode(o), o.id), o.id);
    return db.prepare('SELECT * FROM organizations WHERE id = ?').get(o.id);
  }
  return o;
}

/**
 * What a principal may do with an organisation's profile:
 *   owner, org_admin of the organisation → 'write'
 *   anyone else in the organisation     → 'member' (the router answers 403:
 *                                          they already know it exists)
 *   anyone else                         → null (the router answers 404)
 */
function orgAccessLevel(user, orgId) {
  if (!user) return null;
  const o = getOrganization(orgId);
  if (!o) return null;
  if (user.role === 'owner') return 'write';
  const own = Number(user.organization_id ?? user.organizationId ?? 0);
  if (!own || own !== Number(o.id)) return null;
  return user.role === 'org_admin' ? 'write' : 'member';
}

function orgProfileOf(o) {
  return {
    id: o.id,
    name: o.name,
    slug: o.slug ?? null,
    short_code: o.short_code ?? null,
    timezone: o.timezone ?? null,
    country: o.country ?? null,
    website: o.website ?? null,
    phone: o.phone ?? null,
    industry: o.industry ?? null,
    logo_data: o.logo_data ?? null,
    primary_contact_name: o.primary_contact_name ?? null,
    primary_contact_email: o.primary_contact_email ?? null,
    profile_updated_at: o.profile_updated_at ?? null,
    profile_updated_by: o.profile_updated_by ?? null,
  };
}

function orgProfile(orgId) {
  const o = getOrganization(orgId);
  return o ? orgProfileOf(o) : null;
}

/** The organisation profile counts as filled when timezone and country are set. */
function orgProfileComplete(orgId) {
  const o = getOrganization(orgId);
  return !!(o && o.timezone && o.country);
}

function _logo(v) {
  if (v === undefined) return undefined;
  if (v === null || v === '') return null;
  if (typeof v !== 'string') throw new ProfileError(400, 'logo_data must be a data: URI');
  const m = /^data:image\/(png|jpeg|gif|webp|svg\+xml);base64,([A-Za-z0-9+/]+={0,2})$/.exec(v);
  if (!m) throw new ProfileError(400, 'logo_data must be a base64 data: URI of a PNG, JPEG, GIF, WebP or SVG image');
  const bytes = Buffer.from(m[2], 'base64').length;
  if (bytes > LOGO_MAX_BYTES) {
    throw new ProfileError(400, `The logo is ${Math.ceil(bytes / 1024)} KB; the limit is ${LOGO_MAX_BYTES / 1024} KB`);
  }
  return v;
}

function updateOrgProfile(orgId, body = {}, userId = null) {
  _prep();
  const o = getOrganization(orgId);
  if (!o) throw new ProfileError(404, 'Organisation not found');
  const sets = [];
  const args = [];
  const put = (col, val) => { if (val !== undefined) { sets.push(`${col} = ?`); args.push(val); } };

  const shortCode = _optString(body.short_code, 'short_code', 16);
  if (shortCode === null) throw new ProfileError(400, 'short_code cannot be empty');
  if (shortCode !== undefined) {
    const code = shortCode.toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9-]{1,15}$/.test(code)) {
      throw new ProfileError(400, 'short_code must be 2 to 16 letters, digits or dashes');
    }
    if (_shortCodeTaken(code, o.id)) throw new ProfileError(409, 'That short code is already used by another organisation');
    put('short_code', code);
  }
  const timezone = _optString(body.timezone, 'timezone', 64);
  if (timezone && !_validTimezone(timezone)) throw new ProfileError(400, 'Unknown timezone');
  put('timezone', timezone);
  let country = _optString(body.country, 'country', 80);
  if (country && /^[A-Za-z]{2}$/.test(country)) country = country.toUpperCase();
  put('country', country);
  const website = _optString(body.website, 'website', 200);
  if (website && !_validWebsite(website)) throw new ProfileError(400, 'website must be a web address');
  put('website', website);
  if (body.phone !== undefined) put('phone', _phone(body.phone, 'phone'));
  put('industry', _optString(body.industry, 'industry', 80));
  put('logo_data', _logo(body.logo_data));
  put('primary_contact_name', _optString(body.primary_contact_name, 'primary_contact_name', 120));
  const pcEmail = _optString(body.primary_contact_email, 'primary_contact_email', 200);
  if (pcEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(pcEmail)) throw new ProfileError(400, 'primary_contact_email must be an email address');
  put('primary_contact_email', pcEmail);
  if (!sets.length) throw new ProfileError(400, 'Nothing to update');

  sets.push("profile_updated_at = datetime('now')", 'profile_updated_by = ?');
  args.push(userId, o.id);
  db.prepare(`UPDATE organizations SET ${sets.join(', ')} WHERE id = ?`).run(...args);
  logger.info({ event: 'profile.org_updated', orgId: o.id, userId, fields: sets.length - 2 }, 'organisation profile updated');
  return orgProfileOf(getOrganization(o.id));
}

// ── Conventions: pattern rule ───────────────────────────────────────
const REGEX_HINT = /[\\^$[\]()|{}+?*]/;

/**
 * A convention pattern is either a regular expression or a literal template.
 *
 *   regex    when wrapped in slashes (/…/ or /…/i), or when it contains any of
 *            \ ^ $ [ ] ( ) | { } + ? *  — the characters a template never needs
 *   literal  otherwise: # stands for a digit, A for a letter, every other
 *            character for itself (so "RK-####" matches RK-0001, and "A##"
 *            matches R01 but not RACK)
 *
 * "Whatever parses as a regex" would never fall through — RK-#### parses —
 * so the split is on the characters instead. Both kinds must match the
 * WHOLE example; a regex is anchored for you (^ and $ you wrote still work).
 */
function compilePattern(pattern) {
  const p = String(pattern);
  const slashed = /^\/(.+)\/(i?)$/.exec(p);
  if (slashed || REGEX_HINT.test(p)) {
    const src = slashed ? slashed[1] : p;
    const flags = slashed ? slashed[2] : '';
    try {
      return { ok: true, mode: 'regex', re: new RegExp(`^(?:${src})$`, flags) };
    } catch (err) {
      const reason = String(err.message).replace(/^Invalid regular expression: \/.*\/[a-z]*: /, '');
      return { ok: false, mode: 'regex', reason: `Not a valid regular expression: ${reason}` };
    }
  }
  const src = p.split('').map((ch) => {
    if (ch === '#') return '[0-9]';
    if (ch === 'A') return '[A-Za-z]';
    return ch.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
  }).join('');
  return { ok: true, mode: 'literal', re: new RegExp(`^${src}$`) };
}

/**
 * POST …/conventions/check { pattern, example } → { ok, mode, matches, reason }.
 * ok:false means the pattern itself cannot be used (and says why); the HTTP
 * status stays 200 so a form can show the reason live without special-casing.
 * The match runs under a time budget: a pattern that backtracks for ever is
 * reported, not waited for.
 */
function checkPattern(body = {}) {
  const pattern = typeof body.pattern === 'string' ? body.pattern.trim() : '';
  if (!pattern) throw new ProfileError(400, 'pattern is required');
  if (pattern.length > PATTERN_MAX) throw new ProfileError(400, `pattern is too long (max ${PATTERN_MAX})`);
  const example = body.example === undefined || body.example === null ? '' : String(body.example);
  if (example.length > EXAMPLE_MAX) throw new ProfileError(400, `example is too long (max ${EXAMPLE_MAX})`);

  const c = compilePattern(pattern);
  if (!c.ok) return { ok: false, mode: c.mode, matches: false, reason: c.reason };
  if (!example) return { ok: true, mode: c.mode, matches: false, reason: 'Give an example to test against' };
  let matches;
  try {
    matches = vm.runInNewContext('re.test(s)', { re: c.re, s: example }, { timeout: CHECK_BUDGET_MS });
  } catch (err) {
    if (err.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT') {
      return { ok: false, mode: c.mode, matches: false, reason: 'This pattern takes too long to test; simplify it' };
    }
    throw err;
  }
  const what = c.mode === 'regex' ? 'the regular expression' : 'the template';
  return {
    ok: true,
    mode: c.mode,
    matches: !!matches,
    reason: matches ? `"${example}" matches ${what}` : `"${example}" does not match ${what}`,
  };
}

// ── Tenant profile sections ─────────────────────────────────────────
function _contacts(v) {
  return _list(v, 'contacts', 50).map((c, i) => {
    const role = _oneOf(c.role, `contacts[${i}].role`, CONTACT_ROLES);
    if (!role) throw new ProfileError(400, `contacts[${i}].role is required (${CONTACT_ROLES.join(', ')})`);
    return {
      name: _reqString(c.name, `contacts[${i}].name`, 120),
      role,
      email: _email(c.email, `contacts[${i}].email`),
      phone: _phone(c.phone, `contacts[${i}].phone`),
      hours: _optString(c.hours, `contacts[${i}].hours`, 120) ?? null,
      notes: _optString(c.notes, `contacts[${i}].notes`, 500) ?? null,
    };
  });
}

function _vendors(v) {
  return _list(v, 'vendors', 100).map((r, i) => ({
    name: _reqString(r.name, `vendors[${i}].name`, 120),
    models: _stringList(r.models, `vendors[${i}].models`, 50, 80),
    contact_name: _optString(r.contact_name, `vendors[${i}].contact_name`, 120) ?? null,
    contact_email: _email(r.contact_email, `vendors[${i}].contact_email`),
    contact_phone: _phone(r.contact_phone, `vendors[${i}].contact_phone`),
    support_ref: _optString(r.support_ref, `vendors[${i}].support_ref`, 120) ?? null,
  }));
}

function _conventions(v) {
  const o = _object(v, 'conventions');
  const out = {};
  for (const f of PATTERN_FIELDS) {
    const p = _optString(o[f], f, PATTERN_MAX) ?? null;
    if (p) {
      const c = compilePattern(p);
      if (!c.ok) throw new ProfileError(400, `${f}: ${c.reason}`);
    }
    out[f] = p;
  }
  out.cable_colours = _list(o.cable_colours, 'cable_colours', 40).map((c, i) => ({
    color: _reqString(c.color, `cable_colours[${i}].color`, 40),
    meaning: _optString(c.meaning, `cable_colours[${i}].meaning`, 120) ?? null,
  }));
  out.u_from_bottom = _optBool(o.u_from_bottom, 'u_from_bottom');
  out.faces = _oneOf(o.faces, 'faces', FACES);
  return out;
}

/** The datacentre as a facility record: where it is and how to get in. */
function _facility(v) {
  const o = _object(v, 'facility');
  let country = _optString(o.country, 'country', 80) ?? null;
  if (country && /^[A-Za-z]{2}$/.test(country)) country = country.toUpperCase();
  return {
    code: _optString(o.code, 'code', 40) ?? null,
    address_line1: _optString(o.address_line1, 'address_line1', 200) ?? null,
    address_line2: _optString(o.address_line2, 'address_line2', 200) ?? null,
    city: _optString(o.city, 'city', 120) ?? null,
    region: _optString(o.region, 'region', 120) ?? null,
    postcode: _optString(o.postcode, 'postcode', 32) ?? null,
    country,
    provider: _optString(o.provider, 'provider', 160) ?? null,
    access_notes: _optString(o.access_notes, 'access_notes', 2000) ?? null,
    hours: _optString(o.hours, 'hours', 200) ?? null,
  };
}

const VALIDATORS = {
  contacts: _contacts,
  vendors: _vendors,
  conventions: _conventions,
  facility: _facility,
};

/** What a section looks like before anyone has written it. */
function _emptySection(section) {
  return VALIDATORS[section](section === 'contacts' || section === 'vendors' ? [] : {});
}

// ── Rows ────────────────────────────────────────────────────────────
function _tenant(tenantId) {
  _prep();
  const t = db.prepare('SELECT id, organization_id FROM tenants WHERE id = ?').get(Number(tenantId));
  if (!t) throw new ProfileError(404, 'Site not found');
  return t;
}
function _row(tenantId, section) {
  _prep();
  return db.prepare('SELECT * FROM tenant_profile WHERE tenant_id = ? AND section = ?')
    .get(Number(tenantId), section) || null;
}
function _parse(row) {
  try { return JSON.parse(row.data); } catch { return null; }
}
function _meta(row) {
  if (!row) return null;
  return {
    source: row.source,
    created_by: row.created_by ?? null,
    created_at: row.created_at,
    updated_by: row.updated_by ?? null,
    updated_at: row.updated_at,
  };
}
function _write(tenantId, section, data, source, userId) {
  db.prepare(`
    INSERT INTO tenant_profile (tenant_id, section, data, source, created_by, updated_by)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(tenant_id, section) DO UPDATE SET
      data = excluded.data,
      source = excluded.source,
      updated_by = excluded.updated_by,
      updated_at = datetime('now')
  `).run(Number(tenantId), section, JSON.stringify(data), source, userId, userId);
}

function _sectionOrThrow(section) {
  if (!SECTIONS.includes(section)) throw new ProfileError(404, 'No such section');
  return section;
}

/** Every section plus, per section, who wrote it and when. */
function tenantProfile(tenantId) {
  const t = _tenant(tenantId);
  const out = { updated: {} };
  for (const s of SECTIONS) {
    const row = _row(t.id, s);
    out[s] = (row && _parse(row)) || _emptySection(s);
    out.updated[s] = _meta(row);
  }
  return out;
}

/** Replace a section whole. Returns { data, updated }. */
function putSection(tenantId, section, body, userId = null, source) {
  const t = _tenant(tenantId);
  _sectionOrThrow(section);
  const src = _source(source);
  const data = VALIDATORS[section](body);
  _write(t.id, section, data, src, userId);
  logger.info({ event: 'profile.section_written', tenantId: t.id, section, userId, source: src,
    items: Array.isArray(data) ? data.length : undefined }, `${section} replaced`);
  const row = _row(t.id, section);
  return { data: _parse(row), updated: _meta(row) };
}

/** Clear a section. True when a row was there to remove. */
function deleteSection(tenantId, section, userId = null) {
  const t = _tenant(tenantId);
  _sectionOrThrow(section);
  const r = db.prepare('DELETE FROM tenant_profile WHERE tenant_id = ? AND section = ?').run(t.id, section);
  logger.info({ event: 'profile.section_cleared', tenantId: t.id, section, userId, existed: r.changes > 0 }, `${section} cleared`);
  return r.changes > 0;
}

/** The completeness flags this module answers for (lib/estate.completeness). */
function flags(tenantId) {
  _prep();
  const get = (s) => { const r = _row(tenantId, s); return r ? _parse(r) : null; };
  const conv = get('conventions');
  const vendors = get('vendors');
  const contacts = get('contacts');
  return {
    conventions: !!conv && PATTERN_FIELDS.some((f) => !!conv[f]),
    vendors: Array.isArray(vendors) && vendors.length > 0,
    people: Array.isArray(contacts) && contacts.length > 0,
  };
}

// ── Vendor catalogue ────────────────────────────────────────────────
let _vendorNames = null;

/** The 117 makers switch_ocr knows, display names only, sorted; read once. */
function vendorCatalogue() {
  if (_vendorNames) return _vendorNames;
  let names;
  try {
    names = Object.keys(JSON.parse(fs.readFileSync(VENDORS_FILE, 'utf8')).vendors || {});
  } catch (err) {
    // Not cached: a fixed file is picked up on the next call.
    logger.warn({ event: 'profile.catalogue_unavailable', file: VENDORS_FILE, err: err.message }, 'vendor catalogue could not be read');
    return [];
  }
  _vendorNames = names
    .map((name) => ({ name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }));
  return _vendorNames;
}

module.exports = {
  ProfileError,
  SECTIONS,
  CONTACT_ROLES,
  FACES,
  getOrganization,
  orgAccessLevel,
  orgProfile,
  orgProfileComplete,
  updateOrgProfile,
  compilePattern,
  checkPattern,
  tenantProfile,
  putSection,
  deleteSection,
  flags,
  vendorCatalogue,
};
