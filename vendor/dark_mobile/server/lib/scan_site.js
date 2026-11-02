/**
 * Which Site a scan is for.
 *
 * A technician belongs to one Site and every scan they run is that Site's. An
 * organisation admin and the owner belong to none, so their scans used to land
 * on the default tenant: claimed for nobody's Site, matched against nobody's
 * racks, and sent to no SPOC. The scan screen now names the Site, and that
 * choice replaces the caller's own tenant for the whole request - the claim,
 * the space check, the scan meta and the scope the rack id is minted under -
 * so an admin scanning at Site 32 gets the rack id a Site 32 technician would.
 *
 * Two things live here and nowhere else:
 *   listFor(user)                  the Sites the scan screen offers this person
 *   resolve(authPayload, siteId)   the Site one scan request is for, or a 404
 *
 * Who may name which Site is lib/estate.accessLevel, the rule organisation
 * setup already answers to; read is enough. A Site the caller may not read is
 * 404, never 403, as everywhere else: a 403 would confirm the Site exists.
 *
 * No coordinates and no address leave this file. The Site is chosen by a
 * person, not worked out from where the phone is.
 */
const path = require('path');
const Database = require('better-sqlite3');
const estate = require('./estate');

const dbPath = process.env.RACKTRACK_AUTH_DB
  || path.join(__dirname, '..', 'data', 'auth.db');
const db = new Database(dbPath);
db.pragma('journal_mode = WAL');

/** The most Sites one list carries. An owner with no organisation sees them all. */
const MAX_SITES = 200;

const NOT_FOUND = () => ({ ok: false, status: 404, error: 'Site not found' });

/** The Site's number as a person reads it. */
const labelOf = (id) => `Site ${id}`;

/** The spaces of a Site, flattened depth first, the way the space picker draws them. */
function flatSpaces(tenantId) {
  const out = [];
  const walk = (list, depth) => {
    for (const sp of list || []) {
      out.push({ id: sp.id, name: sp.name, depth });
      walk(sp.children, depth + 1);
    }
  };
  walk(estate.spaceTree(tenantId), 0);
  return out;
}

function siteRow(t) {
  // `id` is the rack's own row in this Site's setup, which is what
  // POST /api/scan/:rackId/identity/confirm takes as knownRackId. The scan
  // screen does not need it; the Overview does, so a person looking at a rack
  // nothing could identify can pick it from the racks of their Site by hand.
  const racks = estate.listRacks(t.id).map((r) => ({
    id: r.id, rackId: r.rack_id ?? null, name: r.name ?? null, spaceId: r.space_id ?? null,
  }));
  return {
    id: t.id,
    siteId: labelOf(t.id),
    name: t.name,
    rackCount: racks.length,
    racks,
    spaces: flatSpaces(t.id),
    hasSpoc: Boolean(estate.getApprover(t.id)),
  };
}

/**
 * The Sites this person may scan for, by name, and the one to start on.
 *
 *   member, site_manager, approver, auditor   their own Site
 *   org_admin, an owner inside an organisation   every Site of it, and nothing
 *                                             outside it - not even the shared
 *                                             default tenant they may sit on
 *   an owner with no organisation             every Site
 *
 * `preselect` is the only Site, else the caller's own when it is listed, else
 * null: somebody with several Sites and none of their own has to choose.
 */
function listFor(user) {
  if (!user) return { sites: [], preselect: null };
  const own = Number(user.tenant_id ?? user.tenantId ?? 0) || null;
  const org = Number(user.organization_id ?? user.organizationId ?? 0) || null;
  const wide = user.role === 'owner' || user.role === 'org_admin';

  let rows;
  if (wide && org) {
    // Their own tenant is listed only when it is a Site of the organisation.
    // An admin made by the owner sits on the shared default tenant, which
    // belongs to no organisation: offering it would put the scan back there.
    rows = db.prepare(
      `SELECT id, name, organization_id FROM tenants
       WHERE organization_id = ? ORDER BY name COLLATE NOCASE, id LIMIT ?`)
      .all(org, MAX_SITES);
  } else if (user.role === 'owner') {
    // The platform owner oversees other organisations; it does not scan for
    // them (the owner, 25 September 2026). An owner of no organisation is
    // offered only the Sites that belong to none.
    rows = db.prepare(
      `SELECT id, name, organization_id FROM tenants WHERE organization_id IS NULL
       ORDER BY name COLLATE NOCASE, id LIMIT ?`)
      .all(MAX_SITES);
  } else {
    rows = own
      ? db.prepare('SELECT id, name, organization_id FROM tenants WHERE id = ?').all(own)
      : [];
  }
  // The list never offers a Site the scan routes would then refuse.
  const sites = rows.filter((t) => estate.accessLevel(user, t.id)).map(siteRow);
  let preselect = null;
  if (sites.length === 1) preselect = sites[0].id;
  else if (own && sites.some((s) => s.id === own)) preselect = own;
  return { sites, preselect };
}

/**
 * The Site one scan request is for.
 *
 * No `siteId` is today's behaviour, untouched, so the phone builds already in
 * the field keep working: the payload as it came, and whichever tenant
 * `ownTenantOf` (app.js's scanOwnerTenantId) gives it.
 *
 * With one, the caller must be able to read that Site. `auth` is then a copy
 * of the payload as a technician of that Site would carry it: the Site as
 * `tenantId`, and the Site's organisation, because the rack id is scoped by
 * organisation first and an owner carries none of their own. A Site that
 * belongs to no organisation (the shared default tenant) never takes the
 * caller's own away: nobody loses an organisation they had.
 */
function resolve(authPayload, rawSiteId, ownTenantOf = (a) => a?.tenantId ?? null) {
  const raw = rawSiteId === undefined || rawSiteId === null ? '' : String(rawSiteId).trim();
  if (raw === '') {
    return { ok: true, chosen: false, tenantId: ownTenantOf(authPayload) ?? null, auth: authPayload };
  }
  if (!authPayload || !/^\d{1,15}$/.test(raw) || Number(raw) < 1) return NOT_FOUND();
  const siteId = Number(raw);
  let level = null;
  let site = null;
  try {
    level = estate.accessLevel({
      role: authPayload.role,
      tenant_id: authPayload.tenantId,
      organization_id: authPayload.organizationId,
    }, siteId);
    site = level ? estate.getTenant(siteId) : null;
  } catch { level = null; }
  if (!level || !site) return NOT_FOUND();
  // Never a scan by the platform owner into another organisation's Site: it
  // would file the rack, its check and its incident under them, and could move
  // a scan of theirs to another Site. Answered as "no such Site", like every
  // other refusal here. Reading their racks is untouched.
  if (authPayload.role === 'owner' && site.organization_id != null
      && Number(site.organization_id) !== Number(authPayload.organizationId || 0)) return NOT_FOUND();
  return {
    ok: true,
    chosen: true,
    tenantId: site.id,
    auth: {
      ...authPayload,
      tenantId: site.id,
      organizationId: site.organization_id ?? authPayload.organizationId ?? null,
    },
  };
}

module.exports = { listFor, resolve, labelOf, MAX_SITES };
