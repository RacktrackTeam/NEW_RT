// Single source of truth for "may this caller touch this rack?".
//
// There were three copies of this policy — app.js's app.param, and a
// router.param in each of two mounted routers, because Express does not fire a
// parent app.param for routes on a mounted router. The three drifted, which is
// exactly what an audit found: one router had no check at all, and the other
// two disagreed with each other about a user whose
// tenant_id is NULL — app.param let them through to every rack on the
// platform, while canAccessRack refused them. Two guards for one policy that
// disagree is the defect; the fix is that there is now one.
//
// The policy, and why 404 rather than 403 throughout: a 403 confirms the rack
// exists, which is itself a cross-tenant disclosure. Denied callers are told
// the rack is not there.
//
//   owner      → every rack on the platform
//   org_admin  → any rack held by a Site in their organization
//   anyone else→ their own Site only, and ONLY if they have one
//
// Every branch fails closed. A principal with no Site inherits nothing.

// Rack ids are `RK-` plus a truncated SHA-256, so anything else is either a
// typo or an attempt to walk out of the outputs directory via path.join.
const RACK_ID_RE = /^RK-[A-Za-z0-9]{4,32}$/;

function isValidRackId(rackId) {
  return typeof rackId === 'string' && RACK_ID_RE.test(rackId);
}

// Callers hand us either a JWT payload (camelCase, from softAuthPayload) or a
// req.user row (snake_case, from requireAuth). Normalise rather than making
// every call site remember which shape it holds.
function normalize(principal) {
  if (!principal) return null;
  return {
    role: principal.role,
    tenantId: principal.tenantId ?? principal.tenant_id ?? null,
    organizationId: principal.organizationId ?? principal.organization_id ?? null,
  };
}

/**
 * @param {object|null} principal JWT payload or req.user; null when unauthenticated
 * @param {string} rackId
 * @param {object} tenant the lib/tenant module
 * @returns {boolean}
 */
function canAccessRack(principal, rackId, tenant) {
  const auth = normalize(principal);
  if (!auth) return false;
  if (auth.role === 'owner') return true;
  // An org_admin sees every rack in their organization...
  if (auth.role === 'org_admin'
      && auth.organizationId
      && tenant.rackInOrg(rackId, auth.organizationId)) {
    return true;
  }
  // ...and, failing that, falls through to the same site check everyone else
  // gets. It used to return early on the org test, which left an org_admin
  // with strictly LESS access than a plain member of the same site: a user
  // whose organization has no site of its own sits on the legacy default
  // tenant, so `rackInOrg` finds nothing and their own scans became
  // unreachable — the results page loaded with zero devices and the rack
  // photo 404'd, for the person who took it.
  //
  // This does not widen anything across a boundary: tenantOwnsRack only ever
  // answers yes for racks the caller's own site owns, which is exactly the
  // access a member of that site already has.
  if (!auth.tenantId) return false;
  return tenant.tenantOwnsRack(auth.tenantId, rackId);
}

/**
 * The rack ids a list (scan history, the rack list) may show this caller.
 * Returns null for "no restriction" (the platform owner), else a Set.
 *
 *   owner         every rack
 *   org_admin     every rack of every Site in their organization, and their own
 *                 Site's
 *   site_manager  every rack scanned at their Site, by anyone. He is the one
 *                 who answers for that Site, so the technicians' scans are his
 *                 to see. Before 28 Sep 2026 he fell through to the member rule
 *                 and his history was empty.
 *   member        with perUser: only the racks he claimed himself (scan
 *                 history); without: every rack his Site holds (the rack list).
 *
 * The same answers canAccessRack gives for one rack, so every rack a list
 * names also opens. Fails closed: no Site, no racks.
 *
 * @param {object} principal req.user row or JWT payload
 * @param {object} tenant    the lib/tenant module
 * @param {object} [opts]
 * @param {number} [opts.userId]  the caller's user id
 * @param {boolean} [opts.perUser] members see only their own claims
 */
function visibleRackIds(principal, tenant, { userId = null, perUser = false } = {}) {
  const auth = normalize(principal);
  if (!auth) return new Set();
  if (auth.role === 'owner') return null;
  if (auth.role === 'org_admin' && auth.organizationId) {
    const ids = tenant.orgRackIds(auth.organizationId);
    if (auth.tenantId) for (const id of tenant.tenantRackIds(auth.tenantId)) ids.add(id);
    return ids;
  }
  if (!auth.tenantId) return new Set();
  if (auth.role === 'site_manager' || !perUser) return tenant.tenantRackIds(auth.tenantId);
  return userId ? tenant.tenantUserRackIds(auth.tenantId, userId) : new Set();
}

/**
 * Build an Express `:rackId` param guard. Mount on the parent app AND on every
 * router that declares `:rackId` — app.param does not reach mounted routers.
 *
 * @param {object} opts
 * @param {object} opts.tenant       lib/tenant
 * @param {object} [opts.logger]     lib/observability logger
 * @param {function} [opts.getPrincipal] req => principal. Defaults to req.user,
 *   which is right for routers behind requireAuth. app.js passes its
 *   softAuthPayload instead, because it reads the cookie as well as the header.
 * @param {function} [opts.allow]    req, rackId => boolean. An escape hatch for
 *   a read-only capability that is not a session.
 */
function rackOwnershipParam({ tenant, logger, getPrincipal, allow } = {}) {
  const principalOf = getPrincipal || ((req) => req.user);

  return function rackIdGuard(req, res, next, rackId) {
    // Shape FIRST, always. This used to sit below the `allow` hatch, so a
    // capability token minted for a rackId of `../../server/data` skipped the
    // one check that exists because rackId reaches path.join — the validation
    // was absent on exactly the path that needed it most.
    if (!isValidRackId(rackId)) {
      return res.status(400).json({ error: 'Invalid rack id' });
    }

    // A capability is READ-ONLY. It used to admit any method, which meant a
    // forwarded link could POST to the rack routes that carry no requireAuth
    // of their own, mutating OCR device lists and side labels and spending
    // server CPU with no attributable identity.
    if (allow && allow(req, rackId)) {
      if (req.method === 'GET' || req.method === 'HEAD') return next();
      return res.status(401).json({ error: 'Authentication required' });
    }

    const principal = principalOf(req);
    if (!principal) {
      return res.status(401).json({ error: 'Authentication required' });
    }

    if (canAccessRack(principal, rackId, tenant)) return next();

    const auth = normalize(principal);
    logger?.warn({
      event: 'tenant.access_denied',
      role: auth.role, tenantId: auth.tenantId,
      organizationId: auth.organizationId, rackId, route: req.path,
    }, `blocked from rack ${rackId}`);
    return res.status(404).json({ error: 'Rack not found' });
  };
}

module.exports = { canAccessRack, visibleRackIds, rackOwnershipParam, isValidRackId, RACK_ID_RE };
